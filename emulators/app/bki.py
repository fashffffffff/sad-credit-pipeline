"""Эмулятор БКИ (кредитные истории). Запуск: uv run uvicorn emulators.app.bki:app --port 8102

Отдаёт настоящие обезличенные кредитные истории из датасета Альфа-Банка (тестовая часть,
модель их не видела). Пул историй готовит ML-сессия: ml/artifacts/bki_pool.parquet.
"""

import hashlib
import json
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from common.secrets_guard import secret_from_env
from emulators.app.common import (BKI_NOT_FOUND, ServiceUnavailable, create_app, log, make_rng,
                                  normalize_passport)

ROOT = Path(__file__).resolve().parents[2]
NOT_FOUND_SHARE = 0.04  # ~4% паспортов без кредитной истории
# магический паспорт -> ключ в bki_demo.json
MAGIC_DEMO = {"0000000001": "good", "0000000002": "bad", "0000000005": "borderline", "0000000006": "good"}
SELF_BAN_SHARE = 0.02  # ~2% паспортов установили самозапрет на кредиты (закон с 1 марта 2025)
SELF_BAN_MAGIC = {"0000000006"}


def self_ban_for(passport: str) -> tuple[bool, str | None]:
    """Самозапрет на кредиты (Госуслуги): детерминированно по паспорту. Возвращает (есть ли, дата установки)."""
    key = normalize_passport(passport)
    rng = make_rng(passport + "selfban")
    if key in SELF_BAN_MAGIC or rng.random() < SELF_BAN_SHARE:
        since = date(2025, 3, 1) + timedelta(days=rng.randint(0, 400))
        return True, since.isoformat()
    return False, None


class ReportRequest(BaseModel):
    passport: str
    full_name: str
    birth_date: str


class HistoryStore:
    """Пул историй в памяти: {id заявки: [кредиты, отсортированные по rn]}."""

    def __init__(self, pool_path: Path, demo_path: Path):
        self.histories: dict[int, list[dict]] = {}
        self.demo: dict[str, int] = {}
        self.problems: list[str] = []

        if pool_path.exists():
            df = pd.read_parquet(pool_path)
            for app_id, group in df.groupby("id"):
                group = group.sort_values("rn").drop(columns=["id"])
                self.histories[int(app_id)] = [{k: int(v) for k, v in row.items()} for row in group.to_dict("records")]
        else:
            self.problems.append("нет пула историй")
            log.error("[bki] не найден пул историй %s — эмулятор не сможет отвечать", pool_path)

        if demo_path.exists():
            self.demo = json.loads(demo_path.read_text())
        else:
            self.problems.append("нет bki_demo.json")
            log.error("[bki] не найден %s — магические паспорта 1/2/5 не будут особенными", demo_path)

        self.ids = sorted(self.histories)  # порядок фиксирован, чтобы индекс был воспроизводим

    def loans_for(self, passport: str) -> list[dict]:
        """История человека по паспорту. Пустой список = в БКИ его нет."""
        key = normalize_passport(passport)
        if key in BKI_NOT_FOUND:
            return []
        if key in MAGIC_DEMO and MAGIC_DEMO[key] in self.demo:
            return self.histories[self.demo[MAGIC_DEMO[key]]]
        if make_rng(passport + "found").random() < NOT_FOUND_SHARE:
            return []
        # номер истории = sha256(паспорт) по модулю числа историй в пуле
        index = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(self.ids)
        return self.histories[self.ids[index]]


def synthetic_money(passport: str, n_open_loans: int) -> dict:
    """Суммы и запросы в датасете отсутствуют, поэтому генерируем их сами из паспорта.

    Платёж считаем только по ОТКРЫТЫМ кредитам: по закрытым человек уже ничего не платит.
    """
    rng = make_rng(passport + "money")
    # ~1,5–5 тыс. ₽ на открытый кредит: в истории много кредитных карт с небольшим минимальным платежом
    payment = int(n_open_loans * rng.uniform(1500, 5000) / 100) * 100
    inq_30d = rng.randint(0, 3)
    return dict(
        monthly_debt_payment=payment,
        total_debt=int(payment * rng.uniform(15, 30) / 1000) * 1000,  # платёж × 15–30 месяцев
        inquiries_30d=inq_30d,
        inquiries_6m=inq_30d + rng.randint(0, 3),  # запросы за 30 дней входят в запросы за 6 месяцев
    )


def make_app(pool_path: Path, demo_path: Path, secret: str, **kwargs):
    store = HistoryStore(pool_path, demo_path)

    def build_response(req: ReportRequest) -> dict:
        if not store.histories:
            raise ServiceUnavailable("нет пула историй")
        loans = store.loans_for(req.passport)
        ban, ban_since = self_ban_for(req.passport)
        report = {"report_id": f"bki-{uuid.uuid4()}", "found": bool(loans), "loans": loans,
                  "self_ban": ban, "self_ban_since": ban_since}
        if loans:
            # fclose_flag = 1: фактическая дата закрытия не определена -> кредит ещё открыт
            n_open = sum(1 for ln in loans if ln.get("fclose_flag") == 1)
            return {**report, **synthetic_money(req.passport, n_open)}
        return {**report, "monthly_debt_payment": None, "total_debt": None,
                "inquiries_30d": None, "inquiries_6m": None}

    def health_info() -> dict:
        if store.problems:
            return {"status": "degraded", "reason": ", ".join(store.problems)}
        return {}

    return create_app("bki", "/bki/v1/report", ReportRequest, build_response, secret,
                      health_info=health_info, **kwargs)


_pool = Path(os.getenv("BKI_POOL_PATH", ROOT / "ml/artifacts/bki_pool.parquet"))
app = make_app(
    _pool,
    Path(os.getenv("BKI_DEMO_PATH", _pool.with_name("bki_demo.json"))),
    secret_from_env("BKI_HMAC_SECRET", "dev-bki-secret"),
)
