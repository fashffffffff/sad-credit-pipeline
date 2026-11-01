"""Эмулятор СМЭВ (госданные). Запуск: uv run uvicorn emulators.app.smev:app --port 8101"""

import os
import uuid

from pydantic import BaseModel

from common.secrets_guard import secret_from_env
from emulators.app.common import create_app, smev_profile


class VerifyRequest(BaseModel):
    passport: str
    full_name: str
    birth_date: str
    employer_inn: str
    declared_income: int | None = None  # необязательное расширение: заявленный доход, чтобы ответ был "≈ ±15%"
    # поля паспорта и стаж тоже необязательны (старые клиенты их не шлют): по ним СМЭВ проверяет паспорт и работу
    passport_issue_date: str | None = None
    passport_division_code: str | None = None
    employment_months: int | None = None


def build_response(req: VerifyRequest) -> dict:
    return {"request_id": f"smev-{uuid.uuid4()}", **smev_profile(req.passport, req.declared_income, req.birth_date, req.passport_issue_date,
                                                                         req.passport_division_code, req.employment_months)}


app = create_app(
    service="smev",
    path="/smev/v1/verify",
    request_model=VerifyRequest,
    build_response=build_response,
    secret=secret_from_env("SMEV_HMAC_SECRET", "dev-smev-secret"),
)
