"""Машина статусов заявки. Любой переход, которого нет в таблице, — ошибка (HTTP 409)."""

from backend.app.db import Database

ALLOWED = {
    "received": {"processing"},
    "processing": {"approved", "rejected", "manual_review"},
    "manual_review": {"approved", "rejected"},
}


class InvalidTransition(Exception):
    def __init__(self, old: str, new: str):
        super().__init__(f"Переход {old} → {new} запрещён")


def change_status(db: Database, app_id: str, new: str) -> None:
    with db.lock:
        row = db.query_one("SELECT status FROM applications WHERE id = ?", (app_id,))
        if row is None:
            raise KeyError(app_id)
        if new not in ALLOWED.get(row["status"], set()):
            raise InvalidTransition(row["status"], new)
        db.update("applications", "id", app_id, {"status": new})
