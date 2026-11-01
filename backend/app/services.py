"""Создание заявки и запуск конвейера — общее для ручки POST /applications и для симуляции."""

import secrets
import string
from datetime import datetime, timezone

from backend.app import audit
from backend.app.pipeline.runner import STAGE_NAMES, run_pipeline
from backend.app.pipeline.status import change_status
from backend.app.schemas import ApplicationIn, monthly_payment
from backend.app.security.masking import mask_name, mask_passport, mask_phone, passport_hash
from backend.app.state import AppState


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return "app_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))


def create_application(state: AppState, data: ApplicationIn, actor: str, role: str) -> str:
    """Сохраняет заявку (в БД — хэш и маски, без открытого паспорта) и переводит её в processing."""
    app_id = new_id()
    state.db.execute(
        "INSERT INTO applications (id, created_at, created_by, status, full_name_masked, passport_hash,"
        " passport_masked, phone_masked, monthly_income, employment_months, dependents, purpose,"
        " loan_amount, loan_term_months, monthly_payment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (app_id, now_iso(), actor, "received", mask_name(data.full_name),
         passport_hash(state.settings.pii_pepper, data.passport), mask_passport(data.passport),
         mask_phone(data.phone), data.monthly_income, data.employment_months, data.dependents,
         data.purpose.value, data.loan_amount, data.loan_term_months,
         monthly_payment(data.loan_amount, data.loan_term_months)),
    )
    for position, name in enumerate(STAGE_NAMES):
        state.db.execute("INSERT INTO stages (application_id, stage, position, status) VALUES (?,?,?,'pending')",
                         (app_id, name, position))
    change_status(state.db, app_id, "processing")
    audit.write(state.db, actor, role, "create_application", "application", app_id,
                {"passport": mask_passport(data.passport), "amount": data.loan_amount})
    return app_id


def start_pipeline(state: AppState, app_id: str, data: ApplicationIn, meta: dict | None = None) -> None:
    """meta: event_ts (время события; у симулятора виртуальное) — нужно антифроду."""
    state.spawn(run_pipeline(state, app_id, data, meta))
