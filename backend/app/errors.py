"""Единый формат ошибок API."""

import json


class ApiError(Exception):
    def __init__(self, status: int, error: str, detail: str = "", **extra):
        self.status, self.error, self.detail, self.extra = status, error, detail, extra

    def body(self) -> dict:
        return {"error": self.error, "detail": self.detail, **self.extra}


async def json_body(request) -> object:
    """Тело запроса как JSON. Кривой JSON и «бомба» из тысяч вложенных скобок дают 422, а не 500."""
    try:
        return json.loads(await request.body())
    except (ValueError, RecursionError):  # RecursionError — не ValueError, а json.loads падает на глубокой вложенности
        raise ApiError(422, "validation_error", "", fields={"body": "Некорректный JSON"})
