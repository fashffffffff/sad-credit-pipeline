"""Настройки из переменных окружения. Значения по умолчанию подходят для демо; список переменных — в README."""

import os
from dataclasses import dataclass, field

from common.secrets_guard import is_production, secret_from_env


@dataclass
class Settings:
    jwt_secret: str = "dev-jwt-secret-change-me"
    smev_secret: str = "dev-smev-secret"
    bki_secret: str = "dev-bki-secret"
    pii_pepper: str = "dev-pii-pepper"
    smev_url: str = "http://127.0.0.1:8101"
    bki_url: str = "http://127.0.0.1:8102"
    database_path: str = "backend/data/app.db"
    # пауза между этапами: чтобы на демо было видно, как заявка «едет» по конвейеру
    stage_delay_ms: int = 150
    request_timeout: float = 3.0  # таймаут одного запроса к СМЭВ/БКИ, секунды
    retry_delays: tuple[float, ...] = (0.3, 0.6)  # паузы перед 2-м и 3-м попыткой
    bcrypt_rounds: int = 12
    jwt_ttl_minutes: int = 60
    employer_check: str = "offline"  # offline | fns
    duplicate_check: bool = True  # одна заявка на паспорт; DUPLICATE_CHECK=off выключает (для демо)
    login_max_failures: int = 5  # неверных паролей подряд -> блокировка логина
    login_lock_seconds: int = 15 * 60
    login_ip_per_minute: int = 10
    fraud_threshold: float = 0.6  # fraud_score от этого порога -> предупреждение и ручная проверка
    create_per_minute: int = 60  # заявок в минуту от одного пользователя (живой оператор столько не подаёт)
    max_body_bytes: int = 64 * 1024
    simulate_max_pause_ms: int = 300  # разброс по времени между заявками при /simulate
    credentials_file: str = "secrets/credentials.local.json"  # секретные логины/пароли сотрудников (вне git)
    admin_password: str = ""  # необязательно: задаёт пароль учётки admin вместо сгенерированного (ADMIN_PASSWORD, от 12 символов)
    production: bool = False  # APP_ENV=production: закрываем /docs, секреты по умолчанию запрещены
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )


def load_settings() -> Settings:
    env = os.environ.get
    base = Settings()
    return Settings(
        jwt_secret=secret_from_env("JWT_SECRET", base.jwt_secret),
        smev_secret=secret_from_env("SMEV_HMAC_SECRET", base.smev_secret),
        bki_secret=secret_from_env("BKI_HMAC_SECRET", base.bki_secret),
        pii_pepper=secret_from_env("PII_PEPPER", base.pii_pepper),
        smev_url=env("SMEV_URL", base.smev_url),
        bki_url=env("BKI_URL", base.bki_url),
        database_path=env("DATABASE_PATH", base.database_path),
        stage_delay_ms=int(env("PIPELINE_STAGE_DELAY_MS", base.stage_delay_ms)),
        employer_check=env("EMPLOYER_CHECK", base.employer_check),
        duplicate_check=env("DUPLICATE_CHECK", "on").lower() != "off",
        production=is_production(),
        fraud_threshold=float(env("FRAUD_THRESHOLD", base.fraud_threshold)),
        credentials_file=env("CREDENTIALS_FILE", base.credentials_file),
        admin_password=env("ADMIN_PASSWORD", ""),
        cors_origins=[o.strip() for o in env("CORS_ORIGINS", ",".join(base.cors_origins)).split(",") if o.strip()],
    )
