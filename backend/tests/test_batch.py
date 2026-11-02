"""Пакетный прогон CSV через credit_core: 91 из 100 совпадений с разметкой, 9 объяснённых расхождений."""

from pathlib import Path

CSV = Path(__file__).resolve().parents[2] / "data" / "case2" / "applications.csv"
DIFFS = ["app_0005", "app_0007", "app_0009", "app_0012", "app_0019", "app_0025", "app_0045", "app_0064", "app_0070"]


async def test_batch_91_of_100_multipart(env, uw):
    files = {"file": ("applications.csv", CSV.read_bytes(), "text/csv")}
    r = await env.client.post("/api/v1/batch", headers=uw, files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["total"] == 100 and body["summary"]["matched_csv"] == 91
    rows = body["rows"]
    assert sorted(x["app_id"] for x in rows if not x["match"]) == DIFFS
    assert all(x["explanation"] for x in rows if not x["match"])
    assert all(set(x) == {"app_id", "decision", "reason", "score", "dti", "csv_decision", "csv_reason", "match",
                          "explanation"} for x in rows)
    first = rows[0]
    assert (first["app_id"], first["decision"], first["score"], first["dti"]) == ("app_0001", "APPROVED", 90, 0.21)


async def test_batch_raw_body_and_last_csv(env, admin):
    r = await env.client.post("/api/v1/batch", headers=admin | {"Content-Type": "text/csv"}, content=CSV.read_bytes())
    assert r.status_code == 200 and r.json()["summary"]["matched_csv"] == 91
    out = await env.client.get("/api/v1/batch/last.csv", headers=admin)
    lines = out.text.strip().splitlines()
    assert lines[0] == "app_id,decision,reason,score,dti,amount_approved,term_approved" and len(lines) == 101
    assert lines[1].startswith("app_0001,APPROVED,,90,0.21,500000,24")


async def test_batch_limits_and_bad_input(env, uw):
    big = b"app_id,age\n" + b"app_0001,30\n" * 100000  # > 1 МБ
    r = await env.client.post("/api/v1/batch", headers=uw | {"Content-Type": "text/csv"}, content=big)
    assert r.status_code == 413 and r.json()["error_code"] == "PAYLOAD_TOO_LARGE"
    r = await env.client.post("/api/v1/batch", headers=uw | {"Content-Type": "text/csv"}, content=b"foo,bar\n1,2\n")
    assert r.status_code == 422
    r = await env.client.post("/api/v1/batch", headers=uw | {"Content-Type": "text/csv"}, content=b"\xff\xfe\x00bad")
    assert r.status_code == 422


async def test_last_csv_404_before_first_run(env, uw):
    assert (await env.client.get("/api/v1/batch/last.csv", headers=uw)).status_code == 404
