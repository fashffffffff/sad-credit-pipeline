"""Сверка наших решений с разметкой applications.csv и объяснение каждого расхождения."""

from . import rules
from .models import Decision


def explain(row: dict, d: Decision) -> str:
    """Почему наше решение отличается от колонок decision/reason в CSV. Пусто — совпало."""
    csv_decision = (row.get("decision") or "").strip()
    csv_reason = (row.get("reason") or "").strip()
    if (csv_decision, csv_reason) == (d.decision, d.reason):
        return ""
    dti = "—" if d.dti is None else f"{d.dti:.2f}".replace(".", ",")
    income = row.get("income") or "?"
    amount = row.get("amount") or "?"

    if csv_reason == "HIGH_DTI" and d.dti is not None and d.dti <= rules.MAX_DTI:
        tail = f"; срабатывает {d.reason}" if d.reason else ""
        return f"В CSV HIGH_DTI, но DTI по формуле аннуитета {dti} ≤ 0,5{tail}. Похоже на ошибку разметки (или DTI считали с другими кредитами, которых в данных нет)."
    if csv_reason == "AMOUNT_LIMIT" and d.reason != "AMOUNT_LIMIT":
        return f"В CSV AMOUNT_LIMIT, но сумма {amount} ≤ 10 × доход {income}. Ни одно правило кейса не отказывает — ошибка разметки."
    if csv_decision == "APPROVED" and d.reason == "SMEV_MISMATCH":
        return "В CSV одобрено, хотя СМЭВ вернул MISMATCH. Одобрять клиента, чей паспорт не подтвердился, нельзя — у нас отказ (правило 9)."
    if csv_decision == "APPROVED" and d.reason == "LOW_SCORE":
        return f"В CSV одобрено при балле {d.score} < 60. По условию кейса порог одобрения 60, поэтому автоматически не одобряем — заявку смотрит андеррайтер (правило 10)."
    if csv_reason == "BKI_DELINQUENCY" and d.reason != "BKI_DELINQUENCY":
        return f"В CSV BKI_DELINQUENCY при скоре БКИ {row.get('bki_score')}; у нас {d.decision} {d.reason or ''}".strip()
    return f"В CSV {csv_decision} {csv_reason}".strip() + f", у нас {d.decision} {d.reason}".rstrip() + "."


def summary(pairs: list[tuple[dict, Decision]]) -> dict:
    by_decision: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    matched = 0
    for row, d in pairs:
        by_decision[d.decision] = by_decision.get(d.decision, 0) + 1
        if d.reason:
            by_reason[d.reason] = by_reason.get(d.reason, 0) + 1
        if not explain(row, d):
            matched += 1
    has_labels = bool(pairs) and "decision" in pairs[0][0]
    return {"total": len(pairs), "by_decision": by_decision, "by_reason": by_reason,
            "matched_csv": matched if has_labels else None,
            "rules_version": rules.RULES_VERSION}
