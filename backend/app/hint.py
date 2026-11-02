"""ML-подсказка андеррайтеру (раздел 4 контракта). На решение НЕ влияет: решают правила кейса."""

import asyncio
import logging

from backend.app.scoring import ModelUnavailable, ModelProvider, prediction_to_dict
from ml.credit_model.history import normalize_paym

log = logging.getLogger("hint")


def compact_loans(loans: list[dict] | None) -> list[dict]:
    """Кредиты для «тепловой карты»: только нужные поля.

    closed: fclose_flag = «фактический срок до закрытия не определён», то есть 1 = кредит ещё открыт, 0 = закрыт.
    paym: коды выравниваем той же функцией, что и модель (в трёх позициях датасета коды сдвинуты на +1).
    """
    return [
        {"rn": l.get("rn"), "credit_type": l.get("enc_loans_credit_type"),
         "status": l.get("enc_loans_credit_status"), "closed": l.get("fclose_flag") == 0,
         "paym": normalize_paym(l)}
        for l in loans or []
    ]


def _level(score: int, thresholds: dict) -> tuple[str, str]:
    """Цветовая подсказка: пороги 680/600 больше ничего не решают, только красят."""
    if score >= thresholds["approve"]:
        return "low", "ниже среднего"
    if score >= thresholds["review"]:
        return "medium", "около среднего"
    return "high", "выше среднего"


async def build_hint(models: ModelProvider, loans: list[dict] | None) -> dict | None:
    """None, если истории нет или модель недоступна: конвейер из-за подсказки не падает."""
    if not loans:
        return None
    try:
        model = models.get()
        prediction = await asyncio.to_thread(model.predict_history, loans)
    except ModelUnavailable:
        return None
    except Exception:
        log.exception("Ошибка модели при построении подсказки")
        return None
    data = prediction_to_dict(prediction)
    level, word = _level(data["score"], models.thresholds())
    pd_text = f"{data['pd'] * 100:.1f}".replace(".", ",")
    return {**data, "risk_level": level, "heatmap": compact_loans(loans),
            "summary_ru": f"Модель на реальных историях Альфа-Банка: риск дефолта {pd_text}% — {word}. "
                          "Решение принимают правила."}
