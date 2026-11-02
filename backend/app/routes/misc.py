"""Продукты, модель, статистика, журналы, пульт стенда (хаос, симулятор, демо), SSE, health."""

import asyncio
import json
import secrets
from collections import Counter, defaultdict

import credit_core
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from backend.app import audit, client_auth
from backend.app.db import dumps
from backend.app.errors import ApiError, json_body
from backend.app.events import CLOSE, TooManyStreams
from backend.app.pipeline.runner import probe_services
from backend.app.routes.applications import external_view
from backend.app.schemas import ChaosIn, SimulateIn, errors_to_fields
from backend.app.scoring import ModelUnavailable
from backend.app.security.deps import ANY_ROLE, User, forbid, get_state, require_roles
from backend.app.simulate import run_simulation

router = APIRouter(prefix="/api/v1", tags=["misc"])
health_router = APIRouter(tags=["health"])
STATUSES = ("APPROVED", "REFUSED", "NEED_MORE_DATA", "MANUAL_REVIEW", "PROCESSING")


def get_model(state):
    try:
        return state.models.get()
    except ModelUnavailable as exc:
        raise ApiError(503, "MODEL_UNAVAILABLE", str(exc), retryable=True)


@router.get("/products")
def products(user: User = Depends(require_roles(*ANY_ROLE))):
    return [p.to_dict() for p in credit_core.PRODUCTS.values()]


@router.get("/model/features")
def model_features(user: User = Depends(require_roles("underwriter", "auditor")), state=Depends(get_state)):
    model = get_model(state)
    return {"feature_specs": model.feature_specs, "paym_codes": model.paym_codes}


@router.get("/model/info")
def model_info(user: User = Depends(require_roles("underwriter", "auditor")), state=Depends(get_state)):
    return get_model(state).metrics


@router.get("/stats")
def stats(user: User = Depends(require_roles("underwriter", "auditor", "admin")), state=Depends(get_state)):
    db = state.db
    rows = db.query_all("SELECT a.status, a.created_at, d.score, d.reason FROM applications a LEFT JOIN decisions d "
                        "ON d.app_id = a.app_id AND d.version = (SELECT MAX(version) FROM decisions WHERE app_id = a.app_id)")
    by_status = {s: 0 for s in STATUSES}
    by_status.update(Counter(r["status"] for r in rows if r["status"] in by_status))
    decided = sum(v for k, v in by_status.items() if k != "PROCESSING")
    scores = [r["score"] for r in rows if r["score"] is not None]
    by_reason = Counter(r["reason"] for r in rows if r["reason"] and r["status"] != "PROCESSING")

    avg_ms = {}
    for name in ("smev", "bki", "scoring"):
        row = db.query_one("SELECT AVG(duration_ms) AS a FROM stages WHERE stage = ? AND status IN ('ok','warn','fail')",
                           (name,))
        avg_ms[name] = round(row["a"] or 0)

    bins = list(range(0, 101, 10))
    counts = [0] * (len(bins) - 1)
    for s in scores:
        counts[min(max(s, 0) // 10, len(counts) - 1)] += 1

    per_minute = defaultdict(lambda: {"APPROVED": 0, "REFUSED": 0, "NEED_MORE_DATA": 0, "MANUAL_REVIEW": 0})
    for r in rows:
        if r["status"] in per_minute.default_factory():
            per_minute[r["created_at"][:16]][r["status"]] += 1
    timeline = [{"minute": minute + ":00", **c} for minute, c in sorted(per_minute.items())[-60:]]
    return {"total": len(rows), "by_status": by_status, "by_reason": dict(by_reason),
            "approval_rate": round(by_status["APPROVED"] / decided, 3) if decided else 0.0,
            "avg_score": round(sum(scores) / len(scores)) if scores else 0, "avg_stage_ms": avg_ms,
            "score_histogram": {"bins": bins, "counts": counts}, "timeline": timeline,
            "services": dict(state.chaos_modes)}


@router.get("/external")
def external(app_id: str | None = None, limit: int = 100, user: User = Depends(require_roles("auditor", "underwriter")),
             state=Depends(get_state)):
    limit = max(1, min(limit, 1000))
    if app_id:
        rows = state.db.query_all("SELECT * FROM external_requests WHERE app_id = ? ORDER BY rowid DESC LIMIT ?",
                                  (app_id, limit))
    else:
        rows = state.db.query_all("SELECT * FROM external_requests ORDER BY rowid DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        out.append({"request_id": r["request_id"], "app_id": r["app_id"], "service": r["service"],
                    "attempt": r["attempt"], "status": r["status"], "latency_ms": r["latency_ms"],
                    "result": json.loads(r["result_json"] or "{}"), "at": r["at"]})
    return out


@router.get("/audit")
def audit_records(limit: int = 100, app_id: str | None = None, user: User = Depends(require_roles("auditor")),
                  state=Depends(get_state)):
    return audit.list_records(state.db, max(1, min(limit, 1000)), app_id)


@router.get("/audit/verify")
def audit_verify(user: User = Depends(require_roles("auditor")), state=Depends(get_state)):
    return audit.verify_chain(state.db)


# ---------- пульт стенда ----------
@router.post("/simulate", status_code=202)
async def simulate(body: SimulateIn, user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    # одна симуляция за раз: иначе серия запросов «300 заявок» копит тысячи одновременных конвейеров
    if state.sim_running:
        raise ApiError(409, "SIMULATION_RUNNING", "Симуляция уже идёт, дождитесь окончания")
    state.sim_running = True
    audit.write(state.db, user.username, user.role, "simulate_started", details={"count": body.count, "scenario": body.scenario})
    state.spawn(run_simulation(state, body.count, user.username, user.role, body.scenario))
    return {"started": body.count, "scenario": body.scenario}


@router.get("/simulate/labels")
def simulate_labels(user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    """Правда о последней симуляции: какая заявка обычная, а какая из схемы. Нужно, чтобы честно измерить, сколько поймал антифрод."""
    return state.sim_labels


def chaos_client(state, service: str):
    if service not in ("smev", "bki"):
        raise ApiError(404, "NOT_FOUND", "Неизвестный сервис (smev или bki)")
    return getattr(state, service)


@router.get("/chaos/{service}")
async def get_chaos(service: str, user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    client = chaos_client(state, service)
    try:
        result = await client.get_json("/chaos")
    except httpx.HTTPError:
        raise ApiError(502, "SERVICE_UNAVAILABLE", f"Сервис {service} недоступен", retryable=True)
    state.chaos_modes[service] = result.get("mode", "normal")
    return result


@router.put("/chaos/{service}")
async def put_chaos(service: str, request: Request, user: User = Depends(require_roles("admin")),
                    state=Depends(get_state)):
    client = chaos_client(state, service)
    try:
        payload = ChaosIn.model_validate(await json_body(request)).model_dump()
    except ValidationError as exc:
        raise RequestValidationError(exc.errors())
    try:
        result = await client.put_json("/chaos", payload)
    except httpx.HTTPError:
        raise ApiError(502, "SERVICE_UNAVAILABLE", f"Сервис {service} недоступен", retryable=True)
    state.chaos_modes[service] = result.get("mode", payload.get("mode", "normal"))
    audit.write(state.db, user.username, user.role, "chaos_changed", details={"service": service, **payload})
    state.bus.publish("chaos", {"service": service, "mode": state.chaos_modes[service]})
    return result


# ---------- демо «подделать запись аудита» (раздел 10.3) ----------
def _demo_only(state) -> None:
    if state.settings.production:
        raise ApiError(404, "NOT_FOUND", "Недоступно в production")


@router.post("/demo/tamper-audit")
def tamper_audit(user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    """Портит одну запись журнала (меняет её данные, хэш не трогает): verify покажет сломанную цепочку."""
    _demo_only(state)
    if state.audit_backup:
        raise ApiError(409, "ALREADY_TAMPERED", "Запись уже подделана: сначала restore-audit")
    row = state.db.query_one("SELECT id, details FROM audit_log WHERE action IN ('decision_made','recalculated','reviewed') "
                             "ORDER BY id DESC LIMIT 1") or state.db.query_one("SELECT id, details FROM audit_log ORDER BY id DESC LIMIT 1")
    if row is None:
        raise ApiError(409, "AUDIT_EMPTY", "В журнале пока нет записей")
    state.audit_backup = (row["id"], row["details"])
    state.db.execute("UPDATE audit_log SET details = ? WHERE id = ?", (dumps({"tampered": True}), row["id"]))
    return {"tampered": f"evt_{row['id']:04d}"}


@router.post("/demo/restore-audit")
def restore_audit(user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    _demo_only(state)
    if not state.audit_backup:
        raise ApiError(409, "NOT_TAMPERED", "Нечего восстанавливать")
    audit_id, details = state.audit_backup
    state.db.execute("UPDATE audit_log SET details = ? WHERE id = ?", (details, audit_id))
    state.audit_backup = None
    return {"restored": f"evt_{audit_id:04d}"}


# ---------- SSE ----------
SSE_TICKET_TTL = 30  # секунд на то, чтобы открыть поток по билету


def token_version_of(state, user: User) -> int | None:
    if user.is_client:
        return client_auth.client_token_version(state.db, user.username)
    row = state.db.query_one("SELECT token_version FROM users WHERE username = ?", (user.username,))
    return None if row is None else row["token_version"]


@router.post("/events/ticket")
def events_ticket(user: User = Depends(require_roles(*ANY_ROLE)), state=Depends(get_state)):
    """Одноразовый билет для SSE. EventSource не умеет заголовки, а JWT в адресе попадает в access-логи
    сервера и прокси; билет живёт 30 секунд, открывает один поток и сам по себе ничего больше не даёт."""
    now = state.clock()
    for old in [t for t, (_, _, _, exp) in state.sse_tickets.items() if exp < now]:  # чистим просроченные
        del state.sse_tickets[old]
    if len(state.sse_tickets) > 1000:
        raise ApiError(429, "TOO_MANY_REQUESTS", "Слишком много билетов, подождите", retryable=True)
    ticket = secrets.token_urlsafe(24)
    state.sse_tickets[ticket] = (user.username, user.role, token_version_of(state, user), now + SSE_TICKET_TTL)
    return {"ticket": ticket, "expires_in": SSE_TICKET_TTL}


def user_from_ticket(state, ticket: str | None) -> User:
    """Билет гасится при первом использовании; после logout-all (смена token_version) он тоже не годится."""
    entry = state.sse_tickets.pop(ticket, None) if ticket else None
    if entry is None or entry[3] < state.clock():
        raise ApiError(401, "UNAUTHORIZED", "Нужен действующий билет (POST /api/v1/events/ticket)")
    username, role, version, _ = entry
    user = User(username, role)
    if token_version_of(state, user) != version:
        raise ApiError(401, "UNAUTHORIZED", "Билет больше не действителен — войдите заново")
    return user


@router.get("/events/stream")
async def event_stream(ticket: str | None = None, state=Depends(get_state)):
    user = user_from_ticket(state, ticket)
    try:
        queue = state.bus.subscribe(user.username, user.role)
    except TooManyStreams:
        raise ApiError(429, "TOO_MANY_REQUESTS", "Слишком много открытых потоков событий", retryable=True)

    async def generate():
        try:
            yield {"event": "hello", "data": "{}"}
            while True:
                event_type, data = await queue.get()
                if event_type == CLOSE:
                    return
                yield {"event": event_type, "data": json.dumps(data, ensure_ascii=False)}
        finally:
            state.bus.unsubscribe(queue)

    return EventSourceResponse(generate())


# ---------- health: без входа, нужен балансировщикам и демо ----------
@health_router.get("/health")
@router.get("/health")
async def health(state=Depends(get_state)):
    services = await probe_services(state)
    up = all(v in ("up", "ready") for v in services.values())
    return {"status": "ok" if up else "degraded", "services": services}
