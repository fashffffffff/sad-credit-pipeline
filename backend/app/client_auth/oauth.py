"""Яндекс ID и VK ID (OAuth, код + PKCE). Адреса проверены по официальной документации 02.11.2026.

Яндекс (https://yandex.ru/dev/id/doc/ru/codes/code-url, https://yandex.ru/dev/id/doc/ru/user-information):
  authorize  GET  https://oauth.yandex.ru/authorize?response_type=code&client_id&redirect_uri&state&scope
                  &code_challenge&code_challenge_method=S256
  token      POST https://oauth.yandex.ru/token (form: grant_type=authorization_code, code, code_verifier;
                  заголовок Authorization: Basic base64(client_id:client_secret))
  userinfo   GET  https://login.yandex.ru/info?format=json, Authorization: OAuth <token>
                  -> {id, default_phone: {id, number}}; телефон отдаётся, если у приложения включён доступ «Номер телефона».
  Имя scope для телефона в найденной странице не названо: берём `login:default_phone` (проверить на живых ключах).

VK ID (https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/api-integration/api-description):
  authorize  GET  https://id.vk.ru/authorize?response_type=code&client_id&redirect_uri&state(≥32 символов)&scope=phone
                  &code_challenge&code_challenge_method=S256; в redirect приходят code, device_id, state
  token      POST https://id.vk.ru/oauth2/auth (form: grant_type=authorization_code, code, code_verifier,
                  redirect_uri, client_id, device_id, state) -> {access_token, user_id, ...}
  user_info  POST https://id.vk.ru/oauth2/user_info (form: client_id, access_token) -> {user: {user_id, phone, ...}}
                  phone — только если выдан scope phone.
"""

import base64
import hashlib
import logging
import secrets

from backend.app.errors import ApiError
from backend.app.client_auth import config, store
from backend.app.client_auth.phone import normalize_phone

log = logging.getLogger("client_auth.oauth")

YANDEX_AUTHORIZE = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN = "https://oauth.yandex.ru/token"
YANDEX_INFO = "https://login.yandex.ru/info?format=json"
VK_AUTHORIZE = "https://id.vk.ru/authorize"
VK_TOKEN = "https://id.vk.ru/oauth2/auth"
VK_USER_INFO = "https://id.vk.ru/oauth2/user_info"


def _challenge_of(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def start(db, provider: str, ip: str) -> dict:
    store.check_rate_limits(db, ip, None)
    state = secrets.token_urlsafe(32)          # 43 символа: подходит и VK (≥32)
    verifier = secrets.token_urlsafe(64)       # 86 символов, допустимо 43–128
    db.execute("INSERT INTO oauth_states (state, provider, code_verifier, ip, created_at) VALUES (?, ?, ?, ?, ?)",
               (state, provider, verifier, ip, config.now()))
    params = {"response_type": "code", "redirect_uri": config.redirect_uri(provider), "state": state,
              "code_challenge": _challenge_of(verifier), "code_challenge_method": "S256"}
    if provider == "yandex":
        base, params = YANDEX_AUTHORIZE, {**params, "client_id": config.env("YANDEX_CLIENT_ID"),
                                          "scope": "login:default_phone"}
    else:
        base, params = VK_AUTHORIZE, {**params, "client_id": config.vk_client_id(), "scope": "phone"}
    from urllib.parse import urlencode
    return {"authorize_url": f"{base}?{urlencode(params)}"}


def consume_state(db, provider: str, state: str) -> str | None:
    """Одноразовый state: найден -> сразу удаляется. Возвращает PKCE verifier или None (нет/чужой/старше 10 минут)."""
    with db.lock:
        row = db.query_one("SELECT * FROM oauth_states WHERE state = ?", (state,))
        if row is None:
            return None
        db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    if row["provider"] != provider or config.now() - row["created_at"] > config.STATE_TTL:
        return None
    return row["code_verifier"]


async def fetch_identity(provider: str, code: str, verifier: str, device_id: str | None) -> tuple[str, str | None]:
    """Обмен кода на токен и запрос профиля. Возвращает (uid, телефон или None)."""
    async with config.http_client() as client:
        if provider == "yandex":
            basic = base64.b64encode(f"{config.env('YANDEX_CLIENT_ID')}:{config.env('YANDEX_CLIENT_SECRET')}".encode())
            r = await client.post(YANDEX_TOKEN, headers={"Authorization": f"Basic {basic.decode()}"},
                                  data={"grant_type": "authorization_code", "code": code, "code_verifier": verifier})
            token = r.json()["access_token"]
            info = (await client.get(YANDEX_INFO, headers={"Authorization": f"OAuth {token}"})).json()
            number = (info.get("default_phone") or {}).get("number")
            return str(info["id"]), normalize_phone(number) if number else None
        r = await client.post(VK_TOKEN, data={
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "redirect_uri": config.redirect_uri("vk"), "client_id": config.vk_client_id(),
            "device_id": device_id or "", "state": secrets.token_urlsafe(32)})
        token = r.json()["access_token"]
        info = (await client.post(VK_USER_INFO, data={"client_id": config.vk_client_id(),
                                                      "access_token": token})).json()
        user = info.get("user", info)
        number = user.get("phone")
        return str(user["user_id"]), normalize_phone(str(number)) if number else None


async def callback(db, provider: str, code: str, state: str, device_id: str | None) -> dict:
    verifier = consume_state(db, provider, state)
    if verifier is None:
        store.login_failed(db, provider, "bad_state")
        raise ApiError(400, "INVALID_STATE", "Ссылка входа устарела или недействительна — начните заново")
    try:
        uid, phone = await fetch_identity(provider, code, verifier, device_id)
    except Exception as exc:  # плохой код, сеть, неожиданный ответ — одна ошибка наружу
        log.warning("%s: обмен кода не удался: %s", provider, type(exc).__name__)
        store.login_failed(db, provider, "provider_error")
        raise ApiError(400, "PROVIDER_ERROR", "Не удалось войти через сервис — попробуйте ещё раз")
    client_id = store.client_for_oauth(db, provider, uid, phone)
    # Экран возврата использует тот же признак завершённого входа, что и подтверждение телефона.
    return {"status": "verified", **store.login_result(
        db, client_id, provider, **({"phone_missing": True} if phone is None else {}))}
