"""Модели тел запросов (кроме анкеты: её проверяет credit_core.validate_for_api). Сообщения об ошибках — по-русски."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginIn(BaseModel):
    # длины ограничены: bcrypt принимает пароль до 72 байт, а имя попадает в память счётчиков и в аудит
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVED", "REFUSED"]
    comment: str = Field(default="", max_length=1000)  # уходит в аудит и в БД: не даём складывать туда мегабайты


class AcceptOfferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_index: int = Field(ge=0, le=10)


class OprfIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blinded: str = Field(min_length=4, max_length=200, pattern=r"^[A-Za-z0-9+/_=-]+$")  # base64 ослеплённой точки


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: str = Field(max_length=4000)
    signature: str = Field(max_length=300)


class ChaosIn(BaseModel):
    """Тело PUT /api/v1/chaos/{service}: только режим и задержка, всё остальное (и огромные поля) в аудит не попадает."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["normal", "slow", "error", "timeout", "bad_signature", "flaky"] = "normal"
    delay_ms: int = Field(default=0, ge=0, le=60000)


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1, le=300)
    scenario: Literal["normal", "fabric", "carousel", "probing", "mixed"] = "normal"


def errors_to_fields(errors: list[dict]) -> dict[str, str]:
    """Ошибки pydantic -> {"поле": "сообщение по-русски"} (details.fields в формате Error)."""
    fields: dict[str, str] = {}
    for err in errors:
        loc = [p for p in err.get("loc", ()) if p not in ("body", "query", "header")]
        name = str(loc[-1]) if loc else "body"
        kind = err.get("type", "")
        if kind == "missing":
            msg = "Поле обязательно"
        elif kind == "extra_forbidden":
            msg = "Лишнее поле"
        elif kind in ("string_too_long", "string_too_short"):
            msg = "Недопустимая длина значения"
        elif kind.startswith("int") or kind in ("float_parsing", "float_type"):
            msg = "Должно быть целым числом"
        elif kind.startswith("string") or kind == "str_type":
            msg = "Должно быть строкой"
        elif kind in ("literal_error", "enum"):
            msg = "Недопустимое значение"
        elif kind == "json_invalid":
            msg = "Некорректный JSON"
        elif kind.startswith("greater") or kind.startswith("less"):
            msg = "Число вне допустимого диапазона"
        else:
            msg = "Некорректное значение"
        fields.setdefault(name, msg)
    return fields
