"""Модели запросов и правила валидации (раздел 2 контракта). Сообщения об ошибках — по-русски."""

import re
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from common.passport import age_on, check_passport

ANNUAL_RATE = 0.18  # фиксированная ставка для расчёта платежа


class Purpose(str, Enum):
    consumer = "consumer"
    auto = "auto"
    refinance = "refinance"
    education = "education"


# ВАЖНО: в регулярках только [0-9], не \d: \d совпадает и с «٤٥١٠» (арабо-индийские цифры) и другими юникод-цифрами.
# Иначе один паспорт можно записать двумя способами, и хэш для проверки дубликатов получится разным.


def inn10_is_valid(inn: str) -> bool:
    """Контрольная сумма ИНН юрлица (10 цифр): взвешенная сумма первых 9 цифр mod 11 mod 10."""
    if not re.fullmatch(r"[0-9]{10}", inn):
        return False
    weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    total = sum(int(d) * w for d, w in zip(inn[:9], weights))
    return total % 11 % 10 == int(inn[9])


def monthly_payment(amount: int, months: int) -> int:
    """Аннуитетный платёж: P * r / (1 - (1+r)^-n)."""
    r = ANNUAL_RATE / 12
    return round(amount * r / (1 - (1 + r) ** -months))


def _in_range(value: int, low: int, high: int, what: str, unit: str = "") -> int:
    if not low <= value <= high:
        raise ValueError(f"{what}: от {low:,} до {high:,}{unit}".replace(",", " "))
    return value


class ApplicationIn(BaseModel):
    # лишние поля — ошибка, строки обрезаем по пробелам и ограничиваем по длине
    # hide_input_in_errors: в тексте ошибки pydantic не будет значения поля (паспорт, ФИО попали бы в логи при сбое)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_max_length=100,
                              hide_input_in_errors=True)

    full_name: str
    birth_date: date
    passport: str
    passport_issue_date: date
    passport_division_code: str
    phone: str
    monthly_income: int
    employer_inn: str
    employment_months: int
    dependents: int
    loan_amount: int
    loan_term_months: int
    purpose: Purpose
    # необязательно: идентификатор устройства КЛИЕНТА (его присылает приложение клиента в онлайн-канале).
    # Нет поля — заявка из офиса, и «устройства» у неё нет вообще (устройство оператора ничего не говорит о клиенте)
    client_device_id: str | None = None

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        parts = v.split(" ")
        if not 2 <= len(parts) <= 3:
            raise ValueError("ФИО: нужно 2–3 слова (фамилия, имя, отчество — по желанию)")
        for part in parts:
            if not 2 <= len(part) <= 40:
                raise ValueError("ФИО: каждое слово от 2 до 40 символов")
            if not re.fullmatch(r"[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)*", part):
                raise ValueError("ФИО: только кириллица и дефис, каждое слово с заглавной буквы")
        if re.search(r"(.)\1\1", v.lower()):
            raise ValueError("ФИО: три одинаковые буквы подряд — похоже на опечатку")
        return v

    @field_validator("birth_date")
    @classmethod
    def _birth(cls, v: date) -> date:
        today = date.today()
        if v > today:
            raise ValueError("Дата рождения не может быть в будущем")
        if not 18 <= age_on(v, today) <= 75:
            raise ValueError("Возраст заёмщика должен быть от 18 до 75 лет")
        return v

    @field_validator("client_device_id")
    @classmethod
    def _client_device(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[A-Za-z0-9._-]{8,64}", v):
            raise ValueError("Идентификатор устройства: 8–64 символа (латиница, цифры, . _ -)")
        return v

    @field_validator("passport")
    @classmethod
    def _passport(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9]{4} [0-9]{6}", v):
            raise ValueError("Паспорт в формате «NNNN NNNNNN»")
        return v

    @field_validator("passport_division_code")
    @classmethod
    def _division(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9]{3}-[0-9]{3}", v):
            raise ValueError("Код подразделения в формате «NNN-NNN»")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if not re.fullmatch(r"\+79[0-9]{9}", v):
            raise ValueError("Телефон мобильный: +7 9XX и ещё 7 цифр")
        return v

    @field_validator("monthly_income")
    @classmethod
    def _income(cls, v: int) -> int:
        return _in_range(v, 10_000, 10_000_000, "Доход", " ₽")

    @field_validator("employer_inn")
    @classmethod
    def _inn(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9]{10}", v):
            raise ValueError("ИНН работодателя: 10 цифр")
        if v == "0" * 10 or v[:2] == "00":
            raise ValueError("ИНН работодателя: код региона (первые 2 цифры) должен быть 01–99")
        if not inn10_is_valid(v):
            raise ValueError("ИНН работодателя: неверная контрольная сумма")
        return v

    @field_validator("employment_months")
    @classmethod
    def _employment(cls, v: int) -> int:
        return _in_range(v, 0, 600, "Стаж (мес.)")

    @field_validator("dependents")
    @classmethod
    def _dependents(cls, v: int) -> int:
        return _in_range(v, 0, 10, "Иждивенцы")

    @field_validator("loan_amount")
    @classmethod
    def _amount(cls, v: int) -> int:
        return _in_range(v, 30_000, 5_000_000, "Сумма кредита", " ₽")

    @field_validator("loan_term_months")
    @classmethod
    def _term(cls, v: int) -> int:
        return _in_range(v, 6, 84, "Срок кредита (мес.)")


def cross_checks(d: ApplicationIn, today: date | None = None) -> dict[str, str]:
    """Проверки, где поля зависят друг от друга. Ошибка привязывается к тому полю, которое нужно исправить."""
    today = today or date.today()
    errors: dict[str, str] = {}
    age = age_on(d.birth_date, today)

    if d.employment_months > (age - 14) * 12:
        errors["employment_months"] = f"Стаж {d.employment_months} мес. невозможен: работать можно с 14 лет (сейчас {age})"
    if d.loan_amount > 60 * d.monthly_income:
        errors["loan_amount"] = "Сумма кредита больше 60 месячных доходов — заявку одобрить невозможно"
    # возраст на дату последнего платежа
    end = today.replace(day=1)
    total_months = end.year * 12 + end.month - 1 + d.loan_term_months
    last_payment = date(total_months // 12, total_months % 12 + 1, 1)
    if age_on(d.birth_date, last_payment) > 75:
        errors["loan_term_months"] = "К дате последнего платежа заёмщику будет больше 75 лет — сократите срок"
    errors.update(check_passport(d.passport, d.passport_issue_date, d.passport_division_code, d.birth_date, today))
    return errors


class LoginIn(BaseModel):
    # длины ограничены: bcrypt принимает пароль до 72 байт, а имя попадает в память счётчиков и в аудит
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class ReviewIn(BaseModel):
    decision: str
    comment: str = Field(default="", max_length=1000)  # комментарий уходит в аудит и в БД: не даём складывать туда мегабайты

    @field_validator("decision")
    @classmethod
    def _decision(cls, v: str) -> str:
        if v not in ("approved", "rejected"):
            raise ValueError("Допустимо: approved или rejected")
        return v


class ChaosIn(BaseModel):
    """Тело PUT /api/chaos/{service}: только режим и задержка, всё остальное (и огромные поля) в аудит не попадает."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["normal", "slow", "error", "timeout", "bad_signature", "flaky"] = "normal"
    delay_ms: int = Field(default=0, ge=0, le=60000)


class SimulateIn(BaseModel):
    count: int
    scenario: str = "normal"  # normal | fabric | carousel | probing | mixed

    @field_validator("scenario")
    @classmethod
    def _scenario(cls, v: str) -> str:
        if v not in ("normal", "fabric", "carousel", "probing", "mixed"):
            raise ValueError("Допустимо: normal, fabric, carousel, probing, mixed")
        return v

    @field_validator("count")
    @classmethod
    def _count(cls, v: int) -> int:
        return _in_range(v, 1, 300, "Количество")


def errors_to_fields(errors: list[dict]) -> dict[str, str]:
    """Ошибки pydantic -> {"поле": "сообщение по-русски"} (формат 422 из контракта)."""
    fields: dict[str, str] = {}
    for err in errors:
        loc = [p for p in err.get("loc", ()) if p not in ("body", "query", "header")]
        name = str(loc[-1]) if loc else "body"
        kind = err.get("type", "")
        if kind == "value_error":
            msg = str(err["ctx"]["error"])
        elif kind == "missing":
            msg = "Поле обязательно"
        elif kind == "extra_forbidden":
            msg = "Лишнее поле: такого нет в анкете"
        elif kind == "string_too_long":
            msg = "Слишком длинное значение"
        elif kind.startswith("int") or kind in ("float_parsing", "float_type"):
            msg = "Должно быть целым числом"
        elif kind.startswith("string") or kind == "str_type":
            msg = "Должно быть строкой"
        elif kind.startswith("date"):
            msg = "Дата в формате ГГГГ-ММ-ДД"
        elif kind == "enum":
            msg = f"Допустимые значения: {err.get('ctx', {}).get('expected', '')}"
        elif kind == "json_invalid":
            msg = "Некорректный JSON"
        else:
            msg = "Некорректное значение"
        fields.setdefault(name, msg)
    return fields
