"""Проверка формата полей заявки (Data Dictionary).

Здесь только «правильно ли заполнено поле». Бизнес-правила (возраст 18–70,
безработный, низкий доход …) — в engine.py.
"""

import re
from dataclasses import dataclass

from . import rules
from .models import Application
from .products import PRODUCTS
from .reasons import FIELD_ERRORS

PASSPORT_HASH_RE = re.compile(r"^[a-f0-9]{16}$")
APP_ID_RE = re.compile(r"^app_[0-9]{4}$")
CLIENT_ID_RE = re.compile(r"^cli_[0-9]{4}$")

# Порядок важен: в пакетном режиме первая ошибка становится причиной NEED_MORE_DATA
REQUIRED_ORDER = ("income", "age", "employment", "family", "amount", "term")


@dataclass
class FieldError:
    field: str
    code: str
    message: str


def _err(field: str, code: str) -> FieldError:
    return FieldError(field, code, FIELD_ERRORS[code])


def validate_fields(app: Application) -> list[FieldError]:
    """Ошибки формата. Пустой список — все поля заполнены и корректны."""
    errors: list[FieldError] = []
    for name in REQUIRED_ORDER:
        if getattr(app, name) in (None, ""):
            errors.append(_err(name, f"MISSING_{name.upper()}"))
    present = {e.field for e in errors}

    if "age" not in present and (not isinstance(app.age, int) or isinstance(app.age, bool)):
        errors.append(_err("age", "INVALID_AGE"))
    if "income" not in present and not (_is_number(app.income) and 0 < app.income <= rules.MAX_INCOME):
        errors.append(_err("income", "INVALID_INCOME"))
    if "employment" not in present and app.employment not in rules.EMPLOYMENTS:
        errors.append(_err("employment", "INVALID_EMPLOYMENT"))
    if "family" not in present and app.family not in rules.FAMILIES:
        errors.append(_err("family", "INVALID_FAMILY"))
    lo, hi = rules.AMOUNT_RANGE
    if "amount" not in present and not (_is_number(app.amount) and lo <= app.amount <= hi):
        errors.append(_err("amount", "INVALID_AMOUNT"))
    lo, hi = rules.TERM_RANGE
    if "term" not in present and not (isinstance(app.term, int) and not isinstance(app.term, bool) and lo <= app.term <= hi):
        errors.append(_err("term", "INVALID_TERM"))
    if app.passport_hash is not None and not (isinstance(app.passport_hash, str) and PASSPORT_HASH_RE.match(app.passport_hash)):
        errors.append(_err("passport_hash", "INVALID_PASSPORT_HASH"))
    if app.product_id not in PRODUCTS:
        errors.append(_err("product_id", "UNKNOWN_PRODUCT"))
    return errors


def validate_for_api(app: Application) -> list[FieldError]:
    """Проверки при подаче через API (ошибка → HTTP 422).

    Сверх формата: возраст 18–70 (так задано в схеме Application организаторов)
    и границы выбранного продукта.
    """
    errors = validate_fields(app)
    if errors:
        return errors
    if not rules.MIN_AGE <= app.age <= rules.MAX_AGE:
        return [_err("age", "AGE_OUT_OF_RANGE")]
    product = PRODUCTS[app.product_id]
    problems = product.problems(app.age, app.income, app.amount, app.term)
    if problems:
        msg = f"Продукт «{product.name}»: нужно " + ", ".join(problems)
        return [FieldError("product_id", "PRODUCT_MISMATCH", msg)]
    return []


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x  # x == x отсекает NaN
