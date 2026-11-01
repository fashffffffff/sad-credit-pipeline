"""Пароли храним только как bcrypt-хэш: он «солёный» и намеренно медленный."""

import bcrypt


def hash_password(password: str, rounds: int = 12) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds)).decode()


def check_password(password: str, password_hash: str) -> bool:
    raw = password.encode()
    if len(raw) > 72:  # bcrypt видит только 72 байта; новые версии библиотеки на длинном пароле падают (500)
        return False
    return bcrypt.checkpw(raw, password_hash.encode())
