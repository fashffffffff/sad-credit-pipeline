"""Антифрод: граф по отпечаткам device / phone / passport, поведение заявки, предупреждение (не отказ), приватность."""

import asyncio
import random

from backend.app.antifraud.attacks import Ids, build_stream
from backend.app.antifraud.engine import AntifraudEngine
from backend.app.antifraud.features import FEATURE_NAMES, build_vector
from backend.app.antifraud.graph import FraudGraph
from backend.app.antifraud.model import event_to_node, reference_model
from backend.app.simulate import SimClients, plan_events
from backend.tests.conftest import good_form, make_env


def hash16(n: int) -> str:
    return f"{n:016x}"


# ---------- модель на отложенных данных ----------
def test_model_catches_schemes_and_rarely_raises_false_alarms():
    """Отложенный поток (другое зерно, чем у эталонов): 1500 обычных заявок + по 40 групп каждой схемы.

    Начиная с 3-й заявки группы схема ловится, ложные тревоги на обычных заявках редки, схема определяется верно.
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
    print(f"поймано {late_hits}/{late}, ложных {normal_flags}/{normal_total}, схема верно {pattern_ok}/{late_hits}")
    assert late_hits / late >= 0.8
    assert normal_flags / normal_total <= 0.02
    assert pattern_ok / late_hits >= 0.9


def test_features_have_no_employer_and_include_phone_apps():
    assert not any("employer" in name for name in FEATURE_NAMES) and "n_phone_apps" in FEATURE_NAMES


# ---------- через API и конвейер ----------
async def submit_and_wait(env, client, **overrides):
    resp = await env.submit(good_form(**overrides), client)
    assert resp.status_code == 201, resp.text
    await env.drain()
    return (await env.client.get(f"/api/v1/applications/{resp.json()['app_id']}", headers=await env.login("uw"))).json()


async def test_factory_is_flagged_but_not_rejected(env):
    """Шесть разных людей (свои телефоны и паспорта) с ОДНОГО устройства подряд: схема «кредитная фабрика»."""
    results = []
    for n in range(1, 7):
        results.append(await submit_and_wait(env, env.make_client(n), passport_hash=hash16(n), client_device_id="farm-device-1",
                                             income=60000 + n * 500, amount=300000))
    flagged = [r for r in results if r["fraud"] and r["fraud"]["flagged"]]
    assert flagged, "хотя бы поздние заявки фабрики должны быть помечены"
    for r in flagged:
        assert r["status"] == "MANUAL_REVIEW" and r["decision"]["reason"] == "FRAUD_SUSPECT"  # предупреждение, не отказ
        assert r["fraud"]["pattern"] == "fabric" and "устройства" in r["fraud"]["text"]
    assert all(r["status"] != "REFUSED" for r in results)
    # клиент помеченной заявки не узнаёт про подозрение
    mine = (await env.client.get(f"/api/v1/applications/{flagged[0]['app_id']}", headers=env.make_client(int(flagged[0]["client_id"][4:])))).json()
    assert "fraud" not in mine and mine["decision"]["reason"] == "LOW_SCORE"


async def test_single_honest_application_is_not_flagged(env, client):
    r = await submit_and_wait(env, client, client_device_id="my-phone-1")
    assert r["fraud"]["flagged"] is False and r["status"] == "APPROVED"


async def test_carousel_one_phone_many_passports(env):
    """Один клиент (телефон) подаёт заявки по разным паспортам. Заявки идут по очереди (иначе дубликат нет: паспорта разные)."""
    client = env.make_client(1)
    results = [await submit_and_wait(env, client, passport_hash=hash16(100 + i), income=50000 + i * 1000) for i in range(6)]
    assert results[-1]["fraud"]["component_size"] >= 5  # телефон связал заявки в одну группу
    assert any(r["fraud"]["flagged"] for r in results) and all(r["status"] != "REFUSED" for r in results)


async def test_probing_income_spread_grows_for_same_passport(env, client):
    """Один паспорт, растущий заявленный доход: признак income_spread (угроза U-4) растёт, рёбра passport есть."""
    env.state.settings.duplicate_check = False  # помеченную заявку (MANUAL_REVIEW) иначе не дали бы подать следующую
    for i in range(4):
        await submit_and_wait(env, client, income=40000 * (1.25 ** i) // 1, amount=100000)
    engine = env.state.antifraud
    last = engine.graph.nodes[f"app_000{4}"]
    vec = build_vector(engine.graph, last)
    assert vec["income_spread"] > 0.5 and vec["n_same_passport"] == 3
    assert any(e.kind == "passport" for e in engine.graph.edges)


async def test_accepted_counter_offer_is_not_probing(env, client, uw):
    d = await submit_and_wait(env, client, income=50000, amount=500000, term=12)  # HIGH_DTI → предложения
    resp = await env.client.post(f"/api/v1/applications/{d['app_id']}/accept-offer", json={"offer_index": 0},
                                 headers=client | {"Idempotency-Key": "acc"})
    await env.drain()
    child = (await env.client.get(f"/api/v1/applications/{resp.json()['app_id']}", headers=uw)).json()
    assert child["fraud"] is None and {s["stage"]: s["status"] for s in child["stages"]}["antifraud"] == "skipped"
    assert child["status"] == "APPROVED"


async def test_graph_contains_no_plain_personal_data(env, uw):
    clients = [env.make_client(n) for n in range(1, 4)]
    for n, c in enumerate(clients, 1):
        await submit_and_wait(env, c, passport_hash=hash16(7000 + n), client_device_id=f"dev-secret-{n}")
    rows = env.state.db.query_all("SELECT * FROM fraud_nodes")
    assert len(rows) == 3
    blob = " ".join(str(v) for r in rows for v in dict(r).values()) + str((await env.client.get(
        "/api/v1/antifraud/graph", headers=uw)).json())
    for secret in (hash16(7001), "dev-secret-1", "phone-hash-1", "999"):
        assert secret not in blob
    assert all(len(r["passport"]) == 24 for r in rows)  # HMAC-отпечатки


async def test_graph_endpoint_roles(env, client, uw, auditor, admin):
    for headers in (uw, auditor, admin):
        r = await env.client.get("/api/v1/antifraud/graph", headers=headers)
        assert r.status_code == 200 and set(r.json()) == {"nodes", "edges", "clusters"}
    assert (await env.client.get("/api/v1/antifraud/graph", headers=client)).status_code == 403


async def test_graph_is_restored_after_restart(env):
    for n in range(1, 4):
        await submit_and_wait(env, env.make_client(n), passport_hash=hash16(n), client_device_id="shared-dev")
    before = env.state.antifraud
    reloaded = AntifraudEngine(env.state.db, env.state.settings.pii_pepper, env.state.settings.fraud_threshold)
    assert set(reloaded.graph.nodes) == set(before.graph.nodes)
    assert len(reloaded.graph.edges) == len(before.graph.edges) > 0


# ---------- симулятор ----------
def test_simulated_applications_are_valid_and_clients_are_fake(env):
    sim = SimClients(env.state, random.Random(1))
    events = plan_events(random.Random(2), Ids(start=10**6), 1e6, 60, "mixed")
    ids = set()
    for event in events:
        app = sim.application_for(event)  # внутри assert, что анкета проходит validate_for_api
        assert len(app.passport_hash) == 16 and set(app.passport_hash) <= set("0123456789abcdef")
        ids.add(sim.client_for(event.phone))
    rows = env.state.db.query_all("SELECT client_id, phone_masked FROM clients")
    assert {r["client_id"] for r in rows} == ids and all(c.startswith("cli_5") for c in ids)
    # телефоны в БД — только фиктивный хэш
    assert all("999" not in r["phone_masked"] for r in rows)


async def test_simulate_scenarios_and_labels(env, admin):
    r = await env.client.post("/api/v1/simulate", json={"count": 40, "scenario": "mixed"}, headers=admin)
    assert r.status_code == 202
    while env.state.sim_running:
        await asyncio.sleep(0.01)
    await env.drain()
    labels = (await env.client.get("/api/v1/simulate/labels", headers=admin)).json()
    assert len(labels) == 40 and set(labels.values()) == {"normal", "fabric", "carousel", "probing"}
    apps = env.state.db.query_all("SELECT status, channel FROM applications")
    assert len(apps) == 40 and all(a["status"] != "PROCESSING" and a["channel"] == "simulator" for a in apps)
    # заявки симулятора идут под клиентами симулятора, а журнал помнит, кто запустил
    created = [x for x in env.state.db.query_all("SELECT actor, role FROM audit_log WHERE action = 'application_created'")]
    assert {(x["actor"], x["role"]) for x in created} == {("admin", "admin")}
    graph = (await env.client.get("/api/v1/antifraud/graph", headers=admin)).json()
    assert len(graph["nodes"]) == 40 and graph["edges"]


async def test_only_one_simulation_at_a_time_and_validation(env, admin):
    env.state.settings.simulate_max_pause_ms = 50
    assert (await env.client.post("/api/v1/simulate", json={"count": 30}, headers=admin)).status_code == 202
    assert (await env.client.post("/api/v1/simulate", json={"count": 5}, headers=admin)).status_code == 409
    while env.state.sim_running:
        await asyncio.sleep(0.01)
    await env.drain()
    for bad in ({"count": 0}, {"count": 301}, {"count": 5, "scenario": "x"}, {"count": 5.5}, {}):
        assert (await env.client.post("/api/v1/simulate", json=bad, headers=admin)).status_code == 422, bad


async def test_consecutive_simulations_do_not_share_identities(env, admin):
    for _ in range(2):
        await env.client.post("/api/v1/simulate", json={"count": 12, "scenario": "normal"}, headers=admin)
        while env.state.sim_running:
            await asyncio.sleep(0.01)
        await env.drain()
    hashes = [r["passport_hash"] for r in env.state.db.query_all("SELECT passport_hash FROM applications")]
    assert len(hashes) == 24 and len(set(hashes)) == 24
