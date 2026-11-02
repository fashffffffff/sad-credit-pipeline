import asyncio
import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from backend.app import audit, client_auth
from backend.app.client_auth import config, oauth, telegram
from backend.app.client_auth.phone import mask_phone, normalize_phone, phone_hash
from backend.app.security.tokens import decode_token
from backend.tests.client_auth.conftest import GOOD_PHONE, GOOD_PHONE_NORM

A = "/api/v1/auth"


# ---------- телефон ----------
@pytest.mark.parametrize("raw", ["89991234567", "+7 999 123 45 67", "8 (999) 123-45-67", "79991234567", "9991234567"])
def test_normalize_ok(raw):
    assert normalize_phone(raw) == GOOD_PHONE_NORM


@pytest.mark.parametrize("raw", ["+7 495 123-45-67", "12345", "+1 999 123 45 67", "abc", "", "+7999123456", "+79991234567 x"])
def test_normalize_bad(raw):
    assert normalize_phone(raw) is None


def test_mask_and_hash_depend_on_pepper(monkeypatch):
    assert mask_phone(GOOD_PHONE_NORM) == "+7 999 ***-**-67"
    h1 = phone_hash(GOOD_PHONE_NORM)
    monkeypatch.setenv("PII_PEPPER", "other-pepper-" + "z" * 32)
    assert phone_hash(GOOD_PHONE_NORM) != h1


# ---------- провайдеры и демо ----------
def test_providers_demo_when_nothing_configured(client):
    p = client.get(f"{A}/providers").json()
    assert p["demo"] is True and p["enabled"] == ["demo"]


def test_demo_off_when_something_configured(client, monkeypatch):
    monkeypatch.setenv("SMSRU_API_ID", "k")
    p = client.get(f"{A}/providers").json()
    assert p["demo"] is False and p["call"] is True
    r = client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "demo"})
    assert r.status_code == 400


def test_demo_disabled_in_production_without_flag(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert client.get(f"{A}/providers").json()["demo"] is False
    assert client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "demo"}).status_code == 400
    monkeypatch.setenv("AUTH_DEMO", "1")
    assert client.get(f"{A}/providers").json()["demo"] is True


def demo_login(client, phone=GOOD_PHONE):
    s = client.post(f"{A}/phone/start", json={"phone": phone, "channel": "demo"})
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["demo"] is True and re.fullmatch(r"\d{6}", body["code"])
    return body, client.post(f"{A}/phone/{body['challenge_id']}/code", json={"code": body["code"]})


def test_demo_success_and_token(client, db, state):
    _, r = demo_login(client)
    assert r.status_code == 200
    data = r.json()
    assert data["client_id"] == "cli_0001" and data["role"] == "client" and data["phone_masked"] == "+7 999 ***-**-67"
    claims = decode_token(config.jwt_secret(), data["access_token"])
    assert claims["sub"] == "cli_0001" and claims["role"] == "client"
    assert client_auth.client_token_version(db, "cli_0001") == claims["tv"]
    assert client_auth.phone_masked_of(db, "cli_0001") == "+7 999 ***-**-67"
    assert client_auth.phone_hash_of(db, "cli_0001") == phone_hash(GOOD_PHONE_NORM)
    # открытый номер нигде в БД не лежит
    dump = json.dumps([dict(r) for t in ("clients", "client_identities", "auth_challenges")
                       for r in db.query_all(f"SELECT * FROM {t}")]) + json.dumps(audit.list_records(db))
    assert "9991234567" not in dump
    log = [x for x in audit.list_records(db) if x["action"] == "login"]
    assert log and log[0]["actor"] == "cli_0001"


def test_demo_wrong_code_five_times_burns_challenge(client, db):
    s = client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "demo"}).json()
    cid, good = s["challenge_id"], s["code"]
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(5):
        assert client.post(f"{A}/phone/{cid}/code", json={"code": wrong}).status_code == 400
    # теперь даже верный код не работает
    assert client.post(f"{A}/phone/{cid}/code", json={"code": good}).status_code == 400
    assert client.get(f"{A}/phone/{cid}").json() == {"status": "expired"}
    assert any(x["action"] == "login_failed" for x in audit.list_records(db))


def test_challenge_expires_after_5_minutes(client, clock):
    s = client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "demo"}).json()
    assert s["expires_in"] == 300
    clock.advance(301)
    assert client.get(f"{A}/phone/{s['challenge_id']}").json()["status"] == "expired"
    assert client.post(f"{A}/phone/{s['challenge_id']}/code", json={"code": s["code"]}).status_code == 400


def test_invalid_phone_and_unknown_challenge(client):
    r = client.post(f"{A}/phone/start", json={"phone": "123", "channel": "demo"})
    assert r.status_code == 422 and r.json()["error_code"] == "INVALID_PHONE"
    assert client.get(f"{A}/phone/nope").json() == {"status": "expired"}
    assert client.post(f"{A}/phone/nope/code", json={"code": "123456"}).status_code == 400


def test_phone_start_limit_3_per_10_minutes(client, clock):
    for _ in range(3):
        assert client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "demo"}).status_code == 200
    assert client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "demo"}).status_code == 429
    clock.advance(601)
    assert client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "demo"}).status_code == 200


def test_ip_limit_per_hour(client, clock, monkeypatch):
    from backend.app.client_auth import config
    monkeypatch.setattr(config, "IP_START_LIMIT", 10)
    for i in range(10):
        r = client.post(f"{A}/phone/start", json={"phone": f"+7 900 000 00 {i:02d}", "channel": "demo"})
        assert r.status_code == 200
    assert client.post(f"{A}/phone/start", json={"phone": "+7 911 000 00 00", "channel": "demo"}).status_code == 429
    clock.advance(3601)
    assert client.post(f"{A}/phone/start", json={"phone": "+7 911 000 00 00", "channel": "demo"}).status_code == 200


# ---------- Telegram ----------
@pytest.fixture
def tg_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "sad_bot")


def tg_start(client, phone=GOOD_PHONE):
    r = client.post(f"{A}/phone/start", json={"phone": phone, "channel": "telegram"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["deep_link"].startswith("https://t.me/sad_bot?start=") and d["expires_in"] == 300
    return d["challenge_id"], d["deep_link"].split("start=")[1]


def run(coro):
    return asyncio.run(coro)


def tg_update(uid, text=None, contact=None, chat=None):
    msg = {"from": {"id": uid}, "chat": {"id": chat or uid, "type": "private"}}
    if text:
        msg["text"] = text
    if contact:
        msg["contact"] = contact
    return {"update_id": 1, "message": msg}


def feed(db, net, *updates):
    sent = []
    net.handler = lambda req: (sent.append(json.loads(req.content)), httpx.Response(200, json={"ok": True}))[1]

    async def go():
        async with config.http_client() as c:
            for u in updates:
                await telegram.handle_update(db, c, u)
    run(go())
    return sent


def test_telegram_success(client, db, net, tg_env):
    cid, nonce = tg_start(client)
    assert client.get(f"{A}/phone/{cid}").json() == {"status": "pending"}
    sent = feed(db, net, tg_update(42, f"/start {nonce}"),
                tg_update(42, contact={"user_id": 42, "phone_number": "79991234567"}))
    assert "request_contact" in json.dumps(sent[0])
    r = client.get(f"{A}/phone/{cid}").json()
    assert r["status"] == "verified" and r["client_id"] == "cli_0001" and r["role"] == "client"
    assert db.query_one("SELECT telegram_chat_id FROM clients")["telegram_chat_id"] == 42
    # токен выдаётся один раз
    assert "access_token" not in client.get(f"{A}/phone/{cid}").json()


def test_telegram_foreign_contact_rejected(client, db, net, tg_env):
    cid, nonce = tg_start(client)
    feed(db, net, tg_update(42, f"/start {nonce}"),
         tg_update(42, contact={"user_id": 777, "phone_number": "79991234567"}))  # контакт друга
    assert client.get(f"{A}/phone/{cid}").json() == {"status": "pending"}
    assert db.query_all("SELECT * FROM clients") == []


def test_telegram_wrong_number_is_mismatch(client, db, net, tg_env):
    cid, nonce = tg_start(client)
    feed(db, net, tg_update(42, f"/start {nonce}"),
         tg_update(42, contact={"user_id": 42, "phone_number": "79997654321"}))
    assert client.get(f"{A}/phone/{cid}").json() == {"status": "mismatch"}
    assert db.query_all("SELECT * FROM clients") == []


def test_telegram_expired_and_stranger_start(client, db, net, tg_env, clock):
    cid, nonce = tg_start(client)
    clock.advance(301)
    sent = feed(db, net, tg_update(42, f"/start {nonce}"))
    assert "устарела" in sent[0]["text"]
    assert client.get(f"{A}/phone/{cid}").json() == {"status": "expired"}
    # контакт без /start ничего не подтверждает
    feed(db, net, tg_update(5, contact={"user_id": 5, "phone_number": "79991234567"}))
    assert db.query_all("SELECT * FROM clients") == []


def test_telegram_second_user_cannot_hijack_nonce(client, db, net, tg_env):
    cid, nonce = tg_start(client)
    feed(db, net, tg_update(42, f"/start {nonce}"), tg_update(99, f"/start {nonce}"))
    assert db.query_one("SELECT tg_user_id FROM auth_challenges")["tg_user_id"] == 42


def test_notify_decision(client, db, net, tg_env, state):
    cid, nonce = tg_start(client)
    feed(db, net, tg_update(42, f"/start {nonce}"),
         tg_update(42, contact={"user_id": 42, "phone_number": "79991234567"}))
    sent = []
    net.handler = lambda req: (sent.append(json.loads(req.content)), httpx.Response(200, json={"ok": True}))[1]
    run(client_auth.notify_decision(state, "cli_0001", "app_0012"))
    assert sent[0]["chat_id"] == 42 and "app_0012" in sent[0]["text"] and "http://localhost:5173" in sent[0]["text"]
    assert not re.search(r"\d{4,}\s*₽|9991234567", sent[0]["text"])
    # клиент без Telegram и сетевая ошибка не роняют
    run(client_auth.notify_decision(state, "cli_9999", "app_1"))
    net.handler = lambda req: (_ for _ in ()).throw(httpx.ConnectError("down"))
    run(client_auth.notify_decision(state, "cli_0001", "app_2"))


def test_bot_loop_survives_network_errors(state, net, tg_env):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if len(calls) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"ok": True, "result": []})
    net.handler = handler

    async def go():
        await client_auth.startup(state)
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(calls) >= 3:
                break
        assert not state.client_auth_bot.done()
        await client_auth.shutdown(state)
    run(go())
    assert len(calls) >= 3 and all(p.endswith("/getUpdates") for p in calls)
    assert "123:TEST" in calls[0]


def test_startup_noop_without_token(state):
    run(client_auth.startup(state))
    assert getattr(state, "client_auth_bot", None) is None
    run(client_auth.shutdown(state))


# ---------- звонок ----------
@pytest.fixture
def call_env(monkeypatch):
    monkeypatch.setenv("SMSRU_API_ID", "TESTKEY")


def sms_handler(status_holder, add_code=100):
    def h(req):
        form = parse_qs(req.content.decode())
        if req.url.path == "/callcheck/add":
            assert form["api_id"] == ["TESTKEY"]
            if add_code != 100:
                return httpx.Response(200, json={"status": "ERROR", "status_code": add_code})
            assert form["phone"] == ["79991234567"]
            return httpx.Response(200, json={"status": "OK", "status_code": 100, "check_id": "chk1",
                                             "call_phone": "78005550001", "call_phone_pretty": "+7 (800) 555-00-01"})
        assert form["check_id"] == ["chk1"]
        return httpx.Response(200, json={"status": "OK", "status_code": 100, "check_status": status_holder[0]})
    return h


def test_call_success(client, net, call_env):
    st = ["400"]
    net.handler = sms_handler(st)
    r = client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "call"}).json()
    assert r["call_to"] == "+7 (800) 555-00-01" and r["expires_in"] == 300
    assert client.get(f"{A}/phone/{r['challenge_id']}").json() == {"status": "pending"}
    st[0] = "401"
    d = client.get(f"{A}/phone/{r['challenge_id']}").json()
    assert d["status"] == "verified" and d["client_id"] == "cli_0001"


def test_call_expired_and_invalid_number_and_outage(client, net, call_env):
    st = ["402"]
    net.handler = sms_handler(st)
    r = client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "call"}).json()
    assert client.get(f"{A}/phone/{r['challenge_id']}").json() == {"status": "expired"}
    net.handler = sms_handler(st, add_code=202)
    assert client.post(f"{A}/phone/start", json={"phone": "+7 999 000 00 00", "channel": "call"}).status_code == 422
    net.handler = lambda req: (_ for _ in ()).throw(httpx.ConnectError("down"))
    assert client.post(f"{A}/phone/start", json={"phone": "+7 999 000 00 01", "channel": "call"}).status_code == 502


def test_call_poll_network_error_stays_pending(client, net, call_env):
    net.handler = sms_handler(["400"])
    r = client.post(f"{A}/phone/start", json={"phone": GOOD_PHONE, "channel": "call"}).json()
    net.handler = lambda req: (_ for _ in ()).throw(httpx.ConnectError("down"))
    assert client.get(f"{A}/phone/{r['challenge_id']}").json() == {"status": "pending"}


# ---------- OAuth ----------
@pytest.fixture
def oauth_env(monkeypatch):
    monkeypatch.setenv("YANDEX_CLIENT_ID", "yid")
    monkeypatch.setenv("YANDEX_CLIENT_SECRET", "ysecret")
    monkeypatch.setenv("VK_CLIENT_ID", "12345")


def provider_handler(phone_yandex="+79991234567", phone_vk="79991234567"):
    def h(req):
        url = str(req.url)
        if url.startswith("https://oauth.yandex.ru/token"):
            assert req.headers["Authorization"].startswith("Basic ")
            assert "code_verifier" in parse_qs(req.content.decode())
            return httpx.Response(200, json={"access_token": "ya-token"})
        if url.startswith("https://login.yandex.ru/info"):
            assert req.headers["Authorization"] == "OAuth ya-token"
            body = {"id": "y-1"}
            if phone_yandex:
                body["default_phone"] = {"id": 1, "number": phone_yandex}
            return httpx.Response(200, json=body)
        if url == "https://id.vk.ru/oauth2/auth":
            form = parse_qs(req.content.decode())
            assert form["device_id"] == ["dev1"] and "code_verifier" in form
            return httpx.Response(200, json={"access_token": "vk-token", "user_id": 555})
        if url == "https://id.vk.ru/oauth2/user_info":
            user = {"user_id": "555"}
            if phone_vk:
                user["phone"] = phone_vk
            return httpx.Response(200, json={"user": user})
        return httpx.Response(404)
    return h


def oauth_start_state(client, provider):
    r = client.get(f"{A}/oauth/{provider}/start")
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["authorize_url"])
    q = parse_qs(url.query)
    assert q["redirect_uri"] == [f"http://localhost:5173/auth/callback/{provider}"]
    assert q["code_challenge_method"] == ["S256"] and q["response_type"] == ["code"]
    return url, q["state"][0]


def test_yandex_success(client, net, oauth_env):
    net.handler = provider_handler()
    url, state = oauth_start_state(client, "yandex")
    assert url.netloc == "oauth.yandex.ru" and url.path == "/authorize"
    r = client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": state})
    assert r.status_code == 200
    d = r.json()
    assert d["client_id"] == "cli_0001" and d["phone_masked"] == "+7 999 ***-**-67" and "phone_missing" not in d


def test_vk_success_and_state_long_enough(client, net, oauth_env):
    net.handler = provider_handler()
    url, state = oauth_start_state(client, "vk")
    assert url.netloc == "id.vk.ru" and len(state) >= 32
    r = client.post(f"{A}/oauth/vk/callback", json={"code": "c", "state": state, "device_id": "dev1"})
    assert r.status_code == 200 and r.json()["phone_masked"] == "+7 999 ***-**-67"


def test_oauth_forged_and_replayed_state(client, net, oauth_env, db):
    net.handler = provider_handler()
    r = client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": "forged"})
    assert r.status_code == 400 and r.json()["error_code"] == "INVALID_STATE"
    _, state = oauth_start_state(client, "yandex")
    assert client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": state}).status_code == 200
    assert client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": state}).status_code == 400
    # state от другого провайдера не подходит и сгорает
    _, state = oauth_start_state(client, "yandex")
    assert client.post(f"{A}/oauth/vk/callback", json={"code": "c", "state": state, "device_id": "dev1"}).status_code == 400
    assert client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": state}).status_code == 400
    assert any(x["action"] == "login_failed" for x in audit.list_records(db))


def test_oauth_state_expires_after_10_minutes(client, net, oauth_env, clock):
    net.handler = provider_handler()
    _, state = oauth_start_state(client, "yandex")
    clock.advance(601)
    assert client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": state}).status_code == 400


def test_oauth_without_phone(client, net, oauth_env):
    net.handler = provider_handler(phone_yandex=None)
    _, state = oauth_start_state(client, "yandex")
    d = client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": state}).json()
    assert d["phone_missing"] is True and d["phone_masked"] == "не указан" and d["client_id"] == "cli_0001"
    # повторный вход — тот же клиент
    _, state = oauth_start_state(client, "yandex")
    assert client.post(f"{A}/oauth/yandex/callback", json={"code": "c", "state": state}).json()["client_id"] == "cli_0001"


def test_oauth_provider_error(client, net, oauth_env):
    net.handler = lambda req: httpx.Response(400, json={"error": "invalid_grant"})
    _, state = oauth_start_state(client, "yandex")
    r = client.post(f"{A}/oauth/yandex/callback", json={"code": "bad", "state": state})
    assert r.status_code == 400 and r.json()["error_code"] == "PROVIDER_ERROR"


def test_oauth_disabled_when_not_configured(client):
    assert client.get(f"{A}/oauth/yandex/start").status_code == 400
    assert client.get(f"{A}/oauth/google/start").status_code == 400


def test_vk_local_client_id(client, monkeypatch, oauth_env):
    monkeypatch.setenv("VK_CLIENT_ID_LOCAL", "999")
    url, _ = oauth_start_state(client, "vk")
    assert parse_qs(url.query)["client_id"] == ["999"]


# ---------- один телефон — один клиент ----------
def test_same_phone_through_all_channels_is_one_client(client, net, db, tg_env, call_env, oauth_env, monkeypatch):
    monkeypatch.setenv("AUTH_DEMO", "1")
    ids = set()
    # демо
    ids.add(demo_login(client)[1].json()["client_id"])
    # Telegram
    cid, nonce = tg_start(client, "89991234567")
    feed(db, net, tg_update(42, f"/start {nonce}"), tg_update(42, contact={"user_id": 42, "phone_number": "79991234567"}))
    ids.add(client.get(f"{A}/phone/{cid}").json()["client_id"])
    # звонок
    net.handler = sms_handler(["401"])
    r = client.post(f"{A}/phone/start", json={"phone": "+7 999 123 45 67", "channel": "call"}).json()
    ids.add(client.get(f"{A}/phone/{r['challenge_id']}").json()["client_id"])
    # Яндекс и VK
    net.handler = provider_handler()
    for p, extra in (("yandex", {}), ("vk", {"device_id": "dev1"})):
        _, state = oauth_start_state(client, p)
        ids.add(client.post(f"{A}/oauth/{p}/callback", json={"code": "c", "state": state, **extra}).json()["client_id"])
    assert ids == {"cli_0001"}
    assert len(db.query_all("SELECT * FROM clients")) == 1
    assert len(db.query_all("SELECT * FROM client_identities")) == 5


def test_different_phones_get_sequential_ids(client):
    a = demo_login(client, "+7 999 000 00 01")[1].json()["client_id"]
    b = demo_login(client, "+7 999 000 00 02")[1].json()["client_id"]
    assert (a, b) == ("cli_0001", "cli_0002")


def test_responses_do_not_reveal_whether_client_exists(client):
    new = client.post(f"{A}/phone/start", json={"phone": "+7 999 000 00 01", "channel": "demo"}).json()
    demo_login(client, "+7 999 000 00 02")
    known = client.post(f"{A}/phone/start", json={"phone": "+7 999 000 00 02", "channel": "demo"}).json()
    assert set(new) == set(known)
