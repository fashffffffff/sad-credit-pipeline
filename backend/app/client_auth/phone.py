"""Телефон: нормализация, маска и HMAC-отпечатки. Открытый номер нигде не хранится."""

import hashlib
import hmac
import re

from backend.app.client_auth import config

_ALLOWED = re.compile(r"^[+\d\s()\-]+$")


def normalize_phone(raw: str) -> str | None:
    """'8 (999) 123-45-67', '+7 999 123 45 67', '79991234567' -> '+79991234567'. Не мобильный или мусор -> None."""
    if not isinstance(raw, str) or not _ALLOWED.match(raw.strip()):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10 or digits[0] != "9":  # только мобильные 9XX
        return None
    return "+7" + digits


def mask_phone(phone: str) -> str:
    """'+79991234567' -> '+7 999 ***-**-67'"""
    return f"+7 {phone[2:5]} ***-**-{phone[-2:]}"


def hmac_hex(value: str) -> str:
    return hmac.new(config.pepper().encode(), value.encode(), hashlib.sha256).hexdigest()


def phone_hash(phone: str) -> str:
    return hmac_hex(phone)


def subject_hash(provider: str, uid: str) -> str:
    return hmac_hex(f"{provider}:{uid}")
