"""Строгие проверки анкеты (контракт v5): каждое правило — и «не проходит», и «проходит»."""

import httpx
import pytest

from backend.app.employer import FnsEmployerChecker
from backend.tests.conftest import good_form, make_env


async def post_fields(env, operator, **overrides) -> dict:
    resp = await env.submit(good_form(**overrides), operator)
    return resp.json().get("fields", {}) if resp.status_code == 422 else {"_status": resp.status_code}


@pytest.mark.parametrize("overrides,field,text", [
    ({"full_name": "иванов иван"}, "full_name", "заглавной"),
    ({"full_name": "Иванов"}, "full_name", "2–3 слова"),
    ({"full_name": "Иванов Иван Иванович Петрович"}, "full_name", "2–3 слова"),
    ({"full_name": "Иванов Ииивван"}, "full_name", "три одинаковые"),
    ({"full_name": "Иванов И"}, "full_name", "от 2 до 40"),
    ({"full_name": "Иванов Ivan"}, "full_name", "кириллица"),
    ({"full_name": "А" + "а" * 120 + " Иван"}, "full_name", "длинное"),
    ({"birth_date": "2999-01-01"}, "birth_date", "будущем"),
    ({"birth_date": "2012-01-01"}, "birth_date", "от 18 до 75"),
    ({"birth_date": "1940-01-01"}, "birth_date", "от 18 до 75"),
    ({"phone": "+78123456789"}, "phone", "мобильный"),
    ({"phone": "89991234567"}, "phone", "мобильный"),
    ({"employer_inn": "0000000000"}, "employer_inn", "01–99"),
    ({"employer_inn": "7707083890"}, "employer_inn", "контрольная сумма"),
    ({"employer_inn": "770708389"}, "employer_inn", "10 цифр"),
    ({"passport": "4510 000000"}, "passport", "нулей"),
    ({"passport": "0010 123456"}, "passport", "не существует"),
    ({"passport": "4527 123456"}, "passport", "невозможен"),
    ({"passport_issue_date": "2999-01-01"}, "passport_issue_date", "будущем"),
    ({"passport_issue_date": "2009-01-01"}, "passport_issue_date", "14-летия"),
    ({"passport_issue_date": "2016-01-01"}, "passport_issue_date", "5 лет"),
    ({"birth_date": "1980-01-01", "passport_issue_date": "2005-01-01"}, "passport_issue_date", "позже"),
    ({"passport_division_code": "780-001"}, "passport_division_code", "не соответствует региону"),
    ({"passport_division_code": "77-0001"}, "passport_division_code", "NNN-NNN"),
    ({"foo": "bar"}, "foo", "Лишнее поле"),
])
async def test_rule_rejects(env, operator, overrides, field, text):
    """Каждое правило анкеты по отдельности.

    Ожидаемый результат: 422, ошибка привязана именно к нужному полю и написана по-русски.
    """
    fields = await post_fields(env, operator, **overrides)
    assert field in fields, fields
    assert text in fields[field]


@pytest.mark.parametrize("overrides,field", [
    ({"employment_months": 300}, "employment_months"),  # 31 год: работать можно максимум (31-14)*12 = 204 мес.
    ({"monthly_income": 10000, "loan_amount": 700000}, "loan_amount"),  # больше 60 доходов
    ({"birth_date": "1952-01-01"}, "loan_term_months"),  # к концу 24-месячного кредита будет 76
])
async def test_cross_field_rules_point_to_the_right_field(env, operator, overrides, field):
    """Перекрёстные правила: стаж <= (возраст-14)*12, сумма <= 60 доходов, возраст на последний платёж <= 75.

    Ожидаемый результат: 422, ошибка стоит на том поле, которое нужно исправить.
    """
    assert field in await post_fields(env, operator, **overrides)


@pytest.mark.parametrize("overrides", [
    {},  # Москва
    {"passport": "9215 123456", "passport_division_code": "160-001"},  # Татарстан
    {"passport": "6515 123456", "passport_division_code": "660-001"},  # Свердловская область
    {"passport": "0300 000042", "passport_division_code": "230-001"} if False else {"passport": "0310 654321", "passport_division_code": "230-123"},  # Краснодарский край
    {"employment_months": 204},  # ровно максимум
    {"monthly_income": 10000, "loan_amount": 600000},  # ровно 60 доходов
    {"phone": "+79000000000"},
    {"full_name": "Петрова-Водкина Анна"},
])
async def test_real_looking_data_passes(env, operator, overrides):
    """Настоящие корректные данные проходят всегда.

    Ожидаемый результат: заявка принята (202) для разных регионов и на границах допустимых значений.
    """
    resp = await env.submit(good_form(**overrides), operator)
    assert resp.status_code == 202, resp.text


async def test_expired_passport_is_rejected_by_smev_stage_not_by_form(env, operator):
    """Просроченный паспорт (20/45).

    Ожидаемый результат: форма принимается (дата рождения и выдачи правдоподобны), решение — СМЭВ сообщает
    о недействительном паспорте, заявка rejected, причина PASSPORT_INVALID с пояснением про просрочку.
    """
    form = good_form(birth_date="1980-03-01", passport="4500 123456", passport_issue_date="2000-04-01")
    app = await env.run_and_get(form, operator)
    assert app["status"] == "rejected"
    assert app["reasons"][0]["code"] == "PASSPORT_INVALID" and "просрочен" in app["reasons"][0]["text"]


async def test_fresh_passport_for_same_person_is_accepted(env, operator):
    """Тот же человек, но паспорт заменён после 45-летия (выдан 2025-06-01).

    Ожидаемый результат: паспорт действителен, заявка не отклоняется по паспорту.
    """
    form = good_form(birth_date="1980-03-01", passport="4525 123456", passport_issue_date="2025-06-01")
    app = await env.run_and_get(form, operator)
    assert "PASSPORT_INVALID" not in [r["code"] for r in app["reasons"]]


# ---------- работодатель ----------
class FakeChecker:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def check(self, inn):
        self.calls.append(inn)
        return self.error


async def test_employer_checker_error_goes_to_inn_field(tmp_path):
    """EmployerChecker сообщил, что организации нет.

    Ожидаемый результат: 422 с ошибкой на поле employer_inn; заявка не создаётся. При успехе проверка вызывается с ИНН.
    """
    checker = FakeChecker("Организация с таким ИНН не найдена в ЕГРЮЛ")
    env = make_env(tmp_path, employer_checker=checker)
    operator = await env.login("operator")
    resp = await env.submit(good_form(), operator)
    assert resp.status_code == 422 and "ЕГРЮЛ" in resp.json()["fields"]["employer_inn"]
    assert checker.calls == ["7707083893"]
    checker.error = None
    assert (await env.submit(good_form(), operator)).status_code == 202
    await env.drain()
    await env.client.aclose()


def fns_checker(handler) -> FnsEmployerChecker:
    return FnsEmployerChecker(transport=httpx.MockTransport(handler))


async def test_fns_checker_found_and_not_found():
    """Сервис ФНС с подменой сети.

    Ожидаемый результат: найдена организация -> None; не найдена -> текст ошибки; в ФНС уходит только ИНН организации.
    """
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode() + str(request.url))
        if request.method == "POST":
            return httpx.Response(200, json={"t": "tok"})
        return httpx.Response(200, json={"rows": [{"n": "ООО"}] if "found" in state else []})

    state = {"found"}
    checker = fns_checker(handler)
    assert await checker.check("7707083893") is None
    state.clear()
    assert "не найдена" in await checker.check("7707083893")
    joined = " ".join(sent)
    assert "7707083893" in joined and "Иван" not in joined


@pytest.mark.parametrize("failure", ["timeout", "http500", "garbage"])
async def test_fns_checker_falls_back_to_offline(failure, caplog):
    """ФНС недоступна (таймаут, 500, мусор в ответе).

    Ожидаемый результат: заявка не падает — проверка возвращает None (offline), в лог пишется warning.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "http500":
            return httpx.Response(500)
        return httpx.Response(200, text="не json")

    with caplog.at_level("WARNING", logger="employer"):
        assert await fns_checker(handler).check("7707083893") is None
    assert "ФНС недоступна" in caplog.text
