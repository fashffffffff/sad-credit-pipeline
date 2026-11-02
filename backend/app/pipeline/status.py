"""Машина статусов заявки (раздел 7 контракта). Любой переход, которого нет в таблице, — ошибка (HTTP 409)."""

from backend.app.db import Database

FINAL = {"APPROVED", "REFUSED", "NEED_MORE_DATA", "MANUAL_REVIEW"}
ALLOWED = {
    "PROCESSING": FINAL,
    "MANUAL_REVIEW": {"APPROVED", "REFUSED", "PROCESSING"},  # ручное решение или пересчёт
    "APPROVED": {"PROCESSING"},  # пересчёт — из любого итогового статуса
    "REFUSED": {"PROCESSING"},
    "NEED_MORE_DATA": {"PROCESSING"},
}


class InvalidTransition(Exception):
    def __init__(self, old: str, new: str):
        super().__init__(f"Переход {old} → {new} запрещён")
        self.old, self.new = old, new


def change_status(db: Database, app_id: str, new: str) -> str:
    """Меняет статус под замком (проверка и запись — одно действие); возвращает прежний статус."""
    with db.lock:
        row = db.query_one("SELECT status FROM applications WHERE app_id = ?", (app_id,))
        if row is None:
            raise KeyError(app_id)
        if new not in ALLOWED.get(row["status"], set()):
            raise InvalidTransition(row["status"], new)
        db.update("applications", "app_id", app_id, {"status": new})
        return row["status"]
