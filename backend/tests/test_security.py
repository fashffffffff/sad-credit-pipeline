"""RBAC, маскирование PII, журнал аудита, машина статусов."""

import pytest

from backend.app import audit
from backend.app.pipeline.status import InvalidTransition, change_status
from backend.app.security.masking import mask_name, mask_passport, mask_phone, passport_hash
from backend.tests.conftest import GOOD_PASSPORT, good_form


# ---------- RBAC ----------
async def test_rbac_operator_cannot_open_audit(env, operator):
    """Оператор открывает журнал аудита.

    Ожидаемый результат: 403; попытка записана в аудит (access_denied); в SSE пришло событие security/forbidden.
    Аудитор той же ручкой пользуется свободно (200).
    """
    queue = env.state.bus.subscribe()
    resp = await env.client.get("/api/audit", headers=operator)
    assert resp.status_code == 403 and resp.json()["error"] == "forbidden"

    auditor = await env.login("auditor")
    records = (await env.client.get("/api/audit", headers=auditor)).json()
    assert any(r["action"] == "access_denied" and r["actor"] == "operator" for r in records)
    event_type, data = queue.get_nowait()
    assert event_type == "security" and data["kind"] == "forbidden"
    assert (await env.client.get("/api/audit/verify", headers=auditor)).status_code == 200


async def test_rbac_matrix(env):
    """Проверка части матрицы ролей из раздела 6.

    Ожидаемый результат: без токена 401; risk_manager не создаёт заявки (403); operator не запускает
    симуляцию и не меняет хаос (403); admin запускает симуляцию (202); operator не делает ручное решение (403).
    """
    assert (await env.client.get("/api/applications")).status_code == 401
    assert (await env.client.get("/api/applications", headers={"Authorization": "Bearer fake"})).status_code == 401

    risk, operator, admin = await env.login("risk"), await env.login("operator"), await env.login("admin")
    assert (await env.submit(good_form(), risk)).status_code == 403
    assert (await env.client.post("/api/simulate", json={"count": 3}, headers=operator)).status_code == 403
    assert (await env.client.put("/api/chaos/smev", json={"mode": "error"}, headers=operator)).status_code == 403
    assert (await env.client.post("/api/applications/app_x/review", json={"decision": "approved"},
                                  headers=operator)).status_code == 403
    assert (await env.client.post("/api/simulate", json={"count": 3}, headers=admin)).status_code == 202
    await env.drain()


async def test_operator_sees_only_own_applications(env, operator):
    """Оператор и чужие заявки.

    Ожидаемый результат: в списке оператора только его заявки; чужую заявку по id открыть нельзя (403);
    risk_manager видит все.
    """
    admin = await env.login("admin")
    mine = await env.run_and_get(good_form(), operator)
    other = await env.run_and_get(good_form(passport="4510 222222"), admin)
    listed = (await env.client.get("/api/applications", headers=operator)).json()
    assert [a["id"] for a in listed] == [mine["id"]]
    assert (await env.client.get(f"/api/applications/{other['id']}", headers=operator)).status_code == 403
    risk = await env.login("risk")
    assert len((await env.client.get("/api/applications", headers=risk)).json()) == 2


async def test_login_wrong_password_is_audited(env):
    """Вход с неверным паролем.

    Ожидаемый результат: 401 и запись login_failed в аудите; правильный вход пишет login.
    """
    resp = await env.client.post("/api/auth/login", json={"username": "operator", "password": "nope"})
    assert resp.status_code == 401
    await env.login("operator")
    actions = [r["action"] for r in env.state.db.query_all("SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["login_failed", "login"]


# ---------- маскирование ----------
def test_masking_functions():
    """Ожидаемый результат: форматы масок строго как в контракте."""
    assert mask_name("Иванов Иван Иванович") == "Иванов И. И."
    assert mask_passport("4510 123456") == "45** ****56"
    assert mask_phone("+79991234567") == "+7 999 ***-**-67"


async def test_pii_never_leaves_api_or_database(env, operator):
    """Открытые ФИО, паспорт и телефон.

    Ожидаемый результат: в ответах API только маски; в БД (заявки и аудит) нет открытого паспорта,
    телефона и ФИО; хранится passport_hash = HMAC(PII_PEPPER, паспорт).
    """
    app = await env.run_and_get(good_form(), operator)
    assert app["passport_masked"] == "45** ****56"
    assert app["phone_masked"] == "+7 999 ***-**-67"
    assert app["full_name_masked"] == "Иванов И. И."
    listing = (await env.client.get("/api/applications", headers=operator)).text
    for secret in (GOOD_PASSPORT, "123456", "+79991234567", "Иван Иванович"):
        assert secret not in listing and secret not in str(app)

    dump = str([tuple(r) for r in env.state.db.query_all("SELECT * FROM applications")]) + \
        str([tuple(r) for r in env.state.db.query_all("SELECT * FROM audit_log")]) + \
        str([tuple(r) for r in env.state.db.query_all("SELECT * FROM stages")])
    for secret in (GOOD_PASSPORT, "4510 1234", "+79991234567", "Иван Иванович"):
        assert secret not in dump
    stored = env.state.db.query_one("SELECT passport_hash FROM applications")["passport_hash"]
    assert stored == passport_hash(env.state.settings.pii_pepper, GOOD_PASSPORT)
    assert stored != passport_hash("другой-перец", GOOD_PASSPORT)


async def test_passwords_are_bcrypt(env):
    """Ожидаемый результат: в таблице users лежат bcrypt-хэши ($2b$...), а не пароли."""
    hashes = [r["password_hash"] for r in env.state.db.query_all("SELECT password_hash FROM users")]
    assert len(hashes) == 4 and all(h.startswith("$2b$") for h in hashes)


# ---------- аудит ----------
async def test_audit_detects_tampering(env, operator):
    """Подделка записи аудита.

    Ожидаемый результат: до подделки /api/audit/verify -> ok=true, broken_at=null; после прямого UPDATE
    записи в БД -> ok=false и broken_at равен id изменённой записи.
    """
    auditor = await env.login("auditor")
    await env.run_and_get(good_form(), operator)
    clean = (await env.client.get("/api/audit/verify", headers=auditor)).json()
    assert clean["ok"] is True and clean["broken_at"] is None and clean["checked"] >= 4

    env.state.db.execute("UPDATE audit_log SET details = '{\"status\": \"approved\"}' WHERE id = 3")
    broken = (await env.client.get("/api/audit/verify", headers=auditor)).json()
    assert broken["ok"] is False and broken["broken_at"] == 3


async def test_audit_detects_deleted_record(env, operator):
    """Удаление записи из середины журнала.

    Ожидаемый результат: verify -> ok=false, broken_at = id следующей записи (её prev_hash уже не сходится).
    """
    await env.run_and_get(good_form(), operator)
    env.state.db.execute("DELETE FROM audit_log WHERE id = 2")
    result = audit.verify_chain(env.state.db)
    assert result["ok"] is False and result["broken_at"] == 3


# ---------- машина статусов ----------
def test_status_machine_rejects_forbidden_transition(env):
    """Ожидаемый результат: approved -> processing запрещён (InvalidTransition); received -> approved тоже;
    допустимый processing -> approved проходит."""
    db = env.state.db
    for app_id, status in (("a1", "approved"), ("a2", "received"), ("a3", "processing")):
        db.execute("INSERT INTO applications (id, created_at, created_by, status, full_name_masked, passport_hash,"
                   " passport_masked, phone_masked) VALUES (?, 'x', 'u', ?, '', '', '', '')", (app_id, status))
    with pytest.raises(InvalidTransition):
        change_status(db, "a1", "processing")
    with pytest.raises(InvalidTransition):
        change_status(db, "a2", "approved")
    change_status(db, "a3", "approved")
    assert db.query_one("SELECT status FROM applications WHERE id = 'a3'")["status"] == "approved"


async def test_manual_review_flow_and_409(env, operator):
    """Ручное решение риск-менеджера.

    Ожидаемый результат: manual_review -> approved проходит (review заполнен, запись в аудите);
    повторное решение по той же заявке -> 409; решение по заявке в статусе approved -> 409.
    """
    risk = await env.login("risk")
    app = await env.run_and_get(good_form(passport="0000 000004"), operator)
    assert app["status"] == "manual_review"
    url = f"/api/applications/{app['id']}/review"

    resp = await env.client.post(url, json={"decision": "approved", "comment": "проверил вручную"}, headers=risk)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved" and body["review"]["by"] == "risk"
    assert (await env.client.post(url, json={"decision": "rejected"}, headers=risk)).status_code == 409
    assert (await env.client.post(url, json={"decision": "maybe"}, headers=risk)).status_code == 422
    actions = [r["action"] for r in env.state.db.query_all("SELECT action FROM audit_log")]
    assert "manual_review_decision" in actions
