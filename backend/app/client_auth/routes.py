"""Ручки входа клиента: /api/v1/auth/*."""

import hmac
import secrets

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.errors import ApiError
from backend.app.client_auth import config, oauth, smsru, store
from backend.app.client_auth.phone import hmac_hex, normalize_phone, phone_hash

router = APIRouter(prefix="/api/v1/auth", tags=["client-auth"])


class StartIn(BaseModel):
    phone: str
    channel: str


class CodeIn(BaseModel):
    code: str


class CallbackIn(BaseModel):
    code: str
    state: str
    device_id: str | None = None


def _db(request: Request):
    return request.app.state.ctx.db


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/providers")
async def providers():
    flags = config.providers()
    return {**flags, "enabled": [name for name, on in flags.items() if on]}


@router.post("/phone/start")
async def phone_start(body: StartIn, request: Request):
    db, ip = _db(request), _ip(request)
    if body.channel not in ("telegram", "call", "demo") or not config.providers().get(body.channel):
        raise ApiError(400, "CHANNEL_UNAVAILABLE", "Этот способ входа не включён")
    phone = normalize_phone(body.phone)
    if phone is None:
        raise ApiError(422, "INVALID_PHONE", "Введите мобильный номер в формате +7 9XX XXX-XX-XX")
    store.check_rate_limits(db, ip, phone_hash(phone))
    if body.channel == "telegram":
        nonce = secrets.token_urlsafe(12)
        ch = store.create_challenge(db, "telegram", phone, ip, nonce=nonce)
        return {"challenge_id": ch["challenge_id"], "expires_in": config.CHALLENGE_TTL,
                "deep_link": f"https://t.me/{config.env('TELEGRAM_BOT_USERNAME')}?start={nonce}"}
    if body.channel == "call":
        try:
            check_id, call_to = await smsru.add(phone, ip)
        except smsru.CallError as exc:
            if exc.invalid_phone:
                raise ApiError(422, "INVALID_PHONE", "Номер не принят оператором")
            raise ApiError(502, "CHANNEL_ERROR", "Звонок временно недоступен, попробуйте другой способ")
        ch = store.create_challenge(db, "call", phone, ip, check_id=check_id)
        return {"challenge_id": ch["challenge_id"], "call_to": call_to, "expires_in": config.CHALLENGE_TTL}
    code = f"{secrets.randbelow(10**6):06d}"
    ch = store.create_challenge(db, "demo", phone, ip, code_hash="")
    db.execute("UPDATE auth_challenges SET code_hash = ? WHERE challenge_id = ?",
               (hmac_hex(f"{ch['challenge_id']}:{code}"), ch["challenge_id"]))
    return {"challenge_id": ch["challenge_id"], "demo": True, "code": code, "expires_in": config.CHALLENGE_TTL}


@router.get("/phone/{challenge_id}")
async def phone_status(challenge_id: str, request: Request):
    db = _db(request)
    row = store.get_challenge(db, challenge_id)
    if row is None:  # неизвестный id неотличим от истёкшего
        return {"status": "expired"}
    if row["status"] == "pending" and row["channel"] == "call" and row["check_id"]:
        remote = await smsru.status(row["check_id"])
        if remote == "verified":
            store.complete_challenge(db, row)
        elif remote == "expired":
            db.execute("UPDATE auth_challenges SET status = 'expired' WHERE challenge_id = ?", (challenge_id,))
        row = store.get_challenge(db, challenge_id)
    if row["status"] != "verified":
        return {"status": row["status"]}
    return _deliver(db, row)


def _deliver(db, row) -> dict:
    """Токен отдаём один раз: повторный опрос уже выданного challenge токена не возвращает."""
    with db.lock:
        taken = db.query_one("SELECT delivered FROM auth_challenges WHERE challenge_id = ?", (row["challenge_id"],))
        if taken["delivered"]:
            return {"status": "verified"}
        db.execute("UPDATE auth_challenges SET delivered = 1 WHERE challenge_id = ?", (row["challenge_id"],))
    return {"status": "verified", **store.login_result(db, row["client_id"], row["channel"])}


@router.post("/phone/{challenge_id}/code")
async def phone_code(challenge_id: str, body: CodeIn, request: Request):
    db = _db(request)
    bad = ApiError(400, "INVALID_CODE", "Неверный код или он истёк")
    row = store.get_challenge(db, challenge_id)
    if row is None or row["channel"] != "demo" or row["status"] != "pending":
        raise bad
    expected = hmac_hex(f"{challenge_id}:{body.code.strip()}")
    if not hmac.compare_digest(expected, row["code_hash"]):  # сравнение за постоянное время
        attempts = row["attempts"] + 1
        status = "expired" if attempts >= config.MAX_BAD_CODES else "pending"  # 5 ошибок — challenge сгорает
        db.execute("UPDATE auth_challenges SET attempts = ?, status = ? WHERE challenge_id = ?",
                   (attempts, status, challenge_id))
        store.login_failed(db, "demo", "bad_code", row["phone_masked"])
        raise bad
    row = {**dict(row), "client_id": store.complete_challenge(db, row)}
    db.execute("UPDATE auth_challenges SET delivered = 1 WHERE challenge_id = ?", (challenge_id,))
    return {"status": "verified", **store.login_result(db, row["client_id"], "demo")}


@router.get("/oauth/{provider}/start")
async def oauth_start(provider: str, request: Request):
    if provider not in ("yandex", "vk") or not config.providers().get(provider):
        raise ApiError(400, "CHANNEL_UNAVAILABLE", "Этот способ входа не включён")
    return oauth.start(_db(request), provider, _ip(request))


@router.post("/oauth/{provider}/callback")
async def oauth_callback(provider: str, body: CallbackIn, request: Request):
    if provider not in ("yandex", "vk") or not config.providers().get(provider):
        raise ApiError(400, "CHANNEL_UNAVAILABLE", "Этот способ входа не включён")
    return await oauth.callback(_db(request), provider, body.code, body.state, body.device_id)
