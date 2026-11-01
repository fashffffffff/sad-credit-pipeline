"""Правила решения (раздел 5 контракта) — чистая функция, легко тестируется без сервера."""

import logging
from datetime import date

from backend.app.pipeline.context import Context
from backend.app.pipeline.stages import debt_burden
from common.passport import passport_expired

log = logging.getLogger("decision")

MAX_DEBT_RATIO = 0.8
REVIEW_DEBT_RATIO = 0.5
MAX_INQUIRIES = 5
STRONG_SCORE_MARGIN = 40  # «уверенный» балл: на 40+ выше порога одобрения (при PDO 60 это шансы ×1.6)


def _r(code: str, text: str) -> dict:
    return {"code": code, "text": text}


OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "==": lambda a, b: a == b}


def data_stop_rules(ctx: Context) -> list[dict]:
    """Стоп-правила из metrics.json модели: подобраны по данным, в backend ничего не хардкодим."""
    reasons = []
    for rule in ctx.state.models.stop_rules() if ctx.features else []:
        value = ctx.features.get(rule["feature"])
        if value is None or rule["op"] not in OPS:
            log.warning("Стоп-правило %s пропущено: нет признака %r или неизвестный оператор %r",
                        rule.get("code"), rule.get("feature"), rule.get("op"))
            continue
        if OPS[rule["op"]](value, rule["value"]):
            text = rule["text_ru"].replace("{value}", f"{value:g}" if isinstance(value, (int, float)) else str(value))
            reasons.append(_r(rule["code"], text))
    return reasons


def decide(ctx: Context) -> tuple[str, int | None, list[dict]]:
    """Вернёт (статус, балл, причины). Порядок: стоп-факторы -> деградация -> балл."""
    smev, bki = ctx.smev or {}, ctx.bki or {}
    limits = ctx.state.models.thresholds()  # пороги из metrics.json модели
    APPROVE_SCORE, REVIEW_SCORE = limits["approve"], limits["review"]
    score = ctx.prediction["score"] if ctx.prediction else None

    # 1. стоп-факторы -> отказ
    stops = []
    if smev.get("passport_valid") is False:
        expired = passport_expired(ctx.data.birth_date, ctx.data.passport_issue_date, date.today())
        stops.append(_r("PASSPORT_INVALID", "Паспорт просрочен: не заменён после 20/45 лет" if expired
                        else "Паспорт недействителен"))
    if smev.get("person_match") is False:
        stops.append(_r("PERSON_MISMATCH", "ФИО или дата рождения не совпадают с паспортом"))
    if bki.get("self_ban"):  # закон о самозапрете: выдавать кредит нельзя, как бы хорошо ни выглядел клиент
        stops.append(_r("SELF_BAN", "Клиент установил самозапрет на кредиты через Госуслуги"))
    ratio = debt_burden(ctx) if bki.get("found") else None
    if ratio is not None and ratio > MAX_DEBT_RATIO:
        stops.append(_r("DTI_TOO_HIGH", f"Долговая нагрузка {ratio:.0%} > {MAX_DEBT_RATIO:.0%}"))
    stops += data_stop_rules(ctx)
    if stops:
        return "rejected", score, stops

    # 2. деградация -> ручная проверка (человек решит там, где автомат не уверен)
    manual = []
    for name, (kind, text, attempts) in ctx.errors.items():
        title = name.upper()
        if kind == "bad_signature":
            manual.append(_r("BAD_SIGNATURE", f"Неверная подпись ответа {title} — данные не приняты"))
        else:
            manual.append(_r(f"{title}_UNAVAILABLE", f"{title} недоступен ({text}), попыток: {attempts}"))
    if smev.get("employment_confirmed") is False:
        manual.append(_r("EMPLOYMENT_NOT_CONFIRMED", "Работа не подтверждена"))
    if bki and not bki.get("found"):
        manual.append(_r("BKI_NO_HISTORY", "Кредитной истории нет — решает человек"))
    if bki.get("found") and (bki.get("inquiries_30d") or 0) >= MAX_INQUIRIES:
        manual.append(_r("MANY_INQUIRIES", f"Запросов в БКИ за 30 дней: {bki['inquiries_30d']} ≥ {MAX_INQUIRIES}"))
    if ctx.fraud and ctx.fraud.get("flagged"):  # антифрод только предупреждает: человек решит, настоящая ли это схема
        manual.append(_r("FRAUD_SUSPECT", ctx.fraud["text"]))
    if ctx.scoring_error:
        manual.append(_r("SCORING_UNAVAILABLE", f"Скоринг недоступен: {ctx.scoring_error}"))
    if manual:
        return "manual_review", score, manual

    # 3. по баллу
    if score is None:  # на всякий случай: сюда без балла попасть нельзя
        return "manual_review", None, [_r("NO_SCORE", "Балл не рассчитан")]
    high_dti = ratio is not None and ratio >= REVIEW_DEBT_RATIO
    if score >= APPROVE_SCORE and high_dti and score < APPROVE_SCORE + STRONG_SCORE_MARGIN:
        # балл в зоне одобрения, но не уверенный, а нагрузка повышенная -> пусть решит человек
        return "manual_review", score, [_r("DTI_HIGH", f"Балл {score} хороший, но долговая нагрузка {ratio:.0%} "
                                           f"в зоне {REVIEW_DEBT_RATIO:.0%}–{MAX_DEBT_RATIO:.0%} — проверит человек")]
    if score >= APPROVE_SCORE:
        reasons = [_r("SCORE_HIGH", f"Балл {score} ≥ {APPROVE_SCORE}")]
        if high_dti:  # уверенный балл перевешивает повышенную нагрузку, но фиксируем это в причинах
            reasons.append(_r("DTI_ELEVATED", f"Нагрузка {ratio:.0%} повышенная, одобрено благодаря баллу ≥ "
                                              f"{APPROVE_SCORE + STRONG_SCORE_MARGIN}"))
        return "approved", score, reasons
    if score >= REVIEW_SCORE:
        return "manual_review", score, [_r("SCORE_MID", f"Балл {score} в зоне ручной проверки ({REVIEW_SCORE}–{APPROVE_SCORE - 1})")]
    return "rejected", score, [_r("SCORE_LOW", f"Балл {score} < {REVIEW_SCORE}")]
