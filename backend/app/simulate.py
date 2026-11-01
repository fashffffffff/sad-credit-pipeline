"""Генератор правдоподобных случайных анкет для демо-потока (POST /api/simulate)."""

import asyncio
import itertools
import random
import time
from datetime import date, timedelta

from backend.app import audit
from backend.app.antifraud.attacks import GROUPS, Event, Ids, build_stream, normal_event
from common.passport import FIRST_ISSUE_DATE, MAGIC_PASSPORTS as MAGIC, OKATO_TO_SUBJECT, UNSURE, add_years, age_on
from backend.app.schemas import ApplicationIn
from backend.app.services import create_application, start_pipeline
from backend.app.state import AppState

MALE = [("Иванов", "Иван", "Иванович"), ("Петров", "Пётр", "Сергеевич"), ("Смирнов", "Алексей", "Дмитриевич"),
        ("Кузнецов", "Дмитрий", "Андреевич"), ("Попов", "Михаил", "Николаевич"), ("Соколов", "Артём", "Игоревич"),
        ("Лебедев", "Сергей", "Олегович"), ("Новиков", "Андрей", "Викторович")]
FEMALE = [("Иванова", "Анна", "Ивановна"), ("Петрова", "Мария", "Сергеевна"), ("Смирнова", "Елена", "Дмитриевна"),
          ("Кузнецова", "Ольга", "Андреевна"), ("Попова", "Наталья", "Николаевна"),
          ("Соколова", "Ирина", "Игоревна"), ("Лебедева", "Юлия", "Олеговна"), ("Новикова", "Татьяна", "Викторовна")]
# паспорта-«сценарии» из контракта: время от времени подмешиваем, чтобы в потоке были отказы и ручные проверки
MAGIC_PASSPORTS = sorted(MAGIC)


def random_inn(rng: random.Random) -> str:
    """ИНН юрлица со случайными цифрами и верной контрольной цифрой."""
    digits = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
    weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    control = sum(d * w for d, w in zip(digits, weights)) % 11 % 10
    return "".join(map(str, digits)) + str(control)


def issue_data(rng: random.Random, birth: date, magic: bool, passport: str | None = None) -> dict:
    """Согласованные паспортные данные: дата выдачи, серия под неё и код подразделения региона серии.

    Дата выдачи — после последней обязательной замены (20/45 лет), поэтому паспорт не «просрочен»;
    бланк — из года выдачи или до 5 лет раньше; код подразделения начинается с кода субъекта региона серии.
    """
    today = date.today()
    lo = max(FIRST_ISSUE_DATE, add_years(birth, 14))
    for milestone in (20, 45):  # паспорт, выданный до 20/45-летия, просрочен
        if add_years(birth, milestone) <= today:
            lo = max(lo, add_years(birth, milestone))
    issue = lo + timedelta(days=rng.randint(0, max(0, (today - lo).days)))
    regions = [r for r in OKATO_TO_SUBJECT if r not in UNSURE]
    okato = rng.choice(regions)
    blank_year = rng.randint(max(1997, issue.year - 5), issue.year)
    subject = rng.choice(sorted(OKATO_TO_SUBJECT[okato]))
    result = {"passport_issue_date": issue.isoformat(),
              "passport_division_code": f"{subject}{rng.randint(0, 9)}-{rng.randint(0, 999):03d}"}
    if not magic:
        result["passport"] = f"{okato}{blank_year % 100:02d} {rng.randint(100000, 999999)}"
    return result


def random_application(rng: random.Random, passport: str | None = None, allow_magic: bool = True) -> dict:
    """Случайная анкета, проходящая ВСЕ проверки (иначе симуляция сыпалась бы на 422)."""
    surname, name, patronymic = rng.choice(rng.choice([MALE, FEMALE]))
    age = rng.randint(21, 65)
    birth = date.today() - timedelta(days=age * 365 + rng.randint(0, 364))
    if passport is None and allow_magic and rng.random() < 0.05:  # немного демо-клиентов для разнообразия
        passport = rng.choice(MAGIC_PASSPORTS)
    magic = passport is not None
    form = issue_data(rng, birth, magic)
    income = int(min(max(rng.lognormvariate(11.4, 0.5), 25_000), 600_000) // 1000 * 1000)
    # сумма кредита 1–8 месячных доходов — типичный потребкредит в РФ (иначе ПДН > 80% и лишние отказы)
    amount = int(min(max(income * rng.uniform(1, 8), 50_000), 3_000_000) // 10_000 * 10_000)
    return {
        "full_name": f"{surname} {name} {patronymic}",
        "birth_date": birth.isoformat(),
        "passport": passport or form.pop("passport"),
        "passport_issue_date": form["passport_issue_date"],
        "passport_division_code": form["passport_division_code"],
        "phone": f"+79{rng.randint(10, 99)}{rng.randint(1000000, 9999999)}",
        "monthly_income": income,
        "employer_inn": random_inn(rng),
        "employment_months": rng.randint(0, min(480, (age_on(birth, date.today()) - 14) * 12 - 1)),
        "dependents": rng.choice([0, 0, 0, 1, 1, 2, 3]),
        "loan_amount": amount,
        "loan_term_months": rng.choice([12, 24, 36, 48, 60]),
        "purpose": rng.choice(["consumer", "consumer", "auto", "refinance", "education"]),
    }


SCENARIOS = ("normal", "fabric", "carousel", "probing", "mixed")


def plan_events(rng: random.Random, ids: Ids, t0: float, count: int, scenario: str) -> list[Event]:
    """Какие заявки подавать: обычные клиенты и/или группы схем (генераторы те же, что дали эталоны антифроду)."""
    if scenario == "normal":
        return build_stream(rng, ids, t0, count, {})[:count]
    if scenario == "mixed":
        # около 30% заявок — три схемы по очереди, и при count >= 24 каждая схема представлена хотя бы одной целой группой
        n_attack = max(round(count * 0.3), min(count // 2, 18))
        attack: list[Event] = []
        t_group = t0
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
        shifted, offset = [], 0.0
        for scheme_group in _split_groups(attack):
            shift = rng.uniform(0, span) - (scheme_group[0].ts - t0)
            shifted.extend(Event(**{**e.__dict__, "ts": e.ts + shift}) for e in scheme_group)
            offset += 1
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


class Identities:
    """Превращает номера из событий (person/phone/employer/device) в настоящие значения анкеты.

    Один и тот же номер всегда даёт одно и то же значение — так общий телефон, ИНН и паспорт между заявками настоящие.
    """

    def __init__(self, rng: random.Random):
        self.rng, self.persons = rng, {}

    def _seeded(self, kind: str, number: int) -> random.Random:
        return random.Random(f"{kind}:{number}")

    def form_for(self, event: Event) -> dict:
        if event.person not in self.persons:
            # у участников схем паспорта обычные (не демонстрационные); у обычных клиентов иногда демо-паспорта
            self.persons[event.person] = random_application(self.rng, allow_magic=event.label == "normal")
        form = dict(self.persons[event.person])
        rng = self._seeded("phone", event.phone)
        income = min(max(event.income, 10_000), 10_000_000)
        form.update(
            client_device_id=f"sim-device-{event.device}" if event.device is not None else None,  # None — заявка из офиса
            phone=f"+79{rng.randint(10, 99)}{rng.randint(1000000, 9999999)}",
            employer_inn=random_inn(self._seeded("inn", event.employer)),
            monthly_income=income,
            loan_amount=min(max(event.amount, 30_000), 60 * income, 5_000_000),
            loan_term_months=event.term)
        return form


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
    identities, labels = Identities(rng), {}
    state.sim_labels = labels
    for event in plan_events(rng, Ids(start=rng.randint(10**6, 10**9)), t0, count, scenario):
        data = ApplicationIn.model_validate(identities.form_for(event))
        app_id = create_application(state, data, actor, role)
        labels[app_id] = event.label
        start_pipeline(state, app_id, data, {"event_ts": event.ts})
        if max_pause:
            await asyncio.sleep(rng.uniform(0, max_pause))
    audit.write(state.db, actor, role, "simulate_finished", details={"count": count, "scenario": scenario})
