"""Встречные предложения: «вот так вам одобрят».

Предлагаем только при отказе по HIGH_DTI или AMOUNT_LIMIT — их можно исправить
суммой, сроком или продуктом. Каждый вариант прогоняется через тот же движок
с теми же ответами БКИ и СМЭВ, поэтому предложение гарантированно одобряется.
"""

import math
from dataclasses import replace

from . import rules
from .engine import evaluate
from .finance import max_amount_for_dti
from .models import Application, BkiResult
from .products import PRODUCTS, Product, eligible_products

FIXABLE = ("HIGH_DTI", "AMOUNT_LIMIT")
STEP = 10_000


def _try(app: Application, bki: BkiResult, smev: str, product: Product, amount: float, term: int) -> dict | None:
    if not product.fits_loan(amount, term):
        return None
    candidate = replace(app, product_id=product.product_id, amount=amount, term=term)
    r = evaluate(candidate, bki=bki, smev=smev)
    if r.decision != "APPROVED":
        return None
    return {"product_id": product.product_id, "product_name": product.name,
            "amount": int(amount), "term": term, "rate": product.rate,
            "monthly_payment": round(r.monthly_payment), "dti": round(r.dti, 2), "score": r.score}


def _max_amount(app: Application, product: Product, term: int) -> float:
    """Наибольшая сумма (кратная 10 000 ₽), которая проходит DTI, лимит дохода и границы продукта."""
    by_dti = max_amount_for_dti(term, app.income, product.rate, rules.MAX_DTI + 0.005)  # DTI сравнивается округлённым
    limit = min(by_dti, rules.AMOUNT_MULTIPLIER * app.income, product.max_amount, app.amount)
    return math.floor(limit / STEP) * STEP


def counter_offers(app: Application, bki: BkiResult, smev: str, reason: str, limit: int = 3) -> list[dict]:
    if reason not in FIXABLE:
        return []
    product = PRODUCTS.get(app.product_id) or PRODUCTS[rules.DEFAULT_PRODUCT]
    offers: list[dict] = []

    # 1. Тот же продукт и срок, меньше сумма
    offer = _try(app, bki, smev, product, _max_amount(app, product, app.term), app.term)
    if offer:
        offers.append(offer)

    # 2. Тот же продукт и сумма, срок длиннее (помогает только от HIGH_DTI)
    if reason == "HIGH_DTI":
        for term in range(app.term + 1, product.max_term + 1):
            offer = _try(app, bki, smev, product, app.amount, term)
            if offer:
                offers.append(offer)
                break

    # 3. Другой продукт, который подходит клиенту: наибольшая сумма на исходный срок
    best = None
    for other in eligible_products(app.age, app.income):
        if other.product_id == product.product_id:
            continue
        term = min(max(app.term, other.min_term), other.max_term)
        offer = _try(app, bki, smev, other, _max_amount(app, other, term), term)
        if offer and (best is None or offer["amount"] > best["amount"]):
            best = offer
    if best:
        offers.append(best)

    # Убираем повторы и сортируем: сначала ближе к тому, что просил клиент
    unique = {(o["product_id"], o["amount"], o["term"]): o for o in offers}
    ordered = sorted(unique.values(), key=lambda o: (-o["amount"], o["monthly_payment"]))
    return ordered[:limit]
