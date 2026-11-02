"""Паспорт не покидает телефон (раздел 5 контракта): backend — только прокси ослеплённых значений к токенизатору OPRF."""

from fastapi import APIRouter, Depends

from backend.app import audit
from backend.app.errors import ApiError
from backend.app.external_client import BadSignature, RequestRejected, ServiceUnavailable
from backend.app.schemas import OprfIn
from backend.app.security.deps import User, get_state, require_roles

router = APIRouter(prefix="/api/v1/passport", tags=["passport"])


def check_oprf_limits(state, client_id: str) -> None:
    """5 запросов в минуту и 20 в час на клиента: иначе токенизатор можно использовать как оракул для перебора паспортов."""
    now = state.clock()
    hits = [t for t in state.oprf_hits.get(client_id, []) if now - t < 3600]
    per_minute = sum(1 for t in hits if now - t < 60)
    if per_minute >= state.settings.oprf_per_minute or len(hits) >= state.settings.oprf_per_hour:
        state.oprf_hits[client_id] = hits
        raise ApiError(429, "TOO_MANY_REQUESTS", "Слишком много попыток проверки паспорта, подождите",
                       retryable=True)
    state.oprf_hits[client_id] = [*hits, now]


async def _tokenizer(state, user: User, call):
    try:
        return await call()
    except BadSignature:
        audit.write(state.db, "system", "system", "signature_error", details={"service": "TOKENIZER"})
        state.bus.publish("security", {"kind": "bad_signature", "detail": "Неверная подпись ответа токенизатора"})
        raise ApiError(502, "TOKENIZER_BAD_RESPONSE", "Сервис паспорта вернул неподписанный ответ", retryable=True)
    except RequestRejected:
        # токенизатор жив, но значение не является точкой кривой P-256: ошибка в запросе клиента, а не сбой
        raise ApiError(422, "INVALID_BLINDED", "Не удалось обработать паспорт, попробуйте ещё раз")
    except ServiceUnavailable:
        raise ApiError(503, "TOKENIZER_UNAVAILABLE", "Сервис проверки паспорта недоступен, попробуйте позже",
                       retryable=True)


@router.post("/oprf")
async def oprf(body: OprfIn, user: User = Depends(require_roles("client")), state=Depends(get_state)):
    check_oprf_limits(state, user.username)
    data = await _tokenizer(state, user, lambda: state.tokenizer.post_signed("/oprf/v1/evaluate",
                                                                             {"blinded": body.blinded}))
    if not (isinstance(data, dict) and isinstance(data.get("evaluated"), str) and isinstance(data.get("proof"), str)):
        raise ApiError(502, "TOKENIZER_BAD_RESPONSE", "Сервис паспорта вернул некорректный ответ", retryable=True)
    return {"evaluated": data["evaluated"], "proof": data["proof"]}


@router.get("/public-key")
async def public_key(user: User = Depends(require_roles("client")), state=Depends(get_state)):
    data = await _tokenizer(state, user, lambda: state.tokenizer.get_signed("/oprf/v1/public-key"))
    if not (isinstance(data, dict) and isinstance(data.get("public_key"), str)):
        raise ApiError(502, "TOKENIZER_BAD_RESPONSE", "Сервис паспорта вернул некорректный ответ", retryable=True)
    return {"suite": data.get("suite", "P256-SHA256"), "public_key": data["public_key"]}
