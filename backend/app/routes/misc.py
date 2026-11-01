"""Модель, what-if, статистика, симуляция, хаос-панель, аудит, SSE, health."""

import asyncio
import json
import secrets
from collections import Counter, defaultdict

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from backend.app import audit
from backend.app.errors import ApiError, json_body
from backend.app.schemas import ChaosIn, SimulateIn, errors_to_fields
from backend.app.scoring import ModelUnavailable, prediction_to_dict
from backend.app.events import CLOSE, TooManyStreams
from backend.app.security.deps import User, get_state, require_roles
from backend.app.simulate import run_simulation

router = APIRouter(prefix="/api", tags=["misc"])
MAX_FEATURE_VALUE = 1e9  # признаки модели — счётчики, месяцы, доли; больше миллиарда — заведомо мусор
ANY_ROLE = ("operator", "risk_manager", "auditor", "admin")


def get_model(state):
    try:
        return state.models.get()
    except ModelUnavailable as exc:
        raise ApiError(503, "model_unavailable", str(exc))


@router.post("/score/whatif")
async def whatif(request: Request, user: User = Depends(require_roles("operator", "risk_manager", "admin")),
                 state=Depends(get_state)):
    """Пересчёт балла без сохранения заявки (ползунки на фронте)."""
    body = await json_body(request)
    if not isinstance(body, dict):
        raise ApiError(422, "validation_error", "", fields={"body": "Нужен JSON-объект с признаками"})
    model = get_model(state)
    fields = {}
    for name in model.features:  # список признаков всегда берём у модели
        value = body.get(name)
        if value is None:
            fields[name] = "Поле обязательно"
        elif not isinstance(value, (int, float)):  # bool в Python — тоже int, подходит для флагов
            fields[name] = "Должно быть числом"
        elif not abs(value) <= MAX_FEATURE_VALUE:  # NaN, Infinity и 10**400 роняли модель (500); для NaN сравнение ложно
            fields[name] = "Число вне допустимого диапазона"
    if fields:
        raise ApiError(422, "validation_error", "", fields=fields)
    prediction = await asyncio.to_thread(model.predict, {name: body[name] for name in model.features})
    return prediction_to_dict(prediction)


@router.get("/model/features")
def model_features(user: User = Depends(require_roles(*ANY_ROLE)), state=Depends(get_state)):
    model = get_model(state)
    return {"feature_specs": model.feature_specs, "paym_codes": model.paym_codes}


@router.get("/model/info")
def model_info(user: User = Depends(require_roles(*ANY_ROLE)), state=Depends(get_state)):
    return get_model(state).metrics


@router.get("/stats")
def stats(user: User = Depends(require_roles(*ANY_ROLE)), state=Depends(get_state)):
    db = state.db
    rows = db.query_all("SELECT status, score, created_at FROM applications")
    by_status = {"approved": 0, "rejected": 0, "manual_review": 0, "processing": 0}
    by_status.update(Counter(r["status"] for r in rows if r["status"] in by_status))
    decided = by_status["approved"] + by_status["rejected"] + by_status["manual_review"]
    scores = [r["score"] for r in rows if r["score"] is not None]

    avg_ms = {}
    for name in ("smev", "bki", "scoring"):
        row = db.query_one("SELECT AVG(duration_ms) AS a FROM stages WHERE stage = ? AND status IN ('ok','warn','fail')",
                           (name,))
        avg_ms[name] = round(row["a"] or 0)

    bins = list(range(300, 851, 50))
    counts = [0] * (len(bins) - 1)
    for s in scores:
        counts[min((max(s, 300) - 300) // 50, len(counts) - 1)] += 1

    per_minute = defaultdict(lambda: {"approved": 0, "rejected": 0, "manual_review": 0})
    for r in rows:
        if r["status"] in ("approved", "rejected", "manual_review"):
            per_minute[r["created_at"][:16]][r["status"]] += 1
    timeline = [{"minute": minute + ":00", **counts_} for minute, counts_ in sorted(per_minute.items())[-60:]]

    return {
        "total": len(rows), "by_status": by_status,
        "approval_rate": round(by_status["approved"] / decided, 3) if decided else 0.0,
        "avg_score": round(sum(scores) / len(scores)) if scores else 0,
        "avg_stage_ms": avg_ms,
        "score_histogram": {"bins": bins, "counts": counts},
        "timeline": timeline,
        "services": dict(state.chaos_modes),
    }


@router.post("/simulate", status_code=202)
async def simulate(body: SimulateIn, user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    # одна симуляция за раз: иначе серия запросов «300 заявок» копит тысячи одновременных конвейеров
    if state.sim_running:
        raise ApiError(409, "simulation_running", "Симуляция уже идёт, дождитесь окончания")
    state.sim_running = True
    audit.write(state.db, user.username, user.role, "simulate", details={"count": body.count, "scenario": body.scenario})
    state.spawn(run_simulation(state, body.count, user.username, user.role, body.scenario))
    return {"started": body.count, "scenario": body.scenario}


@router.get("/simulate/labels")
def simulate_labels(user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    """Правда о последней симуляции: какая заявка обычная, а какая из схемы. Нужно, чтобы честно измерить, сколько поймал антифрод."""
    return state.sim_labels


@router.get("/chaos/{service}")
async def get_chaos(service: str, user: User = Depends(require_roles("admin")), state=Depends(get_state)):
    client = chaos_client(state, service)
    try:
        result = await client.get_json("/chaos")
    except httpx.HTTPError:
        raise ApiError(502, "emulator_unavailable", f"Эмулятор {service} недоступен")
    state.chaos_modes[service] = result.get("mode", "normal")
    return result


@router.put("/chaos/{service}")
async def put_chaos(service: str, request: Request, user: User = Depends(require_roles("admin")),
                    state=Depends(get_state)):
    client = chaos_client(state, service)
    try:
        payload = ChaosIn.model_validate(await json_body(request)).model_dump()
    except ValidationError as exc:
        raise ApiError(422, "validation_error", "", fields=errors_to_fields(exc.errors()))
    try:
        result = await client.put_json("/chaos", payload)
    except httpx.HTTPError:
        raise ApiError(502, "emulator_unavailable", f"Эмулятор {service} недоступен")
    state.chaos_modes[service] = result.get("mode", payload.get("mode", "normal"))
    audit.write(state.db, user.username, user.role, "chaos_change", "service", service, payload)
    state.bus.publish("chaos", {"service": service, "mode": state.chaos_modes[service]})
    return result


def chaos_client(state, service: str):
    if service not in ("smev", "bki"):
        raise ApiError(404, "not_found", "Неизвестный сервис (smev или bki)")
    return getattr(state, service)


@router.get("/audit")
def audit_records(limit: int = 100, user: User = Depends(require_roles("auditor", "admin")),
                  state=Depends(get_state)):
    return audit.list_records(state.db, max(1, min(limit, 1000)))


@router.get("/audit/verify")
def audit_verify(user: User = Depends(require_roles("auditor", "admin")), state=Depends(get_state)):
    return audit.verify_chain(state.db)


SSE_TICKET_TTL = 30  # секунд на то, чтобы открыть поток по билету


@router.post("/events/ticket")
def events_ticket(user: User = Depends(require_roles(*ANY_ROLE)), state=Depends(get_state)):
    """Одноразовый билет для SSE. EventSource не умеет заголовки, а JWT в адресе попадает в access-логи
    сервера и прокси; билет живёт 30 секунд, открывает один поток и сам по себе ничего больше не даёт."""
    now = state.clock()
    for old in [t for t, (_, _, exp) in state.sse_tickets.items() if exp < now]:  # чистим просроченные
        del state.sse_tickets[old]
    if len(state.sse_tickets) > 1000:
        raise ApiError(429, "too_many_requests", "Слишком много билетов, подождите")
    row = state.db.query_one("SELECT token_version FROM users WHERE username = ?", (user.username,))
    ticket = secrets.token_urlsafe(24)
    state.sse_tickets[ticket] = (user.username, row["token_version"], now + SSE_TICKET_TTL)
    return {"ticket": ticket, "expires_in": SSE_TICKET_TTL}


def user_from_ticket(state, ticket: str | None) -> User:
    """Билет гасится при первом использовании; после logout-all (смена token_version) он тоже не годится."""
    entry = state.sse_tickets.pop(ticket, None) if ticket else None
    if entry is None or entry[2] < state.clock():
        raise ApiError(401, "unauthorized", "Нужен действующий билет (POST /api/events/ticket)")
    row = state.db.query_one("SELECT role, token_version FROM users WHERE username = ?", (entry[0],))
    if row is None or row["token_version"] != entry[1]:
        raise ApiError(401, "unauthorized", "Билет больше не действителен — войдите заново")
    return User(entry[0], row["role"])


@router.get("/events/stream")
async def event_stream(ticket: str | None = None, state=Depends(get_state)):
    user = user_from_ticket(state, ticket)
    try:
        queue = state.bus.subscribe(user.username, user.role)
    except TooManyStreams:
        raise ApiError(429, "too_many_requests", "Слишком много открытых потоков событий")

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


@router.get("/health")
async def health(state=Depends(get_state)):
    async def probe(client) -> str:
        try:
            await client.get_json("/health", timeout=1.0)
            return "up"
        except Exception:
            return "down"

    smev, bki = await asyncio.gather(probe(state.smev), probe(state.bki))
    ready = state.models.is_ready()
    return {"backend": "ok", "smev": smev, "bki": bki,
            "model": "ready" if ready else "unavailable", "model_error": state.models.error}
