"""Фикстуры входа клиента: минимальное приложение с одним router и временной БД.
Сеть не используется: внешние сервисы отвечает httpx.MockTransport. От общего backend/tests/conftest.py не зависим."""

import types

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app import client_auth
from backend.app.client_auth import config
from backend.app.db import Database
from backend.app.errors import ApiError

ENV_NAMES = ["AUTH_DEMO", "APP_ENV", "TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME", "SMSRU_API_ID", "YANDEX_CLIENT_ID",
             "YANDEX_CLIENT_SECRET", "VK_CLIENT_ID", "VK_CLIENT_ID_LOCAL", "PUBLIC_BASE_URL"]
GOOD_PHONE = "+7 (999) 123-45-67"
GOOD_PHONE_NORM = "+79991234567"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def advance(self, seconds: float):
        self.t += seconds


class Net:
    """Фейковая «сеть»: handler(request) -> httpx.Response; все запросы запоминаются."""
    def __init__(self):
        self.handler = lambda request: httpx.Response(404)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PII_PEPPER", "test-pepper-" + "x" * 32)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-" + "y" * 32)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(config, "now", c)
    return c


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(config, "transport", httpx.MockTransport(n))
    monkeypatch.setattr(config, "bot_retry_delay", 0)
    return n


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "t.db"), bcrypt_rounds=4)
    client_auth.init_tables(database)
    return database


@pytest.fixture
def state(db):
    return types.SimpleNamespace(db=db)


@pytest.fixture
def client(state, clock, net):
    app = FastAPI()
    app.state.ctx = state

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(exc.body(), status_code=exc.status)

    app.include_router(client_auth.router)
    return TestClient(app)
