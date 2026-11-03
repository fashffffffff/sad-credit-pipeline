"""Подбор вариантов с учётом приоритета суммы и срока.

Кейсные правила не меняются: каждый вариант проверяется тем же движком.
До ответа БКИ вызывающая сторона обязана пометить варианты предварительными.
"""
import math
from dataclasses import replace
from . import rules
from .engine import evaluate
from .finance import max_amount_for_dti
from .models import Application, BkiResult
from .products import PRODUCTS, Product, eligible_products

FIXABLE = ("HIGH_DTI", "AMOUNT_LIMIT", "PRODUCT_MISMATCH")
STEP = 10_000


def _try(app: Application, bki: BkiResult, smev: str, product: Product, amount: float, term: int) -> dict | None:
    if amount <= 0 or not product.fits_loan(amount, term):
        return None
    candidate = replace(app, product_id=product.product_id, amount=amount, term=term)
    result = evaluate(candidate, bki=bki, smev=smev)
    if result.decision != "APPROVED":
        return None
    total = round(result.monthly_payment * term)
    delta_amount = int(amount - app.amount)
    delta_term = term - app.term
    changes = []
    if not delta_amount:
        changes.append("Сохраняем всю запрошенную сумму")
    else:
        changes.append(f"Сумма меньше на {abs(delta_amount):,} ₽".replace(",", " "))
    if not delta_term:
        changes.append("срок остаётся прежним")
    elif delta_term > 0:
        changes.append(f"срок увеличится на {delta_term} мес.")
    else:
        changes.append(f"срок уменьшится на {-delta_term} мес.")
    if product.product_id != app.product_id:
        changes.append("другой кредитный продукт")
    return {"product_id": product.product_id, "product_name": product.name,
            "amount": int(amount), "term": term, "rate": product.rate,
            "monthly_payment": round(result.monthly_payment), "dti": round(result.dti, 2), "score": result.score,
            "total_payment": total, "overpayment": max(0, total - int(amount)),
            "amount_delta": delta_amount, "term_delta": delta_term,
            "explanation": "; ".join(changes)}


def _max_amount(app: Application, product: Product, term: int) -> float:
    by_dti = max_amount_for_dti(term, app.income, product.rate, rules.MAX_DTI + 0.005)
    cap = min(by_dti, rules.AMOUNT_MULTIPLIER * app.income, product.max_amount, app.amount)
    return math.floor(cap / STEP) * STEP


def counter_offers(app: Application, bki: BkiResult, smev: str, reason: str, limit: int = 3) -> list[dict]:
    if reason not in FIXABLE or limit <= 0:
        return []
    candidates = {}
    for product in eligible_products(app.age, app.income):
        for term in range(product.min_term, product.max_term + 1):
            # Пробуем сохранить точную сумму, включая некратные 10 тыс.; затем доступный максимум.
            for amount in {app.amount, _max_amount(app, product, term)}:
                offer = _try(app, bki, smev, product, amount, term)
                if offer:
                    candidates[(product.product_id, offer["amount"], term)] = offer
    weight = app.amount_priority / 10

    def rank(o):
        amount_loss = max(0, app.amount - o["amount"]) / app.amount
        term_change = abs(o["term"] - app.term) / app.term
        distance = weight * amount_loss + (1 - weight) * term_change
        # Сначала близость к запросу, затем стоимость при одинаковом компромиссе.
        return (round(distance, 9), amount_loss + term_change, o["overpayment"], o["monthly_payment"], o["product_id"], o["term"])

    ordered = sorted(candidates.values(), key=rank)
    if not ordered:
        return []
    # Показываем разные компромиссы, а не три почти одинаковых срока подряд.
    chosen = [ordered[0]]
    for pred in [lambda o: o["amount"] == app.amount, lambda o: o["term"] == app.term]:
        if any(pred(o) for o in chosen):
            continue
        alternative = next((o for o in ordered if pred(o) and o not in chosen), None)
        if alternative:
            chosen.append(alternative)
    if len(chosen) < limit:
        other = next((o for o in ordered if o not in chosen and o["product_id"] != chosen[0]["product_id"]), None)
        if other:
            chosen.append(other)
    return sorted(chosen, key=rank)[:limit]
