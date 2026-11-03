"""Конвейер: validation → local_rules → smev ∥ bki → antifraud → scoring → decision (раздел 1 контракта).

process() запускается и для новой заявки, и для пересчёта, и для автоповтора — каждый раз рождается новая версия решения.
Каждый этап: событие «running», работа, запись в БД, итоговое событие. Между этапами — пауза для демо.
"""

import asyncio
import logging
import time

import credit_core
from credit_core import Application, BkiResult

from backend.app import audit, client_auth, decisions
from backend.app.db import dumps, loads
from backend.app.external_calls import (bki_result, bki_score_ok, call_service, report_ok, smev_ok)
from backend.app.hint import build_hint
from backend.app.pipeline.status import change_status
from backend.app.security.redaction import redact_reason
from backend.app.state import AppState

log = logging.getLogger("pipeline")

STAGE_NAMES = ["validation", "local_rules", "smev", "bki", "antifraud", "scoring", "decision"]
OFFER_REASONS = ("HIGH_DTI", "AMOUNT_LIMIT")
# для встречных предложений по HIGH_DTI до обращения к БКИ/СМЭВ (правило 5 срабатывает раньше): считаем при
# «хороших» ответах, предложение помечено preliminary — окончательно решит полный конвейер
ASSUMED_BKI = BkiResult(status="SUCCESS", score=700, delinquency=False, requests_90d=1)


def application_from_row(row) -> Application:
    return Application(app_id=row["app_id"], client_id=row["client_id"], passport_hash=row["passport_hash"],
                       age=row["age"], income=row["income"], employment=row["employment"], family=row["family"],
                       amount=row["amount"], term=row["term"], product_id=row["product_id"])


def reset_stages(state: AppState, app_id: str) -> None:
    state.db.execute("UPDATE stages SET status='pending', duration_ms=NULL, attempts=NULL, detail=NULL "
                     "WHERE application_id = ?", (app_id,))


class Run:
    """Состояние одного прогона: чтобы этапы могли публиковать события и писать в БД."""

    def __init__(self, state: AppState, app_id: str, owner: str):
        self.state, self.app_id, self.owner = state, app_id, owner

    def emit(self, stage: str, status: str, ms: int, detail: str, attempt: int = 1) -> None:
        event = {"application_id": self.app_id, "stage": stage, "status": status,
                 "duration_ms": ms, "attempt": attempt, "detail": detail}
        # клиент про антифрод не знает вообще: этап для него не существует
        self.state.bus.publish("stage", event, owner=self.owner, client_visible=stage != "antifraud")

    def start(self, stage: str) -> float:
        self.emit(stage, "running", 0, "")
        return time.perf_counter()

    def finish(self, stage: str, started: float | None, status: str, detail: str, attempts: int = 1) -> None:
        ms = round((time.perf_counter() - started) * 1000) if started is not None else 0
        self.state.db.execute(
            "UPDATE stages SET status = ?, duration_ms = ?, attempts = ?, detail = ? WHERE application_id = ? AND stage = ?",
            (status, ms, attempts, detail, self.app_id, stage))
        self.emit(stage, status, ms, detail, attempts)

    def skip(self, stage: str, why: str) -> None:
        self.finish(stage, None, "skipped", f"Пропущено: {why}", 0)


async def _smev_stage(run: Run, app: Application) -> str:
    """Возвращает статус СМЭВ для движка: MATCH | MISMATCH | TIMEOUT."""
    started = run.start("smev")

    async def retry(attempt: int) -> None:
        run.emit("smev", "running", 0, f"Попытка {attempt} не удалась, повторяем", attempt + 1)

    status, data, attempts = await call_service(
        run.state, run.state.smev, "SMEV", app.app_id, "/smev/check",
        {"client_id": app.client_id, "passport_hash": app.passport_hash},
        smev_ok, lambda d: {"status": d["status"]}, retry)
    if status != "SUCCESS":
        run.finish("smev", started, "fail", f"СМЭВ не ответил: {'таймаут' if status == 'TIMEOUT' else 'ошибка'}", attempts)
        return "TIMEOUT"
    if data["status"] == "MISMATCH":
        run.finish("smev", started, "fail", "Данные не совпали", attempts)
    else:
        run.finish("smev", started, "ok", "СМЭВ подтвердил данные", attempts)
    return data["status"]


async def _bki_stage(run: Run, app: Application) -> BkiResult:
    started = run.start("bki")

    async def retry(attempt: int) -> None:
        run.emit("bki", "running", 0, f"Попытка {attempt} не удалась, повторяем", attempt + 1)

    status, data, attempts = await call_service(
        run.state, run.state.bki, "BKI", app.app_id, "/bki/score", {"passport_hash": app.passport_hash},
        bki_score_ok, lambda d: {"score": d["score"], "delinquency": d["delinquency"]}, retry)
    result = bki_result(status, data)
    if not result.ok:
        run.finish("bki", started, "fail", f"БКИ не ответил: {'таймаут' if status == 'TIMEOUT' else 'ошибка'}", attempts)
    elif result.delinquency:
        run.finish("bki", started, "warn", f"БКИ: скор {result.score}, есть просрочка", attempts)
    else:
        run.finish("bki", started, "ok", f"БКИ: скор {result.score}", attempts)
    return result


async def _report(run: Run, app: Application) -> list[dict] | None:
    """Полный отчёт БКИ для ML-подсказки. Его сбой на решение не влияет, этапом конвейера он не показывается."""
    status, data, _ = await call_service(
        run.state, run.state.bki, "BKI_REPORT", app.app_id, "/bki/report", {"passport_hash": app.passport_hash},
        report_ok, lambda d: {"loans": len(d.get("loans") or [])})
    return (data.get("loans") or []) if status == "SUCCESS" and data.get("found", True) else None


def _antifraud(run: Run, app: Application, row, loans: list[dict], meta: dict) -> dict | None:
    """Граф связей + модель поведения. Только предупреждение. Сбой антифрода выдачу решений не останавливает."""
    state = run.state
    if row["parent_app_id"]:
        run.skip("antifraud", "принятое встречное предложение (не «подбор»)")
        return None
    try:
        result, new_edges = state.antifraud.assess(
            app.app_id, app.passport_hash, client_auth.phone_hash_of(state.db, app.client_id),
            row["client_device_id_hash"], app.amount, app.income, app.term, loans, meta.get("event_ts"))
    except Exception as exc:
        log.exception("Сбой антифрода %s", app.app_id)
        run.finish("antifraud", None, "warn", f"Антифрод недоступен: {type(exc).__name__}")
        return None
    state.bus.publish("graph", {
        "node": {"id": app.app_id, "status": "PROCESSING", "fraud_score": result["fraud_score"]},
        "edges": [{"a": e.a, "b": e.b, "kind": e.kind} for e in new_edges],
        "cluster_id": result["cluster_id"], "cluster_size": result["component_size"]})
    if result["flagged"]:
        state.bus.publish("fraud", {"application_id": app.app_id, "fraud_score": result["fraud_score"],
                                    "pattern": result["pattern"], "similarity": result["similarity"],
                                    "text": result["text"]}, owner=row["client_id"], client_visible=False)
        run.finish("antifraud", None, "warn", result["text"])
    else:
        run.finish("antifraud", None, "ok",
                   f"Подозрений нет (оценка {result['fraud_score']:.0%}, связанная группа: {result['component_size']})")
    return result


async def process(state: AppState, app_id: str, *, actor: str = "system", action: str = "decision_made",
                  meta: dict | None = None) -> dict:
    """Прогоняет заявку по конвейеру и сохраняет новую версию решения. Что бы ни случилось, заявка не зависает.

    Заявка должна быть в статусе PROCESSING (его ставит создание или ручка пересчёта).
    """
    row = state.db.query_one("SELECT * FROM applications WHERE app_id = ?", (app_id,))
    app = application_from_row(row)
    run = Run(state, app_id, row["client_id"])
    meta = meta or {}
    reset_stages(state, app_id)
    started = time.perf_counter()
    delay = state.settings.stage_delay_ms / 1000
    fraud, hint = None, None
    try:
        # 1. validation: анкету проверили на входе, но конвейер не должен верить никому
        t = run.start("validation")
        errors = credit_core.validate_for_api(app)
        if errors:
            run.finish("validation", t, "fail", errors[0].message)
        else:
            payment = f"{credit_core.monthly_payment(app.amount, app.term, credit_core.PRODUCTS[app.product_id].rate):,.0f}"
            run.finish("validation", t, "ok", f"Анкета корректна, платёж по кредиту {payment.replace(',', ' ')} ₽/мес")
        await asyncio.sleep(delay)

        # 2. local_rules: правила 1–5 без внешних сервисов
        t = run.start("local_rules")
        local = credit_core.evaluate(app)
        if local.decision is None:
            run.finish("local_rules", t, "ok", "Правила 1–5 пройдены, нужны БКИ и СМЭВ")
        else:
            run.finish("local_rules", t, "fail" if local.decision == "REFUSED" else "warn",
                       f"Сработало правило: {local.reason}")
        await asyncio.sleep(delay)

        # 3. smev ∥ bki (+ отчёт БКИ для подсказки) — параллельно, время = максимум, а не сумма
        loans: list[dict] = []
        if local.decision is not None:
            run.skip("smev", "сработало локальное правило, внешние сервисы не вызываем")
            run.skip("bki", "сработало локальное правило, внешние сервисы не вызываем")
            bki, smev = BkiResult(status="NOT_CALLED"), "NOT_CALLED"
        else:
            smev, bki, report = await asyncio.gather(_smev_stage(run, app), _bki_stage(run, app), _report(run, app))
            loans = report or []
            hint = await build_hint(state.models, report)
        await asyncio.sleep(delay)

        # 4. antifraud: при пересчёте берём прежний результат — заявка в графе уже есть
        if action == "recalculated" and row["fraud"]:
            fraud = loads(row["fraud"])
            run.skip("antifraud", "результат прежний (заявка уже в графе)")
        else:
            t = run.start("antifraud")
            fraud = _antifraud(run, app, row, loans, meta)
        await asyncio.sleep(delay)

        # 5. scoring: правила кейса со всеми ответами
        flagged = bool(fraud and fraud.get("flagged"))
        if local.decision is not None:
            final = local
            run.skip("scoring", "решение принято на локальных правилах")
        else:
            t = run.start("scoring")
            final = credit_core.evaluate(app, bki, smev, fraud_flagged=flagged)
            detail = f"Балл {final.score} из 100" if final.score is not None else "Балл не рассчитан"
            run.finish("scoring", t, "ok" if final.score is not None else "warn", detail)
        await asyncio.sleep(delay)

        # 6. decision
        t = run.start("decision")
        offers = _offers(app, final, bki, smev)
        total_ms = round((time.perf_counter() - started) * 1000)
        view = _persist(state, app, final, offers, actor, action, fraud, hint, total_ms)
        level = {"APPROVED": "ok", "REFUSED": "fail"}.get(final.decision, "warn")
        run.finish("decision", t, level, view["reason_text"] or "Одобрено")
    except Exception as exc:  # непредвиденная ошибка → человеку, а не в вечный PROCESSING
        log.exception("Сбой конвейера %s", app_id)  # подробности — только в лог сервера
        failed = credit_core.evaluate(app, BkiResult(status="ERROR"), "TIMEOUT")  # честно: данных нет → NEED_MORE_DATA
        view = _persist(state, app, failed, [], actor, action, fraud, hint,
                        round((time.perf_counter() - started) * 1000))
    await client_auth.notify_decision(state, app.client_id, app_id)
    return view


def _offers(app: Application, final, bki: BkiResult, smev: str) -> list[dict]:
    if final.decision != "REFUSED" or final.reason not in OFFER_REASONS:
        return []
    if bki.ok:
        return credit_core.counter_offers(app, bki, smev, final.reason)
    # правило 5 сработало до обращения к БКИ и СМЭВ: считаем при «хороших» ответах и честно помечаем
    return [{**o, "preliminary": True} for o in credit_core.counter_offers(app, ASSUMED_BKI, "MATCH", final.reason)]


def _persist(state: AppState, app: Application, final, offers: list[dict], actor: str, action: str,
             fraud: dict | None, hint: dict | None, total_ms: int) -> dict:
    """Сохраняет решение, меняет статус, пишет аудит и рассылает событие. Возвращает Decision для staff."""
    db = state.db
    decisions.save_decision(db, final, offers, "system" if actor == "system" else "underwriter")
    change_status(db, app.app_id, final.decision)
    fields = {"total_ms": total_ms, "fraud": dumps(fraud) if fraud else None}
    if hint is not None:
        fields["ml_hint"] = dumps(hint)
    db.update("applications", "app_id", app.app_id, fields)
    audit.write(db, actor, "system" if actor == "system" else "underwriter", action, app.app_id,
                decision=final.decision, reason=final.reason, score=final.score,
                dti=None if final.dti is None else round(final.dti, 2))
    row = decisions.latest_row(db, app.app_id)
    staff_view = decisions.view(db, row, "underwriter")
    client_view = decisions.view(db, row, "client")
    owner = app.client_id
    state.bus.publish(
        "decision",
        {"application_id": app.app_id, "status": final.decision, "reason": final.reason, "score": final.score,
         "version": staff_view["version"]},
        owner=owner,
        client_data={"application_id": app.app_id, "status": final.decision, "reason": redact_reason(final.reason),
                     "score": final.score, "version": staff_view["version"]})
    return staff_view


async def auto_retry_once(state: AppState) -> int:
    """Раз в AUTO_RETRY_SECONDS: перепроверяем NEED_MORE_DATA / EXTERNAL_TIMEOUT, если оба сервиса снова живы.

    До auto_retry_max попыток на заявку; actor = system. Возвращает число перепроверенных заявок.
    """
    rows = state.db.query_all(
        "SELECT a.app_id FROM applications a JOIN decisions d ON d.app_id = a.app_id "
        "WHERE a.status = 'NEED_MORE_DATA' AND a.auto_retries < ? AND d.reason = 'EXTERNAL_TIMEOUT' "
        "AND d.version = (SELECT MAX(version) FROM decisions WHERE app_id = a.app_id) ORDER BY a.created_at",
        (state.settings.auto_retry_max,))
    if not rows:
        return 0
    health = await probe_services(state)
    if health["smev"] != "up" or health["bki"] != "up":
        return 0
    done = 0
    for r in rows:
        app_id = r["app_id"]
        try:
            change_status(state.db, app_id, "PROCESSING")
        except Exception:
            continue
        state.db.execute("UPDATE applications SET auto_retries = auto_retries + 1 WHERE app_id = ?", (app_id,))
        await process(state, app_id, actor="system", action="recalculated")
        done += 1
    return done


async def probe_services(state: AppState) -> dict:
    async def probe(client) -> str:
        try:
            await client.get_json("/health", timeout=1.0)
            return "up"
        except Exception:
            return "down"

    smev, bki, tokenizer = await asyncio.gather(probe(state.smev), probe(state.bki), probe(state.tokenizer))
    return {"smev": smev, "bki": bki, "tokenizer": tokenizer,
            "model": "ready" if state.models.is_ready() else "unavailable"}
