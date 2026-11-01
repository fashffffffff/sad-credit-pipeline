"""Общие фикстуры: фейковые СМЭВ/БКИ (через httpx.MockTransport) и фейковая ML-модель.

Тесты не поднимают ничего по сети: backend думает, что говорит с эмуляторами, а отвечает им Python-функция.
"""

import asyncio
import json
from datetime import date
from dataclasses import dataclass, field

import httpx
import pytest
import pytest_asyncio

from backend.app.config import Settings
from backend.app.factory import create_app
from common.passport import is_magic, passport_expired
from common.signing import sign, verify

GOOD_PASSPORT = "4510 123456"

# учётки только для тестов (файла секретов тесты не трогают); пароли нигде не повторяют «логин+123»
TEST_ROLES = {"operator": "operator", "risk": "risk_manager", "auditor": "auditor", "admin": "admin"}
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

    metrics = {"roc_auc": 0.86, "thresholds": {"approve": 680, "review": 600},
               "stop_rules": [{"code": "RULE_CURRENT_OVERDUE", "feature": "has_current_overdue", "op": ">=",
                               "value": 1, "text_ru": "Текущая просрочка (признак = {value})",
                               "share": 0.03, "default_rate": 0.12}]}
    features = ["n_loans", "bad_months", "has_current_overdue"]
    feature_specs = [
        {"feature": "n_loans", "label_ru": "Число кредитов", "hint_ru": "", "kind": "int",
         "min": 0, "max": 30, "step": 1, "default": 3},
        {"feature": "bad_months", "label_ru": "Месяцев с просрочкой", "hint_ru": "", "kind": "int",
         "min": 0, "max": 100, "step": 1, "default": 0},
        {"feature": "has_current_overdue", "label_ru": "Есть текущая просрочка", "hint_ru": "", "kind": "bool",
         "min": 0, "max": 1, "step": 1, "default": 0},
    ]
    paym_codes = {"0": {"label_ru": "Нет просрочки", "color": "ok"}}

    def predict_history(self, loans: list) -> Prediction:
        bad = sum(1 for l in loans for i in range(25) if l.get(f"enc_paym_{i}", 0) >= 2)
        overdue = int(any(l.get("enc_paym_0", 0) >= 3 for l in loans))
        return self.predict({"n_loans": len(loans), "bad_months": bad, "has_current_overdue": overdue})

    def predict(self, features: dict) -> Prediction:
        score = int(760 - 20 * features["bad_months"] - 200 * features["has_current_overdue"])
        contribs = [Contribution("bad_months", "Месяцев с просрочкой", features["bad_months"],
                                 -20 * features["bad_months"])]
        p = Prediction(pd=0.03, score=score, base_score=640.0, contributions=contribs)
        p.features = dict(features)
        return p


# ---------- фейковые эмуляторы ----------
def loan(rn: int = 1, **fields) -> dict:
    """Одна запись кредита в формате БКИ (раздел 3): 25 статусов платежей и признаки типа/статуса."""
    row = {"rn": rn, "pre_since_opened": 18, "enc_loans_credit_type": 4, "enc_loans_credit_status": 3,
           "pclose_flag": 0, "fclose_flag": 0} | {f"enc_paym_{i}": 0 for i in range(25)}
    return row | fields


GOOD_BKI = {"found": True, "loans": [loan(1), loan(2, fclose_flag=1)], "monthly_debt_payment": 12000,
            "total_debt": 340000, "inquiries_30d": 1, "inquiries_6m": 2, "self_ban": False}
NO_HISTORY_BKI = {"found": False, "loans": [], "monthly_debt_payment": None, "total_debt": None,
                  "inquiries_30d": None, "inquiries_6m": None}
MAGIC_BKI = {
    "0000 000002": GOOD_BKI | {"loans": [loan(1, enc_paym_0=3, enc_paym_1=3)]},  # текущая просрочка
    "0000 000004": NO_HISTORY_BKI,
    "0000 000006": GOOD_BKI | {"self_ban": True, "self_ban_since": "2025-06-01"},  # самозапрет на кредиты
    # 5 месяцев с плохим статусом -> балл 660, зона ручной проверки
    "0000 000005": GOOD_BKI | {"loans": [loan(1, **{f"enc_paym_{i}": 2 for i in range(5, 10)})]},
}


@dataclass
class World:
    """Состояние «внешнего мира»: тесты меняют режимы и ответы."""

    modes: dict = field(default_factory=lambda: {"smev": "normal", "bki": "normal"})
    calls: dict = field(default_factory=lambda: {"smev": 0, "bki": 0})
    signed_ok: list = field(default_factory=list)  # проверка: backend подписал каждый запрос верно
    smev_override: dict = field(default_factory=dict)  # паспорт -> поля ответа СМЭВ
    bki_override: dict = field(default_factory=dict)  # паспорт -> ответ БКИ

    def smev_answer(self, passport: str, payload: dict | None = None) -> dict:
        base = {"passport_valid": True, "person_match": True, "employment_confirmed": True,
                "confirmed_income": 120000}
        if passport == "0000 000003":
            base["passport_valid"] = False
        payload = payload or {}
        if payload.get("passport_issue_date") and not is_magic(passport):  # как настоящий СМЭВ: просрочка по 20/45
            birth, issue = date.fromisoformat(payload["birth_date"]), date.fromisoformat(payload["passport_issue_date"])
            if passport_expired(birth, issue, date.today()):
                base["passport_valid"] = False
        return base | self.smev_override.get(passport, {})

    def bki_answer(self, passport: str) -> dict:
        return self.bki_override.get(passport) or MAGIC_BKI.get(passport, GOOD_BKI)


def make_transport(service: str, secret: str, world: World) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # /health
            return httpx.Response(200, json={"status": "ok", "service": service})
        world.calls[service] += 1
        mode = world.modes[service]
        if mode == "timeout":
            raise httpx.ReadTimeout("emulator hangs", request=request)
        if mode == "error":
            return httpx.Response(503)
        if mode == "flaky_once" and world.calls[service] == 1:
            return httpx.Response(503)
        ok = verify(secret, request.content, request.headers.get("X-Signature"), request.headers.get("X-Timestamp"))
        world.signed_ok.append(ok)
        if not ok:
            return httpx.Response(401)
        payload = json.loads(request.content)
        answer = world.smev_answer(payload["passport"], payload) if service == "smev" else world.bki_answer(payload["passport"])
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
        resp = await self.client.post("/api/auth/login", json={"username": username, "password": TEST_PASSWORDS[username]})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    async def submit(self, form: dict, headers: dict, key: str | None = None):
        self.counter += 1
        headers = headers | {"Idempotency-Key": key or f"key-{self.counter}"}
        return await self.client.post("/api/applications", json=form, headers=headers)

    async def drain(self) -> None:
        """Ждём, пока все фоновые конвейеры закончат."""
        while self.state.tasks:
            await asyncio.gather(*list(self.state.tasks))
            await asyncio.sleep(0)  # даём сработать done-callback, который убирает задачу из набора

    async def run_and_get(self, form: dict, headers: dict) -> dict:
        resp = await self.submit(form, headers)
        assert resp.status_code == 202, resp.text
        await self.drain()
        return (await self.client.get(f"/api/applications/{resp.json()['id']}", headers=headers)).json()


def good_form(**overrides) -> dict:
    form = {
        "full_name": "Иванов Иван Иванович", "birth_date": "1995-04-12", "passport": GOOD_PASSPORT,
        "passport_issue_date": "2015-05-20", "passport_division_code": "770-001",
        "phone": "+79991234567", "monthly_income": 120000, "employer_inn": "7707083893",
        "employment_months": 36, "dependents": 1, "loan_amount": 500000, "loan_term_months": 24,
        "purpose": "consumer",
    }
    return form | overrides


def make_env(tmp_path, model_loader=FakeModel, employer_checker=None, **settings_overrides) -> Env:
    settings = Settings(database_path=str(tmp_path / "test.db"), stage_delay_ms=0, retry_delays=(0, 0),
                        bcrypt_rounds=4, simulate_max_pause_ms=0, **settings_overrides)
    world = World()
    app = create_app(settings,
                     smev_transport=make_transport("smev", settings.smev_secret, world),
                     bki_transport=make_transport("bki", settings.bki_secret, world),
                     model_loader=model_loader, employer_checker=employer_checker, credentials=TEST_CREDENTIALS)
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


class FakeModelNoRules(FakeModel):
    metrics = {"roc_auc": 0.86, "thresholds": {"approve": 680, "review": 600}}


class FakeModelBadRule(FakeModel):
    """Правило ссылается на признак, которого у модели нет: должно быть пропущено."""

    metrics = FakeModel.metrics | {"stop_rules": [{"code": "R", "feature": "no_such", "op": ">=", "value": 1,
                                                   "text_ru": "x", "share": 0, "default_rate": 0}]}


@pytest_asyncio.fixture(params=[FakeModelNoRules, FakeModelBadRule], ids=["no_rules", "unknown_feature"])
async def env_odd_rules(tmp_path, request):
    e = make_env(tmp_path, model_loader=request.param)
    yield e
    await e.client.aclose()


@pytest_asyncio.fixture
async def operator(env):
    return await env.login("operator")


@pytest.fixture
def form():
    return good_form()
