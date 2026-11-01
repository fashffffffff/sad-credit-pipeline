"""JWT: подписанный токен «кто я и какая у меня роль». Подделать без секрета нельзя."""

from datetime import datetime, timedelta, timezone

import jwt


def create_token(secret: str, username: str, role: str, ttl_minutes: int = 60, token_version: int = 0) -> str:
    """tv — версия токенов пользователя: logout-all увеличивает её в БД, и все старые токены перестают работать."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    return jwt.encode({"sub": username, "role": role, "tv": token_version, "exp": exp}, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict | None:
    """Вернёт содержимое токена или None, если токен подделан/просрочен."""
    try:
        # алгоритм зафиксирован (токен с alg=none или RS256 не пройдёт); exp и sub обязательны — токен «навсегда» не принимаем
        return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
