"""Генераторы потоков заявок: обычные клиенты и три известные схемы мошенничества.

Эти же генераторы используют ДВА места:
  * обучение антифрода (эталоны схем) — antifraud/model.py;
  * симулятор POST /api/simulate?scenario=... — чтобы на демо можно было показать атаку.
Важно: эталоны — это НАШИ описания схем (по открытым описаниям «кредитных фабрик» и дропов), а не размеченные
реальные данные банка. Реальных меток мошенничества у нас нет, и мы это не скрываем.

Событие описывает заявку абстрактно: «кто» (person = паспорт), «с какого устройства», «какой телефон (клиент)».
Для эталонов этого достаточно; симулятор превращает номера в настоящие анкеты формата 2.1 контракта.
"""

import math
import random
from dataclasses import dataclass, field
from itertools import count

SCHEMES = ("fabric", "carousel", "probing")
SCHEME_RU = {
    "fabric": "кредитная фабрика", "carousel": "карусель телефонов", "probing": "подбор дохода",
}
FAMILY_DEVICES = 40  # общие домашние устройства: одним компьютером или телефоном иногда честно пользуются два человека
ONLINE_SHARE = 0.85  # доля заявок, где приложение прислало id устройства (иногда localStorage очищен или заблокирован)


@dataclass
class Event:
    label: str  # normal | fabric | carousel | probing
    ts: float  # время события (секунды); у симулятора — «виртуальное», чтобы сжать часы в секунды
    person: int
    device: int | None  # id устройства из приложения клиента; None — приложение его не прислало
    phone: int  # телефон аккаунта клиента
    amount: int
    income: int
    term: int
    n_loans: int
    known_months: int
    group: int = 0


@dataclass
class Ids:
    """Счётчики идентификаторов: каждый новый человек, телефон и устройство получают свой номер."""
    start: int = 1000
    _c: count = field(init=False)

    def __post_init__(self):
        self._c = count(self.start)

    def new(self) -> int:
        return next(self._c)


def _history(rng: random.Random, short: bool) -> tuple[int, int]:
    """(кредитов, месяцев известной истории). У «дропов» истории короткие или пустые."""
    if short:
        return rng.choice([0, 0, 1, 1, 2]), rng.randint(0, 6)
    # у реальных историй БКИ бывает и 20–30 кредитов (в основном закрытые кредитные карты и микрозаймы)
    return min(1 + int(rng.expovariate(1 / 8)), 30), min(int(rng.triangular(3, 25, 24)), 25)


def normal_event(rng: random.Random, ids: Ids, ts: float) -> Event:
    income = int(min(max(rng.lognormvariate(11.4, 0.5), 25_000), 500_000))
    amount = int(min(max(income * rng.uniform(1, 8), 50_000), 3_000_000))
    n_loans, months = _history(rng, short=rng.random() < 0.12)
    return Event(
        "normal", ts, ids.new(),
        device=None if rng.random() >= ONLINE_SHARE else (rng.randrange(FAMILY_DEVICES) if rng.random() < 0.05 else ids.new()),
        phone=ids.new(),
        amount=amount, income=income, term=rng.choice([12, 24, 36, 48, 60]), n_loans=n_loans, known_months=months)


def fabric_group(rng: random.Random, ids: Ids, ts: float, size: int | None = None) -> list[Event]:
    """Кредитная фабрика: 5–8 разных людей (подставных, у каждого своя SIM) с ОДНОГО устройства,
    пачкой за считанные минуты, похожие суммы и доходы, короткие истории."""
    n = size or rng.randint(5, 8)
    group = ids.new()
    farm_device = ids.new()  # «ферма» подаёт заявки с одного устройства, но часть заявок может идти и из офиса
    # суммы и доходы намеренно в тех же диапазонах, что у честных клиентов: ловить должны СВЯЗИ, а не «подозрительную сумму»
    base_income = rng.randint(30, 90) * 1000
    base_amount, term = int(base_income * rng.uniform(2, 8)), rng.choice([24, 36, 48])
    events = []
    for _ in range(n):
        ts += rng.lognormvariate(math.log(70), 0.6)  # около минуты между заявками
        n_loans, months = _history(rng, short=rng.random() < 0.5)
        device = farm_device if rng.random() < 0.8 else None  # 20% заявок без id устройства: схема не идеальна
        events.append(Event("fabric", ts, ids.new(), device, ids.new(),
                            int(base_amount * rng.uniform(0.9, 1.1)), int(base_income * rng.uniform(0.9, 1.1)),
                            term, n_loans, months, group))
    return events


def carousel_group(rng: random.Random, ids: Ids, ts: float, size: int | None = None) -> list[Event]:
    """Карусель: один и тот же телефон (аккаунт клиента) подаёт заявки по разным паспортам; устройства разные."""
    n = size or rng.randint(4, 7)
    group, phone = ids.new(), ids.new()
    events = []
    for _ in range(n):
        ts += rng.uniform(120, 2400)
        income = rng.randint(30, 90) * 1000
        n_loans, months = _history(rng, short=rng.random() < 0.5)
        events.append(Event("carousel", ts, ids.new(), ids.new() if rng.random() < ONLINE_SHARE else None, phone,
                            int(income * rng.uniform(3, 8)),
                            income, rng.choice([24, 36, 48]), n_loans, months, group))
    return events


def probing_group(rng: random.Random, ids: Ids, ts: float, size: int | None = None) -> list[Event]:
    """Подбор дохода: один человек подаёт заявки на одну и ту же сумму, каждый раз завышая доход, пока не пройдёт скоринг."""
    n = size or rng.randint(3, 6)
    group, person, phone = ids.new(), ids.new(), ids.new()
    device = ids.new() if rng.random() < 0.7 else None
    income, amount, term = rng.randint(30, 50) * 1000, rng.randint(40, 90) * 10_000, rng.choice([24, 36, 48])
    n_loans, months = _history(rng, short=False)
    events = []
    for _ in range(n):
        ts += rng.uniform(60, 480)
        income = int(income * rng.uniform(1.12, 1.3))
        events.append(Event("probing", ts, person, device, phone, amount, income, term, n_loans, months, group))
    return events


GROUPS = {"fabric": fabric_group, "carousel": carousel_group, "probing": probing_group}


def build_stream(rng: random.Random, ids: Ids, t0: float, n_normal: int, groups: dict[str, int],
                 mean_gap: float = 360.0) -> list[Event]:
    """Склеивает обычные заявки (пуассоновский поток, в среднем раз в mean_gap секунд) и группы атак в один поток."""
    events, ts = [], t0
    for _ in range(n_normal):
        ts += rng.expovariate(1 / mean_gap)
        events.append(normal_event(rng, ids, ts))
    span = max(ts - t0, 1.0)
    for scheme, how_many in groups.items():
        for _ in range(how_many):
            events.extend(GROUPS[scheme](rng, ids, t0 + rng.uniform(0, span)))
    return sorted(events, key=lambda e: e.ts)
