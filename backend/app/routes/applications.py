import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.app import audit
from backend.app.db import dumps, loads
from backend.app.errors import ApiError, json_body
from common.passport import is_magic
from backend.app.pipeline.status import InvalidTransition, change_status
from backend.app.schemas import ApplicationIn, ReviewIn, cross_checks, errors_to_fields
from backend.app.security.masking import passport_hash
from backend.app.security.redaction import redact_fraud, redact_reasons, redact_stage_detail
from backend.app.security.deps import User, forbid, get_state, require_roles
from backend.app.services import create_application, now_iso, start_pipeline
from ml.credit_model.history import normalize_paym

router = APIRouter(prefix="/api/applications", tags=["applications"])


def validation_error(fields: dict) -> ApiError:
    return ApiError(422, "validation_error", "", fields=fields)


def summary(row) -> dict:
    return {
        "id": row["id"], "created_at": row["created_at"], "full_name_masked": row["full_name_masked"],
        "loan_amount": row["loan_amount"], "loan_term_months": row["loan_term_months"],
        "status": row["status"], "score": row["score"], "created_by": row["created_by"],
        "total_ms": row["total_ms"],
    }


def compact_loans(loans: list[dict] | None) -> list[dict]:
    """Кредиты для «тепловой карты»: только нужные поля.

    closed: fclose_flag = «фактический срок до закрытия не определён», то есть 1 = кредит ещё открыт,
    0 = закрыт. Так же считает модель (ml/credit_model/history.py), признаки и картинка должны совпадать.
    paym: коды выравниваем той же функцией, что и модель (в трёх позициях датасета коды сдвинуты на +1).
    """
    return [
        {"rn": l.get("rn"), "credit_type": l.get("enc_loans_credit_type"),
         "status": l.get("enc_loans_credit_status"), "closed": l.get("fclose_flag") == 0,
         "paym": normalize_paym(l)}
        for l in loans or []
    ]


def detail(state, row, role: str = "admin") -> dict:
    stages = state.db.query_all(
        "SELECT * FROM stages WHERE application_id = ? ORDER BY position", (row["id"],))
    reasons = loads(row["reasons"]) or []
    operator = role == "operator"  # оператору антифрод показываем урезанно (см. security/redaction.py)
    return {
        **summary(row),
        "passport_masked": row["passport_masked"], "phone_masked": row["phone_masked"],
        "monthly_income": row["monthly_income"], "employment_months": row["employment_months"],
        "dependents": row["dependents"], "purpose": row["purpose"],
        "monthly_payment": row["monthly_payment"],
        "stages": [{"stage": s["stage"], "status": s["status"], "duration_ms": s["duration_ms"],
                    "attempts": s["attempts"],
                    "detail": redact_stage_detail(s["stage"], s["status"], s["detail"], reasons or None) if operator else s["detail"]}
                   for s in stages],
        "features": loads(row["features"]), "pdn": row["pdn"],
        "fraud": redact_fraud(loads(row["fraud"])) if operator else loads(row["fraud"]),
        "bki_loans": compact_loans(loads(row["bki_loans"])), "prediction": loads(row["prediction"]),
        "reasons": redact_reasons(reasons) if operator else reasons, "review": loads(row["review"]),
    }


def saved_response(state, key: str, body_hash: str):
    saved = state.db.query_one("SELECT * FROM idempotency WHERE key = ?", (key,))
    if not saved:
        return None
    if saved["body_hash"] != body_hash:
        raise ApiError(409, "idempotency_conflict", "Этот Idempotency-Key уже использован с другим телом запроса")
    return JSONResponse(json.loads(saved["response"]), status_code=202)


REJECTION_COOLDOWN = timedelta(days=30)


def check_duplicate(state, data: ApplicationIn) -> None:
    """Одна заявка на паспорт: пока старая в работе или недавно отклонена, новую не принимаем."""
    if not state.settings.duplicate_check or is_magic(data.passport):
        return
    pass_hash = passport_hash(state.settings.pii_pepper, data.passport)
    now = datetime.now(timezone.utc)
    for row in state.db.query_all(
            "SELECT id, status, created_at FROM applications WHERE passport_hash = ? ORDER BY created_at DESC",
            (pass_hash,)):
        busy = row["status"] in ("received", "processing", "manual_review")
        recently_rejected = (row["status"] == "rejected"
                             and now - datetime.fromisoformat(row["created_at"]) < REJECTION_COOLDOWN)
        if busy or recently_rejected:
            why = "ещё рассматривается" if busy else "была отклонена меньше 30 дней назад"
            raise ApiError(409, "duplicate_application", f"По этому паспорту уже есть заявка {row['id']}: {why}",
                           existing_id=row["id"])


@router.post("", status_code=202)
async def create(request: Request, user: User = Depends(require_roles("operator", "admin")),
                 idempotency_key: str | None = Header(None), state=Depends(get_state)):
    if not idempotency_key:
        raise validation_error({"Idempotency-Key": "Заголовок обязателен"})
    # ключ хранится в БД: ограничиваем длину и алфавит, иначе туда можно сложить что угодно
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
        raise validation_error({"Idempotency-Key": "До 128 символов: латинские буквы, цифры и . _ : -"})
    body = await json_body(request)

    # идемпотентность: ключ ограничен пользователем, тело сравниваем по хэшу
    key = f"{user.username}:{idempotency_key}"
    try:
        body_hash = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    except RecursionError:  # слишком глубокая вложенность
        raise validation_error({"body": "Некорректный JSON"})
    replay = saved_response(state, key, body_hash)
    if replay:
        return replay

    try:
        data = ApplicationIn.model_validate(body)
    except ValidationError as exc:
        raise validation_error(errors_to_fields(exc.errors()))
    fields = cross_checks(data)
    if fields:
        raise validation_error(fields)
    employer_error = await state.employer.check(data.employer_inn)
    if employer_error:
        raise validation_error({"employer_inn": employer_error})

    # лимит частоты: без него один токен заливает базу и граф антифрода тысячами заявок (повтор по ключу не считается)
    now = state.clock()
    hits = [t for t in state.create_hits.get(user.username, []) if now - t < 60]
    if len(hits) >= state.settings.create_per_minute:
        raise ApiError(429, "too_many_requests", "Слишком много заявок в минуту, подождите")

    # после await могла прийти такая же заявка параллельно — проверяем ещё раз, без пауз до создания
    replay = saved_response(state, key, body_hash)
    if replay:
        return replay
    check_duplicate(state, data)

    app_id = create_application(state, data, user.username, user.role)
    state.create_hits[user.username] = [*hits, now]
    response = {"id": app_id, "status": "processing"}
    state.db.execute("INSERT INTO idempotency VALUES (?,?,?,?)", (key, body_hash, dumps(response), now_iso()))
    start_pipeline(state, app_id, data)  # конвейер идёт в фоне, отвечаем сразу
    return JSONResponse(response, status_code=202)


@router.get("")
def list_applications(status: str | None = None, limit: int = 50,
                      user: User = Depends(require_roles("operator", "risk_manager", "auditor", "admin")),
                      state=Depends(get_state)):
    limit = max(1, min(limit, 500))
    sql, params = "SELECT * FROM applications WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if user.role == "operator":  # оператор видит только свои заявки
        sql += " AND created_by = ?"
        params.append(user.username)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    return [summary(r) for r in state.db.query_all(sql, (*params, limit))]


def get_visible(state, user: User, app_id: str):
    row = state.db.query_one("SELECT * FROM applications WHERE id = ?", (app_id,))
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    if user.role == "operator" and row["created_by"] != user.username:
        forbid(state, user, f"чужая заявка {app_id}")
    return row


@router.get("/{app_id}")
def get_application(app_id: str, user: User = Depends(require_roles("operator", "risk_manager", "auditor", "admin")),
                    state=Depends(get_state)):
    return detail(state, get_visible(state, user, app_id), user.role)


@router.post("/{app_id}/review")
def review(app_id: str, body: ReviewIn, user: User = Depends(require_roles("risk_manager")),
           state=Depends(get_state)):
    row = state.db.query_one("SELECT * FROM applications WHERE id = ?", (app_id,))
    if row is None:
        raise ApiError(404, "not_found", "Заявка не найдена")
    # ручное решение — только по заявке, которую конвейер сам передал человеку. Иначе риск-менеджер мог бы
    # «одобрить» заявку прямо во время обработки (processing -> approved разрешён машиной статусов для конвейера)
    # и обойти проверки паспорта и скоринга
    if row["status"] != "manual_review":
        raise ApiError(409, "invalid_transition", "Ручное решение возможно только для заявок на ручной проверке")
    try:
        change_status(state.db, app_id, body.decision)
    except InvalidTransition as exc:
        raise ApiError(409, "invalid_transition", str(exc))
    review_info = {"by": user.username, "decision": body.decision, "comment": body.comment, "at": now_iso()}
    state.db.update("applications", "id", app_id, {"review": dumps(review_info)})
    audit.write(state.db, user.username, user.role, "manual_review_decision", "application", app_id,
                {"decision": body.decision, "comment": body.comment})
    state.bus.publish("decision", {
        "application_id": app_id, "status": body.decision, "score": row["score"],
        "reasons": [{"code": "MANUAL_REVIEW", "text": f"Ручное решение: {body.decision}"}],
    }, owner=row["created_by"])
    return detail(state, state.db.query_one("SELECT * FROM applications WHERE id = ?", (app_id,)), user.role)
