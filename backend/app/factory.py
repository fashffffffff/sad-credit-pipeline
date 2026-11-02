"""Фабрика приложения (отдельно от main.py, чтобы тесты не создавали боевую базу при импорте)."""

import asyncio
import contextlib
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app import client_auth
from backend.app.antifraud.engine import AntifraudEngine
from backend.app.config import Settings, load_settings
from backend.app.credentials import load_or_create_credentials, with_admin_override
from backend.app.db import Database
from backend.app.errors import ApiError
from backend.app.events import EventBus
from backend.app.external_client import ExternalClient
from backend.app.middleware import BodyLimitMiddleware, SecurityHeadersMiddleware
from backend.app.pipeline.runner import auto_retry_once
from backend.app.routes import antifraud as antifraud_routes, applications, auth, batch, misc, passport, verify
from backend.app.schemas import errors_to_fields
from backend.app.scoring import ModelProvider, default_loader
from backend.app.security.passwords import hash_password
from backend.app.state import AppState

log = logging.getLogger("app")


async def auto_retry_loop(state: AppState) -> None:
    """Раз в AUTO_RETRY_SECONDS перепроверяем заявки, которые ждали ответа БКИ/СМЭВ (actor = system)."""
    while True:
        await asyncio.sleep(state.settings.auto_retry_seconds)
        try:
            await auto_retry_once(state)
        except Exception:
            log.exception("Сбой автоповтора")


def create_app(settings: Settings | None = None, *,
               smev_transport: httpx.AsyncBaseTransport | None = None,
               bki_transport: httpx.AsyncBaseTransport | None = None,
               tokenizer_transport: httpx.AsyncBaseTransport | None = None,
               model_loader=default_loader,
               credentials: list[dict] | None = None) -> FastAPI:
    """Параметры transport/model_loader/credentials нужны тестам: туда подставляются фейки и свои учётки (без файла)."""
    settings = settings or load_settings()
    if credentials is None:
        credentials = load_or_create_credentials(settings.credentials_file)
    credentials = with_admin_override(credentials, settings.admin_password)
    db = Database(settings.database_path, settings.bcrypt_rounds, credentials)
    client_auth.init_tables(db)
    ctx = AppState(
        settings=settings,
        db=db,
        bus=EventBus(),
        smev=ExternalClient("smev", settings.smev_url, settings.smev_secret, settings.request_timeout,
                            settings.retry_delays, smev_transport),
        bki=ExternalClient("bki", settings.bki_url, settings.bki_secret, settings.request_timeout,
                           settings.retry_delays, bki_transport),
        tokenizer=ExternalClient("tokenizer", settings.tokenizer_url, settings.tokenizer_secret,
                                 settings.request_timeout, settings.retry_delays, tokenizer_transport),
        models=ModelProvider(model_loader),
        antifraud=AntifraudEngine(db, settings.pii_pepper, settings.fraud_threshold),
    )
    ctx.dummy_hash = hash_password("dummy-password", settings.bcrypt_rounds)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        """Старт: бот Telegram (client_auth) и автоповтор. Стоп: аккуратно гасим всё."""
        await client_auth.startup(ctx)
        retry = ctx.spawn(auto_retry_loop(ctx)) if settings.auto_retry_seconds > 0 else None
        try:
            yield
        finally:
            if retry:
                retry.cancel()
            await client_auth.shutdown(ctx)

    # в проде схему API (/docs, /openapi.json) не раздаём: это готовая карта ручек для атакующего
    docs = {} if not settings.production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Кредитный конвейер SAD", lifespan=lifespan, **docs)
    app.state.ctx = ctx
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes,
                       path_limits={batch.BATCH_PATH: settings.max_batch_bytes})
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST", "PUT", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(ApiError(422, "VALIDATION_ERROR", "Проверьте переданные поля",
                                     details={"fields": errors_to_fields(exc.errors())}).body(), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        message = "Не найдено" if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(ApiError(exc.status_code, code, message).body(), status_code=exc.status_code)

    for module in (auth, applications, passport, verify, batch, misc, antifraud_routes):
        app.include_router(module.router)
    app.include_router(misc.health_router)
    app.include_router(client_auth.router)
    return app
