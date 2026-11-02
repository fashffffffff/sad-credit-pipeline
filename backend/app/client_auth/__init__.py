"""Вход клиента: Telegram-бот, звонок (SMS.RU callcheck), Яндекс ID, VK ID, демо-код.

Контракт — docs/CONTRACTS.md, раздел 6.2. Пакет наполняет сессия «client-auth»;
backend подключает его только через функции ниже и не лезет внутрь.

Интерфейс (менять только через тимлида):
  router                         — APIRouter с ручками /api/v1/auth/phone/*, /api/v1/auth/oauth/*, /api/v1/auth/providers
  init_tables(db)                — создаёт таблицы clients, client_identities, auth_challenges (CREATE TABLE IF NOT EXISTS)
  await startup(state)           — запускает фоновые задачи (Telegram-бот), если настроены ключи
  await shutdown(state)          — останавливает их
  client_token_version(db, client_id) -> int | None   — для проверки JWT клиента (None — клиента нет)
  phone_hash_of(db, client_id) -> str | None          — HMAC-отпечаток телефона (для антифрода)
  phone_masked_of(db, client_id) -> str | None        — «+7 999 ***-**-67» (для справки PDF)
  await notify_decision(state, client_id, app_id)     — уведомление в Telegram, если клиент вошёл через бота

JWT клиента: security.tokens.create_token(secret, username=client_id, role="client", token_version=<из clients>).
"""

from backend.app.client_auth import telegram
from backend.app.client_auth.routes import router
from backend.app.client_auth.store import init_tables


async def startup(state) -> None:
    await telegram.startup(state)


async def shutdown(state) -> None:
    await telegram.shutdown(state)


def client_token_version(db, client_id: str) -> int | None:
    row = db.query_one("SELECT token_version FROM clients WHERE client_id = ?", (client_id,))
    return None if row is None else row["token_version"]


def phone_hash_of(db, client_id: str) -> str | None:
    row = db.query_one("SELECT phone_hash FROM clients WHERE client_id = ?", (client_id,))
    return None if row is None else row["phone_hash"]


def phone_masked_of(db, client_id: str) -> str | None:
    row = db.query_one("SELECT phone_masked FROM clients WHERE client_id = ?", (client_id,))
    return None if row is None else row["phone_masked"]


async def notify_decision(state, client_id: str, app_id: str) -> None:
    await telegram.notify_decision(state, client_id, app_id)
