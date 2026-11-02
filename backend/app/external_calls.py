"""Вызовы СМЭВ и БКИ из конвейера: каждая попытка → таблица external_requests и строка JSON в logs/external.log."""

import json
import os
from datetime import datetime, timezone

from credit_core import BkiResult

from backend.app import audit
from backend.app.db import dumps
from backend.app.external_client import BadSignature, ExternalClient, ServiceUnavailable
from backend.app.state import AppState


def now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_external_log(state: AppState, line: dict) -> None:
    path = state.settings.external_log
    if not path:
        return
    with state.log_lock:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")


def record_attempt(state: AppState, service: str, app_id: str, attempt: int, status: str, latency_ms: int,
                   result: dict | None) -> str:
    """Одна попытка вызова. В result нет ПДн: только скор, флаг просрочки, статус СМЭВ."""
    request_id = state.db.next_id("req")
    at = now_z()
    state.db.execute(
        "INSERT INTO external_requests (request_id, app_id, service, attempt, status, latency_ms, result_json, at)"
        " VALUES (?,?,?,?,?,?,?,?)", (request_id, app_id, service, attempt, status, latency_ms, dumps(result or {}), at))
    write_external_log(state, {"timestamp": at, "service": service, "request_id": request_id, "app_id": app_id,
                               "attempt": attempt, "status": status, "latency_ms": latency_ms, "result": result or {}})
    return request_id


async def call_service(state: AppState, client: ExternalClient, service: str, app_id: str, path: str, payload: dict,
                       check, result_of, on_retry=None) -> tuple[str, dict | None, int]:
    """Вызов с повторами. Возвращает (статус SUCCESS|TIMEOUT|ERROR, ответ, число попыток).

    check(data) — ответ разумной формы? Иначе это ERROR (и повторять нет смысла).
    result_of(data) — что записать в журнал (без ПДн).
    Неверная подпись ответа → ERROR + запись signature_error в аудит и событие безопасности.
    """
    last = {"attempts": 0, "valid": True}

    async def on_attempt(attempt: int, status: str, latency_ms: int, data) -> None:
        last["attempts"] = attempt
        if status == "SUCCESS" and not check(data):
            status, last["valid"] = "ERROR", False
        record_attempt(state, service, app_id, attempt, status, latency_ms,
                       result_of(data) if status == "SUCCESS" else None)
        if on_retry and status != "SUCCESS":
            await on_retry(attempt)

    try:
        data = await client.post_signed(path, payload, on_attempt)
    except BadSignature as exc:
        audit.write(state.db, "system", "system", "signature_error", app_id, details={"service": service})
        state.bus.publish("security", {"kind": "bad_signature",
                                       "detail": f"Неверная подпись ответа {service} (заявка {app_id})"})
        return "ERROR", None, exc.attempts
    except ServiceUnavailable as exc:
        timed_out = "таймаут" in str(exc)
        return ("TIMEOUT" if timed_out else "ERROR"), None, max(exc.attempts, last["attempts"])
    if not last["valid"]:
        return "ERROR", None, last["attempts"]
    return "SUCCESS", data, last["attempts"]


# --- что считать корректным ответом -------------------------------------------
def bki_score_ok(d) -> bool:
    return (isinstance(d, dict) and isinstance(d.get("score"), int) and not isinstance(d["score"], bool)
            and isinstance(d.get("delinquency"), bool))


def smev_ok(d) -> bool:
    return isinstance(d, dict) and d.get("status") in ("MATCH", "MISMATCH")


def report_ok(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("loans", []), list)


def bki_result(status: str, data: dict | None) -> BkiResult:
    if status == "SUCCESS" and data:
        return BkiResult(status="SUCCESS", score=data["score"], delinquency=data["delinquency"],
                         requests_90d=data.get("requests_90d"))
    return BkiResult(status="TIMEOUT" if status == "TIMEOUT" else "ERROR")
