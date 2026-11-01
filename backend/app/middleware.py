"""Защитные прослойки: заголовки безопасности (лимит размера тела живёт в common/middleware.py)."""

from common.middleware import BodyLimitMiddleware, BodyTooLarge  # noqa: F401  (прежний путь импорта)


class SecurityHeadersMiddleware:
    """Добавляет к каждому ответу заголовки, которые просят браузер не «угадывать» тип и не встраивать нас во фреймы."""

    HEADERS = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
               (b"referrer-policy", b"no-referrer")]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = list(self.HEADERS)
                # ответы API (токены, маски PII, заявки) не должны оседать в кэше браузера и прокси
                if b"cache-control" not in present:
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = [*message.get("headers", []), *extra]
            await send(message)

        await self.app(scope, receive, send_with_headers)
