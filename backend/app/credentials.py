"""Учётные записи сотрудников: секретные логины и длинные случайные пароли, хранятся ТОЛЬКО локально (вне git).

Как это работает:
  * при старте читаем файл учёток (env CREDENTIALS_FILE, по умолчанию secrets/credentials.local.json);
  * если файла нет — генерируем 3 учётки (по одной на роль), пишем файл с правами 600 и рядом читаемую таблицу .md;
  * в БД попадают только bcrypt-хэши; в лог пишем путь к файлу, но не пароли.
Самостоятельной регистрации нет: аккаунты сотрудникам выдаёт администратор (как в банке).
"""

import json
import logging
import os
import secrets
from pathlib import Path

from backend.app.security.passwords import check_password

log = logging.getLogger("credentials")

# роль -> префикс логина. Логин = префикс + случайный хвост, поэтому угадать его по роли нельзя
ROLE_PREFIXES = [("underwriter", "uw"), ("auditor", "audit"), ("admin", "admin")]
ROLE_TITLES = {"underwriter": "Андеррайтер", "auditor": "Аудитор", "admin": "Администратор (пульт стенда)"}

# Старые публичные демо-пароли «логин+123» (были в репозитории). Они больше нигде не должны работать.
LEGACY_USERS = ["operator", "risk", "underwriter", "auditor", "admin"]


def generate_credentials() -> list[dict]:
    """3 учётки: логин вида op.7f3a9c, пароль 24 символа из secrets.token_urlsafe."""
    return [{"username": f"{prefix}.{secrets.token_hex(3)}", "role": role, "password": secrets.token_urlsafe(18)}
            for role, prefix in ROLE_PREFIXES]


def _write_private(path: Path, text: str) -> None:
    """Создаёт файл сразу с правами 600 (читать только владельцу), а не «создали и потом закрыли»."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def _markdown(credentials: list[dict]) -> str:
    rows = "\n".join(f"| `{c['username']}` | {ROLE_TITLES.get(c['role'], c['role'])} (`{c['role']}`) | `{c['password']}` |"
                     for c in credentials)
    return ("# Данные для входа (локально, НЕ коммитить)\n\n"
            "Файл создан автоматически при первом запуске. Папка `secrets/` в `.gitignore`.\n\n"
            f"| Логин | Роль | Пароль |\n|---|---|---|\n{rows}\n")


# Роли версии 5, которых больше нет: оператор (заявку теперь подаёт сам клиент) и риск-менеджер (стал андеррайтером)
RETIRED_ROLES = {"operator", "risk_manager"}


def _migrate(file: Path, credentials: list[dict]) -> list[dict]:
    """Файл от прошлой версии: убираем учётки удалённых ролей и создаём учётки для новых ролей.

    Пароли оставшихся учёток не меняются. Файл перезаписывается только если что-то изменилось.
    """
    kept = [c for c in credentials if c["role"] not in RETIRED_ROLES]
    have = {c["role"] for c in kept}
    added = [{"username": f"{prefix}.{secrets.token_hex(3)}", "role": role, "password": secrets.token_urlsafe(18)}
             for role, prefix in ROLE_PREFIXES if role not in have]
    if len(kept) == len(credentials) and not added:
        return credentials
    result = kept + added
    _write_private(file, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    _write_private(file.with_suffix(".md"), _markdown(result))
    log.warning("Файл учёток %s обновлён под новые роли: убрано %d, добавлено %d (%s). Пароли в лог не пишутся.",
                file, len(credentials) - len(kept), len(added), ", ".join(c["role"] for c in added) or "—")
    return result


def load_or_create_credentials(path: str) -> list[dict]:
    file = Path(path)
    if file.exists():
        if os.name == "posix" and file.stat().st_mode & 0o077:  # файл с паролями открыт группе/всем: закрываем
            os.chmod(file, 0o600)
            log.warning("У файла учёток %s были слишком широкие права, выставлено 600", file)
        credentials = json.loads(file.read_text(encoding="utf-8"))
        for c in credentials:
            if not {"username", "role", "password"} <= set(c):
                raise RuntimeError(f"{path}: у каждой учётки должны быть username, role, password")
            if c["role"] not in dict(ROLE_PREFIXES) and c["role"] not in RETIRED_ROLES:
                # опечатка в роли дала бы учётку, которая ничего не может
                raise RuntimeError(f"{path}: неизвестная роль {c['role']!r} у {c['username']!r}")
        return _migrate(file, credentials)
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)  # папка с паролями — только владельцу
    credentials = generate_credentials()
    _write_private(file, json.dumps(credentials, ensure_ascii=False, indent=2) + "\n")
    _write_private(file.with_suffix(".md"), _markdown(credentials))
    log.warning("Созданы учётные записи для входа: %s (таблица — %s). Пароли в лог не пишутся.",
                file, file.with_suffix(".md"))
    return credentials


def with_admin_override(credentials: list[dict], admin_password: str) -> list[dict]:
    """ADMIN_PASSWORD (необязательный env) задаёт пароль администратора вместо сгенерированного. В файл он не пишется."""
    if not admin_password:
        return credentials
    if len(admin_password) < 12:
        raise RuntimeError("ADMIN_PASSWORD короче 12 символов")
    return [{**c, "password": admin_password} if c["role"] == "admin" else c for c in credentials]


def credentials_by_role(path: str) -> dict[str, dict]:
    """Для CLI и скриптов: {роль: {username, password}} из файла учёток. Файл не создаём — его делает backend."""
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"Нет файла с учётками {path}. Запустите backend один раз — он создаст его сам.")
    return {c["role"]: c for c in json.loads(file.read_text(encoding="utf-8"))}
