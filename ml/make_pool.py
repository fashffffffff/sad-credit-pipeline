"""Пул историй для эмулятора БКИ.  Запуск: uv run python -m ml.make_pool

Берём ~3000 случайных заявок из test (модель их не видела) и сохраняем СЫРЫЕ строки
(все колонки, включая id) в ml/artifacts/bki_pool.parquet. Из пула выбираем три демо-истории
для «магических» паспортов -> bki_demo.json {good, bad, borderline}.
"""
import json
import os

import numpy as np
import pandas as pd

from ml.credit_model import artifacts_dir, load_model
from ml.train import SEED, alfa_dir

N_APPS = 3000
MIN_LOANS = 3       # демо-истории должны выглядеть как настоящие: хотя бы 3 кредита


def main():
    folder = alfa_dir() / "test_data"
    files = sorted(folder.glob("*.pq"), key=lambda p: int(p.stem.split("_")[-1]))
    rng = np.random.default_rng(SEED)
    parts = []
    per_file = N_APPS // len(files) + 1
    for f in files:
        df = pd.read_parquet(f)
        ids = df["id"].unique()
        take = rng.choice(ids, size=min(per_file, len(ids)), replace=False)
        parts.append(df[df["id"].isin(take)])
    pool = pd.concat(parts)
    keep = rng.choice(pool["id"].unique(), size=N_APPS, replace=False)
    pool = pool[pool["id"].isin(keep)].sort_values(["id", "rn"]).reset_index(drop=True)

    model = load_model()
    rows = []
    for app_id, g in pool.groupby("id"):
        loans = g.drop(columns="id").to_dict("records")
        p = model.predict_history(loans)
        rows.append((int(app_id), len(loans), p.pd, p.score, p.features["has_current_overdue"]))
    res = pd.DataFrame(rows, columns=["id", "n", "pd", "score", "cur"])
    ok = res[res.n >= MIN_LOANS]
    # «хорошая» история — с самой низкой PD среди не упёршихся в потолок 850 (там сумма вкладов обрезана)
    good = ok[ok.score < 850].sort_values("pd").iloc[0]
    bad = ok[ok.cur == 1].sort_values("pd", ascending=False).iloc[0]
    mid = ok[ok.score.between(620, 660)]
    border = mid.iloc[(mid.score - 640).abs().argsort()[:1]].iloc[0]
    demo = {"good": int(good.id), "bad": int(bad.id), "borderline": int(border.id)}

    out = artifacts_dir()
    pool.to_parquet(out / "bki_pool.parquet", index=False)
    (out / "bki_demo.json").write_text(json.dumps(demo), encoding="utf-8")
    print("Пул:", pool["id"].nunique(), "заявок,", len(pool), "строк,",
          round((out / "bki_pool.parquet").stat().st_size / 1e6, 2), "МБ")
    for k, i in demo.items():
        r = res[res.id == i].iloc[0]
        print(f"  {k}: id={i}, кредитов={int(r.n)}, PD={r.pd:.3f}, балл={int(r.score)}")


if __name__ == "__main__":
    main()
