"""Проверка секретов при старте: пустые и «демонстрационные» значения не должны попасть в боевой запуск.

APP_ENV=production — сервис отказывается стартовать со слабым секретом; в разработке только громкое предупреждение,
чтобы демо запускалось без настройки.
"""

import logging
import os

log = logging.getLogger("security")

# значения по умолчанию из кода: они публичны (лежат в репозитории)
KNOWN_DEFAULTS = {"dev-jwt-secret-change-me", "dev-smev-secret", "dev-bki-secret", "dev-tokenizer-secret", "dev-pii-pepper"}
MIN_LENGTH_PROD = 32  # HS256 просит ключ не короче хэша (256 бит)


def is_production() -> bool:
    return os.environ.get("APP_ENV", "development").strip().lower() == "production"


def check_secret(name: str, value: str) -> None:
    """Пустой секрет запрещён всегда (HMAC с пустым ключом подделывает любой); слабый — только в проде."""
    if not value:
        raise RuntimeError(f"{name} пуст: задайте длинный случайный секрет (например, openssl rand -hex 32)")
    weak = value in KNOWN_DEFAULTS or len(value) < MIN_LENGTH_PROD
    if not weak:
        return
    if is_production():
        raise RuntimeError(f"{name}: слабый или демонстрационный секрет, а APP_ENV=production. "
                           f"Задайте случайное значение от {MIN_LENGTH_PROD} символов (openssl rand -hex 32)")
    log.warning("ВНИМАНИЕ: %s — демонстрационный/короткий секрет. Для демо можно, в проде нельзя "
                "(с APP_ENV=production сервер не стартует)", name)


def secret_from_env(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    check_secret(name, value)
    return value
