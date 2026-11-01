"""Тесты по результатам аудита безопасности (см. docs/SECURITY_AUDIT.md).

Каждый тест соответствует находке: сначала атака воспроизводилась на живой системе, потом писалась правка.
Тесты «проверено, не уязвимо» тоже здесь — чтобы защита не пропала при будущих правках.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.app import audit
from backend.app.config import load_settings
from backend.app.events import MAX_STREAMS_PER_USER, EventBus, TooManyStreams
from backend.app.routes.misc import user_from_ticket
from backend.app.schemas import ApplicationIn
from backend.app.errors import ApiError
from backend.app.security.passwords import check_password, hash_password
from backend.app.services import create_application
from backend.tests.conftest import GOOD_PASSPORT, TEST_CREDENTIALS, TEST_PASSWORDS, good_form, make_env
from common.secrets_guard import check_secret
from common.signing import sign, verify

STRONG = "x" * 40


def actions(env) -> list[str]:
    return [r["action"] for r in audit.list_records(env.state.db, 1000)]


# ---------- 1. Секреты по умолчанию ----------
def test_production_refuses_default_secrets(monkeypatch):
    """Забыли env при APP_ENV=production → сервер не стартует (раньше JWT подписывался публичным секретом из репозитория)."""
    for name in ("JWT_SECRET", "SMEV_HMAC_SECRET", "BKI_HMAC_SECRET", "PII_PEPPER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        load_settings()
    for name in ("JWT_SECRET", "SMEV_HMAC_SECRET", "BKI_HMAC_SECRET", "PII_PEPPER"):
        monkeypatch.setenv(name, STRONG + name)
    assert load_settings().production is True


def test_production_refuses_short_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError):
        check_secret("JWT_SECRET", "short-but-not-default")


def test_dev_warns_loudly_but_starts(monkeypatch, caplog):
    """В разработке демо не ломаем: дефолтные секреты работают, но в лог летит предупреждение."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with caplog.at_level(logging.WARNING, logger="security"):
        settings = load_settings()
    assert settings.production is False
    assert any("JWT_SECRET" in r.message and "ВНИМАНИЕ" in r.message for r in caplog.records)


def test_empty_secret_refused_even_in_dev(monkeypatch):
    """Пустой секрет (JWT_SECRET= в .env) = подпись, которую умеет считать любой: отказ в любом режиме."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("JWT_SECRET", "")
    with pytest.raises(RuntimeError, match="пуст"):
        load_settings()


def test_hmac_with_empty_secret_is_refused():
    body = b"{}"
    with pytest.raises(ValueError):
        sign("", body)
    forged = sign("real-secret", body)
    assert verify("", body, forged["X-Signature"], forged["X-Timestamp"]) is False


def test_hmac_non_ascii_signature_does_not_crash():
    """Заголовок X-Signature с не-ASCII символами раньше давал TypeError в compare_digest → 500."""
    stamp = sign("s", b"x")["X-Timestamp"]
    assert verify("s", b"x", "подпись-é", stamp) is False


async def test_docs_hidden_in_production(tmp_path):
    env = make_env(tmp_path, production=True)
    try:
        assert (await env.client.get("/docs")).status_code == 404
        assert (await env.client.get("/openapi.json")).status_code == 404
    finally:
        await env.client.aclose()


# ---------- 2. JWT ----------
def _claims(**extra):
    return {"sub": "admin", "role": "admin", "tv": 0,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5)} | extra


async def test_forged_jwt_variants_rejected(env):
    """Проверено, не уязвимо: alg=none, чужой секрет, другой алгоритм, токен без exp, токен без sub."""
    secret = env.state.settings.jwt_secret
    forged = {
        "alg_none": jwt.encode(_claims(), key=None, algorithm="none"),
        "wrong_secret": jwt.encode(_claims(), "not-the-secret-0000000000000000000", algorithm="HS256"),
        "hs512": jwt.encode(_claims(), secret, algorithm="HS512"),
        "no_exp": jwt.encode({"sub": "admin", "role": "admin", "tv": 0}, secret, algorithm="HS256"),
        "no_sub": jwt.encode({"exp": _claims()["exp"], "role": "admin", "tv": 0}, secret, algorithm="HS256"),
        "expired": jwt.encode(_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=5)), secret, algorithm="HS256"),
    }
    for name, token in forged.items():
        resp = await env.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, name


async def test_token_role_claim_is_ignored(env):
    """Проверено, не уязвимо: роль берётся из БД. Токен оператора с role=admin (подписан верно) всё равно оператор."""
    token = jwt.encode(_claims(sub="operator", role="admin"), env.state.settings.jwt_secret, algorithm="HS256")
    resp = await env.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.json() == {"username": "operator", "role": "operator"}


# ---------- 3. SSE: билет вместо токена в адресе ----------
async def test_sse_ticket_flow(env, operator):
    """JWT в query (?token=) попадал в access-лог uvicorn/nginx. Теперь поток открывается по одноразовому билету."""
    state = env.state
    # старый способ больше не работает, даже с настоящим токеном
    jwt_token = operator["Authorization"].split()[1]
    assert (await env.client.get(f"/api/events/stream?token={jwt_token}")).status_code == 401
    # билет выдаётся только по заголовку Authorization
    assert (await env.client.post("/api/events/ticket")).status_code == 401
    ticket = (await env.client.post("/api/events/ticket", headers=operator)).json()["ticket"]
    assert ticket != jwt_token and "." not in ticket
    user = user_from_ticket(state, ticket)
    assert (user.username, user.role) == ("operator", "operator")
    with pytest.raises(ApiError):  # билет одноразовый
        user_from_ticket(state, ticket)


async def test_sse_ticket_expires_and_dies_on_logout_all(env, operator):
    state = env.state
    ticket = (await env.client.post("/api/events/ticket", headers=operator)).json()["ticket"]
    state.clock = lambda: __import__("time").time() + 31
    with pytest.raises(ApiError):
        user_from_ticket(state, ticket)
    state.clock = __import__("time").time

    ticket = (await env.client.post("/api/events/ticket", headers=operator)).json()["ticket"]
    await env.client.post("/api/auth/logout-all", headers=operator)
    with pytest.raises(ApiError):
        user_from_ticket(state, ticket)


# ---------- 4. SSE: кто какие события видит ----------
def test_sse_visibility_by_role():
    """Оператор не получает чужие события заявок и события безопасности (логины, пути запросов); аудитор — получает."""
    bus = EventBus()
    op, other_op = bus.subscribe("operator", "operator"), bus.subscribe("op2", "operator")
    auditor, admin = bus.subscribe("auditor", "auditor"), bus.subscribe("admin", "admin")
    bus.publish("decision", {"application_id": "app_1"}, owner="operator")
    bus.publish("security", {"kind": "forbidden"})
    got = {name: [e[0] for e in iter(lambda q=q: q.get_nowait() if not q.empty() else None, None)]
           for name, q in {"op": op, "other": other_op, "auditor": auditor, "admin": admin}.items()}
    assert got == {"op": ["decision"], "other": [], "auditor": ["decision", "security"],
                   "admin": ["decision", "security"]}


def test_sse_stream_limit_and_kick():
    bus = EventBus()
    queues = [bus.subscribe("u", "operator") for _ in range(MAX_STREAMS_PER_USER)]
    with pytest.raises(TooManyStreams):
        bus.subscribe("u", "operator")
    bus.subscribe("other", "operator")  # лимит на пользователя, а не общий
    bus.kick("u")
    assert all(q.get_nowait()[0] == "__close__" for q in queues)


async def test_pipeline_events_carry_owner(env, operator):
    """События конвейера помечены владельцем заявки — иначе фильтровать для оператора было бы нечем."""
    seen_by_stranger = env.state.bus.subscribe("someone_else", "operator")
    seen_by_owner = env.state.bus.subscribe("operator", "operator")
    resp = await env.submit(good_form(), operator)
    assert resp.status_code == 202
    await env.drain()
    assert seen_by_stranger.empty()
    assert {t for t, _ in iter(lambda: seen_by_owner.get_nowait() if not seen_by_owner.empty() else None, None)} \
        >= {"stage", "decision"}


# ---------- 5. Юникод-цифры: обход правила «одна заявка на паспорт» ----------
@pytest.mark.parametrize("field,value", [
    ("passport", "4510 ١٢٣٤٥٦"),             # арабо-индийские цифры
    ("passport", "4510 １２３４５６"),         # полноширинные цифры
    ("phone", "+7999१२३४५६७"),                # деванагари
    ("employer_inn", "٧٧٠٧٠٨٣٨٩٣"),
    ("passport_division_code", "٧٧٠-٠٠١"),
])
async def test_unicode_digits_rejected(env, operator, field, value):
    """Раньше \\d принимал любые юникод-цифры: «4510 ١٢٣٤٥٦» — тот же паспорт, но другой хэш → дубликат проходил."""
    resp = await env.submit(good_form(**{field: value}), operator)
    assert resp.status_code == 422 and field in resp.json()["fields"]


async def test_duplicate_rule_not_bypassed_by_lookalike_digits(env, operator):
    env.state.settings.stage_delay_ms = 200  # первая заявка должна ещё быть в работе
    assert (await env.submit(good_form(), operator)).status_code == 202
    again = await env.submit(good_form(passport="4510 " + "١٢٣٤٥٦"), operator)
    assert again.status_code == 422
    assert (await env.submit(good_form(), operator)).status_code == 409  # честный дубликат по-прежнему 409
    env.state.settings.stage_delay_ms = 0
    await env.drain()


# ---------- 6. Ручное решение только по заявкам на ручной проверке ----------
async def test_review_only_for_manual_review(env):
    """Раньше risk_manager мог «одобрить» заявку, ещё идущую по конвейеру (processing → approved), и обойти проверки."""
    risk = await env.login("risk")
    state = env.state
    data = ApplicationIn.model_validate(good_form())
    app_id = create_application(state, data, "operator", "operator")  # статус processing, конвейер не запущен
    resp = await env.client.post(f"/api/applications/{app_id}/review", json={"decision": "approved"}, headers=risk)
    assert resp.status_code == 409 and resp.json()["error"] == "invalid_transition"
    assert state.db.query_one("SELECT status FROM applications WHERE id = ?", (app_id,))["status"] == "processing"
    assert "manual_review_decision" not in actions(env)


async def test_review_comment_length_limited(env):
    risk = await env.login("risk")
    resp = await env.client.post("/api/applications/app_x/review",
                                 json={"decision": "approved", "comment": "ы" * 1001}, headers=risk)
    assert resp.status_code == 422


# ---------- 7. Кривой и «бомбовый» JSON, мусор в ручках ----------
BOMB = b"[" * 30_000  # < 64 КБ, но json.loads падал с RecursionError → 500


async def test_deep_json_is_422_not_500(env, operator):
    admin = await env.login("admin")
    resp = await env.client.post("/api/applications", content=BOMB,
                                 headers=operator | {"Idempotency-Key": "bomb", "Content-Type": "application/json"})
    assert resp.status_code == 422
    assert (await env.client.post("/api/score/whatif", content=BOMB, headers=operator)).status_code == 422
    assert (await env.client.put("/api/chaos/smev", content=BOMB, headers=admin)).status_code == 422


@pytest.mark.parametrize("raw", [b"{", b"[1]", b'"str"', b"\xff\xfe"])
async def test_garbage_bodies_are_422(env, operator, raw):
    admin = await env.login("admin")
    assert (await env.client.put("/api/chaos/smev", content=raw, headers=admin)).status_code == 422
    assert (await env.client.post("/api/score/whatif", content=raw, headers=operator)).status_code == 422


async def test_chaos_payload_is_whitelisted(env):
    """В аудит и в эмулятор уходит только режим и задержка: раньше туда шло любое тело (до 64 КБ)."""
    admin = await env.login("admin")
    for bad in ({"mode": "nuke"}, {"mode": "slow", "delay_ms": -1}, {"mode": "slow", "delay_ms": 10**9},
                {"mode": "normal", "junk": "y" * 5000}):
        assert (await env.client.put("/api/chaos/smev", json=bad, headers=admin)).status_code == 422
    assert "chaos_change" not in actions(env)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**400, 1e12, -1e12])
async def test_whatif_rejects_absurd_numbers(env, operator, value):
    """NaN, Infinity и 10**400 роняли ручку (500); 1e12 «экстраполировал» модель."""
    body = {"n_loans": value, "bad_months": 0, "has_current_overdue": 0}
    resp = await env.client.post("/api/score/whatif", content=json.dumps(body).encode(), headers=operator)
    assert resp.status_code == 422 and "n_loans" in resp.json()["fields"]


async def test_whatif_normal_values_still_work(env, operator):
    body = {"n_loans": 3, "bad_months": 1, "has_current_overdue": False}
    assert (await env.client.post("/api/score/whatif", json=body, headers=operator)).status_code == 200


async def test_idempotency_key_is_validated(env, operator):
    for key in ("k" * 129, "a b", "a;b", "'; DROP TABLE users;--"):
        resp = await env.client.post("/api/applications", json=good_form(),
                                     headers=operator | {"Idempotency-Key": key})
        assert resp.status_code == 422, key
    ok = await env.client.post("/api/applications", json=good_form(),
                               headers=operator | {"Idempotency-Key": "550e8400-e29b-41d4-a716-446655440000"})
    assert ok.status_code == 202
    await env.drain()


# ---------- 8. Вход: длинные пароли, гонка счётчика, имя в аудите ----------
async def test_long_password_is_401_not_500(env):
    """bcrypt принимает 72 байта; на длинном пароле библиотека падала → 500 (и кто-то мог класть сервис пачкой запросов)."""
    resp = await env.client.post("/api/auth/login", json={"username": "operator", "password": "я" * 60})
    assert resp.status_code == 401
    assert check_password("x" * 100, hash_password("x", 4)) is False
    resp = await env.client.post("/api/auth/login", json={"username": "operator", "password": "x" * 129})
    assert resp.status_code == 422
    resp = await env.client.post("/api/auth/login", json={"username": "u" * 65, "password": "x"})
    assert resp.status_code == 422


async def test_parallel_wrong_passwords_cannot_beat_lockout(env):
    """8 одновременных неверных паролей. Раньше счётчик читался до await bcrypt и писался после: все 8 получали
    проверку пароля. Теперь проверяются только первые 5, остальные сразу 429; верный пароль после блока тоже 429."""
    statuses = await asyncio.gather(*[
        env.client.post("/api/auth/login", json={"username": "auditor", "password": f"bad{i}"}) for i in range(8)])
    codes = sorted(r.status_code for r in statuses)
    assert codes.count(401) <= env.state.settings.login_max_failures - 1
    assert codes.count(429) >= 3
    good = await env.client.post("/api/auth/login", json={"username": "auditor", "password": TEST_PASSWORDS["auditor"]})
    assert good.status_code == 429


async def test_successful_login_resets_counter(env):
    for _ in range(3):
        await env.client.post("/api/auth/login", json={"username": "auditor", "password": "bad"})
    assert (await env.client.post("/api/auth/login", json={"username": "auditor", "password": TEST_PASSWORDS["auditor"]})).status_code == 200
    for _ in range(4):
        assert (await env.client.post("/api/auth/login", json={"username": "auditor", "password": "bad"})).status_code == 401


async def test_audit_does_not_store_unknown_login_text(env):
    """Люди вводят пароль в поле «логин», атакующие — произвольный текст. В аудит такое не пишем."""
    secret = "МойПароль-Hunter2!\nINJECT"
    await env.client.post("/api/auth/login", json={"username": secret, "password": "x"})
    admin = await env.login("admin")
    dump = (await env.client.get("/api/audit", headers=admin)).text
    assert "Hunter2" not in dump and "INJECT" not in dump
    assert "login_failed" in dump


async def test_sql_injection_probes_are_inert(env, operator):
    """Проверено, не уязвимо: все запросы параметризованы."""
    evil = "' OR '1'='1' --"
    assert (await env.client.post("/api/auth/login", json={"username": evil, "password": evil})).status_code == 401
    mine = await env.run_and_get(good_form(), operator)
    assert (await env.client.get("/api/applications", params={"status": evil}, headers=operator)).json() == []
    assert (await env.client.get(f"/api/applications/{evil}", headers=operator)).status_code == 404
    assert (await env.client.get("/api/applications", params={"limit": "1; DROP TABLE users"}, headers=operator)).status_code == 422
    assert [a["id"] for a in (await env.client.get("/api/applications", headers=operator)).json()] == [mine["id"]]
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM users")["n"] == 4


# ---------- 9. RBAC: вся матрица раздела 7 ----------
MATRIX = [  # (метод, путь, тело, роли, которым можно)
    ("GET", "/api/applications", None, {"operator", "risk_manager", "auditor", "admin"}),
    ("POST", "/api/applications/app_x/review", {"decision": "approved"}, {"risk_manager"}),
    ("POST", "/api/score/whatif", {"n_loans": 1}, {"operator", "risk_manager", "admin"}),
    ("GET", "/api/model/features", None, {"operator", "risk_manager", "auditor", "admin"}),
    ("GET", "/api/model/info", None, {"operator", "risk_manager", "auditor", "admin"}),
    ("GET", "/api/stats", None, {"operator", "risk_manager", "auditor", "admin"}),
    ("POST", "/api/simulate", {"count": 0}, {"admin"}),  # count=0 — валидный отказ 422, а не запуск
    ("GET", "/api/chaos/smev", None, {"admin"}),
    ("PUT", "/api/chaos/smev", {"mode": "nuke"}, {"admin"}),
    ("GET", "/api/audit", None, {"auditor", "admin"}),
    ("GET", "/api/audit/verify", None, {"auditor", "admin"}),
    ("POST", "/api/events/ticket", None, {"operator", "risk_manager", "auditor", "admin"}),
]


@pytest.mark.parametrize("method,path,body,allowed", MATRIX, ids=[f"{m} {p}" for m, p, _, _ in MATRIX])
async def test_rbac_matrix_every_route(env, method, path, body, allowed):
    """Каждая ручка × каждая роль: чужой роли — ровно 403, без токена — 401, своей — не 401/403."""
    assert (await env.client.request(method, path, json=body)).status_code == 401
    for role, user in {"operator": "operator", "risk_manager": "risk", "auditor": "auditor", "admin": "admin"}.items():
        headers = await env.login(user)
        status = (await env.client.request(method, path, json=body, headers=headers)).status_code
        if role in allowed:
            assert status not in (401, 403), (role, status)
        else:
            assert status == 403, (role, status)


async def test_auditor_is_read_only(env):
    """Аудитор ничего не меняет: ни заявки, ни решения, ни хаос."""
    auditor = await env.login("auditor")
    assert (await env.submit(good_form(), auditor)).status_code == 403
    for method, path in (("POST", "/api/simulate"), ("PUT", "/api/chaos/bki"), ("POST", "/api/score/whatif")):
        assert (await env.client.request(method, path, json={"mode": "normal", "count": 1}, headers=auditor)).status_code == 403


async def test_operator_cannot_read_foreign_application_via_any_route(env):
    admin, operator = await env.login("admin"), await env.login("operator")
    foreign = await env.run_and_get(good_form(passport="4510 222222"), admin)
    assert (await env.client.get(f"/api/applications/{foreign['id']}", headers=operator)).status_code == 403
    assert foreign["id"] not in (await env.client.get("/api/applications", headers=operator)).text
    assert foreign["id"] not in (await env.client.get("/api/applications?limit=500", headers=operator)).text


# ---------- 10. Утечки данных ----------
async def test_no_plain_pii_anywhere(env, operator):
    """Открытый паспорт, ФИО, телефон: ни в ответах API, ни в БД, ни в аудите (проверка поиском по всей базе)."""
    admin = await env.login("admin")
    app = await env.run_and_get(good_form(passport="4510 987654", phone="+79161112233"), operator)
    dumps = [json.dumps(app, ensure_ascii=False),
             (await env.client.get("/api/audit", headers=admin)).text,
             (await env.client.get("/api/applications", headers=operator)).text]
    for table in ("applications", "stages", "audit_log", "idempotency", "users"):
        dumps.append(json.dumps([dict(r) for r in env.state.db.query_all(f"SELECT * FROM {table}")], ensure_ascii=False))
    blob = "\n".join(dumps)
    for secret in ("987654", "4510 987654", "9161112233", "Иван Иванович", "1995-04-12", "7707083893"):
        assert secret not in blob, secret


def test_validation_error_text_does_not_contain_input():
    """Текст ошибки pydantic раньше содержал input_value='...' — полный паспорт и ФИО попали бы в логи при сбое."""
    with pytest.raises(Exception) as caught:
        ApplicationIn.model_validate(good_form(passport="4510 12345X", full_name="Иванов Иван Иванович"))
    text = str(caught.value)
    assert "12345X" not in text and "Иванов" not in text


async def test_validation_response_does_not_echo_input(env, operator):
    resp = await env.submit(good_form(passport="SECRET-PASSPORT"), operator)
    assert resp.status_code == 422 and "SECRET-PASSPORT" not in resp.text


async def test_model_errors_not_leaked(tmp_path):
    """Текст исключения при загрузке/работе модели (пути к файлам) не уходит ни в /api/health, ни в причины отказа."""
    def broken():
        raise RuntimeError("/Users/secret/path/model.cbm: permission denied")

    env = make_env(tmp_path, model_loader=broken)
    try:
        health = (await env.client.get("/api/health")).json()
        assert "/Users" not in json.dumps(health, ensure_ascii=False)
        operator = await env.login("operator")
        app = await env.run_and_get(good_form(), operator)
        assert "/Users" not in json.dumps(app, ensure_ascii=False)
    finally:
        await env.client.aclose()


async def test_unhandled_error_has_no_traceback(env, operator, monkeypatch):
    """Непредвиденное исключение в ручке: клиент получает короткое «Internal Server Error», без стека и путей."""
    def boom(*a, **k):
        raise RuntimeError("secret internal detail /etc/passwd")

    monkeypatch.setattr(type(env.state.db), "query_all", boom)
    import httpx
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app, raise_app_exceptions=False), base_url="http://test")
    resp = await client.get("/api/stats", headers=operator)
    await client.aclose()
    assert resp.status_code == 500
    assert "secret" not in resp.text and "Traceback" not in resp.text and "/etc/passwd" not in resp.text


async def test_api_responses_not_cacheable_and_cors_is_narrow(env, operator):
    resp = await env.client.get("/api/auth/me", headers=operator)
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    pre = await env.client.options("/api/applications", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in pre.headers  # чужой сайт из браузера API не вызовет
    pre = await env.client.options("/api/applications", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"})
    assert pre.status_code == 400  # DELETE мы не разрешали


# ---------- 11. Учётки: публичных паролей нет нигде (подробные тесты — test_credentials.py) ----------
def test_no_public_demo_users_in_any_mode(tmp_path):
    """Ожидаемый результат: Database без списка учёток не создаёт никого; пароля «логин+123» нет ни у кого."""
    from backend.app.db import Database
    assert Database(str(tmp_path / "a.db"), 4).query_one("SELECT COUNT(*) AS n FROM users")["n"] == 0


def test_load_settings_credentials_file_env(monkeypatch):
    """CREDENTIALS_FILE задаёт путь к секретным учёткам; по умолчанию — secrets/credentials.local.json."""
    monkeypatch.delenv("CREDENTIALS_FILE", raising=False)
    assert load_settings().credentials_file == "secrets/credentials.local.json"
    monkeypatch.setenv("CREDENTIALS_FILE", "/tmp/x.json")
    assert load_settings().credentials_file == "/tmp/x.json"


# ---------- 12. Одновременные запросы (проверено, не уязвимо) ----------
async def test_concurrent_same_idempotency_key_creates_one_application(env, operator):
    headers = operator | {"Idempotency-Key": "same-key"}
    responses = await asyncio.gather(*[env.client.post("/api/applications", json=good_form(), headers=headers)
                                       for _ in range(6)])
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["id"] for r in responses}) == 1
    await env.drain()
    assert env.state.db.query_one("SELECT COUNT(*) AS n FROM applications")["n"] == 1


async def test_concurrent_same_passport_creates_one_application(env, operator):
    env.state.settings.stage_delay_ms = 100
    responses = await asyncio.gather(*[env.submit(good_form(), operator) for _ in range(6)])
    env.state.settings.stage_delay_ms = 0
    assert sorted(r.status_code for r in responses) == [202] + [409] * 5
    await env.drain()


async def test_name_tricks_are_rejected(env, operator):
    """Латиница вместо кириллицы, невидимые символы, RTL-метка, нулевая ширина."""
    for name in ("Ivanov Ivan Ivanovich", "Иванов Иван Иванович", "Иванов\u200b Иван", "Иванов Иван\u202e",
                 "Иванов Иван Иванович\u2066", "Иванов Ivan Иванович"):  # ZWSP, RTL-override, isolate, смесь алфавитов
        ok = name == "Иванов Иван Иванович"
        resp = await env.submit(good_form(full_name=name, passport=f"4510 {abs(hash(name)) % 900000 + 100000}"), operator)
        assert (resp.status_code == 202) == ok, name
    await env.drain()


# ======================= Повторный аудит (01.11) =======================
from backend.app.antifraud.engine import MAX_VIEW_NODES  # noqa: E402
from backend.app.antifraud.graph import MAX_LINKS, FraudGraph, Node  # noqa: E402
from backend.app.credentials import generate_credentials, load_or_create_credentials  # noqa: E402
from backend.app.db import Database  # noqa: E402
from backend.app.security.redaction import GENERIC_FRAUD_TEXT, redact_fraud  # noqa: E402
from backend.tests.test_antifraud import forms  # noqa: E402


async def _factory(env, operator, n=6):
    """n заявок «кредитной фабрики» (общее устройство и работодатель); возвращает ids и подписчика-оператора SSE."""
    sub = env.state.bus.subscribe("operator", "operator")
    ids = []
    for form in forms(n, seed=1, employer_inn="7707083893", client_device_id="farm-pc-0001"):
        resp = await env.submit(form, operator)
        assert resp.status_code == 202, resp.text
        ids.append(resp.json()["id"])
        await env.drain()
    return ids, sub


async def test_operator_does_not_see_antifraud_details(env, operator):
    """Оператор видел оценку, схему и признаки («8 паспортов с одного устройства») и мог подбирать заявки под порог,
    а ещё cluster_id — id чужой заявки. Теперь только «передана на дополнительную проверку»; риск-менеджер видит всё."""
    risk = await env.login("risk")
    ids, sub = await _factory(env, operator)
    as_operator = (await env.client.get(f"/api/applications/{ids[-1]}", headers=operator)).json()
    as_risk = (await env.client.get(f"/api/applications/{ids[-1]}", headers=risk)).json()

    assert as_risk["fraud"]["pattern"] == "fabric" and as_risk["fraud"]["explanation"] and as_risk["fraud"]["cluster_id"]
    fraud = as_operator["fraud"]
    assert fraud["flagged"] is True and fraud["redacted"] is True and fraud["text"] == GENERIC_FRAUD_TEXT
    assert (fraud["fraud_score"], fraud["similarity"], fraud["component_size"]) == (0, 0, 0)
    assert fraud["pattern"] is None and fraud["explanation"] == [] and fraud["cluster_id"] is None

    blob = json.dumps(as_operator, ensure_ascii=False)
    for leak in ("кредитная фабрика", "паспортов с одного устройства", "связанных заявок", as_risk["fraud"]["cluster_id"]):
        assert leak not in blob, leak
    assert [r["text"] for r in as_operator["reasons"] if r["code"] == "FRAUD_SUSPECT"] == [GENERIC_FRAUD_TEXT]
    stages = {s["stage"]: s["detail"] for s in as_operator["stages"]}
    assert stages["antifraud"] == GENERIC_FRAUD_TEXT and "фабрика" not in stages["decision"]

    events = []
    while not sub.empty():
        events.append(sub.get_nowait())
    assert {t for t, _ in events} >= {"fraud", "stage", "decision"}
    wire = json.dumps(events, ensure_ascii=False)
    assert "фабрика" not in wire and "паспортов с одного" not in wire and "связанн" not in wire


async def test_operator_ok_antifraud_result_hides_score(env, operator):
    """Даже «подозрений нет (оценка 7%, группа 1)» оператору не нужна: это тоже обратная связь для подбора."""
    app = await env.run_and_get(forms(1, seed=2)[0], operator)
    assert app["fraud"]["flagged"] is False and app["fraud"]["fraud_score"] == 0
    assert {s["stage"]: s["detail"] for s in app["stages"]}["antifraud"] == "Проверка выполнена"


def test_redact_fraud_none_stays_none():
    assert redact_fraud(None) is None


async def test_antifraud_endpoints_closed_to_operator_and_graph_has_no_pii(env, operator):
    """/api/antifraud/graph и /api/simulate/labels оператору недоступны; в графе только id, статусы и оценки."""
    risk = await env.login("risk")
    await _factory(env, operator, 4)
    assert (await env.client.get("/api/antifraud/graph", headers=operator)).status_code == 403
    assert (await env.client.get("/api/simulate/labels", headers=operator)).status_code == 403
    assert (await env.client.get("/api/simulate/labels", headers=risk)).status_code == 403  # правда о симуляции — только админ
    graph = (await env.client.get("/api/antifraud/graph", headers=risk)).json()
    assert graph["nodes"] and set(graph["nodes"][0]) == {"id", "status", "fraud_score", "ts"}
    text = json.dumps(graph, ensure_ascii=False)
    assert "farm-pc-0001" not in text and "7707083893" not in text


async def test_graph_view_is_capped(env, operator):
    """Ответ графа ограничен самыми свежими узлами: иначе за сутки он вырастает до десятков тысяч и каждый запрос тяжёлый."""
    risk = await env.login("risk")
    graph = env.state.antifraud.graph
    for i in range(MAX_VIEW_NODES + 300):
        graph.add(Node(f"app_bulk{i}", 1000.0 + i, {"phone": f"p{i}"}, arrived=__import__("time").time()))
    view = (await env.client.get("/api/antifraud/graph?minutes=1440", headers=risk)).json()
    assert len(view["nodes"]) == MAX_VIEW_NODES
    assert view["nodes"][-1]["id"] == f"app_bulk{MAX_VIEW_NODES + 299}"  # остались самые свежие


def test_graph_add_does_not_scan_whole_bucket():
    """Общее устройство у тысяч заявок: добавление не должно перебирать всю историю (время росло квадратично)."""
    import time
    g = FraudGraph()
    started = time.perf_counter()
    for i in range(6000):
        g.add(Node(f"a{i}", 1000.0 + i, {"device": "same-device", "phone": f"p{i}"}))
    assert time.perf_counter() - started < 1.0
    assert sum(1 for e in g.edges if e.kind == "device" and e.a == "a5999") == MAX_LINKS


async def test_only_one_simulation_at_a_time(env):
    """Серия запросов «симулировать 300» копила тысячи одновременных конвейеров. Теперь пока идёт одна — 409."""
    admin = await env.login("admin")
    env.state.settings.simulate_max_pause_ms = 50
    first = await env.client.post("/api/simulate", json={"count": 5}, headers=admin)
    second = await env.client.post("/api/simulate", json={"count": 5}, headers=admin)
    assert (first.status_code, second.status_code) == (202, 409) and second.json()["error"] == "simulation_running"
    await env.drain()
    assert env.state.sim_running is False
    assert (await env.client.post("/api/simulate", json={"count": 3}, headers=admin)).status_code == 202  # после конца можно снова
    await env.drain()


async def test_application_creation_is_rate_limited(env, operator):
    env.state.settings.create_per_minute = 3
    env.state.settings.duplicate_check = False
    codes = [(await env.submit(good_form(), operator)).status_code for _ in range(5)]
    assert codes == [202, 202, 202, 429, 429]
    # повтор по тому же Idempotency-Key — не новая заявка и лимитом не считается
    replay = await env.submit(good_form(), operator, key="key-1")
    assert replay.status_code == 202
    await env.drain()
    other = await env.login("admin")  # лимит на пользователя, а не общий
    assert (await env.submit(good_form(), other)).status_code == 202
    await env.drain()


@pytest.mark.parametrize("value", [None, "", "a" * 7, "a" * 65, "ааааааааа", "device id 1", "device\n-id-1", "device٠٠٠٠٠٠٠٠",
                                   "../../etc/passwd", "<script>alert(1)</script>", 12345678, ["abcdefgh"], True])
async def test_client_device_id_garbage(env, operator, value):
    """Идентификатор устройства: только ASCII [A-Za-z0-9._-] 8–64; всё остальное 422, не 500 и не в граф."""
    resp = await env.submit(good_form(client_device_id=value), operator)
    assert resp.status_code == (202 if value is None else 422), (value, resp.text)  # None — заявка из офиса
    await env.drain()


@pytest.mark.parametrize("body", [{"count": 5, "scenario": "x"}, {"count": 5, "scenario": None},
                                  {"count": 5, "scenario": ["normal"]}, {"count": 0}, {"count": 301}, {"count": -1}])
async def test_simulate_input_validation(env, body):
    admin = await env.login("admin")
    resp = await env.client.post("/api/simulate", json=body, headers=admin)
    assert resp.status_code == 422, (body, resp.text)
    assert env.state.sim_running is False  # неудачный запрос не должен «занять» симуляцию


@pytest.mark.parametrize("field", ["birth_date", "passport_issue_date"])
@pytest.mark.parametrize("value", ["0001-01-01", "9999-12-31", "1900-02-29", "1995-04-31", "20150520",
                                   "１９９５-０４-１２", 20150520, "", None])
async def test_date_fields_never_500(env, operator, field, value):
    """Граничные даты (год 1, год 9999, несуществующее 29 февраля, юникод-цифры) — 422, а не 500."""
    resp = await env.submit(good_form(**{field: value}), operator)
    assert resp.status_code == 422, (field, value, resp.status_code)


@pytest.mark.parametrize("field", ["monthly_income", "loan_amount", "loan_term_months", "dependents", "employment_months"])
@pytest.mark.parametrize("value", [-1, 0, 1.5, "10", True, None, 10**30, float("nan"), float("inf")])
async def test_number_fields_never_500(env, operator, field, value):
    raw = json.dumps(good_form(**{field: value})).encode()
    resp = await env.client.post("/api/applications", content=raw, headers=operator | {
        "Idempotency-Key": "num-key", "Content-Type": "application/json"})
    assert resp.status_code in (202, 422)
    await env.drain()


# ---------- учётки сотрудников ----------
def test_credentials_file_created_private(tmp_path, caplog):
    """Файл с паролями создаётся сразу с правами 600, папка — 700; пароли не попадают в лог."""
    import os, stat
    path = tmp_path / "secrets" / "credentials.local.json"
    with caplog.at_level(logging.DEBUG):
        creds = load_or_create_credentials(str(path))
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.with_suffix(".md").stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert all(c["password"] not in caplog.text for c in creds)


def test_credentials_file_with_open_permissions_is_tightened(tmp_path):
    import os, stat
    if os.name != "posix":
        pytest.skip("права POSIX")
    path = tmp_path / "creds.json"
    path.write_text(json.dumps(generate_credentials()))
    path.chmod(0o644)
    load_or_create_credentials(str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_credentials_with_unknown_role_refused(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps([{"username": "x.1", "role": "superadmin", "password": "p" * 20}]))
    with pytest.raises(RuntimeError, match="роль"):
        load_or_create_credentials(str(path))


def test_user_removed_from_credentials_file_loses_access(tmp_path):
    """Файл учёток пересоздали (новые случайные логины): прежние учётки со старыми паролями не должны остаться в БД."""
    path = str(tmp_path / "a.db")
    first, second = generate_credentials(), generate_credentials()
    Database(path, 4, first).conn.close()
    db = Database(path, 4, second)
    names = {r["username"] for r in db.query_all("SELECT username FROM users")}
    assert names == {c["username"] for c in second}
    assert Database(path, 4, []).query_one("SELECT COUNT(*) AS n FROM users")["n"] == 4  # пустой список — не «удалить всех»


def test_password_change_revokes_tokens(tmp_path):
    path = str(tmp_path / "a.db")
    creds = generate_credentials()
    Database(path, 4, creds).conn.close()
    changed = [{**c, "password": "another-long-password-1"} if c["role"] == "admin" else c for c in creds]
    db = Database(path, 4, changed)
    admin = next(c["username"] for c in creds if c["role"] == "admin")
    assert db.query_one("SELECT token_version FROM users WHERE username = ?", (admin,))["token_version"] == 1
