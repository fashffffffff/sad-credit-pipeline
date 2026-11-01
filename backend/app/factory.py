"""Фабрика приложения (отдельно от main.py, чтобы тесты не создавали боевую базу при импорте)."""

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.antifraud.engine import AntifraudEngine
from backend.app.clients import ExternalClient
from backend.app.credentials import load_or_create_credentials, with_admin_override
from backend.app.config import Settings, load_settings
from backend.app.db import Database
from backend.app.errors import ApiError
from backend.app.employer import EmployerChecker, make_employer_checker
from backend.app.events import EventBus
from backend.app.middleware import BodyLimitMiddleware, SecurityHeadersMiddleware
from backend.app.security.passwords import hash_password
from backend.app.routes import antifraud as antifraud_routes, applications, auth, misc
from backend.app.schemas import errors_to_fields
from backend.app.scoring import ModelProvider, default_loader
from backend.app.state import AppState


def create_app(settings: Settings | None = None, *,
               smev_transport: httpx.AsyncBaseTransport | None = None,
               bki_transport: httpx.AsyncBaseTransport | None = None,
               model_loader=default_loader,
               employer_checker: EmployerChecker | None = None,
               credentials: list[dict] | None = None) -> FastAPI:
    """Параметры transport/model_loader/credentials нужны тестам: туда подставляются фейки и свои учётки (без файла)."""
    settings = settings or load_settings()
    if credentials is None:
        credentials = load_or_create_credentials(settings.credentials_file)
    credentials = with_admin_override(credentials, settings.admin_password)
    # в проде схему API (/docs, /openapi.json) не раздаём: это готовая карта ручек для атакующего
    docs = {} if not settings.production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Кредитный конвейер SAD", **docs)
    db = Database(settings.database_path, settings.bcrypt_rounds, credentials)
    app.state.ctx = AppState(
        settings=settings,
        db=db,
        bus=EventBus(),
        smev=ExternalClient("smev", settings.smev_url, settings.smev_secret, settings.request_timeout,
                            settings.retry_delays, smev_transport),
        bki=ExternalClient("bki", settings.bki_url, settings.bki_secret, settings.request_timeout,
                           settings.retry_delays, bki_transport),
        models=ModelProvider(model_loader),
        employer=employer_checker or make_employer_checker(settings.employer_check),
        antifraud=AntifraudEngine(db, settings.pii_pepper, settings.fraud_threshold),
    )

    app.state.ctx.dummy_hash = hash_password("dummy-password", settings.bcrypt_rounds)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST", "PUT", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "validation_error", "fields": errors_to_fields(exc.errors())},
                            status_code=422)

    for module in (auth, applications, misc, antifraud_routes):
        app.include_router(module.router)
    return app

