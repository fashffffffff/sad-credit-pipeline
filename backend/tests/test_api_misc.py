"""Остальные ручки: what-if, статистика, симуляция, хаос-прокси, health, генератор анкет."""

import random

import httpx

from backend.app.schemas import ApplicationIn, inn10_is_valid
from backend.app.simulate import random_application
from backend.tests.conftest import GOOD_BKI, good_form


def test_inn_checksum():
    """Ожидаемый результат: настоящий ИНН Сбера проходит, с изменённой последней цифрой — нет."""
    assert inn10_is_valid("7707083893")
    assert not inn10_is_valid("7707083890")
    assert not inn10_is_valid("770708389")


def test_random_applications_are_valid():
    """Ожидаемый результат: 300 сгенерированных анкет проходят те же правила валидации, что и ручной ввод."""
    rng = random.Random(1)
    for _ in range(300):
        ApplicationIn.model_validate(random_application(rng))


async def test_whatif_and_model_info(env, operator):
    """Ожидаемый результат: what-if возвращает pd/score/contributions без сохранения заявки;
    не хватает признака -> 422; /model/info отдаёт метрики."""
    features = {"n_loans": 3, "bad_months": 1, "has_current_overdue": 0}
    resp = await env.client.post("/api/score/whatif", json=features, headers=operator)
    assert resp.status_code == 200 and resp.json()["score"] == 740
    assert resp.json()["contributions"][0]["feature"] == "bad_months"
    assert env.state.db.query_one("SELECT COUNT(*) AS c FROM applications")["c"] == 0

    del features["n_loans"]
    bad = await env.client.post("/api/score/whatif", json=features, headers=operator)
    assert bad.status_code == 422 and "n_loans" in bad.json()["fields"]
    features["n_loans"] = "много"
    assert (await env.client.post("/api/score/whatif", json=features, headers=operator)).status_code == 422
    assert (await env.client.get("/api/model/info", headers=operator)).json()["roc_auc"] == 0.86


async def test_model_features_endpoint(env, operator):
    """Ожидаемый результат: /api/model/features отдаёт feature_specs и paym_codes модели (для ползунков и тепловой карты)."""
    body = (await env.client.get("/api/model/features", headers=operator)).json()
    assert [s["feature"] for s in body["feature_specs"]] == ["n_loans", "bad_months", "has_current_overdue"]
    assert "0" in body["paym_codes"]


async def test_simulate_and_stats(env):
    """Ожидаемый результат: /simulate принимает count и запускает заявки в фоне; потом статистика
    показывает total=count, все заявки вышли из processing, сумма by_status сходится."""
    admin = await env.login("admin")
    resp = await env.client.post("/api/simulate", json={"count": 12}, headers=admin)
    assert resp.status_code == 202
    await env.drain()
    stats = (await env.client.get("/api/stats", headers=admin)).json()
    assert stats["total"] == 12
    assert stats["by_status"]["processing"] == 0
    assert sum(stats["by_status"].values()) == 12
    assert len(stats["score_histogram"]["counts"]) == len(stats["score_histogram"]["bins"]) - 1
    assert (await env.client.post("/api/simulate", json={"count": 301}, headers=admin)).status_code == 422


async def test_chaos_proxy_writes_audit_and_event(env):
    """Ожидаемый результат: PUT /chaos/smev проксируется к эмулятору, меняет режим в /stats,
    пишет chaos_change в аудит и шлёт SSE-событие chaos."""
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            sent.update(__import__("json").loads(request.content))
            return httpx.Response(200, json=sent)
        return httpx.Response(200, json={"mode": "normal", "delay_ms": 0})

    env.state.smev.http = httpx.AsyncClient(base_url="http://smev", transport=httpx.MockTransport(handler))
    admin = await env.login("admin")
    queue = env.state.bus.subscribe()
    resp = await env.client.put("/api/chaos/smev", json={"mode": "slow", "delay_ms": 4000}, headers=admin)
    assert resp.status_code == 200 and sent["mode"] == "slow"
    assert (await env.client.get("/api/stats", headers=admin)).json()["services"]["smev"] == "slow"
    assert queue.get_nowait() == ("chaos", {"service": "smev", "mode": "slow"})
    actions = [r["action"] for r in env.state.db.query_all("SELECT action FROM audit_log")]
    assert "chaos_change" in actions
    assert (await env.client.get("/api/chaos/other", headers=admin)).status_code == 404


async def test_events_stream_requires_token(env):
    """Ожидаемый результат: SSE без токена или с чужим токеном -> 401."""
    assert (await env.client.get("/api/events/stream")).status_code == 401
    assert (await env.client.get("/api/events/stream?token=fake")).status_code == 401


async def test_health_reports_services(env):
    """Ожидаемый результат: все три компонента доступны."""
    health = (await env.client.get("/api/health")).json()
    assert health["backend"] == "ok" and health["smev"] == "up" and health["bki"] == "up"
    assert health["model"] == "ready"


async def test_many_inquiries_goes_to_manual_review(env, operator):
    """Клиент с 5+ запросами в БКИ за 30 дней.

    Ожидаемый результат: manual_review с причиной MANY_INQUIRIES, хотя балл высокий.
    """
    env.world.bki_override["4510 333333"] = GOOD_BKI | {"inquiries_30d": 6}
    app = await env.run_and_get(good_form(passport="4510 333333"), operator)
    assert app["status"] == "manual_review" and app["reasons"][0]["code"] == "MANY_INQUIRIES"


async def test_cors_allows_frontend(env):
    """Ожидаемый результат: preflight с http://localhost:5173 разрешён."""
    resp = await env.client.options("/api/applications", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
