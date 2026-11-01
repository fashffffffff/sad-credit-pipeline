"""5 обязательных сценариев из ТЗ чемпионата. Каждый — отдельный тест."""

import sqlite3

from backend.tests.conftest import GOOD_BKI, good_form, loan


async def test_1_success_good_client_is_approved(env, operator):
    """Сценарий 1. Успех.

    Ожидаемый результат: POST сразу отвечает 202 «processing»; конвейер проходит все 6 этапов
    со статусом ok; заявка получает статус approved с причиной SCORE_HIGH (балл >= 680);
    в SSE идут события running и ok на каждом этапе и итоговое событие decision.
    """
    queue = env.state.bus.subscribe()
    resp = await env.submit(good_form(), operator)
    assert resp.status_code == 202
    assert resp.json()["status"] == "processing"
    await env.drain()

    app = (await env.client.get(f"/api/applications/{resp.json()['id']}", headers=operator)).json()
    assert app["status"] == "approved"
    assert app["score"] >= 680
    assert [s["status"] for s in app["stages"]] == ["ok"] * 6
    assert [s["stage"] for s in app["stages"]] == ["validation", "smev", "bki", "antifraud", "scoring", "decision"]
    assert app["reasons"][0]["code"] == "SCORE_HIGH"
    assert all(env.world.signed_ok), "backend должен подписывать каждый запрос к СМЭВ/БКИ"

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    stage_events = [(d["stage"], d["status"]) for t, d in events if t == "stage"]
    for stage in ("validation", "smev", "bki", "antifraud", "scoring", "decision"):
        assert (stage, "running") in stage_events and (stage, "ok") in stage_events
    assert events[-1][0] == "decision" and events[-1][1]["status"] == "approved"


async def test_2a_rejection_debt_ratio_over_80_percent(env, operator):
    """Сценарий 2а. Отказ по ПДН.

    Ожидаемый результат: у клиента платёж по долгам 150 000 ₽ при доходе 120 000 ₽, ПДН > 80%;
    заявка rejected, причина DTI_TOO_HIGH с процентами по-русски.
    """
    env.world.bki_override["4510 999999"] = GOOD_BKI | {"monthly_debt_payment": 150000}
    app = await env.run_and_get(good_form(passport="4510 999999"), operator)
    assert app["status"] == "rejected"
    assert app["reasons"][0]["code"] == "DTI_TOO_HIGH"
    assert "> 80%" in app["reasons"][0]["text"]


async def test_2b_rejection_invalid_passport(env, operator):
    """Сценарий 2б. Отказ: недействительный паспорт.

    Ожидаемый результат: паспорт 0000 000003 -> этап smev = fail; этапы bki и scoring пропущены (skipped);
    заявка rejected с причиной PASSPORT_INVALID. БКИ вообще не вызывается.
    """
    app = await env.run_and_get(good_form(passport="0000 000003"), operator)
    assert app["status"] == "rejected"
    assert app["reasons"][0]["code"] == "PASSPORT_INVALID"
    statuses = {s["stage"]: s["status"] for s in app["stages"]}
    assert statuses["smev"] == "fail" and statuses["bki"] == "skipped" and statuses["scoring"] == "skipped"
    assert env.world.calls["bki"] == 0


async def test_3_invalid_data_returns_422_with_fields(env, operator):
    """Сценарий 3. Неверные данные.

    Ожидаемый результат: HTTP 422, тело {"error": "validation_error", "fields": {...}} с русским
    сообщением для КАЖДОГО неверного поля (ФИО, возраст, паспорт, телефон, доход, ИНН с неверной
    контрольной суммой, стаж, иждивенцы, сумма, срок, цель). Заявка не создаётся.
    """
    bad = good_form(full_name="Ivan Petrov", birth_date="2015-01-01", passport="123", phone="89991234567",
                    monthly_income=5, employer_inn="7707083890", employment_months=-1, dependents=11,
                    loan_amount=10, loan_term_months=3, purpose="gift")
    resp = await env.submit(bad, operator)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "validation_error"
    assert set(body["fields"]) == {"full_name", "birth_date", "passport", "phone", "monthly_income",
                                   "employer_inn", "employment_months", "dependents", "loan_amount",
                                   "loan_term_months", "purpose"}
    assert "контрольная сумма" in body["fields"]["employer_inn"]
    assert "кириллица" in body["fields"]["full_name"]
    assert env.state.db.query_one("SELECT COUNT(*) AS c FROM applications")["c"] == 0


async def test_3b_missing_field_and_missing_idempotency_key(env, operator):
    """Сценарий 3 (дополнение). Пропущенное поле и отсутствие Idempotency-Key.

    Ожидаемый результат: оба случая дают 422; для пропуска поля — «Поле обязательно», для заголовка —
    ошибка в fields["Idempotency-Key"].
    """
    form = good_form()
    del form["phone"]
    resp = await env.submit(form, operator)
    assert resp.status_code == 422 and resp.json()["fields"]["phone"] == "Поле обязательно"

    resp = await env.client.post("/api/applications", json=good_form(), headers=operator)
    assert resp.status_code == 422 and "Idempotency-Key" in resp.json()["fields"]


async def test_4_idempotency_same_key_same_application(env, operator):
    """Сценарий 4. Повтор (идемпотентность).

    Ожидаемый результат: тот же ключ + то же тело -> тот же id, вторая заявка не создаётся и конвейер
    не запускается второй раз; тот же ключ + другое тело -> 409 idempotency_conflict.
    """
    first = await env.submit(good_form(), operator, key="abc-1")
    second = await env.submit(good_form(), operator, key="abc-1")
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    await env.drain()
    assert env.state.db.query_one("SELECT COUNT(*) AS c FROM applications")["c"] == 1
    assert env.world.calls["smev"] == 1

    other = await env.submit(good_form(loan_amount=900000), operator, key="abc-1")
    assert other.status_code == 409
    assert other.json()["error"] == "idempotency_conflict"
    assert env.state.db.query_one("SELECT COUNT(*) AS c FROM applications")["c"] == 1


async def test_5a_bki_timeout_retries_then_manual_review(env, operator):
    """Сценарий 5а. Таймаут БКИ.

    Ожидаемый результат: БКИ не отвечает -> 3 попытки (1 + 2 повтора); этап bki = fail с attempts=3;
    scoring пропущен; заявка уходит в manual_review с причиной BKI_UNAVAILABLE. Сервер не падает.
    """
    env.world.modes["bki"] = "timeout"
    app = await env.run_and_get(good_form(), operator)
    assert env.world.calls["bki"] == 3
    stage = {s["stage"]: s for s in app["stages"]}
    assert stage["bki"]["status"] == "fail" and stage["bki"]["attempts"] == 3
    assert stage["scoring"]["status"] == "skipped"
    assert app["status"] == "manual_review"
    assert app["reasons"][0]["code"] == "BKI_UNAVAILABLE"


async def test_5b_bad_response_signature_goes_to_manual_review_and_audit(env, operator):
    """Сценарий 5б. Неверная подпись ответа СМЭВ.

    Ожидаемый результат: ответ с чужой подписью не принимается и НЕ повторяется (1 вызов);
    заявка manual_review с причиной BAD_SIGNATURE; в аудите запись bad_signature;
    в SSE событие security/bad_signature.
    """
    env.world.modes["smev"] = "bad_signature"
    queue = env.state.bus.subscribe()
    app = await env.run_and_get(good_form(), operator)
    assert app["status"] == "manual_review"
    assert app["reasons"][0]["code"] == "BAD_SIGNATURE"
    assert env.world.calls["smev"] == 1
    actions = [r["action"] for r in env.state.db.query_all("SELECT action FROM audit_log")]
    assert "bad_signature" in actions
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(t == "security" and d["kind"] == "bad_signature" for t, d in events)


async def test_5c_flaky_service_recovers_on_retry(env, operator):
    """Сценарий 5в. Временный сбой СМЭВ.

    Ожидаемый результат: первый ответ 503, второй успешный -> этап smev ok с attempts=2,
    заявка в итоге approved (повтор спас заявку от ручной проверки).
    """
    env.world.modes["smev"] = "flaky_once"
    app = await env.run_and_get(good_form(), operator)
    smev = next(s for s in app["stages"] if s["stage"] == "smev")
    assert smev["status"] == "ok" and smev["attempts"] == 2
    assert app["status"] == "approved"


async def test_scoring_unavailable_does_not_crash_server(env_no_model):
    """Модели нет (пакет не установлен).

    Ожидаемый результат: сервер работает; этап scoring = fail с понятной причиной; заявка manual_review
    (SCORING_UNAVAILABLE); /api/health сообщает model = unavailable.
    """
    env = env_no_model
    operator = await env.login("operator")
    app = await env.run_and_get(good_form(), operator)
    scoring = next(s for s in app["stages"] if s["stage"] == "scoring")
    assert scoring["status"] == "fail" and "ml.credit_model" in scoring["detail"]
    assert app["status"] == "manual_review"
    assert app["reasons"][0]["code"] == "SCORING_UNAVAILABLE"
    health = (await env.client.get("/api/health")).json()
    assert health["model"] == "unavailable" and health["smev"] == "up"


async def test_no_history_goes_to_manual_review(env, operator):
    """Нет кредитной истории (паспорт 0000 000004).

    Ожидаемый результат: scoring пропущен, заявка manual_review с причиной BKI_NO_HISTORY.
    """
    app = await env.run_and_get(good_form(passport="0000 000004"), operator)
    assert app["status"] == "manual_review"
    assert app["reasons"][0]["code"] == "BKI_NO_HISTORY"


async def test_stop_rule_from_model_metrics_rejects(env, operator):
    """Стоп-правило из model.metrics["stop_rules"] (паспорт 0000 000002).

    Ожидаемый результат: правило «has_current_overdue >= 1» применено к p.features; заявка rejected
    с кодом правила и текстом text_ru, в который подставлено значение признака.
    """
    app = await env.run_and_get(good_form(passport="0000 000002"), operator)
    assert app["status"] == "rejected"
    assert [r["code"] for r in app["reasons"]] == ["RULE_CURRENT_OVERDUE"]
    assert app["reasons"][0]["text"] == "Текущая просрочка (признак = 1)"


async def test_missing_or_unusable_stop_rules_break_nothing(env_odd_rules):
    """Нет stop_rules в metrics или правило ссылается на несуществующий признак.

    Ожидаемый результат: правила пропускаются, конвейер работает; клиент с текущей просрочкой
    отсекается обычным баллом (SCORE_LOW), хороший клиент одобряется.
    """
    env = env_odd_rules
    operator = await env.login("operator")
    bad = await env.run_and_get(good_form(passport="0000 000002"), operator)
    assert bad["status"] == "rejected" and bad["reasons"][0]["code"] == "SCORE_LOW"
    good = await env.run_and_get(good_form(), operator)
    assert good["status"] == "approved"


async def test_high_pdn_with_good_score_goes_to_manual_review(env, operator):
    """ПДН между 0.5 и 0.8 при хорошем, но не уверенном балле (680–719).

    Ожидаемый результат: платёж по долгам 50 000 ₽ + новый кредит 24 962 ₽ при доходе 120 000 ₽ -> ПДН ≈ 0.62;
    3 месяца с просрочкой -> балл 700: в зоне одобрения, но не уверенный -> manual_review с причиной DTI_HIGH.
    """
    history = [loan(1, **{f"enc_paym_{i}": 2 for i in range(5, 8)})]
    env.world.bki_override["4510 555555"] = GOOD_BKI | {"monthly_debt_payment": 50000, "loans": history}
    app = await env.run_and_get(good_form(passport="4510 555555"), operator)
    assert 680 <= app["score"] < 720
    assert app["status"] == "manual_review"
    assert app["reasons"][0]["code"] == "DTI_HIGH"
    assert 0.5 <= app["pdn"] <= 0.8


async def test_high_pdn_with_strong_score_is_approved_with_note(env, operator):
    """ПДН между 0.5 и 0.8 при уверенном балле (≥ 720).

    Ожидаемый результат: балл 760 перевешивает повышенную нагрузку -> approved,
    но в причинах есть DTI_ELEVATED, чтобы было видно, что нагрузка учтена.
    """
    env.world.bki_override["4510 555556"] = GOOD_BKI | {"monthly_debt_payment": 50000}
    app = await env.run_and_get(good_form(passport="4510 555556"), operator)
    assert app["score"] >= 720
    assert app["status"] == "approved"
    assert [r["code"] for r in app["reasons"]] == ["SCORE_HIGH", "DTI_ELEVATED"]


async def test_borderline_client_goes_to_manual_review(env, operator):
    """Пограничный клиент (паспорт 0000 000005).

    Ожидаемый результат: балл в зоне 600–679 -> manual_review с причиной SCORE_MID.
    """
    app = await env.run_and_get(good_form(passport="0000 000005"), operator)
    assert 600 <= app["score"] < 680
    assert app["reasons"][0]["code"] == "SCORE_MID"


async def test_bki_loans_saved_and_compacted(env, operator):
    """Кредитная история в заявке.

    Ожидаемый результат: в ApplicationDetail есть pdn, features от модели и bki_loans в компактном виде
    (rn, credit_type, status, closed, paym из 25 кодов); closed берётся из fclose_flag (0 = закрыт, 1 = дата закрытия не определена, кредит открыт);
    сырые loans лежат в БД как JSON.
    """
    app = await env.run_and_get(good_form(), operator)
    assert app["status"] == "approved" and app["pdn"] is not None
    assert app["features"] == {"n_loans": 2, "bad_months": 0, "has_current_overdue": 0}
    first, second = app["bki_loans"]
    assert set(first) == {"rn", "credit_type", "status", "closed", "paym"}
    assert len(first["paym"]) == 25 and first["closed"] is True and second["closed"] is False
    raw = env.state.db.query_one("SELECT bki_loans FROM applications")["bki_loans"]
    assert "enc_paym_24" in raw
