"""Пакетный прогон CSV через credit_core (раздел 10.6): тот же движок, что и в консольном прототипе."""

from email import message_from_bytes
from email.policy import HTTP

import credit_core
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from backend.app import audit
from backend.app.errors import ApiError, field_error
from backend.app.security.deps import User, get_state, require_roles

router = APIRouter(prefix="/api/v1/batch", tags=["batch"])
BATCH_PATH = "/api/v1/batch"


def extract_csv(content_type: str, body: bytes) -> str:
    """CSV из multipart/form-data (первая часть-файл) или из «сырого» тела. python-multipart не ставим — разбираем сами."""
    if content_type.lower().startswith("multipart/form-data"):
        msg = message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
                                 policy=HTTP)
        file_part = next((p for p in msg.iter_parts() if p.get_filename() is not None), None) \
            or next(iter(msg.iter_parts()), None)
        if file_part is None:
            raise field_error({"file": "Приложите CSV-файл"})
        body = file_part.get_payload(decode=True) or b""
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise field_error({"file": "Файл должен быть в кодировке UTF-8"})


@router.post("")
async def run_batch(request: Request, user: User = Depends(require_roles("underwriter", "admin")),
                    state=Depends(get_state)):
    text = extract_csv(request.headers.get("content-type", ""), await request.body())
    rows = credit_core.read_csv(text)
    if not rows or "app_id" not in rows[0]:
        raise field_error({"file": "В CSV нужен заголовок с колонкой app_id"})
    pairs = credit_core.decide_rows(rows)
    state.last_batch_csv = credit_core.write_decisions([d for _, d in pairs])
    audit.write(state.db, user.username, user.role, "batch_run", details={"rows": len(rows)})
    labeled = "decision" in rows[0]
    out = []
    for row, d in pairs:
        explanation = credit_core.explain(row, d) if labeled else ""
        out.append({"app_id": d.app_id, "decision": d.decision, "reason": d.reason, "score": d.score,
                    "dti": None if d.dti is None else round(d.dti, 2),
                    "csv_decision": (row.get("decision") or "").strip() or None,
                    "csv_reason": (row.get("reason") or "").strip() or None,
                    "match": (not explanation) if labeled else None, "explanation": explanation})
    return {"summary": credit_core.summary(pairs), "rows": out}


@router.get("/last.csv")
def last_csv(user: User = Depends(require_roles("underwriter", "admin")), state=Depends(get_state)):
    if not state.last_batch_csv:
        raise ApiError(404, "NOT_FOUND", "Пакетный прогон ещё не запускали")
    return Response(state.last_batch_csv, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="decisions.csv"'})
