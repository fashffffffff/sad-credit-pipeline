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
    def _visible(event_type: str, owner: str | None, username: str | None, role: str | None,
                 client_visible: bool) -> bool:
        """Кто что видит. Клиент — только события своих заявок и только «клиентские» (без антифрода);
        граф и антифрод — андеррайтер, аудитор, админ; события безопасности — аудитор и админ; хаос — аудитор и админ."""
        if role is None:
            return True
        if role == "client":
            return client_visible and event_type in ("stage", "decision") and owner == username
        if event_type in ("graph", "fraud"):
            return role in ("underwriter", "auditor", "admin")
        if event_type in ("security", "chaos"):
            return role in ("auditor", "admin")
        return True

    def publish(self, event_type: str, data: dict, owner: str | None = None, client_data: dict | None = None,
                client_visible: bool = True) -> None:
        """owner — client_id автора заявки (для событий конвейера): клиенту уходят только события его заявок.
        client_data — урезанная версия события для клиента; client_visible=False — клиенту не показывать вовсе."""
        for queue, (username, role) in list(self._subscribers.items()):
            if not self._visible(event_type, owner, username, role, client_visible):
                continue
            try:
                queue.put_nowait((event_type, client_data if role == "client" and client_data is not None else data))
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
