"""Проверка справки: подпись Ed25519 + «не отозвано» (раздел 10.5). Вход не нужен: справку показывают третьим лицам."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from backend.app import certificate, decisions
from backend.app.schemas import VerifyIn
from backend.app.security.deps import get_state

router = APIRouter(prefix="/api/v1/verify", tags=["verify"])


def is_revoked(state, data: dict) -> bool:
    """Отозвана, если решение изменилось: заявки нет, статус уже не APPROVED или параметры одобрения другие."""
    row = state.db.query_one("SELECT status FROM applications WHERE app_id = ?", (data.get("app_id"),))
    d = decisions.latest_row(state.db, data.get("app_id") or "")
    if row is None or d is None or row["status"] != "APPROVED":
        return True
    return not (d["decision"] == "APPROVED" and d["amount_approved"] is not None
                and round(d["amount_approved"]) == data.get("amount_approved")
                and d["term_approved"] == data.get("term_approved") and d["rate"] == data.get("rate"))


@router.post("")
def verify(body: VerifyIn, state=Depends(get_state)):
    data = certificate.check_signature(state.settings, body.payload, body.signature)
    if data is None:
        return {"valid": False, "revoked": False, "expired": False, "data": None}
    try:
        expired = datetime.fromisoformat(data["valid_until"]) < datetime.now(timezone.utc)
    except (KeyError, ValueError, TypeError):
        expired = False
    return {"valid": True, "revoked": is_revoked(state, data), "expired": expired, "data": data}


@router.get("/public-key")
def public_key(state=Depends(get_state)):
    return {"alg": "Ed25519", "public_key": certificate.public_key_b64u(state.settings)}
