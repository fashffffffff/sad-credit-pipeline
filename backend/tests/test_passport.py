"""Проверки паспорта (common/passport.py): серия, дата выдачи, код подразделения, правило 20/45."""

import logging
from datetime import date, timedelta

import pytest

from common.passport import (OKATO_TO_SUBJECT, add_years, check_passport, division_region_status,
                             passport_expired)

BIRTH = date(1995, 4, 12)
TODAY = date(2026, 10, 31)


def check(passport="4510 123456", issue=date(2015, 5, 20), division="770-001", birth=BIRTH):
    return check_passport(passport, issue, division, birth, TODAY)


@pytest.mark.parametrize("okato,subject,name", [
    ("45", "77", "Москва"), ("40", "78", "Санкт-Петербург"), ("46", "50", "Московская область"),
    ("92", "16", "Татарстан"), ("65", "66", "Свердловская область"), ("03", "23", "Краснодарский край"),
    ("50", "54", "Новосибирская область")])
def test_known_region_pairs(okato, subject, name):
    """Ожидаемый результат: серия региона по ОКАТО и код субъекта в коде подразделения совпадают (%s)."""
    assert division_region_status(okato, subject + "0-001") == "ok"


def test_region_mismatch_is_error():
    """Ожидаемый результат: паспорт серии Москвы (45) с кодом подразделения СПб (780) — ошибка на поле кода подразделения."""
    assert "passport_division_code" in check(division="780-001")
    assert check() == {}


def test_unsure_region_only_warns(caplog):
    """Регион из списка «сомнительных» (Чукотка, 77).

    Ожидаемый результат: отказа нет, в лог пишется warning.
    """
    with caplog.at_level(logging.WARNING, logger="passport"):
        assert check(passport="7710 123456", division="500-001") == {}
    assert "не подтверждено" in caplog.text


def test_table_is_consistent():
    """Ожидаемый результат: в справочнике ОКАТО нет пустых наборов и все коды субъектов двузначные."""
    assert len(OKATO_TO_SUBJECT) > 80
    assert all(v and all(len(c) == 2 for c in v) for v in OKATO_TO_SUBJECT.values())


@pytest.mark.parametrize("passport", ["0010 123456", "0210 123456", "0610 123456"])
def test_unknown_okato_region(passport):
    """Ожидаемый результат: серия с несуществующим кодом региона ОКАТО отклоняется (поле passport)."""
    assert "не существует" in check(passport=passport)["passport"]


def test_blank_year_rules():
    """Год бланка (последние 2 цифры серии).

    Ожидаемый результат: 1996 и будущий год невозможны; бланк 2016 новее выдачи 2015; бланк 2008 старше выдачи 2015
    больше чем на 5 лет — все отклоняются; бланк 2010 (на 5 лет раньше выдачи) проходит.
    """
    assert "passport" in check(passport="4596 123456", issue=date(2010, 1, 1))
    assert "passport" in check(passport="4527 123456")  # год бланка 2027 — в будущем
    assert "passport_issue_date" in check(passport="4516 123456")
    assert "passport_issue_date" in check(passport="4508 123456")
    assert check(passport="4510 123456") == {}


def test_number_not_zeros():
    """Ожидаемый результат: номер 000000 отклоняется."""
    assert "passport" in check(passport="4510 000000")


def test_issue_date_rules():
    """Дата выдачи.

    Ожидаемый результат: в будущем, раньше 14-летия и раньше 01.10.1997 — ошибки; выдача ровно в 14-летие проходит.
    """
    assert "будущем" in check(issue=TODAY + timedelta(days=1))["passport_issue_date"]
    assert "14" in check(issue=date(2009, 4, 11))["passport_issue_date"]
    assert check(passport="4509 123456", issue=add_years(BIRTH, 14)) == {}
    old = check(passport="4598 123456", issue=date(1997, 9, 30), birth=date(1970, 1, 1))
    assert "1997" in old["passport_issue_date"]


def test_magic_passport_skips_series_checks():
    """Магический паспорт 0000 000001 имеет «серию» 0000.

    Ожидаемый результат: проверки серии и кода подразделения пропускаются, а дата выдачи проверяется как обычно.
    """
    assert check(passport="0000 000001") == {}
    assert "passport_issue_date" in check(passport="0000 000001", issue=date(2009, 1, 1))


@pytest.mark.parametrize("milestone", [20, 45])
def test_expiry_90_day_border(milestone):
    """Правило 20/45 лет, граница 90 дней.

    Ожидаемый результат: паспорт, выданный до %d-летия, не считается просроченным в течение 90 дней после него
    и просрочен на 91-й день; выданный в день %d-летия или позже — не просрочен никогда.
    """
    birth = date(1960, 1, 1)
    turned = add_years(birth, milestone)
    issue = add_years(birth, milestone - 6)
    assert not passport_expired(birth, issue, turned + timedelta(days=90))
    assert passport_expired(birth, issue, turned + timedelta(days=91))
    assert not passport_expired(birth, turned, turned + timedelta(days=2000))
    assert not passport_expired(birth, add_years(birth, 46), date(2026, 1, 1))


def test_expired_passport_replaced_in_time():
    """Паспорт заменён после 20-летия.

    Ожидаемый результат: не просрочен; паспорт «юноши», так и не заменённый через 100 дней после 20 лет — просрочен.
    """
    birth = date(2000, 1, 1)
    assert not passport_expired(birth, date(2020, 2, 1), date(2024, 1, 1))
    assert passport_expired(birth, date(2014, 5, 1), date(2020, 5, 1))
