"""Шина событий для SSE: у каждого подписчика своя очередь, publish кладёт событие тем, кому его можно видеть."""

import asyncio

MAX_STREAMS_PER_USER = 10  # без лимита один пользователь открытием тысяч потоков съедает память и сокеты
CLOSE = "__close__"  # служебное событие: поток закрывается (logout-all)


class TooManyStreams(Exception):
    pass


class EventBus:
    def __init__(self) -> None:
        # очередь -> (логин, роль). Роль None = «видит всё» (внутренние подписчики и тесты)
        self._subscribers: dict[asyncio.Queue, tuple[str | None, str | None]] = {}

    def subscribe(self, username: str | None = None, role: str | None = None) -> asyncio.Queue:
        if username is not None and sum(1 for u, _ in self._subscribers.values() if u == username) >= MAX_STREAMS_PER_USER:
            raise TooManyStreams
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[queue] = (username, role)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.pop(queue, None)

    @staticmethod
    def _visible(event_type: str, owner: str | None, username: str | None, role: str | None) -> bool:
        """Кто что видит: события безопасности — только аудитор и админ; события заявки — оператор только по своим."""
        if role is None:
            return True
        if event_type == "graph":  # граф связей — аналитика для риск-менеджера, аудитора и админа
            return role in ("risk_manager", "auditor", "admin")
        if event_type == "security":  # там логины и пути запросов, оператору они ни к чему
            return role in ("auditor", "admin")
        if owner is not None and role == "operator":
            return owner == username
        return True

    def publish(self, event_type: str, data: dict, owner: str | None = None, operator_data: dict | None = None) -> None:
        """owner — логин автора заявки (для событий конвейера): оператору уходят только события его заявок.
        operator_data — урезанная версия события для роли operator (без деталей антифрода)."""
        for queue, (username, role) in list(self._subscribers.items()):
            if not self._visible(event_type, owner, username, role):
                continue
            try:
                queue.put_nowait((event_type, operator_data if role == "operator" and operator_data is not None else data))
            except asyncio.QueueFull:
                pass  # медленный клиент не должен тормозить конвейер — для него событие пропадёт

    def kick(self, username: str) -> None:
        """Закрыть все открытые потоки пользователя (после logout-all старый токен уже не годится)."""
        for queue, (name, _) in list(self._subscribers.items()):
            if name == username:
                try:
                    queue.put_nowait((CLOSE, {}))
                except asyncio.QueueFull:
                    self._subscribers.pop(queue, None)
