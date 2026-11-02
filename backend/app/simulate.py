"""Генератор правдоподобных заявок для демо-потока и проверки антифрода (POST /api/v1/simulate).

Заявки в формате 2.1 контракта (без ФИО, телефона и паспорта). Клиенты создаются прямо в таблице clients с
фиктивными хэшами телефона (настоящие телефоны симулятору не нужны), passport_hash — случайные 16 hex.
"""

import asyncio
import hashlib
import hmac
import itertools
import random
import time

import credit_core
from credit_core import Application

from backend.app import audit
from backend.app.antifraud.attacks import GROUPS, Event, Ids, build_stream
from backend.app.antifraud.fingerprints import fingerprint
from backend.app.decisions import now_iso
from backend.app.services import create_application, start_pipeline
from backend.app.state import AppState

EMPLOYMENTS = ["employed"] * 6 + ["self-employed"] * 2 + ["retired", "student"]
FAMILIES = ["single", "married", "children"]
SCENARIOS = ("normal", "fabric", "carousel", "probing", "mixed")
SIM_CLIENT_FIRST = 5000  # клиенты симулятора: cli_5000… (реальные клиенты нумеруются с начала)


def plan_events(rng: random.Random, ids: Ids, t0: float, count: int, scenario: str) -> list[Event]:
    """Какие заявки подавать: обычные клиенты и/или группы схем (генераторы те же, что дали эталоны антифроду)."""
    if scenario == "normal":
        return build_stream(rng, ids, t0, count, {})[:count]
    if scenario == "mixed":
        # около 30% заявок — три схемы по очереди, и при count >= 24 каждая схема представлена хотя бы одной целой группой
        n_attack = max(round(count * 0.3), min(count // 2, 18))
        attack: list[Event] = []
        for turn, scheme in enumerate(itertools.cycle(("fabric", "carousel", "probing"))):
            left = n_attack - len(attack)
            if left < 3:
                break
            low, high = {"fabric": (5, 8), "carousel": (4, 7), "probing": (4, 6)}[scheme]
            later = ("carousel", "probing")[turn:] if turn < 2 else ()  # схемы первого круга, которые ещё не показаны
            reserve = sum({"carousel": 4, "probing": 4}[x] for x in later)  # оставляем им место
            size = max(low, min(rng.randint(low, high), left - reserve))
            size = min(size, left)
            attack.extend(GROUPS[scheme](rng, ids, t0, size))
        normal = build_stream(rng, ids, t0, count - len(attack), {})
        span = max(normal[-1].ts - t0, 600.0) if normal else 600.0
        # группы раскладываем по времени потока обычных заявок (каждая начинается в своей точке)
        shifted = []
        for scheme_group in _split_groups(attack):
            shift = rng.uniform(0, span) - (scheme_group[0].ts - t0)
            shifted.extend(Event(**{**e.__dict__, "ts": e.ts + shift}) for e in scheme_group)
        return sorted(normal + shifted, key=lambda e: e.ts)
    events, ts = [], t0
    while len(events) < count:
        group = GROUPS[scenario](rng, ids, ts)
        events.extend(group)
        ts = group[-1].ts + rng.uniform(300, 900)
    return events[:count]


def _split_groups(events: list[Event]) -> list[list[Event]]:
    """Разбивает список событий атак на группы по полю group (порядок внутри группы сохраняется)."""
    groups: dict[int, list[Event]] = {}
    for e in events:
        groups.setdefault(e.group, []).append(e)
    return list(groups.values())


def fake_hash(kind: str, number: int) -> str:
    """Фиктивный хэш: стабильный для одного номера (один человек → один passport_hash) и без настоящих данных."""
    return hashlib.sha256(f"sim-{kind}:{number}".encode()).hexdigest()


class SimClients:
    """Превращает номера из событий (person/phone/device) в клиентов, паспорта и анкеты формата 2.1."""

    def __init__(self, state: AppState, rng: random.Random):
        self.state, self.rng = state, rng
        self.clients: dict[int, str] = {}  # номер телефона из события -> client_id
        self.persons: dict[int, dict] = {}  # номер человека -> его неизменные данные

    def client_for(self, phone: int) -> str:
        if phone not in self.clients:
            db = self.state.db
            n = SIM_CLIENT_FIRST + int(db.next_id("simclient").split("_")[1]) - 1
            client_id = f"cli_{n:04d}"
            db.execute("INSERT OR IGNORE INTO clients (client_id, phone_hash, phone_masked, created_at) VALUES (?,?,?,?)",
                       (client_id, fake_hash("phone", phone), "+7 000 ***-**-00", now_iso()))
            self.clients[phone] = client_id
        return self.clients[phone]

    def person(self, number: int) -> dict:
        if number not in self.persons:
            self.persons[number] = {"passport_hash": fake_hash("passport", number)[:16],
                                    "age": self.rng.randint(21, 65), "employment": self.rng.choice(EMPLOYMENTS),
                                    "family": self.rng.choice(FAMILIES)}
        return self.persons[number]

    def application_for(self, event: Event) -> Application:
        p = self.person(event.person)
        income = min(max(event.income, 30_000), 600_000)  # меньше 30 000 продукт не принимает (PRODUCT_MISMATCH)
        amount = min(max(event.amount, 30_000), 1_000_000)  # prod_standard: до 1 млн
        app = Application(app_id="", passport_hash=p["passport_hash"], age=p["age"], income=income,
                          employment=p["employment"], family=p["family"], amount=amount, term=event.term)
        assert not credit_core.validate_for_api(app), "симулятор выдал анкету, не проходящую проверку"
        return app


async def run_simulation(state: AppState, count: int, actor: str, role: str, scenario: str = "normal") -> None:
    """Подаёт заявки не разом, а с небольшим разбросом — на дашборде получается «поток».

    Время событий для антифрода — ВИРТУАЛЬНОЕ: часы обычного потока сжимаются в секунды реального времени.
    """
    try:  # флаг sim_running выставляет ручка до запуска задачи (иначе два быстрых запроса пройдут оба)
        await _simulate(state, count, actor, role, scenario)
    finally:
        state.sim_running = False


async def _simulate(state: AppState, count: int, actor: str, role: str, scenario: str) -> None:
    rng = random.Random()
    max_pause = state.settings.simulate_max_pause_ms / 1000
    t0 = max(time.time(), state.antifraud.last_ts) + 1
    sim, labels = SimClients(state, rng), {}
    state.sim_labels = labels
    for event in plan_events(rng, Ids(start=rng.randint(10**6, 10**9)), t0, count, scenario):
        app = sim.application_for(event)
        device_hash = fingerprint(state.settings.pii_pepper, "device", f"sim-device-{event.device}") \
            if event.device is not None else None
        app_id = create_application(state, app, sim.client_for(event.phone), device_hash=device_hash,
                                    channel="simulator", actor=actor, actor_role=role)
        labels[app_id] = event.label
        start_pipeline(state, app_id, {"event_ts": event.ts})
        if max_pause:
            await asyncio.sleep(rng.uniform(0, max_pause))
    audit.write(state.db, actor, role, "simulate_finished", details={"count": count, "scenario": scenario})
