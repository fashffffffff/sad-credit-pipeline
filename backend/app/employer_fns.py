"""Проверка работодателя в открытом сервисе ФНС (ЕГРЮЛ). Вынесено в отдельный файл, чтобы источник легко заменить.

Наружу уходит только ИНН организации — никаких данных клиента.
Сервис работает в два шага: POST поискового запроса -> токен, затем GET результата по токену.
"""

import httpx

EGRUL_URL = "https://egrul.nalog.ru"


async def organization_exists(inn: str, client: httpx.AsyncClient) -> bool:
    """True/False — организация найдена/не найдена. Любая сетевая ошибка пробрасывается наверх (там откат на offline)."""
    start = await client.post("/", data={"query": inn, "vyp3CaptchaToken": "", "page": "", "region": "", "PreventChromeAutocomplete": ""})
    start.raise_for_status()
    token = start.json()["t"]
    result = await client.get(f"/search-result/{token}")
    result.raise_for_status()
    return bool(result.json().get("rows"))
