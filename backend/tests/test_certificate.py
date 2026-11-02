"""Справка PDF: подпись Ed25519, QR-ссылка, проверка /verify, отзыв при смене решения; ключ хранится с правами 600."""

import base64
import os
import stat

from backend.app import audit, certificate
from backend.tests.conftest import GOOD_HASH, good_form


def parse_link_from_pdf_source(env, d_row) -> tuple[str, str]:
    """В PDF ссылка лежит только внутри QR-картинки, поэтому подпись берём тем же кодом, что и ручка."""
    payload = certificate.make_payload(d_row, {"product_id": "prod_standard"}, "+7 999 ***-**-67")
    return certificate.sign_payload(env.state.settings, payload)


async def issue(env, client):
    d = await env.run(good_form(), client)
    r = await env.client.get(f"/api/v1/applications/{d['app_id']}/certificate.pdf", headers=client)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    return d, r


async def test_certificate_issued_for_approved_only_and_audited(env, client):
    d, _ = await issue(env, client)
    assert "certificate_issued" in [r["action"] for r in audit.list_records(env.state.db)]
    env.world.bki_default = {"score": 400, "delinquency": True, "requests_90d": 1}
    env.state.settings.duplicate_check = False
    refused = await env.run(good_form(), client)
    r = await env.client.get(f"/api/v1/applications/{refused['app_id']}/certificate.pdf", headers=client)
    assert r.status_code == 409 and r.json()["error_code"] == "NOT_APPROVED"


async def test_verify_valid_then_revoked_after_status_change(env, client, uw):
    d, _ = await issue(env, client)
    row = env.state.db.query_one("SELECT * FROM decisions WHERE app_id = ?", (d["app_id"],))
    payload, signature = parse_link_from_pdf_source(env, row)
    r = await env.client.post("/api/v1/verify", json={"payload": payload, "signature": signature})  # без входа
    body = r.json()
    assert body["valid"] is True and body["revoked"] is False and body["expired"] is False
    assert body["data"]["app_id"] == d["app_id"] and body["data"]["amount_approved"] == 500000
    assert body["data"]["client"] == "+7 999 ***-**-67" and body["data"]["decision"] == "APPROVED"
    # андеррайтер пересчитал — теперь отказ: справка отозвана
    env.world.bki_default = {"score": 400, "delinquency": True, "requests_90d": 1}
    await env.client.post(f"/api/v1/applications/{d['app_id']}/recalculate", headers=uw)
    body = (await env.client.post("/api/v1/verify", json={"payload": payload, "signature": signature})).json()
    assert body["valid"] is True and body["revoked"] is True


async def test_verify_rejects_forged_payload_and_garbage(env, client):
    d, _ = await issue(env, client)
    row = env.state.db.query_one("SELECT * FROM decisions WHERE app_id = ?", (d["app_id"],))
    payload, signature = parse_link_from_pdf_source(env, row)
    forged = certificate.b64u(base64.urlsafe_b64decode(payload + "==").replace(b"500000", b"900000"))
    for body in ({"payload": forged, "signature": signature}, {"payload": payload, "signature": "AAAA"},
                 {"payload": "!!!", "signature": "###"}, {"payload": "", "signature": ""}):
        r = await env.client.post("/api/v1/verify", json=body)
        assert r.status_code == 200 and r.json()["valid"] is False and r.json()["data"] is None


async def test_public_key_matches_signature_and_is_ed25519(env, client):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    d, _ = await issue(env, client)
    key = (await env.client.get("/api/v1/verify/public-key")).json()
    assert key["alg"] == "Ed25519"
    row = env.state.db.query_one("SELECT * FROM decisions WHERE app_id = ?", (d["app_id"],))
    payload, signature = parse_link_from_pdf_source(env, row)
    pub = Ed25519PublicKey.from_public_bytes(certificate.unb64u(key["public_key"]))
    pub.verify(certificate.unb64u(signature), certificate.unb64u(payload))  # InvalidSignature, если не сходится


async def test_signing_key_file_is_private_and_reused(env, client):
    await issue(env, client)
    path = env.state.settings.cert_key_file
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    before = open(path).read()
    certificate._keys.clear()  # «перезапуск»: тот же файл → тот же ключ
    assert certificate.public_key_b64u(env.state.settings) == certificate.public_key_b64u(env.state.settings)
    assert open(path).read() == before


async def test_cert_signing_key_from_env_setting(env):
    raw = os.urandom(32)
    from dataclasses import replace
    settings = replace(env.state.settings, cert_signing_key=base64.b64encode(raw).decode())
    p1, p2 = certificate.public_key_b64u(settings), certificate.public_key_b64u(env.state.settings)
    assert p1 != p2


def test_font_is_searched_in_list(tmp_path, monkeypatch):
    from backend.app.config import Settings
    font = tmp_path / "f.ttf"
    font.write_bytes(b"x")
    assert certificate.find_font(Settings(cert_font=str(font))) == str(font)
    monkeypatch.setattr(certificate, "FONT_CANDIDATES", [str(tmp_path / "nope.ttf")])
    assert certificate.find_font(Settings()) is None


async def test_certificate_without_cyrillic_font_still_renders(env, client, monkeypatch):
    monkeypatch.setattr(certificate, "find_font", lambda s: None)
    d, r = await issue(env, client)
    assert len(r.content) > 1000
