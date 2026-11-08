"""Движок решения: правила кейса 2 строго по порядку, первое сработавшее — итог.

Порядок и обоснования — docs/CONTRACTS.md, раздел 2.4.

evaluate() можно вызвать в два шага (так делает живой конвейер):
  1) evaluate(app) без БКИ и СМЭВ — проверяются только правила 1–5.
     Если сработало — внешние сервисы не вызываем (экономия, меньше передачи ПДн).
     Если нет — decision is None: нужны ответы БКИ и СМЭВ.
  2) evaluate(app, bki=..., smev=...) — полный прогон всех правил.
Консольный прототип сразу делает шаг 2: ответы БКИ и СМЭВ уже лежат в CSV.
"""

from . import rules
from .finance import compute_dti, monthly_payment
from .models import Application, BkiResult, Decision
from .products import PRODUCTS
from .validation import validate_fields


def score_breakdown(app: Application, bki: BkiResult | None) -> tuple[int | None, list[dict], bool]:
    """Балл 0–100 по 5 признакам. Возвращает (балл, разбивка, обрезан_до_нуля).

    Без ответа БКИ полный балл посчитать нельзя: балл None, разбивка — по 4 признакам.
    """
    parts = [
        {"feature": "income", "label_ru": "Доход", "value": app.income,
         "points": rules.income_points(app.income), "max": rules.MAX_POINTS["income"]},
        {"feature": "employment", "label_ru": "Занятость", "value": app.employment,
         "points": rules.EMPLOYMENT_POINTS[app.employment], "max": rules.MAX_POINTS["employment"]},
        {"feature": "age", "label_ru": "Возраст", "value": app.age,
         "points": rules.age_points(app.age), "max": rules.MAX_POINTS["age"]},
        {"feature": "family", "label_ru": "Семья", "value": app.family,
         "points": rules.FAMILY_POINTS[app.family], "max": rules.MAX_POINTS["family"]},
    ]
    if bki is None or not bki.ok:
        return None, parts, False
    parts.append({"feature": "bki", "label_ru": "Кредитная история (БКИ)", "value": bki.score,
                  "points": rules.bki_points(bki.score, bki.delinquency), "max": rules.MAX_POINTS["bki"]})
    total = sum(p["points"] for p in parts)
    return max(0, total), parts, total < 0


def evaluate(app: Application, bki: BkiResult | None = None, smev: str | None = None,
             fraud_flagged: bool = False) -> Decision:
    product = PRODUCTS.get(app.product_id) or PRODUCTS[rules.DEFAULT_PRODUCT]
    d = Decision(app_id=app.app_id, decision=None, product_id=product.product_id,
                 rules_version=rules.RULES_VERSION,
                 smev=smev or "NOT_CALLED")
    if bki is not None and bki.ok:
        d.bki_score, d.bki_delinquency = bki.score, bki.delinquency

    def check(n: int, code: str, check_ru: str, value, triggered: bool) -> bool:
        """Записать шаг в трассировку. True — правило сработало."""
        d.trace.append({"n": n, "code": code, "check_ru": check_ru, "value": value, "passed": not triggered})
        return triggered

    def finish(decision: str, reason: str) -> Decision:
        d.decision, d.reason = decision, reason
        return d

    # Если все поля корректны, сразу считаем платёж, DTI и балл — они нужны в decisions.csv
    # даже для заявок, отказанных по первым правилам
    errors = [e for e in validate_fields(app) if e.field != "passport_hash"]
    if not errors:
        d.monthly_payment = monthly_payment(app.amount, app.term, product.rate)
        # DTI — по формуле кейса с фиксированной ставкой 18%; платёж клиенту — по ставке продукта
        d.dti = compute_dti(app.amount, app.term, app.income, rules.DTI_RATE)
        d.score, d.breakdown, d.score_capped_to_zero = score_breakdown(app, bki)

    # Правила 1–2 можно проверить, даже если другие поля пустые
    age_ok = isinstance(app.age, int) and not isinstance(app.age, bool)
    if check(1, "AGE_OUT_OF_RANGE", "Возраст от 18 до 70 лет", app.age,
             age_ok and not rules.MIN_AGE <= app.age <= rules.MAX_AGE):
        return finish("REFUSED", "AGE_OUT_OF_RANGE")
    if check(2, "INVALID_EMPLOYMENT", "Есть занятость (не unemployed)", app.employment,
             app.employment == "unemployed"):
        return finish("REFUSED", "INVALID_EMPLOYMENT")

    # Правило 3: пустые и некорректные поля → нужны данные
    if check(3, "MISSING_INCOME", "Доход указан", app.income,
             any(e.code == "MISSING_INCOME" for e in errors)):
        return finish("NEED_MORE_DATA", "MISSING_INCOME")
    if errors:
        first = errors[0]
        check(3, first.code, f"Поле «{first.field}» заполнено корректно", getattr(app, first.field, None), True)
        return finish("NEED_MORE_DATA", first.code)

    if check(4, "LOW_INCOME", "Доход не меньше 30 000 ₽", app.income, app.income < rules.MIN_INCOME):
        return finish("REFUSED", "LOW_INCOME")
    # DTI хранится и сравнивается с 2 знаками после запятой (так в data_dictionary и в разметке CSV)
    if check(5, "HIGH_DTI", "Платёж не больше 50% дохода (DTI ≤ 0,5)", round(d.dti, 2), round(d.dti, 2) > rules.MAX_DTI):
        return finish("REFUSED", "HIGH_DTI")

    # Шаг 1 живого конвейера заканчивается здесь
    if bki is None and smev is None:
        return d

    bki = bki or BkiResult(status="NOT_CALLED")
    smev = smev or "NOT_CALLED"
    timeout = bki.status in ("TIMEOUT", "ERROR") or smev == "TIMEOUT"
    if check(6, "EXTERNAL_TIMEOUT", "БКИ и СМЭВ ответили", f"БКИ: {bki.status}, СМЭВ: {smev}", timeout):
        return finish("NEED_MORE_DATA", "EXTERNAL_TIMEOUT")
    if not bki.ok or smev == "NOT_CALLED":
        # сюда попадаем только при ошибке вызывающего кода или пустых колонках CSV
        missing = "MISSING_BKI_SCORE" if not bki.ok else "MISSING_SMEV_STATUS"
        check(6, missing, "Есть ответы БКИ и СМЭВ", None, True)
        return finish("NEED_MORE_DATA", missing)

    if check(7, "BKI_DELINQUENCY", "Скор БКИ ≥ 550 и нет просрочки",
             f"{bki.score}{', просрочка' if bki.delinquency else ''}",
             bki.score < rules.BKI_REFUSE_BELOW or bki.delinquency):
        return finish("REFUSED", "BKI_DELINQUENCY")
    if check(8, "AMOUNT_LIMIT", "Сумма не больше 10 доходов", f"{app.amount:.0f} ≤ {rules.AMOUNT_MULTIPLIER * app.income:.0f}",
             app.amount > rules.AMOUNT_MULTIPLIER * app.income):
        return finish("REFUSED", "AMOUNT_LIMIT")
    if check(9, "SMEV_MISMATCH", "СМЭВ подтвердил паспорт", smev, smev == "MISMATCH"):
        return finish("REFUSED", "SMEV_MISMATCH")
    if check(10, "LOW_SCORE", "Балл не меньше 60", d.score, d.score < rules.MIN_SCORE):
        return finish("MANUAL_REVIEW", "LOW_SCORE")
    if check(11, "FRAUD_SUSPECT", "Антифрод не нашёл признаков схемы", "помечена" if fraud_flagged else "чисто",
             fraud_flagged):
        return finish("MANUAL_REVIEW", "FRAUD_SUSPECT")

    d.amount_approved, d.term_approved, d.rate = app.amount, app.term, product.rate
    return finish("APPROVED", "")
