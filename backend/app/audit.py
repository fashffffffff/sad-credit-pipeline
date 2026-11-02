"""Журнал аудита с хэш-цепочкой.

Каждая запись хранит хэш предыдущей: hash = sha256(prev_hash + JSON остальных полей).
Если кто-то изменил или удалил запись в середине, цепочка ломается — verify_chain покажет где.
"""

import hashlib
import json
from datetime import datetime, timezone

from backend.app.db import Database, dumps, loads

GENESIS = "0" * 64


def _hash(prev_hash: str, row: dict) -> str:
    fields = {k: row[k] for k in ("id", "ts", "actor", "role", "action", "entity", "entity_id", "details")}
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def write(db: Database, actor: str, role: str, action: str, app_id: str | None = None, *,
          decision: str | None = None, reason: str | None = None, score: int | None = None,
          dti: float | None = None, details: dict | None = None) -> int:
    """Добавляет запись в конец журнала. В details — только маски и id, никаких паспортов и телефонов.

    Поля кейса (decision, reason, score, dti) лежат внутри details: так они тоже защищены хэшем записи.
    """
    extra = {"decision": decision, "reason": reason, "score": score, "dti": dti}
    body = {**{k: v for k, v in extra.items() if v is not None}, **(details or {})}
    with db.lock:  # читаем «хвост» и пишем новую запись как одно действие
        last = db.query_one("SELECT id, hash FROM audit_log ORDER BY id DESC LIMIT 1")
        prev_hash = last["hash"] if last else GENESIS
        row = {
            "id": (last["id"] + 1) if last else 1,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "actor": actor, "role": role, "action": action,
            "entity": "application" if app_id else None, "entity_id": app_id,
            "details": dumps(body),
        }
        row_hash = _hash(prev_hash, row)
        db.execute(
            "INSERT INTO audit_log (id, ts, actor, role, action, entity, entity_id, details, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*row.values(), prev_hash, row_hash),
        )
        return row["id"]


def verify_chain(db: Database) -> dict:
    """Проходит по журналу с начала. broken_at — id первой записи, где цепочка не сходится."""
    prev = GENESIS
    checked = 0
    for row in db.query_all("SELECT * FROM audit_log ORDER BY id"):
        if row["prev_hash"] != prev or _hash(prev, dict(row)) != row["hash"]:
            return {"ok": False, "checked": checked, "broken_at": row["id"]}
        prev = row["hash"]
        checked += 1
    return {"ok": True, "checked": checked, "broken_at": None}


def public_record(row) -> dict:
    """Запись в формате кейса: event_id, app_id, action, decision, reason, score, dti, actor, at (+ хэши цепочки)."""
    details = loads(row["details"]) or {}
    return {
        "event_id": f"evt_{row['id']:04d}", "app_id": row["entity_id"], "action": row["action"],
        "decision": details.get("decision"), "reason": details.get("reason"),
        "score": details.get("score"), "dti": details.get("dti"),
        "actor": row["actor"], "role": row["role"], "at": row["ts"],
        "details": details, "prev_hash": row["prev_hash"], "hash": row["hash"],
    }


def list_records(db: Database, limit: int = 100, app_id: str | None = None) -> list[dict]:
    if app_id:
        rows = db.query_all("SELECT * FROM audit_log WHERE entity_id = ? ORDER BY id DESC LIMIT ?", (app_id, limit))
    else:
        rows = db.query_all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return [public_record(r) for r in rows]
