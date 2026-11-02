"""Таблицы и работа с БД: клиенты, личности, challenge'и, OAuth state, лимиты, выдача токена."""

import secrets
from datetime import datetime, timezone

from backend.app import audit
from backend.app.errors import ApiError
from backend.app.security.tokens import create_token

from backend.app.client_auth import config
from backend.app.client_auth.phone import hmac_hex, mask_phone, phone_hash, subject_hash

NO_PHONE_MASK = "не указан"


def init_tables(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS clients (
        client_id TEXT PRIMARY KEY,
        phone_hash TEXT UNIQUE NOT NULL,
        phone_masked TEXT NOT NULL,
        telegram_chat_id INTEGER,
        token_version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS client_identities (
        provider TEXT NOT NULL,
        subject_hash TEXT NOT NULL,
        client_id TEXT NOT NULL,
        PRIMARY KEY (provider, subject_hash))""")
    db.execute("""CREATE TABLE IF NOT EXISTS auth_challenges (
        challenge_id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        phone_hash TEXT NOT NULL,
        phone_masked TEXT NOT NULL,
        ip TEXT,
        nonce TEXT,
        code_hash TEXT,
        check_id TEXT,
        tg_user_id INTEGER,
        tg_chat_id INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        client_id TEXT,
        delivered INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS oauth_states (
        state TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        code_verifier TEXT NOT NULL,
        ip TEXT,
        created_at REAL NOT NULL)""")


# ---------- клиенты ----------
def _resolve_client(db, provider: str, subj: str, p_hash: str, masked: str) -> str:
    """Личность (provider, subj) -> клиент. Порядок: известная личность -> клиент с таким phone_hash -> новый."""
    with db.lock:
        row = db.query_one("SELECT client_id FROM client_identities WHERE provider = ? AND subject_hash = ?",
                           (provider, subj))
        if row:
            return row["client_id"]
        client = db.query_one("SELECT client_id FROM clients WHERE phone_hash = ?", (p_hash,))
        if client:
            client_id = client["client_id"]
        else:
            last = db.query_one("SELECT MAX(CAST(SUBSTR(client_id, 5) AS INTEGER)) AS n FROM clients")
            client_id = f"cli_{((last['n'] if last and last['n'] else 0) + 1):04d}"
            db.execute("INSERT INTO clients (client_id, phone_hash, phone_masked, created_at) VALUES (?, ?, ?, ?)",
                       (client_id, p_hash, masked, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        db.execute("INSERT OR IGNORE INTO client_identities (provider, subject_hash, client_id) VALUES (?, ?, ?)",
                   (provider, subj, client_id))
        return client_id


def client_for_oauth(db, provider: str, uid: str, phone: str | None) -> str:
    """OAuth-вход. Один телефон через разные способы -> один client_id.
    Без телефона (провайдер не отдал) клиент создаётся по subject_hash, маска «не указан»."""
    subj = subject_hash(provider, uid)
    if phone:
        return _resolve_client(db, provider, subj, phone_hash(phone), mask_phone(phone))
    return _resolve_client(db, provider, subj, hmac_hex(f"nophone:{provider}:{uid}"), NO_PHONE_MASK)


def login_result(db, client_id: str, channel: str, **extra) -> dict:
    """Выдаёт JWT клиента и пишет `login` в аудит (без телефона: только client_id и маска)."""
    row = db.query_one("SELECT phone_masked, token_version FROM clients WHERE client_id = ?", (client_id,))
    token = create_token(config.jwt_secret(), username=client_id, role="client",
                         ttl_minutes=config.TOKEN_TTL_MINUTES, token_version=row["token_version"])
    audit.write(db, client_id, "client", "login", "client", client_id,
                {"channel": channel, "phone_masked": row["phone_masked"]})
    return {"access_token": token, "client_id": client_id, "phone_masked": row["phone_masked"],
            "role": "client", **extra}


def login_failed(db, channel: str, reason: str, masked: str | None = None, client_id: str | None = None) -> None:
    details = {"channel": channel, "reason": reason}
    if masked:
        details["phone_masked"] = masked
    audit.write(db, client_id or "-", "client" if client_id else "-", "login_failed",
                "client" if client_id else None, client_id, details)


# ---------- challenge'и ----------
def check_rate_limits(db, ip: str, p_hash: str | None) -> None:
    now = config.now()
    if p_hash:
        n = db.query_one("SELECT COUNT(*) AS n FROM auth_challenges WHERE phone_hash = ? AND created_at > ?",
                         (p_hash, now - config.PHONE_START_WINDOW))["n"]
        if n >= config.PHONE_START_LIMIT:
            raise ApiError(429, "too_many_requests", "Слишком много попыток для этого номера, подождите 10 минут")
    n = db.query_one("SELECT COUNT(*) AS n FROM auth_challenges WHERE ip = ? AND created_at > ?",
                     (ip, now - config.IP_START_WINDOW))["n"]
    n += db.query_one("SELECT COUNT(*) AS n FROM oauth_states WHERE ip = ? AND created_at > ?",
                      (ip, now - config.IP_START_WINDOW))["n"]
    if n >= config.IP_START_LIMIT:
        raise ApiError(429, "too_many_requests", "Слишком много попыток с этого адреса, подождите час")


def create_challenge(db, channel: str, phone: str, ip: str, **fields) -> dict:
    challenge_id = secrets.token_urlsafe(16)
    now = config.now()
    values = {"challenge_id": challenge_id, "channel": channel, "phone_hash": phone_hash(phone),
              "phone_masked": mask_phone(phone), "ip": ip, "created_at": now,
              "expires_at": now + config.CHALLENGE_TTL, **fields}
    cols = ", ".join(values)
    db.execute(f"INSERT INTO auth_challenges ({cols}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))
    return values


def get_challenge(db, challenge_id: str):
    """Challenge; если срок вышел — статус сразу становится expired."""
    row = db.query_one("SELECT * FROM auth_challenges WHERE challenge_id = ?", (challenge_id,))
    if row and row["status"] == "pending" and config.now() >= row["expires_at"]:
        db.execute("UPDATE auth_challenges SET status = 'expired' WHERE challenge_id = ?", (challenge_id,))
        row = db.query_one("SELECT * FROM auth_challenges WHERE challenge_id = ?", (challenge_id,))
    return row


def complete_challenge(db, row) -> str:
    """Подтверждённый challenge -> клиент (находим или создаём) и статус verified.
    Открытого телефона уже нет, есть отпечаток: личность телефонного способа = HMAC(channel:отпечаток)."""
    client_id = _resolve_client(db, row["channel"], hmac_hex(f"{row['channel']}:{row['phone_hash']}"),
                                row["phone_hash"], row["phone_masked"])
    db.execute("UPDATE auth_challenges SET status = 'verified', client_id = ? WHERE challenge_id = ?",
               (client_id, row["challenge_id"]))
    return client_id
