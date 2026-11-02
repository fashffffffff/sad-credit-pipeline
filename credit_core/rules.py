"""Пороги и веса правил кейса 2. Единственное место, где они записаны.

Менять значения можно только через код и ревью: версия правил попадает
в каждое решение (RULES_VERSION), поэтому любое решение можно объяснить
теми правилами, которые действовали в момент его принятия.
"""

RULES_VERSION = "2026.11-1"

# --- Пороги (обоснования — в docs/case/02_правила.md) ---
MIN_SCORE = 60            # ниже — ручная проверка (MANUAL_REVIEW / LOW_SCORE)
MAX_DTI = 0.5             # платёж не больше половины дохода
AMOUNT_MULTIPLIER = 10    # сумма кредита не больше 10 месячных доходов
MIN_INCOME = 30_000       # минимальный месячный доход, ₽
MIN_AGE, MAX_AGE = 18, 70
BKI_REFUSE_BELOW = 550    # скор БКИ ниже — отказ BKI_DELINQUENCY
BKI_DELINQUENT_BELOW = 500  # в CSV нет флага просрочки: скор ниже 500 считаем просрочкой

# --- Справочники полей ---
EMPLOYMENTS = ("employed", "self-employed", "student", "retired", "unemployed")
FAMILIES = ("single", "married", "children")
AMOUNT_RANGE = (10_000, 5_000_000)   # из схемы Application организаторов
TERM_RANGE = (3, 60)
DEFAULT_PRODUCT = "prod_standard"

# --- Веса признаков (сумма максимумов = 100) ---
EMPLOYMENT_POINTS = {"employed": 25, "self-employed": 20, "retired": 10, "student": 5, "unemployed": 0}
FAMILY_POINTS = {"married": 15, "children": 15, "single": 10}
MAX_POINTS = {"income": 30, "employment": 25, "age": 20, "family": 15, "bki": 10}


def income_points(income: float) -> int:
    if income >= 150_000:
        return 30
    if income >= 80_000:
        return 20
    if income >= 40_000:
        return 10
    return 0


def age_points(age: int) -> int:
    if 25 <= age <= 50:
        return 20
    if 18 <= age <= 24 or 51 <= age <= 60:
        return 10
    if 61 <= age <= 70:
        return 5
    return 0


def bki_points(bki_score: int, delinquency: bool) -> int:
    if bki_score < BKI_DELINQUENT_BELOW or delinquency:
        return -20
    if bki_score >= 700:
        return 10
    if bki_score >= 600:
        return 5
    return 0
