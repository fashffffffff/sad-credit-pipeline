"""Готовит пул кредитных историй для Go-эмулятора БКИ (POST /bki/report).

Читает ml/artifacts/bki_pool.parquet (реальные обезличенные истории Альфа-Банка из test-части),
считает PD каждой истории моделью и делит истории на 3 корзины по PD:
нижняя треть — good (хорошие), средняя — medium, верхняя — bad.
Пишет services/bki/data/bki_pool.json.gz: {"good": [история, ...], "medium": [...], "bad": [...]},
история — список кредитов (поля как в датасете, без id), по порядку rn.

Запуск из корня репозитория: uv run python -m ml.export_bki_pool
"""

import gzip
import json
from pathlib import Path

import pandas as pd

from ml.credit_model import load_model

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "ml/artifacts/bki_pool.parquet"
OUT = ROOT / "services/bki/data/bki_pool.json.gz"
BUCKETS = ("good", "medium", "bad")


def main() -> None:
    df = pd.read_parquet(POOL)
    model = load_model()

    # id заявки -> кредиты по порядку rn (поля — целые числа, как в датасете)
    histories: dict[int, list[dict]] = {}
    for app_id, group in df.groupby("id"):
        group = group.sort_values("rn").drop(columns=["id"])
        histories[int(app_id)] = [{k: int(v) for k, v in row.items()} for row in group.to_dict("records")]

    # PD каждой истории; при равенстве PD порядок задаёт id — результат воспроизводим
    scored = sorted((model.predict_history(loans).pd, app_id) for app_id, loans in histories.items())
    third = len(scored) / 3
    buckets: dict[str, list[list[dict]]] = {name: [] for name in BUCKETS}
    pds: dict[str, list[float]] = {name: [] for name in BUCKETS}
    for position, (pd_value, app_id) in enumerate(scored):
        name = BUCKETS[min(int(position // third), 2)]
        buckets[name].append(histories[app_id])
        pds[name].append(pd_value)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0: одинаковый вход даёт одинаковый файл, git не видит лишних изменений
    with gzip.GzipFile(OUT, "wb", compresslevel=9, mtime=0) as f:
        f.write(json.dumps(buckets, separators=(",", ":")).encode())

    for name in BUCKETS:
        print(f"{name:7} историй: {len(buckets[name]):5}  PD {min(pds[name]):.4f} … {max(pds[name]):.4f}")
    print(f"записано {OUT} ({OUT.stat().st_size / 1024:.0f} КБ)")


if __name__ == "__main__":
    main()
