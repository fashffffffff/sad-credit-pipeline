"""Короткие виды идентификаторов для интерфейса. Паспорт backend не получает вообще — только passport_hash."""


def short_hash(passport_hash: str) -> str:
    """f727a849055f2224 -> f727a8…"""
    return passport_hash[:6] + "…"
