"""Здоровье, продукты, расчёт quote, модель, статистика, хаос-прокси, демо-ручки аудита, SSE."""

import json

import httpx

from backend.app import audit
from backend.app.factory import create_app
from backend.tests.conftest import FakeModel, TEST_CREDENTIALS, World, good_form, make_env, make_transport


async def test_health_reports_services(env):
    for path in ("/health", "/api/v1/health"):
        body = (await env.client.get(path)).json()
        assert body == {"status": "ok", "services": {"smev": "up", "bki": "up", "tokenizer": "up", "model": "ready"}}


async def test_health_degraded_when_model_missing(env_no_model):
    body = (await env_no_model.client.get("/api/v1/health")).json()
    assert body["status"] == "degraded" and body["services"]["model"] == "unavailable"


async def test_products_and_quote(env, client, uw):
    products = (await env.client.get("/api/v1/products", headers=client)).json()
    assert {p["product_id"] for p in products} == {"prod_standard", "prod_premium", "prod_student", "prod_pension", "prod_express"}
    form = {k: v for k, v in good_form().items() if k != "passport_hash"}
    q = (await env.client.post("/api/v1/quote", json=form, headers=client)).json()
    assert q["dti"] == 0.21 and q["monthly_payment"] == 24962 and q["partial_score"] == 80 and q["max_partial_score"] == 90
    assert q["blocking"] == [] and "prod_standard" in {p["product_id"] for p in q["eligible_products"]}
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM applications")["n"] == 0  # ничего не сохраняется
    low = (await env.client.post("/api/v1/quote", json=form | {"income": 20000}, headers=client)).json()
    assert low["blocking"] and low["dti"] > 0  # финансовый расчёт есть и при неподходящем продукте
    high = (await env.client.post("/api/v1/quote", json=form | {"income": 50000, "term": 12}, headers=client)).json()
    assert high["blocking"] == ["HIGH_DTI"] and high["dti"] > 0.5
    assert (await env.client.post("/api/v1/quote", json=form | {"employment": "unemployed"}, headers=client)).json()["blocking"] == ["INVALID_EMPLOYMENT"]
    assert (await env.client.post("/api/v1/quote", json=form, headers=uw)).status_code == 403


async def test_model_info_and_features(env, uw, auditor):
    for headers in (uw, auditor):
        assert (await env.client.get("/api/v1/model/info", headers=headers)).json()["roc_auc"] == 0.86
        assert "feature_specs" in (await env.client.get("/api/v1/model/features", headers=headers)).json()


async def test_stats_by_status_and_reason(env, client, uw):
    env.state.settings.duplicate_check = False
    await env.run(good_form(), client)
    env.world.bki_default = {"score": 400, "delinquency": True, "requests_90d": 1}
    await env.run(good_form(), client)
    stats = (await env.client.get("/api/v1/stats", headers=uw)).json()
    assert stats["total"] == 2 and stats["by_status"]["APPROVED"] == 1 and stats["by_status"]["REFUSED"] == 1
    assert stats["by_reason"] == {"BKI_DELINQUENCY": 1} and stats["approval_rate"] == 0.5


async def chaos_env(tmp_path):
    """Приложение с фейковым СМЭВ, который умеет GET/PUT /chaos, как настоящий Go-сервис."""
    state = {"mode": "normal", "delay_ms": 0}
    from backend.app.config import Settings
    settings = Settings(database_path=str(tmp_path / "c.sqlite"), external_log="", cert_key_file=str(tmp_path / "k"),
                        stage_delay_ms=0, retry_delays=(0,), bcrypt_rounds=4, auto_retry_seconds=0)
    inner = make_transport("smev", settings.smev_secret, World())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/chaos":
            if request.method == "PUT":
                state.update(json.loads(request.content))
            return httpx.Response(200, json=dict(state))
        return inner.handler(request)

    app = create_app(settings, smev_transport=httpx.MockTransport(handler), model_loader=FakeModel, credentials=TEST_CREDENTIALS)
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), state


async def test_chaos_proxy_writes_audit_and_event(tmp_path):
    app, http, state = await chaos_env(tmp_path)
    admin_token = (await http.post("/api/v1/auth/staff/login", json={"username": "admin", "password": "test-only-admin-pw-x9Q2"})).json()["access_token"]
    admin = {"Authorization": f"Bearer {admin_token}"}
    ctx = app.state.ctx
    q = ctx.bus.subscribe("admin", "admin")
    r = await http.put("/api/v1/chaos/smev", json={"mode": "slow", "delay_ms": 4000}, headers=admin)
    assert r.status_code == 200 and state["mode"] == "slow"
    assert (await http.get("/api/v1/chaos/smev", headers=admin)).json()["mode"] == "slow"
    assert q.get_nowait() == ("chaos", {"service": "smev", "mode": "slow"})
    rec = audit.list_records(ctx.db)[0]
    assert rec["action"] == "chaos_changed" and rec["actor"] == "admin" and rec["details"]["mode"] == "slow"
    # белый список полей, неизвестный сервис, неизвестный режим
    assert (await http.put("/api/v1/chaos/smev", json={"mode": "slow", "evil": "x" * 1000}, headers=admin)).status_code == 422
    assert (await http.put("/api/v1/chaos/smev", json={"mode": "boom"}, headers=admin)).status_code == 422
    assert (await http.get("/api/v1/chaos/passport", headers=admin)).status_code == 404
    assert state["mode"] == "slow"
    await http.aclose()


async def test_chaos_when_service_is_down(env, admin):
    # у фейкового БКИ из conftest нет /chaos → 404 от сервиса → backend отвечает 502 в формате Error
    r = await env.client.get("/api/v1/chaos/bki", headers=admin)
    assert r.status_code == 502 and r.json()["retryable"] is True


async def test_demo_tamper_and_restore_audit(env, client, admin, auditor, uw):
    await env.run(good_form(), client)
    assert (await env.client.get("/api/v1/audit/verify", headers=auditor)).json()["ok"] is True
    r = await env.client.post("/api/v1/demo/tamper-audit", headers=admin)
    assert r.status_code == 200
    broken = (await env.client.get("/api/v1/audit/verify", headers=auditor)).json()
    assert broken["ok"] is False and f"evt_{broken['broken_at']:04d}" == r.json()["tampered"]
    assert (await env.client.post("/api/v1/demo/tamper-audit", headers=admin)).status_code == 409  # уже подделано
    assert (await env.client.post("/api/v1/demo/restore-audit", headers=admin)).status_code == 200
    assert (await env.client.get("/api/v1/audit/verify", headers=auditor)).json()["ok"] is True
    assert (await env.client.post("/api/v1/demo/restore-audit", headers=admin)).status_code == 409
    assert (await env.client.post("/api/v1/demo/tamper-audit", headers=uw)).status_code == 403


async def test_demo_handles_closed_in_production(tmp_path):
    e = make_env(tmp_path, production=True)
    admin = await e.login("admin")
    for path in ("tamper-audit", "restore-audit"):
        assert (await e.client.post(f"/api/v1/demo/{path}", headers=admin)).status_code == 404
    await e.client.aclose()


async def test_events_stream_requires_ticket_and_old_paths_are_gone(env, uw):
    assert (await env.client.get("/api/v1/events/stream")).status_code == 401
    for old in ("/api/applications", "/api/auth/login", "/api/health"):
        assert (await env.client.get(old, headers=uw)).status_code == 404
