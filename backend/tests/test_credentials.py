"""Секретные учётки: генерация, права на файл, отсутствие паролей в логе, отказ от старых демо-паролей."""

import json
import os
import stat

import httpx

from backend.app.config import Settings
from backend.app.credentials import generate_credentials, load_or_create_credentials, with_admin_override
from backend.app.db import Database
from backend.app.factory import create_app
from backend.app.security.passwords import check_password, hash_password


def test_generated_credentials_are_unguessable():
    """Ожидаемый результат: 3 учётки — по одной на роль; логины с случайным хвостом (не operator/admin);
    пароли не короче 16 символов и все разные."""
    creds = generate_credentials()
    assert sorted(c["role"] for c in creds) == ["admin", "auditor", "underwriter"]
    assert all(len(c["password"]) >= 16 for c in creds)
    assert len({c["password"] for c in creds}) == 3
    assert all(c["username"] not in ("operator", "risk", "underwriter", "auditor", "admin") and "." in c["username"] for c in creds)
    assert generate_credentials()[0]["username"] != creds[0]["username"] or generate_credentials()[0]["password"] != creds[0]["password"]


def test_file_created_with_mode_600_and_reused(tmp_path, caplog):
    """Первый запуск без файла.

    Ожидаемый результат: создаются credentials.local.json и .md с правами 600; в .md таблица логин/роль/пароль;
    в лог пишется путь, но не пароли; второй запуск читает тот же файл и ничего не перегенерирует.
    """
    path = tmp_path / "secrets" / "credentials.local.json"
    with caplog.at_level("INFO"):
        creds = load_or_create_credentials(str(path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    md = path.with_suffix(".md")
    assert stat.S_IMODE(os.stat(md).st_mode) == 0o600
    text = md.read_text(encoding="utf-8")
    assert all(c["username"] in text and c["password"] in text for c in creds)
    assert str(path) in caplog.text
    assert not any(c["password"] in caplog.text for c in creds)
    assert load_or_create_credentials(str(path)) == creds == json.loads(path.read_text(encoding="utf-8"))


def test_db_stores_only_bcrypt_hashes(tmp_path):
    """Ожидаемый результат: в БД нет открытых паролей — только bcrypt-хэши, и они подходят к паролям из файла."""
    creds = generate_credentials()
    db = Database(str(tmp_path / "t.db"), 4, creds)
    rows = {r["username"]: r["password_hash"] for r in db.query_all("SELECT username, password_hash FROM users")}
    assert set(rows) == {c["username"] for c in creds}
    for c in creds:
        assert rows[c["username"]].startswith("$2b$") and c["password"] not in rows[c["username"]]
        assert check_password(c["password"], rows[c["username"]])


def test_legacy_demo_users_are_retired(tmp_path):
    """Старая БД с демо-пользователями (пароль «логин+123»).

    Ожидаемый результат: при старте они удаляются, вместо них — учётки из файла; пользователь со старым логином
    и изменённым (не демо) паролем не трогается.
    """
    path = str(tmp_path / "old.db")
    old = Database(path, 4, [])
    for name, role in (("operator", "operator"), ("risk", "risk_manager"), ("auditor", "auditor"), ("admin", "admin")):
        old.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (name, hash_password(name + "123", 4), role))
    old.execute("UPDATE users SET password_hash = ? WHERE username = 'risk'", (hash_password("custom-long-pass-1", 4),))
    old.conn.close()

    creds = generate_credentials()
    db = Database(path, 4, creds)
    names = {r["username"] for r in db.query_all("SELECT username FROM users")}
    assert not names & {"operator", "auditor", "admin"}
    assert "risk" in names  # пароль не демонстрационный — не трогаем
    assert {c["username"] for c in creds} <= names


async def test_old_demo_passwords_do_not_log_in(tmp_path):
    """Ожидаемый результат: после старта на старой БД вход по admin/admin123 и по operator/operator123 невозможен (401),
    а по паролям из файла — работает."""
    path = str(tmp_path / "old.db")
    old = Database(path, 4, [])
    for name in ("operator", "admin"):
        old.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (name, hash_password(name + "123", 4), name))
    old.conn.close()
    creds = generate_credentials()
    app = create_app(Settings(database_path=path, bcrypt_rounds=4), credentials=creds)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        for name in ("operator", "admin"):
            r = await client.post("/api/v1/auth/staff/login", json={"username": name, "password": name + "123"})
            assert r.status_code == 401
        ok = await client.post("/api/v1/auth/staff/login", json={"username": creds[0]["username"], "password": creds[0]["password"]})
        assert ok.status_code == 200 and ok.json()["role"] == creds[0]["role"]


def test_changed_password_in_file_updates_hash_and_revokes_tokens(tmp_path):
    """Пароль учётки в файле изменили.

    Ожидаемый результат: при старте хэш обновляется, старый пароль перестаёт подходить, токены пользователя отзываются
    (token_version растёт).
    """
    path = str(tmp_path / "t.db")
    creds = generate_credentials()
    Database(path, 4, creds).conn.close()
    changed = [dict(c) for c in creds]
    changed[0]["password"] = "brand-new-long-password-1"
    db = Database(path, 4, changed)
    row = db.query_one("SELECT password_hash, token_version FROM users WHERE username = ?", (creds[0]["username"],))
    assert check_password("brand-new-long-password-1", row["password_hash"])
    assert not check_password(creds[0]["password"], row["password_hash"])
    assert row["token_version"] == 1


def test_admin_password_env_override():
    """ADMIN_PASSWORD (если задан) подменяет пароль администратора, остальные не меняются; короткий пароль — ошибка."""
    creds = generate_credentials()
    out = with_admin_override(creds, "long-enough-admin-pass")
    assert [c for c in out if c["role"] == "admin"][0]["password"] == "long-enough-admin-pass"
    assert [c for c in out if c["role"] != "admin"] == [c for c in creds if c["role"] != "admin"]
    assert with_admin_override(creds, "") == creds
    try:
        with_admin_override(creds, "short")
    except RuntimeError:
        pass
    else:
        raise AssertionError("короткий ADMIN_PASSWORD должен отклоняться")


def test_secrets_dir_is_git_ignored():
    """Ожидаемый результат: папка secrets/ перечислена в .gitignore."""
    from pathlib import Path
    assert "secrets/" in Path(".gitignore").read_text().split()


def test_old_v5_file_is_migrated_without_touching_kept_passwords(tmp_path):
    import json
    from backend.app.credentials import load_or_create_credentials
    path = tmp_path / "credentials.local.json"
    old = [{"username": "op.aaaaaa", "role": "operator", "password": "p1" * 12},
           {"username": "risk.bbbbbb", "role": "risk_manager", "password": "p2" * 12},
           {"username": "audit.cccccc", "role": "auditor", "password": "p3" * 12},
           {"username": "admin.dddddd", "role": "admin", "password": "p4" * 12}]
    path.write_text(json.dumps(old))
    creds = load_or_create_credentials(str(path))
    roles = {c["role"]: c for c in creds}
    assert set(roles) == {"underwriter", "auditor", "admin"}
    assert roles["auditor"]["password"] == "p3" * 12 and roles["admin"]["password"] == "p4" * 12
    assert json.loads(path.read_text()) == creds and (tmp_path / "credentials.local.md").exists()
