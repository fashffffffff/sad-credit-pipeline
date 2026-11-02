"""Заявки: подача, просмотр, пересчёт, ручное решение, встречное предложение, справка (раздел 7 контракта)."""

import hashlib
import json
import re

import credit_core
from credit_core import Decision
from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from backend.app import audit, certificate, client_auth, decisions
from backend.app.db import dumps, loads
from backend.app.decisions import now_iso
from backend.app.errors import ApiError, field_error, json_body
from backend.app.pipeline.runner import process
from backend.app.pipeline.status import InvalidTransition, change_status
from backend.app.reasons import MANUAL_REFUSED
from backend.app.schemas import AcceptOfferIn, ReviewIn
from backend.app.security.deps import ANY_ROLE, STAFF_ROLES, User, forbid, get_state, require_roles
from backend.app.security.masking import short_hash
from backend.app.security.redaction import redact_reason, visible_stages
from backend.app.services import create_application, parse_form, start_pipeline

router = APIRouter(prefix="/api/v1", tags=["applications"])

ACTIVE = ("PROCESSING", "MANUAL_REVIEW")  # «одна активная заявка на паспорт»
KEY_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def summary(state, row, role: str) -> dict:
    d = decisions.latest_row(state.db, row["app_id"])
    reason = None if d is None else (d["reason"] if role != "client" else redact_reason(d["reason"]))
    return {"app_id": row["app_id"], "client_id": row["client_id"], "created_at": row["created_at"],
            "status": row["status"], "reason": reason, "amount": row["amount"], "term": row["term"],
            "product_id": row["product_id"], "score": None if d is None else d["score"], "total_ms": row["total_ms"]}


def external_view(state, app_id: str) -> list[dict]:
    rows = state.db.query_all("SELECT * FROM external_requests WHERE app_id = ? ORDER BY rowid", (app_id,))
    return [{"request_id": r["request_id"], "app_id": r["app_id"], "service": r["service"], "attempt": r["attempt"],
             "status": r["status"], "latency_ms": r["latency_ms"], "result": loads(r["result_json"]), "at": r["at"]}
            for r in rows]


def detail(state, row, role: str) -> dict:
    """ApplicationDetail. Клиенту НЕ отдаём fraud, ml_hint, external (ключей нет вовсе), антифрод-этап и строку правила 11."""
    stages = state.db.query_all("SELECT * FROM stages WHERE application_id = ? ORDER BY position", (row["app_id"],))
    out = {
        **summary(state, row, role),
        "age": row["age"], "income": row["income"], "employment": row["employment"], "family": row["family"],
        "passport_hash_short": short_hash(row["passport_hash"]), "parent_app_id": row["parent_app_id"],
        "stages": visible_stages(
            [{"stage": s["stage"], "status": s["status"], "duration_ms": s["duration_ms"], "attempts": s["attempts"],
              "detail": s["detail"]} for s in stages], role),
        "decision": decisions.latest(state.db, row["app_id"], role),
        "decisions_history": decisions.history(state.db, row["app_id"], role),
        "review": loads(row["review"]),
    }
    if role in ("underwriter", "auditor"):
        out["external"] = external_view(state, row["app_id"])
        out["ml_hint"] = loads(row["ml_hint"])
    if role in ("underwriter", "auditor", "admin"):
        out["fraud"] = loads(row["fraud"])
    return out


def get_visible(state, user: User, app_id: str):
    row = state.db.query_one("SELECT * FROM applications WHERE app_id = ?", (app_id,))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Заявка не найдена")
    if user.is_client and row["client_id"] != user.username:
        forbid(state, user, f"чужая заявка {app_id}", app_id)
    return row


# ---------- идемпотентность ----------
def check_key(key: str | None) -> str:
    if not key:
        raise field_error({"Idempotency-Key": "Заголовок обязателен"})
    # ключ хранится в БД: ограничиваем длину и алфавит, иначе туда можно сложить что угодно
    if not KEY_RE.fullmatch(key):
        raise field_error({"Idempotency-Key": "До 128 символов: латинские буквы, цифры и . _ : -"})
    return key


def body_digest(body) -> str:
    try:
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    except RecursionError:  # слишком глубокая вложенность
        raise field_error({"body": "Некорректный JSON"})


def saved_response(state, key: str, digest: str):
    saved = state.db.query_one("SELECT * FROM idempotency WHERE key = ?", (key,))
    if not saved:
        return None
    if saved["body_hash"] != digest:
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "Этот Idempotency-Key уже использован с другим телом запроса")
    return JSONResponse(json.loads(saved["response"]), status_code=201)


def check_rate(state, who: str) -> list:
    """Лимит частоты: без него один токен заливает базу и граф антифрода тысячами заявок."""
    now = state.clock()
    hits = [t for t in state.create_hits.get(who, []) if now - t < 60]
    if len(hits) >= state.settings.create_per_minute:
        raise ApiError(429, "TOO_MANY_REQUESTS", "Слишком много заявок в минуту, подождите", retryable=True)
    return [*hits, now]


def check_duplicate(state, passport_hash: str) -> None:
    """Одна активная заявка на паспорт: пока старая PROCESSING или MANUAL_REVIEW, новую не принимаем."""
    if not state.settings.duplicate_check:
        return
    row = state.db.query_one(
        "SELECT app_id FROM applications WHERE passport_hash = ? AND status IN ('PROCESSING','MANUAL_REVIEW') LIMIT 1",
        (passport_hash,))
    if row:
        raise ApiError(409, "DUPLICATE_APPLICATION", f"По этому паспорту уже есть заявка {row['app_id']} в работе",
                       details={"existing_app_id": row["app_id"]})


@router.post("/quote")
async def quote(request: Request, user: User = Depends(require_roles("client")), state=Depends(get_state)):
    """Предварительный расчёт без БКИ. Ничего не сохраняет и в аудит не пишет."""
    body = await json_body(request)
    if not isinstance(body, dict):
        raise field_error({"body": "Нужен JSON-объект"})
    body = {k: v for k, v in body.items() if k != "client_device_id"}
    try:
        app, _ = parse_form(body, need_passport=False)
    except ApiError as exc:
        if exc.status != 422:
            raise
        blocking = [exc.error_code]
        return {"dti": None, "monthly_payment": None, "partial_score": None, "max_partial_score": 90,
                "eligible_products": [], "blocking": blocking, "fields": exc.details.get("fields", {})}
    local = credit_core.evaluate(app)
    _, parts, _ = credit_core.score_breakdown(app, None)
    return {"dti": None if local.dti is None else round(local.dti, 2),
            "monthly_payment": None if local.monthly_payment is None else round(local.monthly_payment),
            "partial_score": sum(p["points"] for p in parts), "max_partial_score": 90,
            "eligible_products": [p.to_dict() for p in credit_core.eligible_products(app.age, app.income, app.amount, app.term)],
            "blocking": [local.reason] if local.decision else [], "fields": {}}


@router.post("/applications", status_code=201)
async def create(request: Request, user: User = Depends(require_roles("client")),
                 idempotency_key: str | None = Header(None), state=Depends(get_state)):
    key = check_key(idempotency_key)
    body = await json_body(request)
    # идемпотентность: ключ ограничен пользователем, тело сравниваем по хэшу
    full_key, digest = f"{user.username}:{key}", body_digest(body)
    replay = saved_response(state, full_key, digest)
    if replay:
        return replay
    app, device_id = parse_form(body)
    hits = check_rate(state, user.username)
    # после await могла прийти такая же заявка параллельно — проверяем ещё раз, без пауз до создания
    replay = saved_response(state, full_key, digest)
    if replay:
        return replay
    check_duplicate(state, app.passport_hash)
    app_id = create_application(state, app, user.username, device_id=device_id, idempotency_key=key)
    state.create_hits[user.username] = hits
    response = {"app_id": app_id, "status": "PROCESSING"}
    state.db.execute("INSERT INTO idempotency VALUES (?,?,?,?)", (full_key, digest, dumps(response), now_iso()))
    start_pipeline(state, app_id)
    return JSONResponse(response, status_code=201)


@router.get("/applications")
def list_applications(status: str | None = None, limit: int = 50, user: User = Depends(require_roles(*ANY_ROLE)),
                      state=Depends(get_state)):
    limit = max(1, min(limit, 500))
    sql, params = "SELECT * FROM applications WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        params.append(status.upper())
    if user.is_client:  # клиент видит только свои заявки
        sql += " AND client_id = ?"
        params.append(user.username)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    return [summary(state, r, user.role) for r in state.db.query_all(sql, (*params, limit))]


@router.get("/applications/{app_id}")
def get_application(app_id: str, user: User = Depends(require_roles(*ANY_ROLE)), state=Depends(get_state)):
    return detail(state, get_visible(state, user, app_id), user.role)


@router.get("/applications/{app_id}/decision")
def get_decision(app_id: str, user: User = Depends(require_roles(*ANY_ROLE)), state=Depends(get_state)):
    row = get_visible(state, user, app_id)
    d = None if row["status"] == "PROCESSING" else decisions.latest(state.db, app_id, user.role)
    if d is None:
        raise ApiError(425, "STILL_PROCESSING", "Заявка ещё обрабатывается", retryable=True)
    return d


@router.post("/applications/{app_id}/recalculate")
async def recalculate(app_id: str, user: User = Depends(require_roles("underwriter")), state=Depends(get_state)):
    """Новая версия решения: заново спрашиваем СМЭВ и БКИ и прогоняем правила. История версий сохраняется."""
    row = state.db.query_one("SELECT status FROM applications WHERE app_id = ?", (app_id,))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Заявка не найдена")
    try:
        change_status(state.db, app_id, "PROCESSING")
    except InvalidTransition:
        raise ApiError(409, "INVALID_TRANSITION", "Заявка уже обрабатывается", retryable=True)
    return await process(state, app_id, actor=user.username, action="recalculated")


@router.post("/applications/{app_id}/review")
async def review(app_id: str, body: ReviewIn, user: User = Depends(require_roles("underwriter")),
                 state=Depends(get_state)):
    row = state.db.query_one("SELECT * FROM applications WHERE app_id = ?", (app_id,))
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Заявка не найдена")
    # ручное решение — только по заявке, которую конвейер сам передал человеку
    if row["status"] != "MANUAL_REVIEW":
        raise ApiError(409, "INVALID_TRANSITION", "Ручное решение возможно только для заявок на ручной проверке")
    try:
        change_status(state.db, app_id, body.decision)
    except InvalidTransition as exc:
        raise ApiError(409, "INVALID_TRANSITION", str(exc))
    prev = decisions.latest_row(state.db, app_id)
    product = credit_core.PRODUCTS[row["product_id"]]
    approved = body.decision == "APPROVED"
    d = Decision(app_id=app_id, decision=body.decision, reason="" if approved else MANUAL_REFUSED,
                 score=prev["score"], dti=prev["dti"], monthly_payment=prev["monthly_payment"],
                 amount_approved=row["amount"] if approved else None, term_approved=row["term"] if approved else None,
                 rate=product.rate if approved else None, product_id=row["product_id"],
                 bki_score=prev["bki_score"],
                 bki_delinquency=None if prev["bki_delinquency"] is None else bool(prev["bki_delinquency"]),
                 smev=prev["smev_status"], breakdown=decisions._breakdown(state.db, app_id, prev["version"]),
                 trace=loads(prev["trace_json"]) or [], rules_version=prev["rules_version"])
    decisions.save_decision(state.db, d, [], "underwriter")
    info = {"by": user.username, "decision": body.decision, "comment": body.comment, "at": now_iso()}
    state.db.update("applications", "app_id", app_id, {"review": dumps(info)})
    audit.write(state.db, user.username, user.role, "reviewed", app_id, decision=body.decision, reason=d.reason,
                score=d.score, dti=d.dti, details={"comment": body.comment})
    event = {"application_id": app_id, "status": body.decision, "reason": d.reason, "score": d.score}
    state.bus.publish("decision", event, owner=row["client_id"],
                      client_data={**event, "reason": redact_reason(d.reason)})
    await client_auth.notify_decision(state, row["client_id"], app_id)
    return decisions.latest(state.db, app_id, user.role)


@router.post("/applications/{app_id}/accept-offer", status_code=201)
async def accept_offer(app_id: str, body: AcceptOfferIn, user: User = Depends(require_roles("client")),
                       idempotency_key: str | None = Header(None), state=Depends(get_state)):
    """Клиент принимает встречное предложение: новая заявка с теми же данными и параметрами предложения."""
    key = check_key(idempotency_key)
    row = get_visible(state, user, app_id)
    full_key, digest = f"{user.username}:{key}", body_digest({"app": app_id, "i": body.offer_index})
    replay = saved_response(state, full_key, digest)
    if replay:
        return replay
    d = decisions.latest_row(state.db, app_id)
    offers = (loads(d["offers_json"]) if d else None) or []
    if row["status"] != "REFUSED" or body.offer_index >= len(offers):
        raise ApiError(409, "NO_SUCH_OFFER", "По этой заявке нет такого предложения")
    busy = state.db.query_one("SELECT app_id FROM applications WHERE parent_app_id = ? AND status IN "
                              "('PROCESSING','MANUAL_REVIEW','APPROVED') LIMIT 1", (app_id,))
    if busy:
        raise ApiError(409, "DUPLICATE_APPLICATION", f"Предложение уже принято: заявка {busy['app_id']}",
                       details={"existing_app_id": busy["app_id"]})
    hits = check_rate(state, user.username)
    offer = offers[body.offer_index]
    app = credit_core.Application(
        app_id="", passport_hash=row["passport_hash"], age=row["age"], income=row["income"],
        employment=row["employment"], family=row["family"], amount=offer["amount"], term=offer["term"],
        product_id=offer["product_id"])
    errors = credit_core.validate_for_api(app)
    if errors:  # предложение устарело (правила изменились)
        raise ApiError(422, errors[0].code, errors[0].message, details={"fields": {e.field: e.message for e in errors}})
    new_id = create_application(state, app, user.username, device_hash=row["client_device_id_hash"],
                                parent_app_id=app_id, idempotency_key=key, actor_role="client")
    state.create_hits[user.username] = hits
    audit.write(state.db, user.username, "client", "offer_accepted", app_id,
                details={"offer_index": body.offer_index, "new_app_id": new_id})
    response = {"app_id": new_id, "status": "PROCESSING", "parent_app_id": app_id}
    state.db.execute("INSERT INTO idempotency VALUES (?,?,?,?)", (full_key, digest, dumps(response), now_iso()))
    start_pipeline(state, new_id)
    return JSONResponse(response, status_code=201)


@router.get("/applications/{app_id}/certificate.pdf")
def certificate_pdf(app_id: str, user: User = Depends(require_roles("client")), state=Depends(get_state)):
    row = get_visible(state, user, app_id)
    d = decisions.latest_row(state.db, app_id)
    if row["status"] != "APPROVED" or d is None or d["decision"] != "APPROVED":
        raise ApiError(409, "NOT_APPROVED", "Справка выдаётся только по одобренной заявке")
    phone = client_auth.phone_masked_of(state.db, row["client_id"]) or ""
    pdf = certificate.build_certificate(state.settings, d, row, phone)
    audit.write(state.db, user.username, "client", "certificate_issued", app_id, decision="APPROVED",
                score=d["score"], dti=d["dti"])
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="certificate_{app_id}.pdf"'})
