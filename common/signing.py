"""HMAC-подпись сообщений между банком и внешними системами (СМЭВ, БКИ).

Идея простая: у банка и у внешней системы есть общий секрет.
Отправитель считает "печать" = HMAC-SHA256(секрет, "<время>.<тело>")
и кладёт её в заголовки. Получатель считает печать сам и сравнивает.
Если тело подменили по дороге или подделали ответ без секрета — печати не совпадут.
Время в подписи защищает от повтора старого сообщения (replay-атаки).
"""

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-Signature"
TIMESTAMP_HEADER = "X-Timestamp"
MAX_AGE_SECONDS = 300


def sign(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    """Вернёт заголовки с подписью для тела запроса/ответа."""
    if not secret:  # HMAC с пустым ключом может посчитать кто угодно
        raise ValueError("Пустой секрет подписи")
    ts = int(time.time()) if timestamp is None else timestamp
    message = f"{ts}.".encode() + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER: digest, TIMESTAMP_HEADER: str(ts)}


def verify(secret: str, body: bytes, signature: str | None, timestamp: str | None) -> bool:
    """True, если подпись верная и сообщение не старше MAX_AGE_SECONDS."""
    if not secret or not signature or not timestamp:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > MAX_AGE_SECONDS:
        return False
    expected = sign(secret, body, ts)[SIGNATURE_HEADER]
    # compare_digest сравнивает за постоянное время, чтобы нельзя было угадывать подпись по скорости ответа
    # сравниваем байты: str с не-ASCII символами (подложенный заголовок) иначе даёт TypeError и 500
    return hmac.compare_digest(expected.encode(), signature.encode("utf-8", "replace"))
