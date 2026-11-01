"""Зависимости FastAPI: текущий пользователь и проверка ролей (RBAC)."""

from dataclasses import dataclass

from fastapi import Request

from backend.app import audit
from backend.app.errors import ApiError
from backend.app.security.tokens import decode_token
from backend.app.state import AppState


@dataclass
class User:
    username: str
    role: str


def get_state(request: Request) -> AppState:
    return request.app.state.ctx


def user_from_token(state: AppState, token: str | None) -> User:
    claims = decode_token(state.settings.jwt_secret, token) if token else None
    if not claims:
        raise ApiError(401, "unauthorized", "Нужен действующий токен")
    # версия токена и роль берутся из БД: после logout-all старые токены не работают, смену роли видим сразу
    row = state.db.query_one("SELECT role, token_version FROM users WHERE username = ?", (claims["sub"],))
    if row is None or row["token_version"] != claims.get("tv"):
        raise ApiError(401, "unauthorized", "Токен больше не действителен — войдите заново")
    return User(claims["sub"], row["role"])


def current_user(request: Request) -> User:
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else None
    return user_from_token(get_state(request), token)


def forbid(state: AppState, user: User, what: str) -> None:
    """Отказ в доступе: пишем в аудит, шлём событие безопасности в SSE и отвечаем 403."""
    audit.write(state.db, user.username, user.role, "access_denied", details={"what": what[:200]})
    state.bus.publish("security", {"kind": "forbidden", "detail": f"{user.username} ({user.role}): {what[:200]}"})
    raise ApiError(403, "forbidden", "Недостаточно прав для этого действия")


def require_roles(*roles: str):
    """Фабрика зависимостей: пускает только перечисленные роли."""
    def dependency(request: Request) -> User:
        user = current_user(request)
        if user.role not in roles:
            forbid(get_state(request), user, f"{request.method} {request.url.path}")
        return user

    return dependency
