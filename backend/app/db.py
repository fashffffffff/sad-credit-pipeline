"""SQLite без ORM: одно соединение, замок на запись и несколько простых функций."""

import json
import logging
import sqlite3
import threading
from pathlib import Path

from backend.app.credentials import LEGACY_USERS
from backend.app.security.passwords import check_password, hash_password

log = logging.getLogger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
    status TEXT NOT NULL,
    full_name_masked TEXT NOT NULL, passport_hash TEXT NOT NULL, passport_masked TEXT NOT NULL,
    phone_masked TEXT NOT NULL,
    monthly_income INTEGER, employment_months INTEGER, dependents INTEGER, purpose TEXT,
    loan_amount INTEGER, loan_term_months INTEGER, monthly_payment INTEGER,
    score INTEGER, total_ms INTEGER,
    features TEXT, prediction TEXT, reasons TEXT, review TEXT,
    pdn REAL, bki_loans TEXT, fraud TEXT
);
CREATE TABLE IF NOT EXISTS stages (
    application_id TEXT NOT NULL, stage TEXT NOT NULL, position INTEGER NOT NULL,
    status TEXT NOT NULL, duration_ms INTEGER, attempts INTEGER, detail TEXT,
    PRIMARY KEY (application_id, stage)
);
CREATE TABLE IF NOT EXISTS fraud_nodes (
    app_id TEXT PRIMARY KEY, ts REAL NOT NULL,
    device TEXT, phone TEXT, employer TEXT, passport TEXT,  -- только HMAC-отпечатки, открытых данных нет
    amount INTEGER, income INTEGER, term INTEGER, n_loans INTEGER, known_months INTEGER,
    score REAL, pattern TEXT, flagged INTEGER, channel TEXT
);
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY, body_hash TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL,
    action TEXT NOT NULL, entity TEXT, entity_id TEXT, details TEXT,
    prev_hash TEXT NOT NULL, hash TEXT NOT NULL
);
"""

class Database:
    def __init__(self, path: str, bcrypt_rounds: int = 12, credentials: list[dict] | None = None):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: к базе ходят из потоков to_thread; порядок держит замок
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self._add_missing_columns()
        self.sync_users(credentials or [], bcrypt_rounds)

    def _add_missing_columns(self) -> None:
        """Старая база (до новых версий) без новых колонок: дописываем их, чтобы не удалять данные."""
        wanted = {"applications": (("pdn", "REAL"), ("bki_loans", "TEXT"), ("fraud", "TEXT")),
                  "users": (("token_version", "INTEGER NOT NULL DEFAULT 0"),),
                  "fraud_nodes": (("channel", "TEXT"),)}
        for table, columns in wanted.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, kind in columns:
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")

    def sync_users(self, credentials: list[dict], rounds: int) -> None:
        """Приводит таблицу users в соответствие с файлом учёток (его пароли секретные, хранятся вне git).

        1) Старые публичные демо-пользователи (пароль «логин+123») удаляются, если этот пароль ещё подходит:
           такой пароль знал каждый, кто видел репозиторий. Вместо них будут учётки из файла.
        2) Учётки из файла, которых нет в БД, создаются; если пароль в файле изменили — хэш обновляется
           и старые токены пользователя отзываются.
        3) Пользователи, которых в файле нет (кроме старых логинов из LEGACY_USERS), удаляются: убрали учётку из файла — доступ закрыт (иначе уволенный
           сотрудник входил бы по старому паролю). Пустой список не трогаем: это «файла нет», а не «всех убрать».
        В БД лежат только bcrypt-хэши.
        """
        for username in LEGACY_USERS:
            row = self.query_one("SELECT password_hash FROM users WHERE username = ?", (username,))
            if row and check_password(username + "123", row["password_hash"]):
                self.execute("DELETE FROM users WHERE username = ?", (username,))
                log.warning("Удалён устаревший демо-пользователь %r с публичным паролем", username)
        if credentials:
            # старые логины (operator, risk…) с изменённым паролем не трогаем: их могли завести руками
            keep = {c["username"] for c in credentials} | set(LEGACY_USERS)
            for row in self.query_all("SELECT username FROM users"):
                if row["username"] not in keep:
                    self.execute("DELETE FROM users WHERE username = ?", (row["username"],))
                    log.warning("Удалён пользователь %r: его нет в файле учёток", row["username"])
        for c in credentials:
            row = self.query_one("SELECT password_hash FROM users WHERE username = ?", (c["username"],))
            if row is None:
                self.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                             (c["username"], hash_password(c["password"], rounds), c["role"]))
            elif not check_password(c["password"], row["password_hash"]):
                self.execute("UPDATE users SET password_hash = ?, role = ?, token_version = token_version + 1 "
                             "WHERE username = ?", (hash_password(c["password"], rounds), c["role"], c["username"]))

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.lock:
            self.conn.execute(sql, params)
            self.conn.commit()

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def update(self, table: str, key_col: str, key: str, fields: dict) -> None:
        """UPDATE table SET <fields> WHERE key_col = key. Имена колонок берём только из нашего кода."""
        cols = ", ".join(f"{c} = ?" for c in fields)
        self.execute(f"UPDATE {table} SET {cols} WHERE {key_col} = ?", (*fields.values(), key))


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(text: str | None):
    return json.loads(text) if text else None
