"""Деньги: аннуитетный платёж и долговая нагрузка (DTI)."""


def monthly_payment(amount: float, term: int, annual_rate: float) -> float:
    """Аннуитетный платёж: одинаковый каждый месяц.

    payment = amount × r / (1 − (1 + r)^(−term)), где r — месячная ставка.
    Пример (app_0001): 500 000 ₽ на 24 мес. под 18% → r = 0,015 → ≈ 24 962 ₽.
    """
    r = annual_rate / 12
    if r == 0:
        return amount / term
    return amount * r / (1 - (1 + r) ** (-term))


def compute_dti(amount: float, term: int, income: float, annual_rate: float) -> float:
    """DTI = платёж / месячный доход. 0,21 значит «платёж съедает 21% дохода»."""
    return monthly_payment(amount, term, annual_rate) / income


def max_amount_for_dti(term: int, income: float, annual_rate: float, max_dti: float) -> float:
    """Наибольшая сумма, при которой DTI не превысит max_dti (обратная формула аннуитета)."""
    r = annual_rate / 12
    max_payment = income * max_dti
    if r == 0:
        return max_payment * term
    return max_payment * (1 - (1 + r) ** (-term)) / r
