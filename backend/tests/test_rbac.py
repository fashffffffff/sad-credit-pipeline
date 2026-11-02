"""RBAC по матрице 6.1 контракта: кто что может, 403 пишется в аудит, клиент видит только своё и не видит служебного."""

import json

import pytest

from backend.app import audit
from backend.tests.conftest import good_form

STAFF = ("uw", "audit", "admin")


async def headers_of(env, who: str) -> dict:
    if who == "anon":
        return {}
    if who.startswith("cli"):
        return env.make_client(int(who[4:]))
    return await env.login(who)


# (метод, путь, роли, которым МОЖНО)  — тела подбираем так, чтобы прошло всё, кроме проверки роли
MATRIX = [
    ("GET", "/api/v1/products", ("cli_1", "uw", "audit", "admin")),
    ("GET", "/api/v1/applications", ("cli_1", "uw", "audit", "admin")),
    ("GET", "/api/v1/stats", ("uw", "audit", "admin")),
    ("GET", "/api/v1/audit", ("audit",)),
    ("GET", "/api/v1/audit/verify", ("audit",)),
    ("GET", "/api/v1/external", ("uw", "audit")),
    ("GET", "/api/v1/model/info", ("uw", "audit")),
    ("GET", "/api/v1/model/features", ("uw", "audit")),
    ("GET", "/api/v1/antifraud/graph", ("uw", "audit", "admin")),
    ("GET", "/api/v1/simulate/labels", ("admin",)),
    ("GET", "/api/v1/batch/last.csv", ("uw", "admin")),  # разрешённым вернётся 404 «ещё не запускали»
    ("POST", "/api/v1/simulate", ("admin",)),
    ("POST", "/api/v1/batch", ("uw", "admin")),
    ("POST", "/api/v1/quote", ("cli_1",)),
    ("POST", "/api/v1/demo/tamper-audit", ("admin",)),
    ("POST", "/api/v1/demo/restore-audit", ("admin",)),
    ("POST", "/api/v1/passport/oprf", ("cli_1",)),
    ("GET", "/api/v1/passport/public-key", ("cli_1",)),
    ("PUT", "/api/v1/chaos/smev", ("admin",)),
    ("POST", "/api/v1/events/ticket", ("cli_1", "uw", "audit", "admin")),
]
BODIES = {"/api/v1/simulate": {"count": 1}, "/api/v1/quote": good_form(), "/api/v1/passport/oprf": {"blinded": "YWJjZA=="},
          "/api/v1/chaos/smev": {"mode": "normal"}, "/api/v1/batch": None}


@pytest.mark.parametrize("method,path,allowed", MATRIX, ids=[f"{m} {p}" for m, p, _ in MATRIX])
async def test_role_matrix(env, method, path, allowed):
    """Роль, которой нельзя, получает 403 (и запись access_denied в аудит); анонимный запрос — 401."""
    for who in ("anon", "cli_1", "uw", "audit", "admin"):
        headers = await headers_of(env, who)
        body = BODIES.get(path)
        kwargs = {"json": body} if body is not None else {}
        resp = await env.client.request(method, path, headers=headers, **kwargs)
        if who == "anon":
            assert resp.status_code == 401, path
        elif who in allowed:
            assert resp.status_code != 403, (who, path, resp.text)
        else:
            assert resp.status_code == 403, (who, path)
            assert resp.json()["error_code"] == "FORBIDDEN"
    denied = [r for r in audit.list_records(env.state.db, 1000) if r["action"] == "access_denied"]
    if len(allowed) < 4:
        assert any(path in r["details"].get("what", "") for r in denied)


async def test_create_application_is_client_only(env, uw, admin, auditor):
    for headers in (uw, admin, auditor):
        r = await env.submit(good_form(), headers)
        assert r.status_code == 403


async def test_recalculate_and_review_are_underwriter_only(env, client, admin, auditor):
    d = await env.run(good_form(), client)
    for headers in (client, admin, auditor):
        for path, body in ((f"/api/v1/applications/{d['app_id']}/recalculate", {}),
                           (f"/api/v1/applications/{d['app_id']}/review", {"decision": "REFUSED", "comment": ""})):
            assert (await env.client.post(path, json=body, headers=headers)).status_code == 403


async def test_only_system_and_underwriter_recalculate_not_auditor_audit_read_only(env, client, auditor):
    """Аудитор читает журнал, но ничего не меняет."""
    d = await env.run(good_form(), client)
    assert (await env.client.get("/api/v1/audit", headers=auditor)).status_code == 200
    for method, path in (("POST", f"/api/v1/applications/{d['app_id']}/recalculate"), ("PUT", "/api/v1/chaos/bki"),
                         ("POST", "/api/v1/simulate")):
        assert (await env.client.request(method, path, headers=auditor, json={})).status_code == 403
    assert (await env.client.delete("/api/v1/audit", headers=auditor)).status_code == 405  # удалять аудит нельзя никому


async def test_client_sees_only_own_applications(env):
    a, b = env.make_client(1), env.make_client(2)
    mine = await env.run(good_form(), a)
    theirs = await env.run(good_form(passport_hash="0123456789abcdef"), b)
    listed = (await env.client.get("/api/v1/applications", headers=a)).json()
    assert [x["app_id"] for x in listed] == [mine["app_id"]]
    for path in ("", "/decision", "/certificate.pdf"):
        r = await env.client.get(f"/api/v1/applications/{theirs['app_id']}{path}", headers=a)
        assert r.status_code == 403, path
    denied = [r for r in audit.list_records(env.state.db) if r["action"] == "access_denied"]
    assert denied and denied[0]["app_id"] == theirs["app_id"] and denied[0]["actor"] == "cli_0001"
    # сотрудники видят все
    uw = await env.login("uw")
    assert len((await env.client.get("/api/v1/applications", headers=uw)).json()) == 2


async def test_unknown_application_is_404_in_error_format(env, uw):
    r = await env.client.get("/api/v1/applications/app_9999", headers=uw)
    assert r.status_code == 404
    assert set(r.json()) == {"error_code", "message", "retryable", "details"}


async def test_client_never_sees_staff_fields(env, client, uw, auditor, admin):
    """fraud, ml_hint, external — только сотрудникам (fraud: и админу); клиенту — ни ключей, ни staff-текстов."""
    resp = await env.submit(good_form(), client)
    await env.drain()
    app_id = resp.json()["app_id"]
    mine = (await env.client.get(f"/api/v1/applications/{app_id}", headers=client)).json()
    for key in ("fraud", "ml_hint", "external"):
        assert key not in mine
    blob = json.dumps(mine, ensure_ascii=False)
    assert "антифрод" not in blob.lower() and "правило 1" not in blob
    for headers, has in ((uw, {"fraud", "ml_hint", "external"}), (auditor, {"fraud", "ml_hint", "external"}),
                         (admin, {"fraud"})):
        d = (await env.client.get(f"/api/v1/applications/{app_id}", headers=headers)).json()
        assert has <= set(d)
        assert ({"ml_hint", "external"} - has).isdisjoint(d)
    # тексты причин: клиенту без цифр и номеров правил, сотруднику — подробно
    env.world.bki_default = {"score": 400, "delinquency": True, "requests_90d": 1}
    env.state.settings.duplicate_check = False
    d2 = await env.run(good_form(), client)
    staff = (await env.client.get(f"/api/v1/applications/{d2['app_id']}/decision", headers=uw)).json()
    cl = (await env.client.get(f"/api/v1/applications/{d2['app_id']}/decision", headers=client)).json()
    assert "правило 7" in staff["reason_text"] and "правило" not in cl["reason_text"]


async def test_client_events_only_own_and_not_fraud(env):
    """SSE: клиент получает stage и decision только своих заявок; fraud/graph/security — ему не уходят."""
    bus = env.state.bus
    mine, other = bus.subscribe("cli_0001", "client"), bus.subscribe("cli_0002", "client")
    staff = bus.subscribe("uw", "underwriter")
    bus.publish("decision", {"x": 1}, owner="cli_0001")
    bus.publish("fraud", {"x": 2}, owner="cli_0001", client_visible=False)
    bus.publish("graph", {"x": 3})
    bus.publish("security", {"x": 4})
    bus.publish("stage", {"stage": "antifraud"}, owner="cli_0001", client_visible=False)
    assert [mine.get_nowait()[0]] == ["decision"] and mine.empty()
    assert other.empty()
    assert [staff.get_nowait()[0] for _ in range(staff.qsize())] == ["decision", "fraud", "graph", "stage"]
