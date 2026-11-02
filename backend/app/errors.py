"""Единый формат ошибок API (раздел 7 контракта): {error_code, message, retryable, details}."""

import json


class ApiError(Exception):
    def __init__(self, status: int, error_code: str, message: str = "", *, retryable: bool = False,
                 details: dict | None = None):
        self.status, self.error_code, self.message = status, error_code, message
        self.retryable, self.details = retryable, details or {}

    def body(self) -> dict:
        return {"error_code": self.error_code, "message": self.message,
                "retryable": self.retryable, "details": self.details}


def field_error(fields: dict, message: str = "Проверьте поля анкеты", code: str = "VALIDATION_ERROR") -> ApiError:
    """422 с описанием по полям: {"fields": {"age": "Возраст должен быть от 18 до 70 лет"}}."""
    return ApiError(422, code, message, details={"fields": fields})


async def json_body(request) -> object:
    """Тело запроса как JSON. Кривой JSON и «бомба» из тысяч вложенных скобок дают 422, а не 500."""
    try:
        return json.loads(await request.body())
    except (ValueError, RecursionError):  # RecursionError — не ValueError, а json.loads падает на глубокой вложенности
        raise field_error({"body": "Некорректный JSON"}, "Некорректный JSON")
