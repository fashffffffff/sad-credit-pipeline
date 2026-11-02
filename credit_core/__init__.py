"""Ядро кредитного решения кейса 2 — одна реализация для прототипа, backend и тестов.

Только стандартная библиотека Python, без внешних зависимостей.
Контракт — docs/CONTRACTS.md, раздел 2.
"""

from .compare import explain, summary
from .csv_io import decide_rows, read_csv, row_to_application, write_decisions
from .engine import evaluate, score_breakdown
from .finance import compute_dti, max_amount_for_dti, monthly_payment
from .models import BKI_STATUSES, DECISIONS, SMEV_STATUSES, Application, BkiResult, Decision
from .offers import counter_offers
from .products import PRODUCTS, Product, eligible_products
from .reasons import FIELD_ERRORS, REASONS, category, client_text
from .rules import RULES_VERSION
from .validation import FieldError, validate_fields, validate_for_api

__all__ = [
    "Application", "BkiResult", "Decision", "FieldError", "Product",
    "BKI_STATUSES", "DECISIONS", "SMEV_STATUSES", "FIELD_ERRORS", "PRODUCTS", "REASONS", "RULES_VERSION",
    "category", "client_text", "compute_dti", "counter_offers", "decide_rows", "eligible_products",
    "evaluate", "explain", "max_amount_for_dti", "monthly_payment", "read_csv", "row_to_application",
    "score_breakdown", "summary", "validate_fields", "validate_for_api", "write_decisions",
]
