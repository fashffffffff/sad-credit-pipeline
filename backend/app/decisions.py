"""Решения по заявкам: запись версий в таблицу decisions и представление для API (с учётом роли)."""

from datetime import datetime, timezone

from credit_core import Decision

from backend.app.db import Database, dumps, loads
from backend.app.reasons import reason_text
from backend.app.security.redaction import redact_reason, redact_trace

FEATURE_LABELS = {"income": "Доход", "employment": "Занятость", "age": "Возраст", "family": "Семья",
                  "bki": "Кредитная история (БКИ)"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def next_version(db: Database, app_id: str) -> int:
    row = db.query_one("SELECT MAX(version) AS v FROM decisions WHERE app_id = ?", (app_id,))
    return (row["v"] or 0) + 1


def save_decision(db: Database, d: Decision, offers: list[dict], decided_by: str, trace: list[dict] | None = None) -> int:
    """Новая версия решения (старые не перезаписываются). Возвращает номер версии."""
    with db.lock:
        version = next_version(db, d.app_id)
        db.execute(
            "INSERT INTO decisions (app_id, version, decision, reason, score, dti, monthly_payment, amount_approved,"
            " term_approved, rate, bki_score, bki_delinquency, smev_status, rules_version, decided_by, decided_at,"
            " offers_json, trace_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.app_id, version, d.decision, d.reason, d.score, None if d.dti is None else round(d.dti, 2),
             d.monthly_payment, d.amount_approved, d.term_approved, d.rate, d.bki_score,
             None if d.bki_delinquency is None else int(d.bki_delinquency), d.smev, d.rules_version,
             decided_by, now_iso(), dumps(offers), dumps(trace if trace is not None else d.trace)))
        for part in d.breakdown:
            db.execute("INSERT INTO scoring_results (app_id, decision_version, feature, value, points, max_points)"
                       " VALUES (?,?,?,?,?,?)",
                       (d.app_id, version, part["feature"], dumps(part["value"]), part["points"], part["max"]))
    return version


def _breakdown(db: Database, app_id: str, version: int) -> list[dict]:
    rows = db.query_all("SELECT * FROM scoring_results WHERE app_id = ? AND decision_version = ? ORDER BY id",
                        (app_id, version))
    return [{"feature": r["feature"], "label_ru": FEATURE_LABELS.get(r["feature"], r["feature"]),
             "value": loads(r["value"]), "points": r["points"], "max": r["max_points"]} for r in rows]


def view(db: Database, row, role: str) -> dict:
    """Decision из контракта (раздел 7). Клиенту — без правила 11, без кода FRAUD_SUSPECT и без служебных текстов."""
    staff = role != "client"
    reason = row["reason"] if staff else redact_reason(row["reason"])
    trace = loads(row["trace_json"]) or []
    bki = None if row["bki_score"] is None else {"score": row["bki_score"], "delinquency": bool(row["bki_delinquency"])}
    return {
        "app_id": row["app_id"], "decision": row["decision"], "reason": reason,
        "reason_text": reason_text(row["reason"], staff),
        "score": row["score"], "dti": row["dti"],
        "monthly_payment": None if row["monthly_payment"] is None else round(row["monthly_payment"]),
        "amount_approved": None if row["amount_approved"] is None else round(row["amount_approved"]),
        "term_approved": row["term_approved"], "rate": row["rate"], "product_id": _product_of(db, row["app_id"]),
        "bki": bki, "smev": row["smev_status"],
        "breakdown": _breakdown(db, row["app_id"], row["version"]),
        "trace": trace if staff else redact_trace(trace),
        "offers": loads(row["offers_json"]) or [],
        "rules_version": row["rules_version"], "version": row["version"],
        "decided_by": row["decided_by"], "decided_at": row["decided_at"],
    }


def _product_of(db: Database, app_id: str) -> str | None:
    row = db.query_one("SELECT product_id FROM applications WHERE app_id = ?", (app_id,))
    return row["product_id"] if row else None


def latest_row(db: Database, app_id: str):
    return db.query_one("SELECT * FROM decisions WHERE app_id = ? ORDER BY version DESC LIMIT 1", (app_id,))


def latest(db: Database, app_id: str, role: str) -> dict | None:
    row = latest_row(db, app_id)
    return view(db, row, role) if row else None


def history(db: Database, app_id: str, role: str) -> list[dict]:
    return [view(db, r, role) for r in
            db.query_all("SELECT * FROM decisions WHERE app_id = ? ORDER BY version", (app_id,))]
