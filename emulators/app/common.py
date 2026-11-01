"""Общий код эмуляторов СМЭВ и БКИ.

Здесь всё, что одинаково у обоих: профиль человека, подпись, хаос-режимы, логи.
Сами эмуляторы (smev.py, bki.py) только описывают, что отвечать на запрос.
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import random
import time
from datetime import date
from typing import Callable, Literal

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field, ValidationError

from common.middleware import BodyLimitMiddleware
from common.passport import check_passport, passport_expired
from common.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign, verify

# --- Логи: одна строка на запрос, в консоль ---------------------------------
log = logging.getLogger("emulator")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def mask_passport(passport: str) -> str:
    """'4510 123456' -> '45** ****56' (в логах полного паспорта быть не должно)."""
    digits = "".join(ch for ch in passport if ch.isdigit())
    if len(digits) < 6:
        return "****"
    return f"{digits[:2]}** ****{digits[-2:]}"


# --- Профиль человека -------------------------------------------------------
def normalize_passport(passport: str) -> str:
    """Оставляем только цифры: '0000 000001' и '0000000001' — один и тот же паспорт."""
    return "".join(ch for ch in passport if ch.isdigit())


def make_rng(passport: str) -> random.Random:
    """Генератор случайных чисел, засеянный sha256(паспорта): один паспорт -> одни и те же числа."""
    digest = hashlib.sha256(normalize_passport(passport).encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


# Магические паспорта (раздел 3 контракта). 0003 — недействителен в СМЭВ;
# 0004 — нет в БКИ; 0001/0002/0005 в БКИ берут историю из bki_demo.json (см. bki.py).
SMEV_INVALID = {"0000000003"}
BKI_NOT_FOUND = {"0000000004"}
# Доход, который "знает" ФНС, у магических паспортов (рубли)
MAGIC_INCOME = {"0000000001": 150000, "0000000002": 60000, "0000000005": 60000}


def smev_profile(passport: str, declared_income: int | None = None, birth_date: str | None = None,
                 issue_date: str | None = None, division_code: str | None = None,
                 employment_months: int | None = None) -> dict:
    """Что "знают" госсистемы: паспорт, работа, доход. Ответ детерминирован и не содержит случайных отказов.

    passport_valid=false только если: паспорт в «реестре недействительных» (магический 0003), не проходит
    проверки common/passport.py или просрочен по правилу 20/45. employment_confirmed=false только при стаже 0.
    """
    key = normalize_passport(passport)
    if key in SMEV_INVALID:
        return dict(passport_valid=False, person_match=False, employment_confirmed=False, confirmed_income=None)

    rng = make_rng(passport + "smev")  # отдельный поток случайности, чтобы не зависеть от БКИ
    if key in MAGIC_INCOME:
        return dict(passport_valid=True, person_match=True, employment_confirmed=True,
                    confirmed_income=MAGIC_INCOME[key])

    # доход: если банк передал заявленный — ФНС знает его ±15%; иначе просто правдоподобное число
    if declared_income:
        income = int(declared_income * rng.uniform(0.85, 1.15) / 100) * 100
    else:
        income = int(min(400000, max(20000, rng.lognormvariate(11.2, 0.5))) / 100) * 100

    valid = True
    if birth_date and issue_date and division_code:  # старые клиенты без этих полей не проверяем
        birth, issue = date.fromisoformat(birth_date), date.fromisoformat(issue_date)
        pretty = f"{passport[:4]} {passport[-6:]}" if " " not in passport else passport
        valid = not check_passport(pretty, issue, division_code, birth) and not passport_expired(birth, issue, date.today())
    return dict(
        passport_valid=valid,
        person_match=True,
        employment_confirmed=employment_months is None or employment_months > 0,
        confirmed_income=None if rng.random() < 0.08 else income,  # иногда "не нашли" (детерминированно по паспорту)
    )


class ServiceUnavailable(Exception):
    """build_response кидает её, если эмулятор не может ответить (например, нет пула историй) -> HTTP 503."""


# --- Хаос-режимы ------------------------------------------------------------
class ChaosState(BaseModel):
    mode: Literal["normal", "slow", "error", "timeout", "bad_signature", "flaky"] = "normal"
    delay_ms: int = Field(default=0, ge=0, le=60000)  # нужен только для slow


LOCAL_NETS = "127.0.0.1/32,::1/128"  # по умолчанию /chaos доступен только с localhost


def parse_nets(text: str) -> list:
    """'127.0.0.1/32, 172.16.0.0/12' -> список сетей."""
    return [ipaddress.ip_network(part.strip()) for part in text.split(",") if part.strip()]


SLOW_DEFAULT_MS = 4000


def create_app(
    service: str,
    path: str,
    request_model: type[BaseModel],
    build_response: Callable[[BaseModel], dict],
    secret: str,
    base_delay_ms: tuple[int, int] | None = None,
    timeout_s: float | None = None,
    health_info: Callable[[], dict] | None = None,
    chaos_allow_nets: str | None = None,
) -> FastAPI:
    """Собирает приложение эмулятора.

    base_delay_ms и timeout_s можно переопределить (тесты ставят маленькие значения),
    иначе берутся из env EMU_BASE_DELAY_MIN_MS / EMU_BASE_DELAY_MAX_MS / EMU_TIMEOUT_S.
    """
    if base_delay_ms is None:
        base_delay_ms = (int(os.getenv("EMU_BASE_DELAY_MIN_MS", "80")),
                         int(os.getenv("EMU_BASE_DELAY_MAX_MS", "400")))
    if timeout_s is None:
        timeout_s = float(os.getenv("EMU_TIMEOUT_S", "30"))

    # кому можно трогать /chaos: env CHAOS_ALLOW_NETS (в Docker сюда добавляют сеть compose)
    allowed_nets = parse_nets(chaos_allow_nets or os.getenv("CHAOS_ALLOW_NETS", LOCAL_NETS))

    app = FastAPI(title=f"Эмулятор {service.upper()}")
    # тело читается целиком ещё до проверки подписи: без лимита любой может прислать гигабайт
    app.add_middleware(BodyLimitMiddleware, max_bytes=64 * 1024)
    chaos = ChaosState()  # состояние режима живёт в памяти процесса

    def json_response(data: dict, status: int, signing_secret: str | None) -> Response:
        # подписываем именно те байты, которые отправляем
        body = json.dumps(data, ensure_ascii=False).encode()
        headers = sign(signing_secret, body) if signing_secret else {}
        return Response(body, status_code=status, media_type="application/json", headers=headers)

    @app.get("/health")
    async def health():
        # health_info может вернуть {"status": "degraded", "reason": ...}
        return {"status": "ok", "service": service, **(health_info() if health_info else {})}

    def is_allowed(host: str | None) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:  # не IP (например, "testclient") — не пускаем
            return False
        return any(ip in net for net in allowed_nets)

    def only_localhost(request: Request) -> Response | None:
        if not is_allowed(request.client.host if request.client else None):
            return Response(json.dumps({"error": "forbidden"}), status_code=403, media_type="application/json")
        return None

    @app.get("/chaos")
    async def get_chaos(request: Request):
        return only_localhost(request) or chaos

    @app.put("/chaos")
    async def put_chaos(request: Request, new_state: ChaosState):
        # неверный mode отсеет pydantic (422)
        if (denied := only_localhost(request)) is not None:
            return denied
        chaos.mode, chaos.delay_ms = new_state.mode, new_state.delay_ms
        log.info("[%s] chaos -> %s delay_ms=%s", service, chaos.mode, chaos.delay_ms)
        return chaos

    @app.post(path)
    async def handle(request: Request):
        started = time.monotonic()
        body = await request.body()
        passport = "-"

        def finish(resp: Response, note: str = "") -> Response:
            ms = int((time.monotonic() - started) * 1000)
            log.info("[%s] POST %s passport=%s -> %s %dms mode=%s %s",
                     service, path, passport, resp.status_code, ms, chaos.mode, note)
            return resp

        # 1. подпись запроса (до всего остального)
        if not verify(secret, body, request.headers.get(SIGNATURE_HEADER), request.headers.get(TIMESTAMP_HEADER)):
            return finish(json_response({"error": "bad_signature"}, 401, None), "подпись запроса неверна")

        # 2. разбор тела
        try:
            req = request_model.model_validate_json(body)
        except ValidationError:
            return finish(json_response({"error": "validation_error"}, 422, secret))
        passport = mask_passport(getattr(req, "passport", ""))

        # 3. обычная сетевая задержка
        await asyncio.sleep(random.uniform(*base_delay_ms) / 1000)

        # 4. хаос
        if chaos.mode == "slow":
            await asyncio.sleep((chaos.delay_ms or SLOW_DEFAULT_MS) / 1000)
        elif chaos.mode == "error":
            return finish(json_response({"error": "service_unavailable"}, 503, secret))
        elif chaos.mode == "timeout":
            await asyncio.sleep(timeout_s)  # клиент к этому моменту уже сдался по своему таймауту
            return finish(json_response({"error": "gateway_timeout"}, 504, secret))
        elif chaos.mode == "flaky" and random.random() < 0.5:
            return finish(json_response({"error": "service_unavailable"}, 503, secret))

        # 5. нормальный ответ; при bad_signature подписываем чужим секретом
        sign_with = secret + "-wrong" if chaos.mode == "bad_signature" else secret
        try:
            data = build_response(req)
        except ServiceUnavailable as e:
            return finish(json_response({"error": "service_unavailable", "reason": str(e)}, 503, secret), str(e))
        note = f"loans={len(data['loans'])}" if "loans" in data else ""  # сколько кредитов в истории
        return finish(json_response(data, 200, sign_with), note)

    return app
