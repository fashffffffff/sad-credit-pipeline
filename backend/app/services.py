"""Создание заявки и запуск конвейера — общее для POST /applications, accept-offer и симулятора."""

import re

import credit_core
from credit_core import Application

from backend.app import audit
from backend.app.antifraud.fingerprints import fingerprint
from backend.app.decisions import now_iso
from backend.app.errors import ApiError, field_error
from backend.app.pipeline.runner import STAGE_NAMES, process
from backend.app.state import AppState

# поля анкеты 2.1 контракта (+ id устройства из приложения). Всё остальное (ФИО, телефон, паспорт…) — ошибка 422:
# мы такие данные не собираем (минимизация ПДн, запрет дискриминации)
FORM_FIELDS = ("passport_hash", "age", "income", "employment", "family", "amount", "term", "product_id")
ALLOWED_KEYS = set(FORM_FIELDS) | {"client_device_id", "amount_priority"}
MAX_INCOME = 1_000_000_000  # ₽ в месяц: больше — заведомо мусор (и не влезает в SQLite)
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def parse_form(body, need_passport: bool = True, validate_product: bool = True) -> tuple[Application, str | None]:
    """Тело запроса → Application и id устройства. Ошибки анкеты — 422 в формате Error (credit_core.validate_for_api)."""
    if not isinstance(body, dict):
        raise field_error({"body": "Нужен JSON-объект с полями анкеты"})
    extra = sorted(set(body) - ALLOWED_KEYS)
    if extra:
        raise field_error({k: "Это поле не принимается" for k in extra}, "Лишние поля в анкете")
    priority = body.get("amount_priority", 8)
    if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 10:
        raise ApiError(422, "INVALID_PRIORITY", "Выберите важность суммы от 0 до 10",
                       details={"fields": {"amount_priority": "Целое число от 0 до 10"}})
    device = body.get("client_device_id")
    if device is not None and not (isinstance(device, str) and DEVICE_ID_RE.match(device)):
        raise field_error({"client_device_id": "До 64 символов: латинские буквы, цифры и . _ : -"})
    # credit_core ждёт строки там, где строки: мусор другого типа (число, список) отсекаем здесь, а не падаем с 500
    for name, code in (("passport_hash", "INVALID_PASSPORT_HASH"), ("employment", "INVALID_EMPLOYMENT"),
                       ("family", "INVALID_FAMILY"), ("product_id", "UNKNOWN_PRODUCT")):
        if body.get(name) is not None and not isinstance(body[name], str):
            message = credit_core.FIELD_ERRORS[code]
            raise ApiError(422, code, message, details={"fields": {name: message}})
    app = Application(app_id="", passport_hash=body.get("passport_hash") if need_passport else None,
                      age=body.get("age"), income=body.get("income"), employment=body.get("employment"),
                      family=body.get("family"), amount=body.get("amount"), term=body.get("term"),
                      product_id=body.get("product_id") or "prod_standard", amount_priority=priority)
    if need_passport and app.passport_hash is None:
        message = credit_core.FIELD_ERRORS["INVALID_PASSPORT_HASH"]
        raise ApiError(422, "INVALID_PASSPORT_HASH", message, details={"fields": {"passport_hash": message}})
    errors = credit_core.validate_for_api(app)
    if not validate_product:
        errors = [e for e in errors if e.code != "PRODUCT_MISMATCH"]
    income = app.income
    if not errors and isinstance(income, (int, float)) and not (income <= MAX_INCOME):  # ядро верхней границы не знает
        errors = [credit_core.FieldError("income", "INVALID_INCOME", credit_core.FIELD_ERRORS["INVALID_INCOME"])]
    if errors:
        first = errors[0]
        details = {"fields": {e.field: e.message for e in errors}}
        if first.code == "PRODUCT_MISMATCH":
            details["eligible_products"] = [p.to_dict() for p in credit_core.eligible_products(
                app.age, app.income, app.amount, app.term)] or \
                [p.to_dict() for p in credit_core.eligible_products(app.age, app.income)]
        raise ApiError(422, first.code, first.message, details=details)
    return app, device


def create_application(state: AppState, app: Application, client_id: str, *, device_id: str | None = None,
                       device_hash: str | None = None, channel: str = "online", parent_app_id: str | None = None,
                       idempotency_key: str | None = None, actor: str | None = None, actor_role: str = "client") -> str:
    """Сохраняет заявку (в БД — только passport_hash и хэш устройства) и переводит её в PROCESSING."""
    if device_id and not device_hash:
        device_hash = fingerprint(state.settings.pii_pepper, "device", device_id)
    db = state.db
    app_id = db.next_id("app")
    db.execute(
        "INSERT INTO applications (app_id, client_id, passport_hash, age, income, employment, family, amount, term,"
        " product_id, channel, client_device_id_hash, parent_app_id, status, created_at, idempotency_key, amount_priority)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (app_id, client_id, app.passport_hash, app.age, app.income, app.employment, app.family, app.amount,
         app.term, app.product_id, channel, device_hash, parent_app_id, "PROCESSING", now_iso(), idempotency_key, app.amount_priority))
    for position, name in enumerate(STAGE_NAMES):
        db.execute("INSERT INTO stages (application_id, stage, position, status) VALUES (?,?,?,'pending')",
                   (app_id, name, position))
    audit.write(db, actor or client_id, actor_role, "application_created", app_id,
                details={"amount": app.amount, "term": app.term, "product_id": app.product_id,
                         "amount_priority": app.amount_priority,
                         **({"parent_app_id": parent_app_id} if parent_app_id else {})})
    return app_id


def start_pipeline(state: AppState, app_id: str, meta: dict | None = None) -> None:
    """Конвейер идёт в фоне, ручка отвечает сразу."""
    state.spawn(process(state, app_id, meta=meta))
