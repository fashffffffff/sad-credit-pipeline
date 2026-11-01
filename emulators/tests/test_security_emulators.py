"""Безопасность эмуляторов (аудит, docs/SECURITY_AUDIT.md): подпись, /chaos, размер тела, секреты."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from common.secrets_guard import secret_from_env
from common.signing import sign
from emulators.tests.test_emulators import SMEV_SECRET, make_client, person, smev_call

URL = "/smev/v1/verify"


@pytest.fixture
def smev():
    return make_client("smev")


GOOD = {**person("4510 123456"), "employer_inn": "7707083893"}


def test_non_ascii_signature_is_401_not_500(smev):
    """Раньше hmac.compare_digest(str, str) с не-ASCII подписью падал с TypeError → 500."""
    body = json.dumps(GOOD).encode()
    headers = {"X-Signature": "é".encode("latin-1"), "X-Timestamp": str(int(time.time()))}
    assert smev.post(URL, content=body, headers=headers).status_code == 401


@pytest.mark.parametrize("signature,timestamp", [("", "1"), ("abc", ""), ("abc", "not-a-number"), ("abc", "99999999999999999999")])
def test_malformed_signature_headers_are_401(smev, signature, timestamp):
    body = json.dumps(GOOD).encode()
    assert smev.post(URL, content=body, headers={"X-Signature": signature, "X-Timestamp": timestamp}).status_code == 401


def test_signature_must_cover_the_body(smev):
    """Подпись от одного тела к другому не переносится."""
    body = json.dumps(GOOD).encode()
    headers = sign(SMEV_SECRET, body)
    assert smev.post(URL, content=body + b" ", headers=headers).status_code == 401


def test_signature_made_with_empty_secret_is_rejected(smev):
    body = json.dumps(GOOD).encode()
    import hashlib, hmac
    ts = str(int(time.time()))
    forged = hmac.new(b"", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert smev.post(URL, content=body, headers={"X-Signature": forged, "X-Timestamp": ts}).status_code == 401


def test_oversized_body_is_413_before_anything_else(smev):
    """Тело читается до проверки подписи: без лимита любой присылал мегабайты без подписи."""
    assert smev.post(URL, content=b"x" * 100_000).status_code == 413


@pytest.mark.parametrize("kind", ["smev", "bki"])
def test_chaos_only_from_allowed_networks(kind):
    """/chaos открыт только с localhost (или из CHAOS_ALLOW_NETS). Заголовки X-Forwarded-For/X-Real-IP не помогают."""
    stranger = TestClient(make_client(kind).app, client=("203.0.113.9", 4000))
    spoof = {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1", "Forwarded": "for=127.0.0.1"}
    assert stranger.get("/chaos", headers=spoof).status_code == 403
    assert stranger.put("/chaos", json={"mode": "error"}, headers=spoof).status_code == 403
    assert make_client(kind).get("/chaos").status_code == 200


def test_chaos_rejects_garbage(smev):
    for bad in ({"mode": "nuke"}, {"mode": "slow", "delay_ms": -5}, {"mode": "slow", "delay_ms": 10**9}):
        assert smev.put("/chaos", json=bad).status_code == 422


def test_emulator_refuses_empty_secret(monkeypatch):
    monkeypatch.setenv("SMEV_HMAC_SECRET", "")
    with pytest.raises(RuntimeError, match="пуст"):
        secret_from_env("SMEV_HMAC_SECRET", "dev-smev-secret")


def test_emulator_refuses_default_secret_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SMEV_HMAC_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SMEV_HMAC_SECRET"):
        secret_from_env("SMEV_HMAC_SECRET", "dev-smev-secret")


def test_logs_contain_only_masked_passport(smev, caplog):
    """Проверено, не уязвимо: в логе эмулятора маска паспорта, но не сам номер."""
    import logging
    logging.getLogger("emulator").propagate = True
    with caplog.at_level(logging.INFO, logger="emulator"):
        smev_call(smev, "4510 123456")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "45** ****56" in text and "123456" not in text.replace("****56", "")
