"""Маскирование PII и хэш паспорта. Наружу уходят только маски."""

import hashlib
import hmac


def mask_name(full_name: str) -> str:
    """Иванов Иван Иванович -> Иванов И. И."""
    parts = full_name.split()
    return " ".join([parts[0]] + [p[0].upper() + "." for p in parts[1:]])


def mask_passport(passport: str) -> str:
    """4510 123456 -> 45** ****56"""
    return f"{passport[:2]}** ****{passport[-2:]}"


def mask_phone(phone: str) -> str:
    """+79991234567 -> +7 999 ***-**-67"""
    return f"+7 {phone[2:5]} ***-**-{phone[-2:]}"


def passport_hash(pepper: str, passport: str) -> str:
    """HMAC-SHA256 с «перцем»: без перца перебрать все паспорта по хэшу не получится."""
    return hmac.new(pepper.encode(), passport.encode(), hashlib.sha256).hexdigest()
