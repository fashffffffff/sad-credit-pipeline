"""Запуск конвейера: validation → smev → bki → antifraud → scoring → decision.

Каждый этап: событие «running», работа, запись в БД, итоговое событие. Между этапами — пауза для демо.
"""

import asyncio
import logging
import time

from backend.app import audit
from backend.app.db import dumps
from backend.app.pipeline import stages
from backend.app.pipeline.context import Context, StageResult
from backend.app.pipeline.decision import decide
from backend.app.pipeline.status import change_status
from backend.app.schemas import ApplicationIn, monthly_payment
from backend.app.security.redaction import redact_reasons, redact_stage_detail
from backend.app.state import AppState

log = logging.getLogger("pipeline")

STAGE_NAMES = ["validation", "smev", "bki", "antifraud", "scoring", "decision"]


def _emit_stage(ctx: Context, status: str, duration_ms: int, detail: str, operator_detail: str | None = None) -> None:
    event = {"application_id": ctx.app_id, "stage": ctx.stage, "status": status,
             "duration_ms": duration_ms, "attempt": ctx.attempt, "detail": detail}
    if operator_detail is None:
        operator_detail = redact_stage_detail(ctx.stage, status, detail)
    ctx.state.bus.publish("stage", event, owner=ctx.owner, operator_data={**event, "detail": operator_detail})


async def run_stage(ctx: Context, name: str, func) -> StageResult:
    ctx.stage, ctx.attempt = name, 1
    _emit_stage(ctx, "running", 0, "")
    started = time.perf_counter()
    result = await func(ctx)
    ms = round((time.perf_counter() - started) * 1000)
    save_stage(ctx, result.status, ms, result.detail)
    operator_detail = None
    if name == "decision" and ctx.final:  # причины итога — без подробностей антифрода
        operator_detail = redact_stage_detail(name, result.status, result.detail, ctx.final[2])
    _emit_stage(ctx, result.status, ms, result.detail, operator_detail)
    return result


def save_stage(ctx: Context, status: str, ms: int, detail: str) -> None:
    ctx.state.db.execute(
        "UPDATE stages SET status = ?, duration_ms = ?, attempts = ?, detail = ? "
        "WHERE application_id = ? AND stage = ?",
        (status, ms, ctx.attempt, detail, ctx.app_id, ctx.stage),
    )


def skip_stage(ctx: Context, name: str, reason: str) -> None:
    ctx.stage, ctx.attempt = name, 0
    save_stage(ctx, "skipped", 0, f"Пропущено: {reason}")
    _emit_stage(ctx, "skipped", 0, f"Пропущено: {reason}")


async def decision_stage(ctx: Context) -> StageResult:
    if ctx.bki and ctx.bki.get("found"):
        ctx.pdn = round(stages.debt_burden(ctx), 4)
    status, score, reasons = decide(ctx)
    ctx.final = (status, score, reasons)
    level = {"approved": "ok", "manual_review": "warn", "rejected": "fail"}[status]
    return StageResult(level, "; ".join(r["text"] for r in reasons))


async def run_pipeline(state: AppState, app_id: str, data: ApplicationIn, meta: dict | None = None) -> None:
    """Фоновая задача. Что бы ни случилось, заявка не должна зависнуть в «processing»."""
    row = state.db.query_one("SELECT created_by FROM applications WHERE id = ?", (app_id,))
    ctx = Context(state, app_id, data, monthly_payment(data.loan_amount, data.loan_term_months),
                  owner=row["created_by"] if row else "", meta=meta or {})
    ctx.stage = ""
    started = time.perf_counter()
    delay = state.settings.stage_delay_ms / 1000
    try:
        await run_stage(ctx, "validation", stages.validation)

        await asyncio.sleep(delay)
        await run_stage(ctx, "smev", stages.smev)

        await asyncio.sleep(delay)
        if stages.smev_stop(ctx):
            skip_stage(ctx, "bki", "СМЭВ вернул стоп-фактор")
        else:
            await run_stage(ctx, "bki", stages.bki)

        await asyncio.sleep(delay)
        await run_stage(ctx, "antifraud", stages.antifraud)

        await asyncio.sleep(delay)
        reason = stages.scoring_skip_reason(ctx)
        if reason:
            skip_stage(ctx, "scoring", reason)
        else:
            await run_stage(ctx, "scoring", stages.scoring)

        await asyncio.sleep(delay)
        await run_stage(ctx, "decision", decision_stage)
        status, score, reasons = ctx.final
    except Exception as exc:  # непредвиденная ошибка -> человеку, а не в вечный processing
        log.exception("Сбой конвейера %s", app_id)  # подробности — только в лог сервера, пользователю — тип ошибки
        status, score = "manual_review", None
        reasons = [{"code": "PIPELINE_ERROR", "text": f"Внутренняя ошибка конвейера: {type(exc).__name__}"}]

    finish(ctx, status, score, reasons, round((time.perf_counter() - started) * 1000))


def finish(ctx: Context, status: str, score: int | None, reasons: list[dict], total_ms: int) -> None:
    state = ctx.state
    change_status(state.db, ctx.app_id, status)
    state.db.update("applications", "id", ctx.app_id, {
        "score": score, "total_ms": total_ms, "reasons": dumps(reasons),
        "features": dumps(ctx.features), "prediction": dumps(ctx.prediction),
        "pdn": ctx.pdn, "fraud": dumps(ctx.fraud) if ctx.fraud else None,
        # сырые кредитные истории — не PII (обезличены), нужны для «тепловой карты» на фронте
        "bki_loans": dumps((ctx.bki or {}).get("loans")) if ctx.bki else None,
    })
    audit.write(state.db, "system", "system", "decision", "application", ctx.app_id,
                {"status": status, "score": score, "reasons": [r["code"] for r in reasons]})
    event = {"application_id": ctx.app_id, "status": status, "score": score, "reasons": reasons}
    state.bus.publish("decision", event, owner=ctx.owner,
                      operator_data={**event, "reasons": redact_reasons(reasons)})
