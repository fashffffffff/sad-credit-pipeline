"""HTTP-клиенты СМЭВ и БКИ: подпись запроса, проверка подписи ответа, таймаут и повторы."""

import asyncio
import json
import time

import httpx

from common.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign, verify


class ServiceUnavailable(Exception):
    """Сервис не ответил нормально (таймаут, 5xx, мусор в ответе) даже после повторов."""

    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


class BadSignature(Exception):
    """Ответ пришёл, но подпись неверная: ему нельзя верить. Повторять бессмысленно."""

    def __init__(self, attempts: int):
        super().__init__("Неверная подпись ответа")
        self.attempts = attempts


class ExternalClient:
    def __init__(self, name: str, base_url: str, secret: str, timeout: float,
                 retry_delays: tuple[float, ...], transport: httpx.AsyncBaseTransport | None = None):
        self.name = name
        self.secret = secret
        self.retry_delays = retry_delays
        # transport нужен тестам: через него подставляется фейковый эмулятор
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def post_signed(self, path: str, payload: dict, on_attempt=None) -> dict:
        """Подписанный POST. Повторяем только на таймаут/5xx: 1 попытка + len(retry_delays) повторов.

        on_attempt(attempt, status, latency_ms, data) вызывается после КАЖДОЙ попытки: по нему пишем
        external_requests и logs/external.log. status: SUCCESS | TIMEOUT | ERROR; data — ответ только при SUCCESS.
        """
        body = json.dumps(payload, ensure_ascii=False).encode()
        return await self._signed("POST", path, body, on_attempt)

    async def get_signed(self, path: str, on_attempt=None) -> dict:
        """Подписанный ответ на GET (у запроса тело пустое, но подпись всё равно ставим)."""
        return await self._signed("GET", path, b"", on_attempt)

    async def _signed(self, method: str, path: str, body: bytes, on_attempt) -> dict:
        headers = {"Content-Type": "application/json", **sign(self.secret, body)}
        total = len(self.retry_delays) + 1
        last_error = ""
        for attempt in range(1, total + 1):
            if attempt > 1:
                await asyncio.sleep(self.retry_delays[attempt - 2])
                headers = {"Content-Type": "application/json", **sign(self.secret, body)}  # свежая метка времени
            started = time.perf_counter()

            async def report(status: str, data=None) -> None:
                if on_attempt:
                    await on_attempt(attempt, status, round((time.perf_counter() - started) * 1000), data)

            try:
                resp = await self.http.request(method, path, content=body or None, headers=headers)
            except httpx.TimeoutException:
                last_error = "таймаут"
                await report("TIMEOUT")
                continue
            except httpx.TransportError as exc:
                last_error = f"сеть: {type(exc).__name__}"
                await report("ERROR")
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                await report("ERROR")
                continue
            if resp.status_code >= 400:
                await report("ERROR")
                raise ServiceUnavailable(f"HTTP {resp.status_code}", attempt)  # наша ошибка, повтор не поможет
            if not verify(self.secret, resp.content, resp.headers.get(SIGNATURE_HEADER),
                          resp.headers.get(TIMESTAMP_HEADER)):
                await report("ERROR")
                raise BadSignature(attempt)
            try:
                data = resp.json()
            except ValueError:
                await report("ERROR")
                raise ServiceUnavailable("некорректный JSON в ответе", attempt)
            await report("SUCCESS", data)
            return data
        raise ServiceUnavailable(last_error, total)

    # служебные ручки (/health, /chaos) не подписываются — как в контракте
    async def get_json(self, path: str, timeout: float = 2.0) -> dict:
        resp = await self.http.get(path, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    async def put_json(self, path: str, payload: dict, timeout: float = 2.0) -> dict:
        resp = await self.http.put(path, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
