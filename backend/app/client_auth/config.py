"""Настройки входа клиента. Всё читается из переменных окружения в момент вызова (так проще тестировать).

Имена переменных — из docs/REGISTRATION_GUIDE.md. Секреты (JWT_SECRET, PII_PEPPER) те же, что у backend,
поэтому читаем их напрямую из окружения, а не из backend/app/config.py.
"""

import os
import time

import httpx

DEFAULT_JWT_SECRET = "dev-jwt-secret-change-me"  # как в backend/app/config.py
DEFAULT_PEPPER = "dev-pii-pepper"

CHALLENGE_TTL = 300        # challenge живёт 5 минут
STATE_TTL = 600            # OAuth state живёт 10 минут
PHONE_START_LIMIT = 3      # стартов на один номер...
PHONE_START_WINDOW = 600   # ...за 10 минут
# Стартов (телефон + OAuth) с одного IP в час. Не делать маленьким: на защите всё жюри сидит в одной Wi-Fi
# за одним внешним IP. От перебора номеров защищает лимит на номер, а этот — от ботов с одного адреса.
IP_START_LIMIT = int(os.environ.get("AUTH_IP_LIMIT_PER_HOUR", "60"))
IP_START_WINDOW = 3600     # окно для IP_START_LIMIT
MAX_BAD_CODES = 5          # неверных демо-кодов до сгорания challenge
TOKEN_TTL_MINUTES = 60

# Подмены для тестов: время, сетевой транспорт httpx и пауза бота после сетевой ошибки.
now = time.time
transport: httpx.AsyncBaseTransport | None = None
bot_retry_delay = 5.0


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def pepper() -> str:
    return os.environ.get("PII_PEPPER", DEFAULT_PEPPER)


def jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", DEFAULT_JWT_SECRET)


def production() -> bool:
    return env("APP_ENV").lower() == "production"


def public_base_url() -> str:
    return (env("PUBLIC_BASE_URL") or "http://localhost:5173").rstrip("/")


def redirect_uri(provider: str) -> str:
    return f"{public_base_url()}/auth/callback/{provider}"


def vk_client_id() -> str:
    """Для localhost у VK отдельное приложение (VK_CLIENT_ID_LOCAL, см. REGISTRATION_GUIDE.md)."""
    if "localhost" in public_base_url() and env("VK_CLIENT_ID_LOCAL"):
        return env("VK_CLIENT_ID_LOCAL")
    return env("VK_CLIENT_ID")


def configured() -> dict[str, bool]:
    """Какие внешние способы настроены ключами."""
    return {
        "telegram": bool(env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_BOT_USERNAME")),
        "call": bool(env("SMSRU_API_ID")),
        "yandex": bool(env("YANDEX_CLIENT_ID") and env("YANDEX_CLIENT_SECRET")),
        "vk": bool(vk_client_id()),
    }


def demo_enabled() -> bool:
    """AUTH_DEMO=1 включает всегда; AUTH_DEMO=0 выключает всегда.
    Без флага: в разработке демо включено, если ни один способ не настроен; в production — выключено."""
    flag = env("AUTH_DEMO")
    if flag:
        return flag == "1"
    if production():
        return False
    return not any(configured().values())


def providers() -> dict[str, bool]:
    return {**configured(), "demo": demo_enabled()}


def http_client(timeout: float = 10.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, timeout=timeout)
