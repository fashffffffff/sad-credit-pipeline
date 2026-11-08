"""Защита входа, лимиты, дубликаты заявок, идемпотентность, лимит тела, заголовки безопасности."""

import asyncio
import time

import jwt

from backend.app import audit
from backend.tests.conftest import TEST_PASSWORDS, good_form

LOGIN = "/api/v1/auth/staff/login"


async def attempt(env, user="uw", password="wrong"):
    return await env.client.post(LOGIN, json={"username": user, "password": password})


def actions(env):
    return [r["action"] for r in audit.list_records(env.state.db, 1000)]


# ---------- блокировка входа ----------
async def test_five_wrong_passwords_lock_login_for_15_minutes(env):
    """Попытки 1–4 → 401; 5-я неверная → 429 (блок); с верным паролем во время блока тоже 429; через 15 минут вход снова работает."""
    now = [1000.0]
    env.state.clock = lambda: now[0]
    for _ in range(4):
        assert (await attempt(env)).status_code == 401
        now[0] += 10  # 4 попытки за ~40 секунд, чтобы не упереться в лимит по IP
    fifth = await attempt(env)
    assert fifth.status_code == 429 and fifth.json()["error_code"] == "ACCOUNT_LOCKED"
    assert (await attempt(env, password=TEST_PASSWORDS["uw"])).status_code == 429
    now[0] += 14 * 60
    assert (await attempt(env, password=TEST_PASSWORDS["uw"])).status_code == 429
    now[0] += 2 * 60
    assert (await attempt(env, password=TEST_PASSWORDS["uw"])).status_code == 200
    records = audit.list_records(env.state.db, 1000)
    assert [r["details"]["why"] for r in records if r["action"] == "login_failed"].count("bad_credentials") == 4
    assert any(r["action"] == "login_failed" and r["details"]["why"] == "locked" for r in records)
    assert "login" in actions(env)


async def test_unknown_login_and_wrong_password_look_the_same(env):
    wrong_pass = await attempt(env, "uw", "xxx")
    wrong_login = await attempt(env, "nobody", "xxx")
    assert wrong_pass.status_code == wrong_login.status_code == 401
    assert wrong_pass.json() == wrong_login.json() and wrong_pass.json()["error_code"] == "BAD_CREDENTIALS"
    for _ in range(3):
        await attempt(env, "nobody", "xxx")
    assert (await attempt(env, "nobody", "xxx")).status_code == 429  # блок и для несуществующего логина


async def test_other_users_are_not_blocked(env):
    for _ in range(5):
        await attempt(env)
    assert (await attempt(env, "audit", TEST_PASSWORDS["audit"])).status_code == 200


async def test_ip_rate_limit(env):
    now = [5000.0]
    env.state.clock = lambda: now[0]
    for i in range(10):
        assert (await attempt(env, f"user{i}", "x")).status_code == 401
    limited = await attempt(env, "admin", TEST_PASSWORDS["admin"])
    assert limited.status_code == 429 and limited.json()["error_code"] == "TOO_MANY_REQUESTS"
    now[0] += 61
    assert (await attempt(env, "admin", TEST_PASSWORDS["admin"])).status_code == 200


async def test_parallel_wrong_passwords_cannot_beat_lockout(env):
    results = await asyncio.gather(*[attempt(env) for _ in range(8)])
    assert sum(1 for r in results if r.status_code == 401) <= 4  # 5-я и дальше — уже 429
    assert any(r.status_code == 429 for r in results)


async def test_successful_login_resets_counter(env):
    now = [100.0]
    env.state.clock = lambda: now[0]
    for _ in range(3):
        await attempt(env)
        now[0] += 10
    assert (await attempt(env, password=TEST_PASSWORDS["uw"])).status_code == 200
    now[0] += 10
    for _ in range(4):
        assert (await attempt(env)).status_code == 401
        now[0] += 10


async def test_long_password_is_401_not_500(env):
    assert (await attempt(env, "uw", "я" * 60)).status_code == 401  # > 72 байт в bcrypt
    r = await attempt(env, "uw", "x" * 300)
    assert r.status_code == 422  # длина ограничена схемой


# ---------- токены ----------
async def test_token_lives_60_minutes(env):
    token = (await env.login("uw"))["Authorization"].split()[1]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert 3500 <= claims["exp"] - time.time() <= 3600 and claims["sub"] == "uw"


async def test_logout_all_invalidates_old_tokens(env):
    old, other = await env.login("uw"), await env.login("audit")
    assert (await env.client.get("/api/v1/auth/me", headers=old)).status_code == 200
    assert (await env.client.post("/api/v1/auth/logout-all", headers=old)).status_code == 200
    assert (await env.client.get("/api/v1/auth/me", headers=old)).status_code == 401
    assert (await env.client.get("/api/v1/applications", headers=old)).status_code == 401
    assert (await env.client.get("/api/v1/auth/me", headers=other)).status_code == 200
    fresh = await env.login("uw")
    assert (await env.client.get("/api/v1/auth/me", headers=fresh)).status_code == 200
    assert "logout_all" in actions(env)


async def test_client_token_dies_when_token_version_changes(env):
    """Токен клиента проверяется через client_auth.client_token_version (версия в таблице clients)."""
    headers = env.make_client(1)
    assert (await env.client.get("/api/v1/auth/me", headers=headers)).json() == {"username": "cli_0001", "role": "client"}
    env.state.db.execute("UPDATE clients SET token_version = token_version + 1 WHERE client_id = 'cli_0001'")
    assert (await env.client.get("/api/v1/auth/me", headers=headers)).status_code == 401
    ghost = jwt.encode({"sub": "cli_7777", "role": "client", "tv": 0, "exp": time.time() + 600},
                       env.state.settings.jwt_secret, algorithm="HS256")
    assert (await env.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ghost}"})).status_code == 401


# ---------- тело запроса и заголовки ----------
async def test_body_limit_64kb(env, client):
    big = good_form() | {"client_device_id": "a" * 70_000}
    resp = await env.submit(big, client)
    assert resp.status_code == 413 and resp.json()["error_code"] == "PAYLOAD_TOO_LARGE"
    assert (await env.submit(good_form(), client)).status_code == 201
    await env.drain()


async def test_security_headers_and_no_cache(env):
    resp = await env.client.get("/api/v1/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"
    assert (await env.client.get("/health")).status_code == 200  # и без /api/v1


# ---------- одна активная заявка на паспорт ----------
async def test_duplicate_application_for_same_passport(env, client):
    """Пока первая заявка в PROCESSING или MANUAL_REVIEW — вторая по тому же passport_hash → 409."""
    env.state.settings.stage_delay_ms = 50  # чтобы первая ещё была в работе
    first = await env.submit(good_form(), client)
    second = await env.submit(good_form(), client)
    assert second.status_code == 409 and second.json()["error_code"] == "DUPLICATE_APPLICATION"
    assert second.json()["details"]["existing_app_id"] == first.json()["app_id"]
    await env.drain()
    # одобренная заявка не блокирует новую
    assert (await env.submit(good_form(), client)).status_code == 201
    await env.drain()
    # ручная проверка блокирует
    env.world.bki_default = {"score": 580, "delinquency": False, "requests_90d": 1}
    held = await env.run(good_form(passport_hash="aaaaaaaaaaaaaaaa", age=24, employment="student", family="single",
                                   income=45000, amount=100000, product_id="prod_express"), client)
    assert held["status"] == "MANUAL_REVIEW"
    again = await env.submit(good_form(passport_hash="aaaaaaaaaaaaaaaa"), client)
    assert again.status_code == 409


async def test_duplicate_check_can_be_switched_off(env, client):
    env.state.settings.duplicate_check = False
    env.state.settings.stage_delay_ms = 30
    assert (await env.submit(good_form(), client)).status_code == 201
    assert (await env.submit(good_form(), client)).status_code == 201
    await env.drain()


async def test_concurrent_same_passport_creates_one_application(env):
    env.state.settings.stage_delay_ms = 30
    clients = [env.make_client(n) for n in range(1, 6)]
    results = await asyncio.gather(*[env.submit(good_form(), c) for c in clients])
    assert sorted(r.status_code for r in results).count(201) == 1
    await env.drain()


# ---------- идемпотентность ----------
async def test_idempotent_retry_is_not_a_duplicate(env, client):
    r1 = await env.submit(good_form(), client, key="same-key")
    r2 = await env.submit(good_form(), client, key="same-key")
    assert r1.status_code == r2.status_code == 201 and r1.json() == r2.json()
    other = await env.submit(good_form(amount=400000), client, key="same-key")
    assert other.status_code == 409 and other.json()["error_code"] == "IDEMPOTENCY_CONFLICT"
    await env.drain()
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM applications")["n"] == 1


async def test_idempotency_key_required_and_validated(env, client):
    r = await env.client.post("/api/v1/applications", json=good_form(), headers=client)
    assert r.status_code == 422 and "Idempotency-Key" in r.json()["details"]["fields"]
    for bad in ("a b", "x" * 200, "a;b", "a/b"):
        r = await env.client.post("/api/v1/applications", json=good_form(), headers=client | {"Idempotency-Key": bad})
        assert r.status_code == 422, bad


async def test_concurrent_same_idempotency_key_creates_one_application(env, client):
    results = await asyncio.gather(*[env.submit(good_form(), client, key="race") for _ in range(6)])
    assert {r.status_code for r in results} <= {201, 409}
    await env.drain()
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM applications")["n"] == 1


async def test_idempotency_keys_are_per_user(env):
    a, b = env.make_client(1), env.make_client(2)
    ra = await env.submit(good_form(), a, key="k")
    await env.drain()
    rb = await env.submit(good_form(passport_hash="bbbbbbbbbbbbbbbb"), b, key="k")
    assert ra.json()["app_id"] != rb.json()["app_id"]
    await env.drain()


# ---------- частота подачи ----------
async def test_application_creation_is_rate_limited(env, client):
    env.state.settings.create_per_minute = 3
    env.state.settings.duplicate_check = False
    codes = [(await env.submit(good_form(), client)).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]
    await env.drain()


# ---------- сквозная нумерация ----------
async def test_ids_are_sequential(env, client):
    env.state.settings.duplicate_check = False
    ids = [(await env.submit(good_form(), client)).json()["app_id"] for _ in range(3)]
    assert ids == ["app_0001", "app_0002", "app_0003"]
    await env.drain()
    events = audit.list_records(env.state.db, 1000)
    assert {e["event_id"] for e in events} >= {"evt_0001", "evt_0002"}
