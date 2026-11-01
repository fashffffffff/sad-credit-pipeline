"""СМЭВ после контракта v5: никаких случайных отказов."""

import random
from datetime import date

from backend.app.simulate import random_application
from emulators.app.common import smev_profile


def call(form):
    return smev_profile(form["passport"], form["monthly_income"], form["birth_date"], form["passport_issue_date"],
                        form["passport_division_code"], form["employment_months"])


def test_real_looking_passports_are_never_rejected():
    """Ожидаемый результат: 3000 правдоподобных паспортов с согласованными датами и кодами — passport_valid=true
    у всех (раньше 2% отказывались случайно); работа подтверждена при стаже > 0."""
    rng = random.Random(7)
    for _ in range(3000):
        form = random_application(rng)
        if form["passport"].startswith("0000"):
            continue
        answer = call(form)
        assert answer["passport_valid"] is True, form
        assert answer["employment_confirmed"] == (form["employment_months"] > 0)


def test_answer_is_deterministic():
    """Ожидаемый результат: один и тот же запрос всегда даёт один и тот же ответ."""
    form = random_application(random.Random(1))
    assert call(form) == call(form)


def test_invented_and_expired_passports_are_invalid():
    """Ожидаемый результат: несуществующий регион серии, неподходящий код подразделения и просрочка 20/45 → passport_valid=false."""
    base = dict(passport="4510 123456", monthly_income=100000, birth_date="1995-04-12",
                passport_issue_date="2015-05-20", passport_division_code="770-001", employment_months=12)
    assert call(base)["passport_valid"] is True
    assert call(base | {"passport": "0010 123456"})["passport_valid"] is False
    assert call(base | {"passport_division_code": "780-001"})["passport_valid"] is False
    expired = base | {"birth_date": "1980-03-01", "passport": "4500 123456", "passport_issue_date": "2000-04-01"}
    assert date.today() > date(2025, 6, 1)
    assert call(expired)["passport_valid"] is False


def test_old_clients_without_new_fields_still_work():
    """Ожидаемый результат: запрос без новых полей (старый контракт) обрабатывается, паспорт считается действительным."""
    assert smev_profile("4510 123456", 100000)["passport_valid"] is True
