"""Общее состояние приложения: настройки, база, шина событий, клиенты, модель."""

import asyncio
import time
from typing import Callable
from dataclasses import dataclass, field

from backend.app.clients import ExternalClient
from backend.app.antifraud.engine import AntifraudEngine
from backend.app.config import Settings
from backend.app.db import Database
from backend.app.employer import EmployerChecker
from backend.app.events import EventBus
from backend.app.scoring import ModelProvider


@dataclass
class AppState:
    settings: Settings
    db: Database
    bus: EventBus
    smev: ExternalClient
    bki: ExternalClient
    models: ModelProvider
    employer: EmployerChecker
    antifraud: AntifraudEngine
    sim_running: bool = False  # идёт ли симуляция: вторую параллельно не запускаем
    create_hits: dict = field(default_factory=dict)  # логин -> времена последних созданных заявок (лимит частоты)
    sim_labels: dict = field(default_factory=dict)  # app_id -> normal|fabric|carousel|probing (последняя симуляция)
    tasks: set = field(default_factory=set)  # ссылки на фоновые задачи, чтобы их не съел сборщик мусора
    # защита входа (в памяти): логин -> [число неудач, время конца блокировки]; IP -> времена попыток
    login_failures: dict = field(default_factory=dict)
    login_ip_hits: dict = field(default_factory=dict)
    # одноразовые билеты на SSE: билет -> (логин, версия токенов, время истечения). Нужны, чтобы JWT не ездил в URL
    sse_tickets: dict = field(default_factory=dict)
    dummy_hash: str = ""  # хэш-пустышка для сверки пароля несуществующего логина
    clock: Callable[[], float] = time.time  # подменяется в тестах, чтобы «перемотать» блокировку
    chaos_modes: dict = field(default_factory=lambda: {"smev": "normal", "bki": "normal"})

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task
