"""Вход по звонку: SMS.RU callcheck. Документация: https://sms.ru/api/call (проверено 02.11.2026).

  POST https://sms.ru/callcheck/add     api_id, phone, ip (адрес клиента), json=1
      -> {status:"OK", status_code:100, check_id, call_phone (только цифры), call_phone_pretty}
         status_code 202 — номер неверный.
  POST https://sms.ru/callcheck/status  api_id, check_id, json=1
      -> {status:"OK", status_code:100, check_status, check_status_text}
         check_status: 400 — звонка ещё нет, 401 — номер подтверждён, 402 — время (5 минут) вышло или check_id неверный.
Документация не говорит, лежат ли коды в status_code или в check_status, — на живом ответе проверить;
здесь читается check_status (как в примере документации).
"""

import logging

from backend.app.client_auth import config

log = logging.getLogger("client_auth.call")
BASE = "https://sms.ru"


class CallError(Exception):
    def __init__(self, invalid_phone: bool = False):
        self.invalid_phone = invalid_phone


async def add(phone: str, ip: str) -> tuple[str, str]:
    """Возвращает (check_id, номер для звонка в виде '+7…')."""
    try:
        async with config.http_client() as client:
            r = await client.post(f"{BASE}/callcheck/add",
                                  data={"api_id": config.env("SMSRU_API_ID"), "phone": phone.lstrip("+"),
                                        "ip": ip, "json": 1})
        data = r.json()
    except Exception as exc:  # сеть/не-JSON: наружу — одна ошибка канала
        log.warning("SMS.RU callcheck/add: %s", type(exc).__name__)
        raise CallError() from exc
    if str(data.get("status_code")) == "202":
        raise CallError(invalid_phone=True)
    if str(data.get("status_code")) != "100" or not data.get("check_id") or not data.get("call_phone"):
        log.warning("SMS.RU callcheck/add: status_code=%s", data.get("status_code"))
        raise CallError()
    digits = "".join(c for c in str(data["call_phone"]) if c.isdigit())
    return str(data["check_id"]), data.get("call_phone_pretty") or f"+{digits}"


async def status(check_id: str) -> str:
    """'verified' | 'pending' | 'expired'. Сетевая ошибка — pending: клиент просто опросит ещё раз."""
    try:
        async with config.http_client() as client:
            r = await client.post(f"{BASE}/callcheck/status",
                                  data={"api_id": config.env("SMSRU_API_ID"), "check_id": check_id, "json": 1})
        code = str(r.json().get("check_status"))
    except Exception as exc:
        log.warning("SMS.RU callcheck/status: %s", type(exc).__name__)
        return "pending"
    return {"401": "verified", "402": "expired"}.get(code, "pending")
