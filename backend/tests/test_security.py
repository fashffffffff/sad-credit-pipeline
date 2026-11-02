"""Безопасность: секреты, подпись HMAC, JWT, SSE-билеты, журнал аудита, формат ошибок, мусор на входе, роли учёток."""

import asyncio
import json
import logging
import os
import stat
import time

import jwt
import pytest

from backend.app import audit
from backend.app.config import load_settings
from backend.app.credentials import credentials_by_role, generate_credentials, load_or_create_credentials
from backend.app.events import MAX_STREAMS_PER_USER, EventBus, TooManyStreams
from backend.app.pipeline.status import InvalidTransition, change_status
from backend.app.routes.misc import user_from_ticket
from backend.app.errors import ApiError
from backend.app.security.passwords import check_password, hash_password
from backend.tests.conftest import TEST_CREDENTIALS, TEST_PASSWORDS, good_form, make_env
from common.secrets_guard import check_secret
from common.signing import sign, verify

STRONG = "x" * 40
SECRETS = ("JWT_SECRET", "SMEV_HMAC_SECRET", "BKI_HMAC_SECRET", "TOKENIZER_HMAC_SECRET", "PII_PEPPER")


# ---------- секреты ----------
def test_production_refuses_default_secrets(monkeypatch):
    for name in SECRETS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        load_settings()
    for name in SECRETS:
        monkeypatch.setenv(name, STRONG + name)
    assert load_settings().production is True


def test_production_refuses_short_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError):
        check_secret("TOKENIZER_HMAC_SECRET", "short-but-not-default")


def test_dev_warns_loudly_but_starts(monkeypatch, caplog):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with caplog.at_level(logging.WARNING, logger="security"):
        settings = load_settings()
    assert settings.production is False
    assert any("JWT_SECRET" in r.message and "ВНИМАНИЕ" in r.message for r in caplog.records)


def test_empty_secret_refused_even_in_dev(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("TOKENIZER_HMAC_SECRET", "")
    with pytest.raises(RuntimeError, match="пуст"):
        load_settings()


def test_new_env_variables_are_read(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKENIZER_URL", "http://tok:9")
    monkeypatch.setenv("EXTERNAL_LOG", str(tmp_path / "e.log"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.org/")
    monkeypatch.setenv("AUTO_RETRY_SECONDS", "5")
    monkeypatch.setenv("CERT_FONT", "/x.ttf")
    s = load_settings()
    assert (s.tokenizer_url, s.public_base_url, s.auto_retry_seconds, s.cert_font) == ("http://tok:9", "https://example.org", 5, "/x.ttf")
    assert s.database_path.endswith("credit_v6.sqlite")


# ---------- подпись HMAC ----------
def test_hmac_with_empty_secret_is_refused():
    with pytest.raises(ValueError):
        sign("", b"{}")
    forged = sign("real-secret", b"{}")
    assert verify("", b"{}", forged["X-Signature"], forged["X-Timestamp"]) is False


def test_hmac_non_ascii_signature_does_not_crash():
    ok = sign("s", b"{}")
    assert verify("s", b"{}", "подпись", ok["X-Timestamp"]) is False


def test_hmac_old_timestamp_rejected():
    old = sign("s", b"{}", timestamp=int(time.time()) - 1000)
    assert verify("s", b"{}", old["X-Signature"], old["X-Timestamp"]) is False


async def test_bad_response_signature_is_error_and_audited(env, client):
    env.world.modes["bki"] = "bad_signature"
    d = await env.run(good_form(), client)
    assert d["status"] == "NEED_MORE_DATA"
    assert "signature_error" in [r["action"] for r in audit.list_records(env.state.db)]
    assert all(r["status"] == "ERROR" for r in env.state.db.query_all(
        "SELECT status FROM external_requests WHERE service IN ('BKI', 'BKI_REPORT')"))


# ---------- /docs ----------
async def test_docs_hidden_in_production(tmp_path):
    e = make_env(tmp_path, production=True)
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert (await e.client.get(path)).status_code == 404
    await e.client.aclose()
    e = make_env(tmp_path / "dev")
    assert (await e.client.get("/openapi.json")).status_code == 200
    await e.client.aclose()


# ---------- JWT ----------
async def test_forged_jwt_variants_rejected(env):
    secret = env.state.settings.jwt_secret
    exp = time.time() + 600
    forged = [
        jwt.encode({"sub": "uw", "role": "underwriter", "tv": 0, "exp": exp}, "wrong-secret-xxxxxxxxxxxxxxxxxxxxxx", algorithm="HS256"),
        jwt.encode({"sub": "uw", "role": "underwriter", "tv": 0, "exp": time.time() - 10}, secret, algorithm="HS256"),
        jwt.encode({"sub": "uw", "role": "underwriter", "tv": 0}, secret, algorithm="HS256"),  # без exp
        jwt.encode({"sub": "uw", "role": "underwriter", "tv": 0, "exp": exp}, None, algorithm="none"),
        "garbage.token.here", "",
    ]
    for token in forged:
        r = await env.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token[:30]
    assert (await env.client.get("/api/v1/auth/me")).status_code == 401


async def test_staff_role_claim_is_taken_from_db_not_token(env):
    """Токен с role=admin, выпущенный на логин uw, всё равно даёт роль из БД; role=client без клиента в БД — 401."""
    secret = env.state.settings.jwt_secret
    token = jwt.encode({"sub": "uw", "role": "admin", "tv": 0, "exp": time.time() + 600}, secret, algorithm="HS256")
    me = await env.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "underwriter"
    as_client = jwt.encode({"sub": "uw", "role": "client", "tv": 0, "exp": time.time() + 600}, secret, algorithm="HS256")
    assert (await env.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {as_client}"})).status_code == 401


# ---------- SSE ----------
async def test_sse_ticket_flow(env, client):
    r = await env.client.post("/api/v1/events/ticket", headers=client)
    ticket = r.json()["ticket"]
    user = user_from_ticket(env.state, ticket)
    assert (user.username, user.role) == ("cli_0001", "client")
    with pytest.raises(ApiError):  # билет одноразовый
        user_from_ticket(env.state, ticket)
    assert (await env.client.get("/api/v1/events/stream")).status_code == 401
    assert (await env.client.get("/api/v1/events/stream", params={"ticket": "nope"})).status_code == 401
    assert (await env.client.get("/api/v1/events/stream", params={"ticket": client["Authorization"][7:]})).status_code == 401  # JWT в URL не принимаем


async def test_sse_ticket_expires_and_dies_on_logout_all(env, uw):
    now = [100.0]
    env.state.clock = lambda: now[0]
    t1 = (await env.client.post("/api/v1/events/ticket", headers=uw)).json()["ticket"]
    now[0] += 31
    with pytest.raises(ApiError):
        user_from_ticket(env.state, t1)
    t2 = (await env.client.post("/api/v1/events/ticket", headers=uw)).json()["ticket"]
    await env.client.post("/api/v1/auth/logout-all", headers=uw)
    with pytest.raises(ApiError):
        user_from_ticket(env.state, t2)


def test_sse_stream_limit_and_kick():
    bus = EventBus()
    for _ in range(MAX_STREAMS_PER_USER):
        bus.subscribe("uw", "underwriter")
    with pytest.raises(TooManyStreams):
        bus.subscribe("uw", "underwriter")
    q = bus.subscribe("audit", "auditor")
    bus.kick("audit")
    assert q.get_nowait()[0] == "__close__"


def test_sse_visibility_by_role():
    bus = EventBus()
    q = {r: bus.subscribe(r, r) for r in ("underwriter", "auditor", "admin")}
    bus.publish("security", {"x": 1})
    bus.publish("graph", {"x": 2})
    got = {r: [q[r].get_nowait()[0] for _ in range(q[r].qsize())] for r in q}
    assert got == {"underwriter": ["graph"], "auditor": ["security", "graph"], "admin": ["security", "graph"]}


async def test_pipeline_events_carry_owner_and_hide_antifraud_stage(env):
    bus = env.state.bus
    mine, other = bus.subscribe("cli_0001", "client"), bus.subscribe("cli_0002", "client")
    headers = env.make_client(1)
    await env.run(good_form(), headers)
    events = [mine.get_nowait() for _ in range(mine.qsize())]
    stages = {e[1]["stage"] for e in events if e[0] == "stage"}
    assert "antifraud" not in stages and {"validation", "smev", "bki", "decision"} <= stages
    assert [e[0] for e in events].count("decision") == 1 and other.empty()


# ---------- журнал аудита ----------
async def test_audit_detects_tampering_and_deletion(env, client, auditor):
    await env.run(good_form(), client)
    assert (await env.client.get("/api/v1/audit/verify", headers=auditor)).json()["ok"] is True
    db = env.state.db
    target = db.query_one("SELECT id FROM audit_log WHERE action = 'decision_made'")["id"]
    db.execute("UPDATE audit_log SET details = '{\"decision\": \"APPROVED\", \"score\": 100}' WHERE id = ?", (target,))
    res = (await env.client.get("/api/v1/audit/verify", headers=auditor)).json()
    assert res["ok"] is False and res["broken_at"] == target
    db2 = make_env.__globals__["make_env"]  # noqa: F841  (проверка удаления — ниже, на свежей базе)


async def test_audit_detects_deleted_record(env, client):
    await env.run(good_form(), client)
    assert audit.verify_chain(env.state.db)["ok"]
    env.state.db.execute("DELETE FROM audit_log WHERE id = 1")
    assert audit.verify_chain(env.state.db)["ok"] is False


async def test_audit_format_is_case_format(env, client, auditor):
    d = await env.run(good_form(), client)
    rows = (await env.client.get("/api/v1/audit", headers=auditor, params={"app_id": d["app_id"]})).json()
    actions = {r["action"] for r in rows}
    assert actions == {"application_created", "decision_made"}
    made = next(r for r in rows if r["action"] == "decision_made")
    assert made["event_id"].startswith("evt_") and made["app_id"] == d["app_id"]
    assert (made["decision"], made["score"], made["dti"], made["actor"]) == ("APPROVED", 90, 0.21, "system")
    assert made["at"] and len(made["hash"]) == 64 and len(made["prev_hash"]) == 64
    # персональных данных в журнале нет: только id и суммы
    blob = json.dumps(rows)
    assert GOOD_HASH_FULL not in blob


GOOD_HASH_FULL = "f727a849055f2224"


async def test_audit_does_not_store_unknown_login_text(env):
    await env.client.post("/api/v1/auth/staff/login", json={"username": "мой-пароль-123", "password": "x"})
    assert "мой-пароль-123" not in json.dumps(audit.list_records(env.state.db, 100), ensure_ascii=False)


async def test_login_failures_are_audited_and_role_checked(env):
    await env.client.post("/api/v1/auth/staff/login", json={"username": "uw", "password": "bad"})
    rec = audit.list_records(env.state.db)[0]
    assert rec["action"] == "login_failed" and rec["actor"] == "uw"


# ---------- машина статусов ----------
def test_status_machine_rejects_forbidden_transition(env):
    db = env.state.db
    db.execute("INSERT INTO applications (app_id, client_id, passport_hash, product_id, channel, status, created_at) "
               "VALUES ('app_0001','cli_0001','h','prod_standard','online','APPROVED','x')")
    with pytest.raises(InvalidTransition):
        change_status(db, "app_0001", "REFUSED")
    with pytest.raises(InvalidTransition):
        change_status(db, "app_0001", "MANUAL_REVIEW")
    assert change_status(db, "app_0001", "PROCESSING") == "APPROVED"  # пересчёт разрешён


async def test_review_only_for_manual_review_and_comment_limited(env, client, uw):
    d = await env.run(good_form(), client)
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/review", headers=uw,
                              json={"decision": "REFUSED", "comment": "x"})
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_TRANSITION"
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/review", headers=uw,
                              json={"decision": "REFUSED", "comment": "я" * 1001})
    assert r.status_code == 422
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/review", headers=uw, json={"decision": "approved"})
    assert r.status_code == 422


# ---------- мусор на входе ----------
async def test_error_format_for_validation_errors(env, client):
    r = await env.client.post("/api/v1/applications", headers=client | {"Idempotency-Key": "k"}, content=b"{not json")
    assert r.status_code == 422 and set(r.json()) == {"error_code", "message", "retryable", "details"}
    r = await env.client.post("/api/v1/auth/staff/login", json={"username": 5})
    assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR"
    assert "username" in r.json()["details"]["fields"]


async def test_deep_json_and_garbage_bodies_are_422_not_500(env, client):
    deep = b"[" * 5000 + b"]" * 5000
    r = await env.client.post("/api/v1/applications", headers=client | {"Idempotency-Key": "deep"}, content=deep)
    assert r.status_code == 422
    for body in (b"null", b"[]", b'"str"', b"123", b"{}"):
        r = await env.client.post("/api/v1/applications", headers=client | {"Idempotency-Key": "g" + str(len(body))}, content=body)
        assert r.status_code == 422, body


@pytest.mark.parametrize("field,values", [
    ("age", [None, "abc", 17.5, -1, 71, 10**30, True, [], {}, float("inf")]),
    ("income", [None, "abc", 0, -5, 10**400, True, [], {}]),
    ("amount", [None, "x", 9999, 5_000_001, 10**20, True]),
    ("term", [None, "x", 2, 61, 1.5, True]),
    ("employment", [None, "boss", 5, "UNEMPLOYED "]),
    ("family", [None, "x", 1]),
    ("product_id", ["prod_x", 5, [], "prod_student "]),
    ("passport_hash", [None, "short", "G" * 16, "F727A849055F2224", 123, "f727a849055f2224f"]),
])
async def test_form_fields_never_500(env, client, field, values):
    for value in values:
        form = good_form() | {field: value}
        try:
            r = await env.submit(form, client)
        except ValueError:  # инфинити и NaN не умеет сериализовать сам httpx: до сервера это не дойдёт
            continue
        assert r.status_code in (201, 409, 422), (field, value, r.status_code, r.text)
        if r.status_code == 422:
            body = r.json()
            assert body["error_code"] and field in body["details"]["fields"] or body["error_code"] == "PRODUCT_MISMATCH"
            assert str(value) not in body["message"] or isinstance(value, bool)  # введённое значение не эхо-ится
    await env.drain()


async def test_validation_error_names_field_in_catalog_codes(env, client):
    for field, value, code in (("age", 17, "AGE_OUT_OF_RANGE"), ("employment", "boss", "INVALID_EMPLOYMENT"),
                               ("amount", 5, "INVALID_AMOUNT"), ("term", 1, "INVALID_TERM"),
                               ("passport_hash", "zz", "INVALID_PASSPORT_HASH"), ("family", "x", "INVALID_FAMILY")):
        r = await env.submit(good_form() | {field: value}, client)
        assert r.status_code == 422 and r.json()["error_code"] == code, (field, r.text)
    r = await env.submit(good_form(product_id="prod_premium", income=40000), client)  # премиум от 150 000 ₽
    assert r.status_code == 422 and r.json()["error_code"] == "PRODUCT_MISMATCH"
    assert r.json()["details"]["eligible_products"]


async def test_client_device_id_garbage(env, client):
    for bad in ("<script>", "a b", "x" * 65, 5, "'; DROP TABLE applications;--"):
        r = await env.submit(good_form(client_device_id=bad), client)
        assert r.status_code == 422, bad
    assert (await env.submit(good_form(client_device_id="dev-1234.abc"), client)).status_code == 201
    await env.drain()


async def test_sql_injection_probes_are_inert(env, client, uw):
    d = await env.run(good_form(), client)
    for probe in ("' OR '1'='1", "1; DROP TABLE applications;--", "%' UNION SELECT * FROM users--"):
        for path, params in (("/api/v1/applications", {"status": probe}), ("/api/v1/external", {"app_id": probe}),
                             (f"/api/v1/applications/{probe}", {})):
            r = await env.client.get(path, params=params, headers=uw)
            assert r.status_code in (200, 404, 422), (path, r.status_code)
    assert (await env.client.get(f"/api/v1/applications/{d['app_id']}", headers=uw)).status_code == 200
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM users")["n"] == 3


async def test_unhandled_error_has_no_traceback(env, uw):
    def boom(*a, **k):
        raise RuntimeError("секретный путь /etc/passwd")
    env.state.bus.publish = boom
    r = await env.client.post("/api/v1/events/ticket", headers=uw)  # не использует publish
    assert r.status_code == 200
    env.app.router.routes  # noqa: B018
    r = await env.client.get("/api/v1/applications/app_0001", headers=uw)
    assert "passwd" not in r.text and "Traceback" not in r.text


async def test_model_errors_not_leaked(tmp_path):
    def loader():
        raise RuntimeError("не удалось открыть /Users/secret/model.cbm")

    e = make_env(tmp_path, model_loader=loader)
    uw = await e.login("uw")
    r = await e.client.get("/api/v1/model/info", headers=uw)
    assert r.status_code == 503 and "/Users" not in r.text and r.json()["retryable"] is True
    client = e.make_client(1)
    d = await e.run(good_form(), client)
    assert d["status"] == "APPROVED"  # подсказка не работает, решение — работает
    health = (await e.client.get("/api/v1/health")).json()
    assert health["services"]["model"] == "unavailable" and "/Users" not in json.dumps(health)
    await e.client.aclose()


async def test_cors_is_narrow(env):
    ok = await env.client.options("/api/v1/products", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = await env.client.options("/api/v1/products", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


# ---------- пароли и учётки сотрудников ----------
def test_passwords_are_bcrypt(env):
    rows = env.state.db.query_all("SELECT username, password_hash FROM users")
    assert {r["username"] for r in rows} == set(TEST_PASSWORDS)
    for r in rows:
        assert r["password_hash"].startswith("$2") and TEST_PASSWORDS[r["username"]] not in r["password_hash"]
        assert check_password(TEST_PASSWORDS[r["username"]], r["password_hash"])
    assert not check_password("я" * 60, hash_password("x", 4))  # > 72 байт — просто «нет», без исключения


def test_generated_credentials_roles():
    creds = generate_credentials()
    assert [c["role"] for c in creds] == ["underwriter", "auditor", "admin"]
    assert len({c["password"] for c in creds}) == 3 and all(len(c["password"]) >= 20 for c in creds)
    assert all("operator" not in c["role"] for c in creds)


def test_credentials_file_private_tightened_and_validated(tmp_path):
    path = tmp_path / "secrets" / "c.json"
    creds = load_or_create_credentials(str(path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(path.with_suffix(".md")).st_mode) == 0o600
    os.chmod(path, 0o644)
    assert load_or_create_credentials(str(path)) == creds and stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert credentials_by_role(str(path))["underwriter"]["username"].startswith("uw.")
    path.write_text(json.dumps([{"username": "a", "role": "superuser", "password": "x"}]))
    with pytest.raises(RuntimeError, match="неизвестная роль"):
        load_or_create_credentials(str(path))


async def test_removed_user_loses_access_and_password_change_revokes_tokens(tmp_path):
    from backend.app.factory import create_app  # noqa: F401
    e = make_env(tmp_path)
    old = await e.login("uw")
    assert (await e.client.get("/api/v1/auth/me", headers=old)).status_code == 200
    e.state.db.sync_users([c for c in TEST_CREDENTIALS if c["username"] != "uw"], 4)
    assert (await e.client.get("/api/v1/auth/me", headers=old)).status_code == 401  # пользователя убрали из файла
    changed = [dict(c, password="new-password-for-audit-123") if c["username"] == "audit" else c for c in TEST_CREDENTIALS]
    token = await e.login("audit")
    e.state.db.sync_users(changed, 4)
    assert (await e.client.get("/api/v1/auth/me", headers=token)).status_code == 401
    await e.client.aclose()
