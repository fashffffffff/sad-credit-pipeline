"""Формулы эмуляторов БКИ и СМЭВ из спецификации организаторов (06_external_services.md).

Настоящие эмуляторы — Go-сервисы в services/. Эта копия на Python нужна
консольному прототипу (режим --emulate) и тесту, который проверяет,
что Go и Python считают одинаково.
"""

import hashlib

from .models import BkiResult


def _h(value: str) -> int:
    return int(hashlib.md5(value.encode()).hexdigest(), 16)


def bki_score(passport_hash: str) -> BkiResult:
    h = _h(passport_hash) % 10
    if h <= 5:
        return BkiResult(status="SUCCESS", score=600 + h * 20, delinquency=False, requests_90d=1)
    if h <= 8:
        return BkiResult(status="SUCCESS", score=450 + (h - 6) * 50, delinquency=False, requests_90d=1)
    return BkiResult(status="SUCCESS", score=400, delinquency=True, requests_90d=1)


def bki_bucket(passport_hash: str) -> tuple[str, int]:
    """Корзина кредитной истории для /bki/report: та же h, что у скора, чтобы они не противоречили."""
    big = _h(passport_hash)
    h = big % 10
    bucket = "good" if h <= 5 else "medium" if h <= 8 else "bad"
    return bucket, big // 10


def smev_status(client_id: str) -> str:
    return "MISMATCH" if _h(client_id) % 10 == 0 else "MATCH"
