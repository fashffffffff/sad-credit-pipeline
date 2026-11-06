"""Общие фикстуры: фейковые СМЭВ/БКИ/токенизатор (httpx.MockTransport) и фейковая ML-модель.

Тесты ничего не поднимают по сети: backend думает, что говорит с Go-сервисами, а отвечает им Python-функция
по контракту v6 (раздел 3): /smev/check, /bki/score, /bki/report, /oprf/v1/evaluate, /oprf/v1/public-key.
"""

import asyncio
import json
from dataclasses import dataclass, field

import httpx
import pytest
import pytest_asyncio

from backend.app import client_auth
from backend.app.config import Settings
from backend.app.factory import create_app
from backend.app.security.tokens import create_token
from common.signing import sign, verify

GOOD_HASH = "f727a849055f2224"

# учётки только для тестов (файла секретов тесты не трогают)
TEST_ROLES = {"uw": "underwriter", "audit": "auditor", "admin": "admin"}
TEST_PASSWORDS = {name: f"test-only-{name}-pw-x9Q2" for name in TEST_ROLES}
TEST_CREDENTIALS = [{"username": name, "role": role, "password": TEST_PASSWORDS[name]} for name, role in TEST_ROLES.items()]


# ---------- фейковая модель по интерфейсу из раздела 4 контракта ----------
@dataclass
class Contribution:
    feature: str
    label_ru: str
    value: float
    points: float


@dataclass
class Prediction:
    pd: float
    score: int
    base_score: float
    contributions: list


class FakeModel:
    """Фейк по интерфейсу раздела 4: predict_history, predict, features, feature_specs, paym_codes."""

    metrics = {"roc_auc": 0.86, "thresholds": {"approve": 680, "review": 600}}
    features = ["n_loans", "bad_months", "has_current_overdue"]
    feature_specs = [
        {"feature": "n_loans", "label_ru": "Число кредитов", "hint_ru": "", "kind": "int",
         "min": 0, "max": 30, "step": 1, "default": 3},
    ]
    paym_codes = {"0": {"label_ru": "Нет просрочки", "color": "ok"}}

    def predict_history(self, loans: list) -> Prediction:
        bad = sum(1 for l in loans for i in range(25) if l.get(f"enc_paym_{i}", 0) >= 2)
        return self.predict({"n_loans": len(loans), "bad_months": bad, "has_current_overdue": 0})

    def predict(self, features: dict) -> Prediction:
        score = int(760 - 20 * features["bad_months"])
        contribs = [Contribution("bad_months", "Месяцев с просрочкой", features["bad_months"], -20 * features["bad_months"])]
        p = Prediction(pd=0.021, score=score, base_score=640.0, contributions=contribs)
        p.features = dict(features)
        return p


def loan(rn: int = 1, **fields) -> dict:
    """Одна запись кредита в формате БКИ: 25 статусов платежей и признаки типа/статуса."""
    row = {"rn": rn, "pre_since_opened": 18, "enc_loans_credit_type": 4, "enc_loans_credit_status": 3,
           "pclose_flag": 0, "fclose_flag": 0} | {f"enc_paym_{i}": 0 for i in range(25)}
    return row | fields


# ---------- фейковые внешние сервисы ----------
@dataclass
class World:
    """Состояние «внешнего мира»: тесты меняют режимы и ответы."""

    modes: dict = field(default_factory=lambda: {"smev": "normal", "bki": "normal", "tokenizer": "normal"})
    calls: dict = field(default_factory=lambda: {"smev": 0, "bki": 0, "report": 0, "tokenizer": 0})
    signed_ok: list = field(default_factory=list)  # проверка: backend подписал каждый запрос верно
    bki_default: dict = field(default_factory=lambda: {"score": 720, "delinquency": False, "requests_90d": 1})
    bki_override: dict = field(default_factory=dict)  # passport_hash -> ответ /bki/score
    smev_override: dict = field(default_factory=dict)  # client_id -> MATCH|MISMATCH
    loans: list = field(default_factory=lambda: [loan(1), loan(2, fclose_flag=1)])
    report_down: bool = False  # /bki/report отвечает 503, а /bki/score работает


def make_transport(service: str, secret: str, world: World) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": service})
        mode = world.modes[service]
        counter = "report" if path == "/bki/report" else service
        world.calls[counter] += 1
        if mode == "timeout":
            raise httpx.ReadTimeout("service hangs", request=request)
        if mode == "error":
            return httpx.Response(503)
        if mode == "reject":  # сервис жив, но отверг запрос (как токенизатор на мусор вместо точки кривой)
            return httpx.Response(400, json={"error": "bad request"})
        if mode == "flaky_once" and world.calls[counter] == 1:
            return httpx.Response(503)
        if path == "/bki/report" and world.report_down:
            return httpx.Response(503)
        ok = verify(secret, request.content, request.headers.get("X-Signature"), request.headers.get("X-Timestamp"))
        world.signed_ok.append(ok)
        if not ok:
            return httpx.Response(401)
        payload = json.loads(request.content) if request.content else {}
        if path == "/smev/check":
            answer = {"status": world.smev_override.get(payload["client_id"], "MATCH")}
        elif path == "/bki/score":
            answer = world.bki_override.get(payload["passport_hash"], world.bki_default)
        elif path == "/bki/report":
            answer = {"found": True, "loans": world.loans}
        elif path == "/oprf/v1/evaluate":
            answer = {"evaluated": "ZXZhbHVhdGVk", "proof": "cHJvb2Y="}
        elif path == "/oprf/v1/public-key":
            answer = {"suite": "P256-SHA256", "public_key": "a2V5"}
        else:
            return httpx.Response(404)
        body = json.dumps(answer).encode()
        key = "wrong-secret" if mode == "bad_signature" else secret
        return httpx.Response(200, content=body, headers=sign(key, body))

    return httpx.MockTransport(handler)


# ---------- приложение и клиент ----------
class Env:
    def __init__(self, app, client, world):
        self.app, self.client, self.world = app, client, world
        self.state = app.state.ctx
        self.counter = 0

    async def login(self, username: str) -> dict:
        resp = await self.client.post("/api/v1/auth/staff/login",
                                      json={"username": username, "password": TEST_PASSWORDS[username]})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def make_client(self, n: int = 1) -> dict:
        """Клиент без настоящего входа: строка в clients + JWT (так же, как выдаёт client_auth)."""
        client_id = f"cli_{n:04d}"
        self.state.db.execute(
            "INSERT OR IGNORE INTO clients (client_id, phone_hash, phone_masked, created_at) VALUES (?,?,?,?)",
            (client_id, f"phone-hash-{n}", "+7 999 ***-**-67", "2026-11-02T10:00:00+00:00"))
        version = client_auth.client_token_version(self.state.db, client_id)
        token = create_token(self.state.settings.jwt_secret, client_id, "client", 60, version)
        return {"Authorization": f"Bearer {token}"}

    async def submit(self, form: dict, headers: dict, key: str | None = None):
        self.counter += 1
        headers = headers | {"Idempotency-Key": key or f"key-{self.counter}"}
        return await self.client.post("/api/v1/applications", json=form, headers=headers)

    async def drain(self) -> None:
        """Ждём, пока все фоновые конвейеры закончат."""
        while self.state.tasks:
            await asyncio.gather(*list(self.state.tasks))
            await asyncio.sleep(0)  # даём сработать done-callback, который убирает задачу из набора

    async def run(self, form: dict, headers: dict, as_staff: dict | None = None) -> dict:
        """Подаёт заявку, ждёт конвейер, возвращает ApplicationDetail (глазами того, кто смотрит)."""
        resp = await self.submit(form, headers)
        assert resp.status_code == 201, resp.text
        await self.drain()
        return (await self.client.get(f"/api/v1/applications/{resp.json()['app_id']}", headers=as_staff or headers)).json()


def good_form(**overrides) -> dict:
    form = {"passport_hash": GOOD_HASH, "age": 32, "income": 120000, "employment": "employed", "family": "married",
            "amount": 500000, "term": 24, "product_id": "prod_standard", "consent": True}
    return form | overrides


def make_env(tmp_path, model_loader=FakeModel, **settings_overrides) -> Env:
    settings = Settings(database_path=str(tmp_path / "test.sqlite"), external_log=str(tmp_path / "external.log"),
                        cert_key_file=str(tmp_path / "cert.key"), stage_delay_ms=0, retry_delays=(0,),
                        bcrypt_rounds=4, simulate_max_pause_ms=0, auto_retry_seconds=0, **settings_overrides)
    world = World()
    app = create_app(settings,
                     smev_transport=make_transport("smev", settings.smev_secret, world),
                     bki_transport=make_transport("bki", settings.bki_secret, world),
                     tokenizer_transport=make_transport("tokenizer", settings.tokenizer_secret, world),
                     model_loader=model_loader, credentials=TEST_CREDENTIALS)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return Env(app, client, world)


@pytest_asyncio.fixture
async def env(tmp_path):
    e = make_env(tmp_path)
    yield e
    await e.client.aclose()


@pytest_asyncio.fixture
async def env_no_model(tmp_path):
    def broken_loader():
        raise ImportError("ml.credit_model не найден")

    e = make_env(tmp_path, model_loader=broken_loader)
    yield e
    await e.client.aclose()


@pytest.fixture
def client(env):
    """Заголовки клиента cli_0001."""
    return env.make_client(1)


@pytest_asyncio.fixture
async def uw(env):
    return await env.login("uw")


@pytest_asyncio.fixture
async def auditor(env):
    return await env.login("audit")


@pytest_asyncio.fixture
async def admin(env):
    return await env.login("admin")


@pytest.fixture
def form():
    return good_form()
