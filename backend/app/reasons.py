"""Тексты причин решения. Каталог кода кейса — credit_core.REASONS; здесь только то, чего в нём нет (ручное решение)."""

from credit_core import REASONS, client_text

from backend.app.security.redaction import redact_reason

MANUAL_REFUSED = "MANUAL_REFUSED"  # отказ андеррайтера после ручной проверки (не из каталога кейса)
_MANUAL = {
    "client_text": "Заявка отклонена по итогам рассмотрения специалистом банка",
    "staff_text": "Отказ андеррайтера после ручной проверки",
}


def reason_text(reason: str, staff: bool) -> str:
    """Текст причины: клиенту — без цифр и внутренних деталей, сотруднику — подробный."""
    if not reason:
        return ""
    if reason == MANUAL_REFUSED:
        return _MANUAL["staff_text" if staff else "client_text"]
    if staff:
        return REASONS.get(reason, {}).get("staff_text") or client_text(reason)
    return client_text(redact_reason(reason))
