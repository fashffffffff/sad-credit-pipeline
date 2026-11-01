"""Четыре этапа конвейера, кроме решения. Каждый возвращает StageResult и кладёт данные в Context."""

import asyncio
import logging
from datetime import date

from backend.app import audit
from backend.app.clients import BadSignature, ExternalClient, ServiceUnavailable
from backend.app.pipeline.context import Context, StageResult
from backend.app.schemas import ApplicationIn, age_on
from backend.app.scoring import ModelUnavailable, prediction_to_dict
from backend.app.security.redaction import GENERIC_FRAUD_TEXT
from common.passport import is_magic

log = logging.getLogger("pipeline")

SMEV_KEYS = ("passport_valid", "person_match", "employment_confirmed")


def smev_stop(ctx: Context) -> bool:
    """СМЭВ дал стоп-фактор: паспорт недействителен или ФИО не совпадает."""
    s = ctx.smev
    return bool(s) and (s.get("passport_valid") is False or s.get("person_match") is False)


async def validation(ctx: Context) -> StageResult:
    # второй рубеж защиты: анкета уже проверена на входе, но конвейер не должен верить никому
    ApplicationIn.model_validate(ctx.data.model_dump())
    ctx.age = age_on(ctx.data.birth_date, date.today())
    payment = f"{ctx.payment:,}".replace(",", " ")
    return StageResult("ok", f"Анкета корректна, платёж по кредиту {payment} ₽/мес")


async def _call_external(ctx: Context, client: ExternalClient, path: str, payload: dict) -> dict | None:
    """Вызов СМЭВ/БКИ. Ошибки не пробрасываем, а записываем в ctx.errors — решение примет этап decision."""
    def on_attempt(n: int) -> None:
        ctx.attempt = n
        ctx.state.bus.publish("stage", {
            "application_id": ctx.app_id, "stage": ctx.stage, "status": "running",
            "duration_ms": 0, "attempt": n, "detail": f"Повторная попытка {n}",
        }, owner=ctx.owner)

    try:
        return await client.post_signed(path, payload, on_attempt)
    except BadSignature as exc:
        ctx.attempt = exc.attempts
        ctx.errors[client.name] = ("bad_signature", "неверная подпись ответа", exc.attempts)
        # фиксируем в аудите и сообщаем в SSE: это возможная подмена ответа
        audit.write(ctx.state.db, "system", "system", "bad_signature", "application", ctx.app_id,
                    {"service": client.name})
        ctx.state.bus.publish("security", {
            "kind": "bad_signature",
            "detail": f"Неверная подпись ответа {client.name.upper()} (заявка {ctx.app_id})",
        })
    except ServiceUnavailable as exc:
        ctx.attempt = exc.attempts
        ctx.errors[client.name] = ("unavailable", str(exc), exc.attempts)
    return None


async def smev(ctx: Context) -> StageResult:
    d = ctx.data
    payload = {"passport": d.passport, "full_name": d.full_name,
               "birth_date": d.birth_date.isoformat(), "employer_inn": d.employer_inn,
               # необязательное расширение эмулятора: по заявленному доходу он делает confirmed_income ≈ ±15%
               "declared_income": d.monthly_income,
               "passport_issue_date": d.passport_issue_date.isoformat(),
               "passport_division_code": d.passport_division_code,
               "employment_months": d.employment_months}
    resp = await _call_external(ctx, ctx.state.smev, "/smev/v1/verify", payload)
    if resp is None:
        kind, text, _ = ctx.errors["smev"]
        return StageResult("fail", f"СМЭВ: {text}")
    if any(k not in resp for k in SMEV_KEYS):
        ctx.errors["smev"] = ("unavailable", "некорректный ответ", ctx.attempt)
        return StageResult("fail", "СМЭВ: некорректный ответ")
    ctx.smev = resp
    if resp["passport_valid"] is False:
        return StageResult("fail", "Паспорт недействителен")
    if resp["person_match"] is False:
        return StageResult("fail", "ФИО и дата рождения не совпадают с паспортом")
    if resp["employment_confirmed"] is False:
        return StageResult("warn", "Паспорт действителен, работа не подтверждена")
    return StageResult("ok", "Паспорт действителен, работа подтверждена")


async def bki(ctx: Context) -> StageResult:
    d = ctx.data
    payload = {"passport": d.passport, "full_name": d.full_name, "birth_date": d.birth_date.isoformat()}
    resp = await _call_external(ctx, ctx.state.bki, "/bki/v1/report", payload)
    if resp is None:
        _, text, _ = ctx.errors["bki"]
        return StageResult("fail", f"БКИ: {text}")
    if "found" not in resp:
        ctx.errors["bki"] = ("unavailable", "некорректный ответ", ctx.attempt)
        return StageResult("fail", "БКИ: некорректный ответ")
    ctx.bki = resp
    if resp.get("self_ban"):  # самозапрет: решение примет этап decision, здесь только отмечаем
        return StageResult("fail", f"Самозапрет на кредиты (установлен {resp.get('self_ban_since') or 'ранее'})")
    if not resp["found"]:
        return StageResult("warn", "Кредитной истории нет")
    loans = resp.get("loans") or []
    open_loans = sum(1 for l in loans if l.get("fclose_flag") == 1)  # 1 = дата закрытия не определена -> открыт
    return StageResult("ok", f"Кредитов в истории: {len(loans)}, открытых: {open_loans}")


def income_of(ctx: Context) -> int:
    """Доход: подтверждённый СМЭВ, если есть, иначе заявленный."""
    return (ctx.smev or {}).get("confirmed_income") or ctx.data.monthly_income


def debt_burden(ctx: Context) -> float:
    """ПДН = (платежи по долгам из БКИ + платёж по новому кредиту) / доход. Это только стоп-фактор, не признак модели."""
    return (ctx.bki["monthly_debt_payment"] + ctx.payment) / income_of(ctx)


async def antifraud(ctx: Context) -> StageResult:
    """Граф связей + модель поведения. Только предупреждение: решение принимает decision (ручная проверка).

    Сбой антифрода не должен останавливать выдачу решений: пишем warning и едем дальше без него.
    """
    if is_magic(ctx.data.passport):  # демо-паспорта подаются снова и снова: связывать их в граф нельзя
        return StageResult("skipped", "демонстрационный паспорт не участвует в антифроде")
    engine = ctx.state.antifraud
    try:
        result, new_edges = engine.assess(
            ctx.app_id, ctx.data, (ctx.bki or {}).get("loans") or [], ctx.meta.get("event_ts"))
    except Exception as exc:
        log.exception("Сбой антифрода %s", ctx.app_id)
        return StageResult("warn", f"Антифрод недоступен: {type(exc).__name__}")
    ctx.fraud = result
    bus = ctx.state.bus
    bus.publish("graph", {
        "node": {"id": ctx.app_id, "status": "processing", "fraud_score": result["fraud_score"]},
        "edges": [{"a": e.a, "b": e.b, "kind": e.kind} for e in new_edges],
        "cluster_id": result["cluster_id"], "cluster_size": result["component_size"],
    })
    if result["flagged"]:
        bus.publish("fraud", {"application_id": ctx.app_id, "fraud_score": result["fraud_score"],
                              "pattern": result["pattern"], "similarity": result["similarity"],
                              "text": result["text"]}, owner=ctx.owner,
                    operator_data={"application_id": ctx.app_id, "fraud_score": 0, "pattern": "", "similarity": 0,
                                   "text": GENERIC_FRAUD_TEXT})
        return StageResult("warn", result["text"])
    return StageResult("ok", f"Подозрений нет (оценка {result['fraud_score']:.0%}, связанная группа: {result['component_size']})")


def scoring_skip_reason(ctx: Context) -> str | None:
    if smev_stop(ctx):
        return "СМЭВ вернул стоп-фактор"
    if ctx.bki is None:
        return "нет данных БКИ"
    if ctx.bki.get("self_ban"):
        return "у клиента самозапрет на кредиты"
    if not ctx.bki.get("found"):
        return "у клиента нет кредитной истории"
    return None


async def scoring(ctx: Context) -> StageResult:
    # признаки считает сама модель из сырых кредитов: одна функция агрегации для обучения и для прода
    try:
        model = ctx.state.models.get()
        prediction = await asyncio.to_thread(model.predict_history, ctx.bki["loans"])
    except ModelUnavailable as exc:
        ctx.scoring_error = str(exc)
        return StageResult("fail", f"Скоринг недоступен: {exc}")
    except Exception as exc:
        # текст исключения (пути, внутренности библиотеки) пользователю не показываем — только в лог сервера
        log.exception("Ошибка модели в заявке %s", ctx.app_id)
        ctx.scoring_error = f"ошибка модели: {type(exc).__name__}"
        return StageResult("fail", f"Ошибка модели: {type(exc).__name__}")
    ctx.features = dict(prediction.features)
    ctx.prediction = prediction_to_dict(prediction)
    return StageResult("ok", f"Балл {ctx.prediction['score']}, вероятность дефолта {ctx.prediction['pd']:.1%}")
