"""Проверка существования работодателя. Режим задаёт env EMPLOYER_CHECK: offline (по умолчанию) или fns.

offline — ничего дополнительного: контрольная сумма, не нули и регион уже проверены в schemas.py.
fns — спрашиваем открытый сервис ФНС; при ошибке или таймауте 3 с откатываемся на offline и пишем warning:
заявка из-за недоступности ФНС не должна падать.
"""

import logging

import httpx

from backend.app.employer_fns import EGRUL_URL, organization_exists

log = logging.getLogger("employer")


class EmployerChecker:
    """Интерфейс: вернуть текст ошибки (если работодателя точно нет) или None."""

    async def check(self, inn: str) -> str | None:
        raise NotImplementedError


class OfflineEmployerChecker(EmployerChecker):
    async def check(self, inn: str) -> str | None:
        return None


class FnsEmployerChecker(EmployerChecker):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 3.0):
        self.http = httpx.AsyncClient(base_url=EGRUL_URL, timeout=timeout, transport=transport)

    async def check(self, inn: str) -> str | None:
        try:
            found = await organization_exists(inn, self.http)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("ФНС недоступна (%s), работодатель %s проверен только офлайн", type(exc).__name__, inn)
            return None
        return None if found else "Организация с таким ИНН не найдена в ЕГРЮЛ"


def make_employer_checker(mode: str) -> EmployerChecker:
    return FnsEmployerChecker() if mode == "fns" else OfflineEmployerChecker()
