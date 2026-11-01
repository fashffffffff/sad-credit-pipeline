"""Антифрод: граф связей, поведение заявки, предупреждение (не отказ), приватность и самозапрет."""

import random

from backend.app.antifraud.attacks import Ids, build_stream
from backend.app.antifraud.engine import AntifraudEngine
from backend.app.antifraud.model import event_to_node, reference_model, vectors_for
from backend.app.antifraud.graph import FraudGraph
from backend.app.antifraud.features import build_vector
from backend.app.schemas import ApplicationIn
from backend.app.simulate import random_application
from backend.tests.conftest import good_form, loan


def history(n_loans: int, known_months: int) -> list[dict]:
    """История БКИ с нужным числом кредитов и известных месяцев (остальные месяцы — «нет данных», сырой код 3)."""
    from ml.credit_model.history import SHIFTED_POSITIONS
    codes = {f"enc_paym_{i}": (0 if i < known_months else 3) + (1 if i in SHIFTED_POSITIONS else 0) for i in range(25)}
    return [loan(k + 1, **codes) for k in range(n_loans)]


def forms(n: int, seed: int, **common) -> list[dict]:
    """n правдоподобных анкет РАЗНЫХ людей (разные паспорта, ФИО, телефоны), без демо-паспортов."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        form = random_application(rng, allow_magic=False)
        # скромная сумма, чтобы честные заявки одобрялись и отказ не мешал проверять именно антифрод
        form["loan_amount"] = max(30_000, min(form["loan_amount"], form["monthly_income"] * 3))
        form["loan_term_months"] = 36
        out.append(form | common)
    return out


# ---------- модель на отложенных данных ----------
def test_model_catches_schemes_and_rarely_raises_false_alarms():
    """Отложенный поток (другое зерно, чем у эталонов): 1500 обычных заявок + по 40 групп каждой схемы.

    Ожидаемый результат: начиная с 3-й заявки группы схема ловится почти всегда (>= 95%), ложных тревог на обычных
    заявках не больше 3%, схема определяется верно в >= 90% случаев. Первые заявки группы ещё не отличить от честных.
    """
    model = reference_model()
    events = build_stream(random.Random(4242), Ids(), 5e6, 1500, {"fabric": 40, "carousel": 40, "probing": 40})
    graph, seen, normal_flags, normal_total, late, late_hits, pattern_ok = FraudGraph(), {}, 0, 0, 0, 0, 0
    for i, event in enumerate(events):
        node = event_to_node(event, i)
        graph.add(node)
        result = model.score_vector(build_vector(graph, node))
        flagged = result["score"] >= 0.6
        if event.label == "normal":
            normal_total += 1
            normal_flags += flagged
            continue
        seen[event.group] = seen.get(event.group, 0) + 1
        if seen[event.group] >= 3:
            late += 1
            late_hits += flagged
            pattern_ok += flagged and result["pattern"] == event.label
    assert late_hits / late >= 0.95
    assert normal_flags / normal_total <= 0.03
    assert pattern_ok / late_hits >= 0.9


# ---------- через API и конвейер ----------
async def test_factory_is_flagged_but_not_rejected(env, operator):
    """Кредитная фабрика: 6 разных паспортов с одного устройства и от одного работодателя за секунды.

    Ожидаемый результат: ближе к концу группы заявки получают предупреждение — manual_review с причиной FRAUD_SUSPECT
    и объяснением («Похожа на схему «кредитная фабрика»…»); отказа нет; в ApplicationDetail есть блок fraud
    (оценка, схема, сходство, топ-3 признака, размер группы); в SSE пришло событие fraud; в графе — одна группа из 6 узлов.
    """
    queue = env.state.bus.subscribe()
    risk = await env.login("risk")  # подробности антифрода видит риск-менеджер, оператору они урезаны (test_security_audit)
    apps = []
    for form in forms(6, seed=1, employer_inn="7707083893", client_device_id="farm-pc-0001"):
        resp = await env.submit(form, operator)
        assert resp.status_code == 202, resp.text
        await env.drain()
        apps.append((await env.client.get(f"/api/applications/{resp.json()['id']}", headers=risk)).json())
    flagged = [a for a in apps if a["fraud"]["flagged"]]
    assert len(flagged) >= 4 and apps[-1]["fraud"]["flagged"]
    last = apps[-1]
    assert last["status"] == "manual_review" and "FRAUD_SUSPECT" in [r["code"] for r in last["reasons"]]
    assert all(a["status"] != "rejected" for a in apps)  # только предупреждение
    fraud = last["fraud"]
    assert fraud["pattern"] == "fabric" and fraud["fraud_score"] >= 0.6 and fraud["component_size"] == 6
    assert len(fraud["explanation"]) == 3 and "кредитная фабрика" in fraud["text"]
    antifraud_stage = next(s for s in last["stages"] if s["stage"] == "antifraud")
    assert antifraud_stage["status"] == "warn"

    types = []
    while not queue.empty():
        types.append(queue.get_nowait()[0])
    assert "fraud" in types and "graph" in types

    admin = await env.login("admin")
    graph = (await env.client.get("/api/antifraud/graph?minutes=60", headers=admin)).json()
    assert len(graph["nodes"]) == 6 and graph["clusters"][0]["nodes"] and len(graph["clusters"][0]["nodes"]) == 6
    assert {e["kind"] for e in graph["edges"]} >= {"device", "employer"}
    assert last["fraud"]["channel"] == "online"


async def test_single_honest_application_is_not_flagged(env, operator):
    """Одна честная заявка.

    Ожидаемый результат: этап antifraud = ok, fraud.flagged = false, итог approved (антифрод не мешает честным).
    """
    app = await env.run_and_get(forms(1, seed=2)[0], operator)
    stage = next(s for s in app["stages"] if s["stage"] == "antifraud")
    assert stage["status"] == "ok" and app["fraud"]["flagged"] is False
    assert app["status"] == "approved"


async def test_colleagues_of_one_employer_in_normal_pace_do_not_alarm(env):
    """Пять коллег одного работодателя подают заявки с разных устройств с интервалом около часа и более.

    Ожидаемый результат: ни одна не помечена (ложной тревоги нет), хотя ИНН у них общий.
    """
    engine: AntifraudEngine = env.state.antifraud
    base = 1e9
    for i, form in enumerate(forms(5, seed=3, employer_inn="7707083893")):
        data = ApplicationIn.model_validate(form)
        result, _ = engine.assess(f"col{i}", data, [loan(1), loan(2)], base + i * 4000)
        assert result["flagged"] is False, (i, result)


async def test_normal_stream_has_few_false_alarms(env):
    """300 обычных заявок симулятора (в том числе коллеги крупных работодателей и общие устройства отделений).

    Ожидаемый результат: не больше 3% заявок помечены.
    """
    from backend.app.simulate import Identities
    engine: AntifraudEngine = env.state.antifraud
    events = build_stream(random.Random(11), Ids(), 2e9, 300, {})
    people = Identities(random.Random(12))
    flags = 0
    for i, event in enumerate(events):
        data = ApplicationIn.model_validate(people.form_for(event))  # общие работодатели и телефоны — как в симуляторе
        loans = history(event.n_loans, event.known_months)
        result, _ = engine.assess(f"n{i}", data, loans, event.ts)
        flags += result["flagged"]
    assert flags / 300 <= 0.03


async def test_graph_contains_no_plain_personal_data(env, operator):
    """Приватность графа.

    Ожидаемый результат: в таблице fraud_nodes, в ответе /api/antifraud/graph и в самом графе нет открытых телефонов,
    ИНН, паспортов и значения X-Device-Id — только HMAC-отпечатки.
    """
    sample = forms(3, seed=5, employer_inn="7707083893")
    for form in sample:
        await env.run_and_get(form, operator)
    await env.submit(forms(1, seed=6, client_device_id="secret-device-token")[0], operator)
    await env.drain()
    dump = str([tuple(r) for r in env.state.db.query_all("SELECT * FROM fraud_nodes")])
    admin = await env.login("admin")
    dump += (await env.client.get("/api/antifraud/graph?minutes=600", headers=admin)).text
    dump += str({k: v.fps for k, v in env.state.antifraud.graph.nodes.items()})
    for form in sample:
        for secret in (form["phone"], form["passport"], form["passport"].replace(" ", ""), form["full_name"]):
            assert secret not in dump
    assert "7707083893" not in dump and "secret-device-token" not in dump
    # а отпечатки есть и они разные у разных людей
    rows = env.state.db.query_all("SELECT phone, passport FROM fraud_nodes")
    assert len({r["phone"] for r in rows}) == len(rows) and all(len(r["phone"]) == 24 for r in rows)


async def test_graph_endpoint_roles(env, operator):
    """Ожидаемый результат: граф доступен admin, risk_manager, auditor; оператору — 403."""
    assert (await env.client.get("/api/antifraud/graph", headers=operator)).status_code == 403
    for name in ("admin", "risk", "auditor"):
        resp = await env.client.get("/api/antifraud/graph", headers=await env.login(name))
        assert resp.status_code == 200 and set(resp.json()) == {"nodes", "edges", "clusters"}


async def test_demo_passports_skip_antifraud(env, operator):
    """Демо-паспорта (0000 00000N) подаются снова и снова, поэтому в граф не попадают.

    Ожидаемый результат: этап antifraud = skipped, заявка обрабатывается как раньше.
    """
    app = await env.run_and_get(good_form(passport="0000 000001"), operator)
    assert next(s for s in app["stages"] if s["stage"] == "antifraud")["status"] == "skipped"
    assert app["status"] == "approved" and app["fraud"] is None


async def test_graph_is_restored_after_restart(env, operator):
    """Ожидаемый результат: новый движок на той же БД восстанавливает узлы и рёбра графа (пересчитываются из отпечатков)."""
    for form in forms(3, seed=8, employer_inn="7707083893", client_device_id="same-pc-0001"):
        await env.submit(form, operator)
        await env.drain()
    again = AntifraudEngine(env.state.db, env.state.settings.pii_pepper)
    assert set(again.graph.nodes) == set(env.state.antifraud.graph.nodes)
    assert len(again.graph.edges) == len(env.state.antifraud.graph.edges) > 0


# ---------- симулятор ----------
async def test_simulate_scenarios_and_labels(env):
    """POST /api/simulate со сценариями.

    Ожидаемый результат: scenario=fabric подаёт группы заявок разных людей с общим устройством и работодателем;
    неизвестный сценарий — 422; /api/simulate/labels отдаёт правду о заявках; mixed содержит и обычные, и заявки схем.
    """
    admin = await env.login("admin")
    bad = await env.client.post("/api/simulate", json={"count": 5, "scenario": "nope"}, headers=admin)
    assert bad.status_code == 422 and "scenario" in bad.json()["fields"]
    assert (await env.client.post("/api/simulate", json={"count": 14, "scenario": "fabric"}, headers=admin)).status_code == 202
    await env.drain()
    labels = (await env.client.get("/api/simulate/labels", headers=admin)).json()
    assert len(labels) == 14 and set(labels.values()) == {"fabric"}
    graph = (await env.client.get("/api/antifraud/graph?minutes=1440", headers=admin)).json()
    assert len(graph["clusters"]) >= 2 and any(c["pattern"] == "fabric" for c in graph["clusters"])

    assert (await env.client.post("/api/simulate", json={"count": 40, "scenario": "mixed"}, headers=admin)).status_code == 202
    await env.drain()
    kinds = set((await env.client.get("/api/simulate/labels", headers=admin)).json().values())
    assert "normal" in kinds and len(kinds) >= 2


# ---------- самозапрет ----------
async def test_self_ban_is_hard_rejection(env, operator):
    """Самозапрет на кредиты (поле self_ban в ответе БКИ, магический паспорт 0000 000006).

    Ожидаемый результат: rejected с причиной SELF_BAN и текстом про Госуслуги; скоринг пропущен; не ручная проверка.
    """
    app = await env.run_and_get(good_form(passport="0000 000006"), operator)
    assert app["status"] == "rejected" and [r["code"] for r in app["reasons"]] == ["SELF_BAN"]
    assert "Госуслуги" in app["reasons"][0]["text"]
    assert next(s for s in app["stages"] if s["stage"] == "scoring")["status"] == "skipped"
    # и обычный паспорт с самозапретом тоже
    env.world.bki_override["4510 999111"] = env.world.bki_answer("4510 000000") | {"self_ban": True}
    other = await env.run_and_get(good_form(passport="4510 999111"), operator)
    assert other["status"] == "rejected" and other["reasons"][0]["code"] == "SELF_BAN"


async def test_operator_browser_is_not_a_device(env, operator):
    """Оператор подряд вводит 30 честных заявок разных клиентов из офиса (без client_device_id) с одного браузера и IP.

    Ожидаемый результат: ни одной тревоги, графа «устройств» нет вовсе (ни одного ребра device), канал заявок — office;
    все заявки одобрены. Устройство оператора не участвует в антифроде.
    """
    for form in forms(30, seed=77):
        app = await env.run_and_get(form, operator)
        assert app["fraud"]["flagged"] is False and app["fraud"]["channel"] == "office", app["fraud"]
        assert app["status"] == "approved"
    assert not [e for e in env.state.antifraud.graph.edges if e.kind == "device"]
    rows = env.state.db.query_all("SELECT device, channel FROM fraud_nodes")
    assert len(rows) == 30 and all(r["device"] is None and r["channel"] == "office" for r in rows)


async def test_client_device_id_validation(env, operator):
    """client_device_id необязателен; если задан — 8–64 символа (латиница, цифры, . _ -).

    Ожидаемый результат: короткий или с пробелами идентификатор — 422 на поле client_device_id; корректный принимается.
    """
    bad = await env.submit(forms(1, seed=90, client_device_id="short")[0], operator)
    assert bad.status_code == 422 and "client_device_id" in bad.json()["fields"]
    ok = await env.submit(forms(1, seed=91, client_device_id="phone-app-01.A")[0], operator)
    assert ok.status_code == 202
    await env.drain()


async def test_consecutive_simulations_do_not_share_identities(env):
    """Два запуска симуляции подряд.

    Ожидаемый результат: у «новых» людей второго запуска нет общих телефонов с первым запуском
    (раньше счётчики номеров начинались заново, и второй запуск давал ложные тревоги «каруселью»).
    """
    admin = await env.login("admin")
    for _ in range(2):
        assert (await env.client.post("/api/simulate", json={"count": 40, "scenario": "normal"}, headers=admin)).status_code == 202
        await env.drain()
    phones = [r["phone"] for r in env.state.db.query_all("SELECT phone FROM fraud_nodes")]
    assert len(phones) > 40 and len(set(phones)) == len(phones)


async def test_mixed_small_run_contains_all_three_schemes_and_graph_shows_them(env):
    """scenario=mixed на 50 заявках (как кнопка на демо).

    Ожидаемый результат: в потоке есть целые группы всех трёх схем (а не обрезанные по count); после прогона граф за последний
    час показывает группы fabric, carousel и probing (раньше окно считалось по виртуальному времени и старые группы пропадали);
    в каждой из групп помечено не меньше двух заявок.
    """
    admin = await env.login("admin")
    assert (await env.client.post("/api/simulate", json={"count": 50, "scenario": "mixed"}, headers=admin)).status_code == 202
    await env.drain()
    labels = (await env.client.get("/api/simulate/labels", headers=admin)).json()
    assert len(labels) == 50 and {"fabric", "carousel", "probing"} <= set(labels.values())
    graph = (await env.client.get("/api/antifraud/graph?minutes=60", headers=admin)).json()
    flagged = {c["pattern"] for c in graph["clusters"] if c["pattern"]}
    assert {"fabric", "carousel", "probing"} <= flagged, graph["clusters"]


def test_simulated_loans_are_affordable():
    """Генератор обычных заявок (после правки сумм «1–8 доходов»).

    Ожидаемый результат: у 2000 обычных заявок симулятора ПДН без долгов (платёж / доход) выше 80% меньше чем у 3%,
    иначе в потоке было бы слишком много отказов «Долговая нагрузка > 80%».
    """
    from backend.app.schemas import monthly_payment
    people = random.Random(5)
    over = 0
    events = build_stream(random.Random(6), Ids(), 0.0, 2000, {})
    for e in events:
        form_income = max(e.income, 10_000)
        over += monthly_payment(min(max(e.amount, 30_000), 60 * form_income), e.term) / form_income > 0.8
    assert over / 2000 < 0.03
