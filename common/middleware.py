"""Защитные прослойки, общие для backend и эмуляторов: лимит размера тела запроса."""

import json


class BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Тело запроса больше max_bytes -> 413. Проверяем и заголовок Content-Length, и реально прочитанные байты."""

    def __init__(self, app, max_bytes: int = 64 * 1024, path_limits: dict | None = None):
        self.app = app
        self.max_bytes = max_bytes
        self.path_limits = path_limits or {}  # путь -> свой лимит (например, загрузка CSV до 1 МБ)

    async def _reject(self, send):
        body = json.dumps({"error_code": "PAYLOAD_TOO_LARGE", "message": "Тело запроса слишком большое", "retryable": False, "details": {}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = self.path_limits.get(scope.get("path"), self.max_bytes)
        declared = dict(scope["headers"]).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return await self._reject(send)

        received = 0
        started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                await self._reject(send)
