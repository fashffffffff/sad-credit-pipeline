"""Защита входа, дубликаты заявок, лимит тела, заголовки безопасности."""

import jwt

from backend.tests.conftest import GOOD_BKI, TEST_PASSWORDS, good_form, make_env

LOGIN = "/api/auth/login"


async def attempt(env, user="operator", password="wrong"):
    return await env.client.post(LOGIN, json={"username": user, "password": password})


def actions(env):
    return [r["action"] for r in env.state.db.query_all("SELECT action FROM audit_log ORDER BY id")]


# ---------- блокировка входа ----------
async def test_five_wrong_passwords_lock_login_for_15_minutes(env):
    """Подбор пароля.

    Ожидаемый результат: попытки 1–4 → 401; 5-я неверная → 429 (логин заблокирован); с верным паролем во время блокировки
    тоже 429; через 15 минут вход снова работает; блокировка и отказы записаны в аудит.
    """
    now = [1000.0]
    env.state.clock = lambda: now[0]
    for _ in range(4):
        assert (await attempt(env)).status_code == 401
        now[0] += 10  # 4 попытки за ~40 секунд, чтобы не упереться в лимит по IP
    fifth = await attempt(env)
    assert fifth.status_code == 429 and fifth.json()["error"] == "account_locked"
    assert (await attempt(env, password=TEST_PASSWORDS["operator"])).status_code == 429
    now[0] += 14 * 60
    assert (await attempt(env, password=TEST_PASSWORDS["operator"])).status_code == 429
    now[0] += 2 * 60
    assert (await attempt(env, password=TEST_PASSWORDS["operator"])).status_code == 200
    log = actions(env)
    assert "login_locked" in log and "login_blocked" in log and log.count("login_failed") == 4


async def test_unknown_login_and_wrong_password_look_the_same(env):
    """Ожидаемый результат: неверный логин и неверный пароль дают одинаковый ответ (401, то же тело);
    несуществующий логин блокируется так же, как существующий — по блокировке логины не угадать."""
    wrong_pass = await attempt(env, "operator", "xxx")
    wrong_login = await attempt(env, "nobody", "xxx")
    assert wrong_pass.status_code == wrong_login.status_code == 401
    assert wrong_pass.json() == wrong_login.json()
    for _ in range(3):
        await attempt(env, "nobody", "xxx")
    assert (await attempt(env, "nobody", "xxx")).status_code == 429


async def test_other_users_are_not_blocked(env):
    """Ожидаемый результат: блокировка логина operator не мешает войти другому пользователю."""
    for _ in range(5):
        await attempt(env)
    assert (await attempt(env, "auditor", TEST_PASSWORDS["auditor"])).status_code == 200


async def test_ip_rate_limit(env):
    """Не больше 10 попыток входа в минуту с одного IP.

    Ожидаемый результат: 11-я попытка за минуту → 429 (даже с верным паролем); через минуту снова можно.
    """
    now = [5000.0]
    env.state.clock = lambda: now[0]
    for i in range(10):
        assert (await attempt(env, f"user{i}", "x")).status_code == 401
    limited = await attempt(env, "admin", TEST_PASSWORDS["admin"])
    assert limited.status_code == 429 and limited.json()["error"] == "too_many_requests"
    now[0] += 61
    assert (await attempt(env, "admin", TEST_PASSWORDS["admin"])).status_code == 200


# ---------- токены ----------
async def test_token_lives_60_minutes(env):
    """Ожидаемый результат: срок жизни JWT — 60 минут."""
    token = (await env.login("operator"))["Authorization"].split()[1]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert 3500 <= claims["exp"] - __import__("time").time() <= 3600


async def test_logout_all_invalidates_old_tokens(env):
    """Выход со всех устройств.

    Ожидаемый результат: до logout-all токен работает; после — 401 на любой ручке; новый вход выдаёт рабочий токен;
    токены других пользователей не затронуты; событие записано в аудит.
    """
    old = await env.login("operator")
    other = await env.login("auditor")
    assert (await env.client.get("/api/auth/me", headers=old)).status_code == 200
    assert (await env.client.post("/api/auth/logout-all", headers=old)).status_code == 200
    assert (await env.client.get("/api/auth/me", headers=old)).status_code == 401
    assert (await env.client.get("/api/applications", headers=old)).status_code == 401
    assert (await env.client.get("/api/auth/me", headers=other)).status_code == 200
    fresh = await env.login("operator")
    assert (await env.client.get("/api/auth/me", headers=fresh)).status_code == 200
    assert "logout_all" in actions(env)
    assert (await env.client.get(f"/api/events/stream?token={old['Authorization'].split()[1]}")).status_code == 401


# ---------- тело запроса и заголовки ----------
async def test_body_limit_64kb(env, operator):
    """Ожидаемый результат: тело больше 64 КБ → 413 без разбора; нормальная анкета проходит."""
    big = good_form() | {"full_name": "А" * 70_000}
    resp = await env.submit(big, operator)
    assert resp.status_code == 413 and resp.json()["error"] == "payload_too_large"
    assert (await env.submit(good_form(), operator)).status_code == 202
    await env.drain()


async def test_security_headers(env):
    """Ожидаемый результат: в каждом ответе есть X-Content-Type-Options, X-Frame-Options, Referrer-Policy."""
    resp = await env.client.get("/api/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


# ---------- одна заявка на паспорт ----------
async def test_duplicate_application_for_same_passport(env, operator):
    """Одна заявка на паспорт.

    Ожидаемый результат: пока первая заявка ещё в работе или на ручной проверке — вторая по тому же паспорту
    (с другим Idempotency-Key) → 409 duplicate_application с existing_id; отказ меньше 30 дней назад тоже блокирует;
    одобренная заявка не мешает новой.
    """
    env.state.settings.stage_delay_ms = 200  # замедляем конвейер, чтобы первая заявка точно была в работе
    first = await env.submit(good_form(), operator)
    second = await env.submit(good_form(), operator)
    env.state.settings.stage_delay_ms = 0
    assert second.status_code == 409
    body = second.json()
    assert body["error"] == "duplicate_application" and body["existing_id"] == first.json()["id"]
    await env.drain()
    # теперь первая заявка одобрена -> можно подавать новую
    assert (await env.client.get(f"/api/applications/{first.json()['id']}", headers=operator)).json()["status"] == "approved"
    assert (await env.submit(good_form(), operator)).status_code == 202
    await env.drain()

    env.world.bki_override["4510 777777"] = {"found": False, "loans": [], "monthly_debt_payment": None,
                                            "total_debt": None, "inquiries_30d": None, "inquiries_6m": None}
    manual = await env.run_and_get(good_form(passport="4510 777777"), operator)
    assert manual["status"] == "manual_review"
    again = await env.submit(good_form(passport="4510 777777"), operator)
    assert again.status_code == 409 and again.json()["existing_id"] == manual["id"]

    env.world.bki_override["4510 888888"] = GOOD_BKI | {"monthly_debt_payment": 500000}
    rejected = await env.run_and_get(good_form(passport="4510 888888"), operator)
    assert rejected["status"] == "rejected"
    assert (await env.submit(good_form(passport="4510 888888"), operator)).status_code == 409


async def test_idempotent_retry_is_not_a_duplicate(env, operator):
    """Ожидаемый результат: повтор с тем же Idempotency-Key возвращает ту же заявку (202), а не 409 duplicate."""
    a = await env.submit(good_form(), operator, key="k1")
    b = await env.submit(good_form(), operator, key="k1")
    assert a.status_code == b.status_code == 202 and a.json() == b.json()
    await env.drain()


async def test_magic_passports_and_switch_off_skip_duplicate_check(env, operator, tmp_path):
    """Ожидаемый результат: магические паспорта можно подавать сколько угодно раз; при DUPLICATE_CHECK=off
    дубликат по обычному паспорту тоже принимается."""
    for _ in range(3):
        assert (await env.submit(good_form(passport="0000 000001"), operator)).status_code == 202
    await env.drain()
    off = make_env(tmp_path / "off", duplicate_check=False) if (tmp_path / "off").mkdir() is None else None
    operator_off = await off.login("operator")
    assert (await off.submit(good_form(), operator_off)).status_code == 202
    assert (await off.submit(good_form(), operator_off)).status_code == 202
    await off.drain()
    await off.client.aclose()


async def test_simulate_is_not_blocked_by_duplicates_and_data_is_valid(env):
    """Симуляция.

    Ожидаемый результат: все сгенерированные анкеты проходят проверки (ни одна не ушла в сбой конвейера);
    повторные запуски не упираются в дубликаты.
    """
    admin = await env.login("admin")
    for _ in range(2):
        assert (await env.client.post("/api/simulate", json={"count": 25}, headers=admin)).status_code == 202
        await env.drain()
    rows = env.state.db.query_all("SELECT status FROM applications")
    assert len(rows) == 50 and all(r["status"] != "processing" for r in rows)
    errors = env.state.db.query_all("SELECT reasons FROM applications WHERE reasons LIKE '%PIPELINE_ERROR%'")
    assert not errors
