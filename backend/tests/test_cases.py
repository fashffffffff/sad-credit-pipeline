"""5 сценариев кейса T2-1…T2-5 через API, таймаут и автоповтор, пересчёт, ручное решение, встречные предложения."""

import json

from backend.app.pipeline.runner import auto_retry_once
from backend.tests.conftest import GOOD_HASH, good_form

# passport_hash, у которых БКИ-эмулятор даёт нужный ответ — в тестах задаём ответы прямо в World
BAD_BKI = {"score": 400, "delinquency": True, "requests_90d": 1}


async def test_t2_1_approved(env, client, uw):
    d = await env.run(good_form(), client)
    assert d["status"] == "APPROVED"
    dec = d["decision"]
    assert (dec["decision"], dec["reason"], dec["score"], dec["dti"]) == ("APPROVED", "", 90, 0.21)
    assert (dec["amount_approved"], dec["term_approved"], dec["rate"], dec["monthly_payment"]) == (500000, 24, 0.18, 24962)
    assert dec["bki"] == {"score": 720, "delinquency": False} and dec["smev"] == "MATCH"
    assert dec["version"] == 1 and dec["decided_by"] == "system" and dec["rules_version"]
    assert sum(p["points"] for p in dec["breakdown"]) == 90
    assert [s["stage"] for s in d["stages"] if s["stage"] != "antifraud"] == [
        "validation", "local_rules", "smev", "bki", "scoring", "decision"]
    assert d["app_id"] == "app_0001"


async def test_t2_2_refused_bki(env, client):
    env.world.bki_override[GOOD_HASH] = BAD_BKI
    d = await env.run(good_form(), client)
    assert d["status"] == "REFUSED"
    assert (d["decision"]["reason"], d["decision"]["score"]) == ("BKI_DELINQUENCY", 60)
    assert d["decision"]["amount_approved"] is None


async def test_t2_3_missing_income_is_422(env, client):
    """Пустой доход через API — 422 MISSING_INCOME (в пакетном режиме это NEED_MORE_DATA)."""
    form = good_form()
    del form["income"]
    resp = await env.submit(form, client)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "MISSING_INCOME" and body["retryable"] is False
    assert "income" in body["details"]["fields"]
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM applications")["n"] == 0


async def test_t2_4_low_income_local_rules_skip_external(env, client):
    """Правила 1–5 сработали → СМЭВ и БКИ не вызываем (NOT_CALLED), в external_requests пусто."""
    d = await env.run(good_form(income=25000, amount=50000, product_id="prod_student", age=20), client)
    assert d["decision"]["reason"] == "LOW_INCOME" and d["status"] == "REFUSED"
    assert env.world.calls["smev"] == 0 and env.world.calls["bki"] == 0 and env.world.calls["report"] == 0
    assert d["decision"]["smev"] == "NOT_CALLED" and d["decision"]["bki"] is None
    stages = {s["stage"]: s["status"] for s in d["stages"]}
    assert stages["smev"] == stages["bki"] == stages["scoring"] == "skipped"
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM external_requests")["n"] == 0


async def test_t2_5_high_load_amount_limit_with_offers(env, client):
    """Сумма больше 10 доходов: отказ AMOUNT_LIMIT, но правила дают встречные предложения (тот же движок)."""
    d = await env.run(good_form(income=60000, amount=800000, term=48, employment="self-employed", family="single", age=28), client)
    assert (d["decision"]["reason"], d["status"]) == ("AMOUNT_LIMIT", "REFUSED")
    assert d["decision"]["dti"] == 0.39
    offers = d["decision"]["offers"]
    assert offers and all(o["amount"] <= 600000 for o in offers)
    assert not any(o.get("preliminary") for o in offers)  # ответы БКИ и СМЭВ были настоящие


async def test_high_dti_offers_are_marked_preliminary(env, client):
    d = await env.run(good_form(income=50000, amount=500000, term=12), client)
    assert d["decision"]["reason"] == "HIGH_DTI"
    assert d["decision"]["offers"] and all(o["preliminary"] for o in d["decision"]["offers"])
    assert env.world.calls["bki"] == 0


async def test_accept_offer_creates_child_application(env, client):
    d = await env.run(good_form(income=50000, amount=500000, term=12, amount_priority=2), client)
    parent = d["app_id"]
    resp = await env.client.post(f"/api/v1/applications/{parent}/accept-offer", json={"offer_index": 0},
                                 headers=client | {"Idempotency-Key": "accept-1"})
    assert resp.status_code == 201
    child_id = resp.json()["app_id"]
    assert env.state.db.query_one("SELECT amount_priority FROM applications WHERE app_id=?", (child_id,))["amount_priority"] == 2
    assert resp.json()["parent_app_id"] == parent
    # тот же ключ → тот же ответ, вторая заявка не создаётся
    again = await env.client.post(f"/api/v1/applications/{parent}/accept-offer", json={"offer_index": 0},
                                  headers=client | {"Idempotency-Key": "accept-1"})
    assert again.json()["app_id"] == child_id
    await env.drain()
    child = (await env.client.get(f"/api/v1/applications/{child_id}", headers=client)).json()
    assert child["status"] == "APPROVED" and child["parent_app_id"] == parent
    row = env.state.db.query_one("SELECT passport_hash FROM applications WHERE app_id = ?", (child_id,))
    assert row["passport_hash"] == GOOD_HASH
    assert "offer_accepted" in [r["action"] for r in __import__("backend.app.audit", fromlist=["x"]).list_records(env.state.db)]


async def test_accept_offer_needs_existing_offer_and_owner(env, client):
    d = await env.run(good_form(), client)  # одобрена, предложений нет
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/accept-offer", json={"offer_index": 0},
                              headers=client | {"Idempotency-Key": "a"})
    assert r.status_code == 409
    other = env.make_client(2)
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/accept-offer", json={"offer_index": 0},
                              headers=other | {"Idempotency-Key": "b"})
    assert r.status_code == 403


async def test_timeout_gives_need_more_data_then_auto_retry(env, client):
    env.world.modes["bki"] = "timeout"
    d = await env.run(good_form(), client)
    assert d["status"] == "NEED_MORE_DATA" and d["decision"]["reason"] == "EXTERNAL_TIMEOUT"
    # 2 попытки на сервис: БКИ /score + /report, каждая по 2 попытки
    attempts = [r for r in d.get("external", [])] or []
    env.state.db.query_all("SELECT 1")
    rows = env.state.db.query_all("SELECT service, attempt, status FROM external_requests WHERE service = 'BKI'")
    assert [(r["attempt"], r["status"]) for r in rows] == [(1, "TIMEOUT"), (2, "TIMEOUT")]
    # пока БКИ «лежит» по health тоже — автоповтор не запускается. Здесь /health отвечает, но сервис не оживает:
    env.world.modes["bki"] = "normal"
    assert await auto_retry_once(env.state) == 1
    d = (await env.client.get(f"/api/v1/applications/{d['app_id']}", headers=client)).json()
    assert d["status"] == "APPROVED"
    assert [x["version"] for x in d["decisions_history"]] == [1, 2]
    assert d["decisions_history"][1]["decided_by"] == "system"
    assert await auto_retry_once(env.state) == 0  # больше нечего перепроверять
    from backend.app import audit
    assert any(r["action"] == "recalculated" and r["actor"] == "system" for r in audit.list_records(env.state.db))


async def test_auto_retry_gives_up_after_limit(env, client):
    env.world.modes["smev"] = "timeout"
    d = await env.run(good_form(), client)
    for _ in range(env.state.settings.auto_retry_max + 2):
        await auto_retry_once(env.state)
    row = env.state.db.query_one("SELECT status, auto_retries FROM applications WHERE app_id = ?", (d["app_id"],))
    assert row["status"] == "NEED_MORE_DATA" and row["auto_retries"] == 10


async def test_timeout_is_never_a_refusal(env, client):
    for mode in ("timeout", "error", "bad_signature"):
        env.world.modes["smev"] = mode
        env.state.settings.duplicate_check = False
        d = await env.run(good_form(), client)
        assert d["status"] == "NEED_MORE_DATA", mode
    assert "signature_error" in [r["action"] for r in __import__("backend.app.audit", fromlist=["x"]).list_records(env.state.db)]


async def test_retry_only_on_timeout_and_5xx(env, client):
    env.world.modes["bki"] = "flaky_once"  # первый вызов 503, второй проходит
    d = await env.run(good_form(), client)
    assert d["status"] == "APPROVED"
    rows = env.state.db.query_all("SELECT status FROM external_requests WHERE service = 'BKI' ORDER BY rowid")
    assert "ERROR" in [r["status"] for r in rows]


async def test_external_calls_logged_to_table_and_file(env, client, uw):
    d = await env.run(good_form(), client)
    rows = env.state.db.query_all("SELECT * FROM external_requests ORDER BY rowid")
    assert {r["service"] for r in rows} == {"SMEV", "BKI", "BKI_REPORT"}
    assert all(r["request_id"].startswith("req_") for r in rows)
    lines = [json.loads(l) for l in open(env.state.settings.external_log, encoding="utf-8")]
    assert len(lines) == len(rows)
    bki = next(l for l in lines if l["service"] == "BKI")
    assert set(bki) == {"timestamp", "service", "request_id", "app_id", "attempt", "status", "latency_ms", "result"}
    assert bki["result"] == {"score": 720, "delinquency": False} and bki["app_id"] == d["app_id"]
    assert bki["timestamp"].endswith("Z")
    assert env.world.signed_ok and all(env.world.signed_ok)  # backend подписывает каждый запрос
    api = (await env.client.get("/api/v1/external", headers=uw, params={"app_id": d["app_id"]})).json()
    assert len(api) == len(rows)


async def test_report_failure_does_not_affect_decision(env, client, uw):
    env.world.report_down = True
    d = await env.run(good_form(), client, as_staff=uw)
    assert d["status"] == "APPROVED" and d["ml_hint"] is None


async def test_ml_hint_for_staff_only(env, client, uw):
    d = await env.run(good_form(), client, as_staff=uw)
    hint = d["ml_hint"]
    assert hint["pd"] == 0.021 and "Решение принимают правила" in hint["summary_ru"] and hint["heatmap"]
    assert hint["risk_level"] == "low"
    assert d["decision"]["decided_by"] == "system"  # на решение подсказка не влияет


async def test_recalculate_creates_new_version(env, client, uw):
    d = await env.run(good_form(), client)
    env.world.bki_override[GOOD_HASH] = BAD_BKI
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/recalculate", headers=uw)
    assert r.status_code == 200
    dec = r.json()
    assert (dec["decision"], dec["reason"], dec["version"], dec["decided_by"]) == ("REFUSED", "BKI_DELINQUENCY", 2, "underwriter")
    detail = (await env.client.get(f"/api/v1/applications/{d['app_id']}", headers=uw)).json()
    assert [x["decision"] for x in detail["decisions_history"]] == ["APPROVED", "REFUSED"]
    assert detail["status"] == "REFUSED"


async def test_recalculate_conflict_while_processing(env, client, uw):
    d = await env.run(good_form(), client)
    env.state.db.execute("UPDATE applications SET status = 'PROCESSING' WHERE app_id = ?", (d["app_id"],))
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/recalculate", headers=uw)
    assert r.status_code == 409
    r = await env.client.get(f"/api/v1/applications/{d['app_id']}/decision", headers=client)
    assert r.status_code == 425 and r.json()["retryable"] is True


async def test_manual_review_and_underwriter_decision(env, client, uw):
    # балл < 60 → MANUAL_REVIEW / LOW_SCORE: студент, 600–699 БКИ
    env.world.bki_default = {"score": 580, "delinquency": False, "requests_90d": 1}
    d = await env.run(good_form(age=24, employment="student", family="single", income=45000, amount=100000,
                                product_id="prod_student"), client)
    assert d["status"] == "MANUAL_REVIEW" and d["decision"]["reason"] == "LOW_SCORE"
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/review", headers=uw,
                              json={"decision": "APPROVED", "comment": "доход подтверждён"})
    assert r.status_code == 200 and r.json()["decision"] == "APPROVED" and r.json()["decided_by"] == "underwriter"
    assert r.json()["amount_approved"] == 100000
    detail = (await env.client.get(f"/api/v1/applications/{d['app_id']}", headers=uw)).json()
    assert detail["status"] == "APPROVED" and detail["review"]["comment"] == "доход подтверждён"
    # повторное ручное решение — только из MANUAL_REVIEW
    again = await env.client.post(f"/api/v1/applications/{d['app_id']}/review", headers=uw,
                                  json={"decision": "REFUSED", "comment": ""})
    assert again.status_code == 409
    from backend.app import audit
    assert "reviewed" in [x["action"] for x in audit.list_records(env.state.db)]


async def test_review_refusal_has_client_text(env, client, uw):
    env.world.bki_default = {"score": 580, "delinquency": False, "requests_90d": 1}
    d = await env.run(good_form(age=24, employment="student", family="single", income=45000, amount=100000,
                                product_id="prod_student"), client)
    r = await env.client.post(f"/api/v1/applications/{d['app_id']}/review", headers=uw,
                              json={"decision": "REFUSED", "comment": "x"})
    assert r.json()["reason"] == "MANUAL_REFUSED"
    mine = (await env.client.get(f"/api/v1/applications/{d['app_id']}/decision", headers=client)).json()
    assert "специалистом" in mine["reason_text"]


async def test_fraud_suspect_is_hidden_from_client(env, client, uw):
    """Антифрод пометил заявку → MANUAL_REVIEW/FRAUD_SUSPECT; клиент видит только нейтральную причину."""
    env.state.antifraud.assess = lambda *a, **k: ({"fraud_score": 0.9, "pattern": "fabric", "pattern_ru": "фабрика",
        "similarity": 0.8, "anomaly": 0.1, "channel": "app", "flagged": True, "inherited_from_cluster": False,
        "explanation": [], "component_size": 3, "cluster_id": "x", "text": "Похожа на схему"}, [])
    resp = await env.submit(good_form(), client)
    await env.drain()
    app_id = resp.json()["app_id"]
    staff = (await env.client.get(f"/api/v1/applications/{app_id}", headers=uw)).json()
    mine = (await env.client.get(f"/api/v1/applications/{app_id}", headers=client)).json()
    assert staff["decision"]["reason"] == "FRAUD_SUSPECT" and staff["fraud"]["flagged"] is True
    assert any(t["n"] == 11 for t in staff["decision"]["trace"])
    assert mine["status"] == "MANUAL_REVIEW" and mine["reason"] == "LOW_SCORE"
    blob = json.dumps(mine)
    for secret in ("fraud", "FRAUD", "ml_hint", "external", "антифрод", "Антифрод"):
        assert secret not in blob, secret
    assert all(t["n"] != 11 for t in mine["decision"]["trace"])
    assert "antifraud" not in [s["stage"] for s in mine["stages"]]
