"""Прокси паспорта к токенизатору OPRF: только client, лимиты 5/мин и 20/час, подпись ответа, backend паспорт не видит."""

import json

from backend.tests.conftest import good_form

BODY = {"blinded": "YWJjZGVmZ2g="}


async def test_oprf_proxy_roundtrip_and_signed_request(env, client):
    r = await env.client.post("/api/v1/passport/oprf", json=BODY, headers=client)
    assert r.status_code == 200 and r.json() == {"evaluated": "ZXZhbHVhdGVk", "proof": "cHJvb2Y="}
    assert env.world.calls["tokenizer"] == 1 and all(env.world.signed_ok)  # запрос подписан TOKENIZER_HMAC_SECRET
    k = await env.client.get("/api/v1/passport/public-key", headers=client)
    assert k.json() == {"suite": "P256-SHA256", "public_key": "a2V5"}


async def test_oprf_limits_per_minute_and_per_hour(env, client):
    for _ in range(5):
        assert (await env.client.post("/api/v1/passport/oprf", json=BODY, headers=client)).status_code == 200
    r = await env.client.post("/api/v1/passport/oprf", json=BODY, headers=client)
    assert r.status_code == 429 and r.json()["error_code"] == "TOO_MANY_REQUESTS" and r.json()["retryable"] is True
    # другой клиент не блокируется
    assert (await env.client.post("/api/v1/passport/oprf", json=BODY, headers=env.make_client(2))).status_code == 200
    # через минуту — снова можно, но в час не больше 20
    t = [1000.0]
    env.state.clock = lambda: t[0]
    env.state.oprf_hits.clear()
    sent = 0
    for _ in range(10):
        for _ in range(5):
            r = await env.client.post("/api/v1/passport/oprf", json=BODY, headers=client)
            if r.status_code == 200:
                sent += 1
        t[0] += 61
    assert sent == 20


async def test_oprf_input_validation_and_tokenizer_failures(env, client):
    for bad in ({}, {"blinded": ""}, {"blinded": "x" * 500}, {"blinded": "<script>"}, {"blinded": "YWJj", "passport": "4510 123456"}):
        r = await env.client.post("/api/v1/passport/oprf", json=bad, headers=client)
        assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR", bad
    env.world.modes["tokenizer"] = "timeout"
    r = await env.client.post("/api/v1/passport/oprf", json=BODY, headers=client)
    assert r.status_code == 503 and r.json()["retryable"] is True
    env.world.modes["tokenizer"] = "bad_signature"  # подмена ответа токенизатора
    env.state.oprf_hits.clear()
    r = await env.client.post("/api/v1/passport/oprf", json=BODY, headers=client)
    assert r.status_code == 502
    from backend.app import audit
    assert "signature_error" in [x["action"] for x in audit.list_records(env.state.db)]


async def test_backend_does_not_accept_passport_or_personal_data(env, client):
    for extra in ({"passport": "4510 123456"}, {"full_name": "Иванов Иван Иванович"}, {"phone": "+79991234567"},
                  {"birth_date": "1995-04-12"}):
        r = await env.submit(good_form(**extra), client)
        assert r.status_code == 422 and list(extra)[0] in r.json()["details"]["fields"]
