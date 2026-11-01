"""Тесты эмуляторов. Задержки и таймаут делаем крошечными, чтобы тесты шли быстро."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign, verify
from emulators.app import bki as bki_mod
from emulators.app import smev as smev_mod
from emulators.app.common import create_app

SMEV_SECRET, BKI_SECRET = "test-smev", "test-bki"
LOCAL = ("127.0.0.1", 50000)


FIX = Path(__file__).parent / "fixtures"


def bki_app(pool=FIX / "pool_small.parquet", demo=FIX / "demo.json"):
    return bki_mod.make_app(pool, demo, BKI_SECRET, base_delay_ms=(0, 5), timeout_s=0.3)


def make_client(kind: str) -> TestClient:
    if kind == "smev":
        app = create_app("smev", "/smev/v1/verify", smev_mod.VerifyRequest, smev_mod.build_response,
                         SMEV_SECRET, base_delay_ms=(0, 5), timeout_s=0.3)
    else:
        app = bki_app()
    return TestClient(app, client=LOCAL)


@pytest.fixture
def smev():
    return make_client("smev")


@pytest.fixture
def bki():
    return make_client("bki")


def person(passport: str) -> dict:
    return {"passport": passport, "full_name": "Иванов Иван Иванович", "birth_date": "1995-04-12"}


def post(client: TestClient, path: str, secret: str, payload: dict, signed_with: str | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode()
    return client.post(path, content=body, headers=sign(signed_with or secret, body))


def smev_call(c, passport, **kw):
    return post(c, "/smev/v1/verify", SMEV_SECRET, {**person(passport), "employer_inn": "7707083893"}, **kw)


def bki_call(c, passport, **kw):
    return post(c, "/bki/v1/report", BKI_SECRET, person(passport), **kw)


def set_chaos(c, mode, delay_ms=0):
    r = c.put("/chaos", json={"mode": mode, "delay_ms": delay_ms})
    assert r.status_code == 200


# --- детерминизм и профили ---------------------------------------------------
def test_deterministic_bki(bki):
    a = bki_call(bki, "4510 123456").json()
    b = bki_call(bki, "4510 123456").json()
    a.pop("report_id"), b.pop("report_id")  # id каждый раз новый
    assert a == b


def test_deterministic_smev(smev):
    a = smev_call(smev, "4510 123456").json()
    b = smev_call(smev, "4510 123456").json()
    a.pop("request_id"), b.pop("request_id")
    assert a == b


def test_bki_returns_real_history_from_pool(bki):
    b = bki_call(bki, "4510 123456").json()
    assert b["found"] and len(b["loans"]) > 0
    assert "id" not in b["loans"][0] and b["loans"][0]["rn"] == 1
    assert [l["rn"] for l in b["loans"]] == sorted(l["rn"] for l in b["loans"])
    # старые синтетические поля убраны: их теперь считает модель из loans
    assert not {"bureau_score", "overdue_30_59", "revolving_utilization", "open_credit_lines"} & b.keys()


def test_bki_synthetic_fields_consistent(bki):
    for i in range(60):
        b = bki_call(bki, f"45{i:02d} 000{i:03d}").json()
        if not b["found"]:
            assert b["loans"] == [] and b["total_debt"] is None and b["monthly_debt_payment"] is None
            continue
        n_open = sum(1 for l in b["loans"] if l["fclose_flag"] == 1)
        if n_open == 0:  # все кредиты закрыты -> платить нечего
            assert b["monthly_debt_payment"] == 0 and b["total_debt"] == 0
        else:
            assert b["monthly_debt_payment"] > 0 and b["total_debt"] >= 15 * b["monthly_debt_payment"] - 1000
        assert 0 <= b["inquiries_30d"] <= b["inquiries_6m"] <= 6


def test_bki_not_found_share(bki):
    n = 400
    missing = sum(not bki_call(bki, f"{i:04d} {i:06d}").json()["found"] for i in range(1000, 1000 + n))
    assert 0.01 < missing / n < 0.09  # ~4%


def test_bki_degraded_without_pool(tmp_path):
    c = TestClient(bki_app(pool=tmp_path / "нет.parquet", demo=tmp_path / "нет.json"), client=LOCAL)
    h = c.get("/health").json()
    assert h["status"] == "degraded" and "нет пула историй" in h["reason"]
    assert bki_call(c, "4510 123456").status_code == 503  # сервер не упал, а честно отказал


# --- магические паспорта -----------------------------------------------------
def demo_loans(name):
    """Кредиты демо-истории напрямую из фикстур (для сверки с ответом)."""
    store = bki_mod.HistoryStore(FIX / "pool_small.parquet", FIX / "demo.json")
    return store.histories[store.demo[name]]


def test_magic_1_perfect(smev, bki):
    s = smev_call(smev, "0000 000001").json()
    assert s["passport_valid"] and s["person_match"] and s["employment_confirmed"]
    assert bki_call(bki, "0000 000001").json()["loans"] == demo_loans("good")


def test_magic_2_bad_history(bki):
    assert bki_call(bki, "0000 000002").json()["loans"] == demo_loans("bad")


def test_magic_3_invalid_passport(smev):
    assert smev_call(smev, "0000 000003").json()["passport_valid"] is False


def test_magic_4_no_history(bki):
    b = bki_call(bki, "0000 000004").json()
    assert b["found"] is False and b["loans"] == []
    assert b["total_debt"] is None and b["monthly_debt_payment"] is None and b["inquiries_6m"] is None


def test_magic_5_borderline(bki):
    assert bki_call(bki, "0000 000005").json()["loans"] == demo_loans("borderline")


# --- подпись -----------------------------------------------------------------
def test_response_signature_valid(smev, bki):
    for r, secret in ((smev_call(smev, "4510 123456"), SMEV_SECRET), (bki_call(bki, "4510 123456"), BKI_SECRET)):
        assert r.status_code == 200
        assert verify(secret, r.content, r.headers[SIGNATURE_HEADER], r.headers[TIMESTAMP_HEADER])


def test_bad_request_signature_401(smev, bki):
    assert smev_call(smev, "4510 123456", signed_with="чужой секрет").status_code == 401
    assert bki_call(bki, "4510 123456", signed_with="чужой секрет").status_code == 401


def test_missing_signature_401(bki):
    assert bki.post("/bki/v1/report", json=person("4510 123456")).status_code == 401


def test_old_timestamp_401(bki):
    """Повтор старого сообщения (replay): подпись верна, но время давно прошло."""
    body = json.dumps(person("4510 123456")).encode()
    old = sign(BKI_SECRET, body, timestamp=int(time.time()) - 3600)
    assert bki.post("/bki/v1/report", content=body, headers=old).status_code == 401


def test_invalid_body_422(bki):
    body = b'{"passport": "4510 123456"}'
    assert bki.post("/bki/v1/report", content=body, headers=sign(BKI_SECRET, body)).status_code == 422


# --- хаос-режимы -------------------------------------------------------------
def test_chaos_default_and_validation(bki):
    assert bki.get("/chaos").json() == {"mode": "normal", "delay_ms": 0}
    assert bki.put("/chaos", json={"mode": "взрыв"}).status_code == 422


def test_chaos_localhost_only():
    c = TestClient(bki_app(), client=("8.8.8.8", 1234))
    assert c.get("/chaos").status_code == 403
    assert c.put("/chaos", json={"mode": "error"}).status_code == 403
    assert c.get("/health").json() == {"status": "ok", "service": "bki"}  # health открыт


def test_chaos_allow_nets():
    """CHAOS_ALLOW_NETS расширяет доступ: сеть compose пускаем, чужих — нет; localhost остаётся закрытым."""
    def client(ip):
        app = bki_mod.make_app(FIX / "pool_small.parquet", FIX / "demo.json", BKI_SECRET,
                               chaos_allow_nets="172.16.0.0/12")
        return TestClient(app, client=(ip, 1234))
    assert client("172.18.0.5").get("/chaos").status_code == 200
    assert client("8.8.8.8").get("/chaos").status_code == 403
    assert client("127.0.0.1").get("/chaos").status_code == 403  # явно заданный список заменяет дефолт


def test_chaos_error(bki):
    set_chaos(bki, "error")
    assert bki_call(bki, "4510 123456").status_code == 503


def test_chaos_slow(bki):
    set_chaos(bki, "slow", 300)
    t = time.monotonic()
    r = bki_call(bki, "4510 123456")
    assert r.status_code == 200 and time.monotonic() - t >= 0.3


def test_chaos_timeout(bki):
    set_chaos(bki, "timeout")
    t = time.monotonic()
    r = bki_call(bki, "4510 123456")
    assert r.status_code == 504 and time.monotonic() - t >= 0.3


def test_chaos_bad_signature(smev):
    set_chaos(smev, "bad_signature")
    r = smev_call(smev, "4510 123456")
    assert r.status_code == 200
    assert not verify(SMEV_SECRET, r.content, r.headers[SIGNATURE_HEADER], r.headers[TIMESTAMP_HEADER])


def test_chaos_flaky(bki):
    set_chaos(bki, "flaky")
    codes = {bki_call(bki, "4510 123456").status_code for _ in range(40)}
    assert codes == {200, 503}  # шанс, что все 40 попыток одинаковые, ~1e-12


def test_chaos_back_to_normal(bki):
    set_chaos(bki, "error")
    set_chaos(bki, "normal")
    assert bki_call(bki, "4510 123456").status_code == 200


def test_real_apps_import():
    """Боевые приложения (с дефолтными секретами) собираются и отвечают на health."""
    assert TestClient(smev_mod.app).get("/health").json()["service"] == "smev"
    assert TestClient(bki_mod.app).get("/health").json()["service"] == "bki"
