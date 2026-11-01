"""Один раз делает маленький пул (20 заявок) из реальных данных Альфа-Банка для тестов.
Запуск из корня: uv run python emulators/tests/fixtures/make_fixtures.py <test_data_0.pq>"""

import json
import sys
from pathlib import Path

import pandas as pd

out = Path(__file__).parent
df = pd.read_parquet(sys.argv[1])
ids = sorted(df["id"].unique())[:20]
pool = df[df["id"].isin(ids)]
pool.to_parquet(out / "pool_small.parquet", index=False)
# разные истории для демо-паспортов (в тестах им важна только разница, не качество)
json.dump({"good": int(ids[0]), "bad": int(ids[1]), "borderline": int(ids[2])}, open(out / "demo.json", "w"))
print(len(pool), "кредитов,", len(ids), "заявок")
