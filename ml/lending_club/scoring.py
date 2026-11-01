"""Общие константы и формулы: признаки, подписи, перевод PD в баллы.

Вынесено отдельно, чтобы train.py и credit_model использовали ОДНИ И ТЕ ЖЕ формулы.
"""
import math

# Порядок признаков фиксирован контрактом (docs/CONTRACTS.md, раздел 4, версия 3).
FEATURES = [
    "monthly_income", "employment_months", "loan_amount", "loan_term_months", "purpose",
    "payment_to_income", "bureau_score", "open_credit_lines", "total_debt_to_income",
    "revolving_utilization", "delinq_2y", "overdue_90_plus", "credit_history_months",
    "inquiries_6m",
]
CAT_FEATURES = ["purpose"]               # единственный категориальный признак
PURPOSES = ["consumer", "auto", "refinance", "education"]
DEFAULT_PURPOSE = "consumer"             # если цель кредита неизвестна

FEATURE_LABELS_RU = {
    "monthly_income": "Ежемесячный доход",
    "employment_months": "Стаж на текущем месте",
    "loan_amount": "Сумма кредита",
    "loan_term_months": "Срок кредита",
    "purpose": "Цель кредита",
    "payment_to_income": "Платёж по кредиту к доходу",
    "bureau_score": "Скоринг бюро кредитных историй",
    "open_credit_lines": "Открытые кредиты",
    "total_debt_to_income": "Общий долг к годовому доходу",
    "revolving_utilization": "Использование кредитных лимитов",
    "delinq_2y": "Просрочки 30+ дней за 2 года",
    "overdue_90_plus": "Просрочки 90+ дней",
    "credit_history_months": "Длина кредитной истории",
    "inquiries_6m": "Запросы в бюро за 6 месяцев",
}

# Шкала: BASE_POINTS баллов = шансы BASE_ODDS:1 («вернёт / не вернёт»),
# каждые +PDO баллов = шансы ×2. BASE_ODDS («якорь») подбирается при обучении
# и лежит в metrics.json -> scale; здесь его НЕТ, чтобы не было двух источников правды.
BASE_POINTS = 600.0
PDO = 20.0
FACTOR = PDO / math.log(2)      # сколько баллов даёт один "ln" шансов
SCORE_MIN, SCORE_MAX = 300, 850
THRESHOLDS = {"approve": 680, "review": 600}   # фиксированы контрактом


def raw_score(log_odds_bad: float, base_odds: float) -> float:
    """Балл до обрезки по log-odds ПЛОХОГО исхода (то, что отдаёт CatBoost)."""
    # log-odds хорошего = -log-odds плохого; балл = 600 + FACTOR * ln(шансы / base_odds)
    return BASE_POINTS + FACTOR * (-log_odds_bad - math.log(base_odds))


def clip_score(x: float) -> int:
    return int(round(min(SCORE_MAX, max(SCORE_MIN, x))))
