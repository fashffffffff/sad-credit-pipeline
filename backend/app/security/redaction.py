"""Что оператору НЕ показываем про антифрод.

Оператор — сотрудник на передовой и потенциальный «свой» мошенник: если он видит оценку, схему и конкретные признаки
(«8 паспортов с одного устройства»), то может подбирать заявки так, чтобы обойти порог. Поэтому оператор получает только
факт «заявка передана на дополнительную проверку»; подробности — риск-менеджер, аудитор и админ.
"""

GENERIC_FRAUD_TEXT = "Заявка передана на дополнительную проверку"


def redact_fraud(fraud: dict | None) -> dict | None:
    """Поле fraud заявки в урезанном виде (та же форма, чтобы фронт не ломался; redacted=True скрывает цифры)."""
    if fraud is None:
        return None
    return {"fraud_score": 0, "pattern": None, "pattern_ru": None, "similarity": 0, "anomaly": 0,
            "channel": fraud.get("channel"), "flagged": bool(fraud.get("flagged")), "inherited_from_cluster": False,
            "explanation": [], "component_size": 0, "cluster_id": None, "redacted": True,
            "text": GENERIC_FRAUD_TEXT if fraud.get("flagged") else None}


def redact_reasons(reasons: list[dict]) -> list[dict]:
    return [{**r, "text": GENERIC_FRAUD_TEXT} if r.get("code") == "FRAUD_SUSPECT" else r for r in reasons]


def redact_stage_detail(stage: str, status: str, detail: str, reasons: list[dict] | None = None) -> str:
    """Текст этапа для оператора: у antifraud без оценки и размера группы, у decision — причины без деталей антифрода."""
    if stage == "antifraud":
        return {"warn": GENERIC_FRAUD_TEXT, "ok": "Проверка выполнена"}.get(status, detail)
    if stage == "decision" and reasons is not None:
        return "; ".join(r["text"] for r in redact_reasons(reasons))
    return detail
