"""Telegram-бот: подтверждение номера и уведомления. Bot API: https://core.telegram.org/bots/api

Схема: сайт даёт ссылку https://t.me/<бот>?start=<nonce> -> бот получает `/start <nonce>` и просит «Поделиться номером»
(кнопка request_contact) -> приходит message.contact. Принимаем только свой контакт (contact.user_id == from.id)
и только номер, совпадающий с тем, что ввели на сайте. Бот работает через long polling getUpdates внутри backend.
Токен бота лежит в URL запроса, поэтому логи httpx на INFO отключаем. Ошибки сети бот глотает и повторяет.
"""

import asyncio
import logging

from backend.app import audit
from backend.app.client_auth import config, store
from backend.app.client_auth.phone import mask_phone, normalize_phone, phone_hash

log = logging.getLogger("client_auth.telegram")
API = "https://api.telegram.org"
CONTACT_KEYBOARD = {"keyboard": [[{"text": "Поделиться номером", "request_contact": True}]],
                    "resize_keyboard": True, "one_time_keyboard": True}


async def call_api(client, method: str, **payload) -> dict:
    r = await client.post(f"{API}/bot{config.env('TELEGRAM_BOT_TOKEN')}/{method}", json=payload)
    return r.json()


async def send(client, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        await call_api(client, "sendMessage", **payload)
    except Exception as exc:
        log.warning("sendMessage: %s", type(exc).__name__)


async def handle_update(db, client, update: dict) -> None:
    """Разбирает одно обновление. Любая неожиданность — просто игнорируем."""
    msg = update.get("message") or {}
    sender = msg.get("from") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if not sender.get("id") or chat_id is None or (msg.get("chat") or {}).get("type") != "private":
        return
    text = msg.get("text") or ""
    if text.startswith("/start"):
        nonce = text[len("/start"):].strip()
        row = db.query_one("SELECT * FROM auth_challenges WHERE nonce = ? AND channel = 'telegram'", (nonce,)) if nonce else None
        row = store.get_challenge(db, row["challenge_id"]) if row else None
        if row is None or row["status"] != "pending" or (row["tg_user_id"] not in (None, sender["id"])):
            await send(client, chat_id, "Ссылка устарела. Вернитесь на сайт и начните вход заново.")
            return
        db.execute("UPDATE auth_challenges SET tg_user_id = ?, tg_chat_id = ? WHERE challenge_id = ?",
                   (sender["id"], chat_id, row["challenge_id"]))
        await send(client, chat_id, "Чтобы войти, поделитесь своим номером телефона кнопкой ниже.", CONTACT_KEYBOARD)
        return
    contact = msg.get("contact")
    if contact:
        await handle_contact(db, client, sender["id"], chat_id, contact)


async def handle_contact(db, client, user_id: int, chat_id: int, contact: dict) -> None:
    rows = db.query_all("SELECT challenge_id FROM auth_challenges WHERE tg_user_id = ? AND channel = 'telegram' "
                        "AND status = 'pending' ORDER BY created_at DESC LIMIT 1", (user_id,))
    row = store.get_challenge(db, rows[0]["challenge_id"]) if rows else None
    if row is None or row["status"] != "pending":
        await send(client, chat_id, "Нет активного входа. Начните на сайте заново.")
        return
    if contact.get("user_id") != user_id:  # чужой контакт («переслать номер друга») не принимаем
        store.login_failed(db, "telegram", "foreign_contact", row["phone_masked"])
        await send(client, chat_id, "Нужен ваш собственный номер: нажмите кнопку «Поделиться номером».", CONTACT_KEYBOARD)
        return
    phone = normalize_phone("+" + str(contact.get("phone_number", "")).lstrip("+"))
    if phone is None or phone_hash(phone) != row["phone_hash"]:
        db.execute("UPDATE auth_challenges SET status = 'mismatch' WHERE challenge_id = ?", (row["challenge_id"],))
        store.login_failed(db, "telegram", "phone_mismatch", row["phone_masked"])
        await send(client, chat_id, "Этот номер не совпадает с введённым на сайте. Начните вход заново.",
                   {"remove_keyboard": True})
        return
    client_id = store.complete_challenge(db, row)
    db.execute("UPDATE clients SET telegram_chat_id = ? WHERE client_id = ?", (chat_id, client_id))
    await send(client, chat_id, "Номер подтверждён. Вернитесь на сайт.", {"remove_keyboard": True})


async def poll_loop(state) -> None:
    offset = 0
    async with config.http_client(timeout=40) as client:
        while True:
            try:
                data = await call_api(client, "getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
                for update in data.get("result", []):
                    offset = max(offset, update["update_id"] + 1)
                    try:
                        await handle_update(state.db, client, update)
                    except Exception:
                        log.exception("telegram: ошибка обработки обновления")
                await asyncio.sleep(0)  # отдать управление другим задачам, даже если ответ пришёл мгновенно
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # сеть/не-JSON: бот не должен ронять backend
                log.warning("telegram getUpdates: %s", type(exc).__name__)
                await asyncio.sleep(config.bot_retry_delay)


async def startup(state) -> None:
    if not config.configured()["telegram"]:
        return
    logging.getLogger("httpx").setLevel(logging.WARNING)  # иначе токен бота попадёт в лог вместе с URL
    state.client_auth_bot = asyncio.create_task(poll_loop(state))


async def shutdown(state) -> None:
    task = getattr(state, "client_auth_bot", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    state.client_auth_bot = None


async def notify_decision(state, client_id: str, app_id: str) -> None:
    """Без сумм и ПДн: только номер заявки и ссылка на приложение. Любая ошибка — молча."""
    try:
        row = state.db.query_one("SELECT telegram_chat_id FROM clients WHERE client_id = ?", (client_id,))
        if row is None or row["telegram_chat_id"] is None or not config.configured()["telegram"]:
            return
        async with config.http_client() as client:
            await send(client, row["telegram_chat_id"],
                       f"Решение по заявке {app_id} готово — откройте приложение: {config.public_base_url()}")
    except Exception as exc:
        log.warning("notify_decision: %s", type(exc).__name__)
