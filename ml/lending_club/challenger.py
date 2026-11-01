"""Модель-соперник: TabICL (табличная foundation-модель) против CatBoost.

Запуск: uv run --group challenger python -m ml.lending_club.challenger
Обе модели видят ОДНИ И ТЕ ЖЕ подвыборки. Результат дописывается в metrics.json -> challenger.
В прод эта модель не идёт.
"""
import json
import time

import numpy as np
import torch
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from ml.lending_club.features import load_prepared
from ml.lending_club.scoring import CAT_FEATURES, FEATURES, PURPOSES
from ml.lending_club.train import ART_DIR, SEED, catboost_params, split

N_TRAIN, N_TEST = 10_000, 5_000


def pick_device() -> str:
    # MPS — видеокарта Mac; иначе обычный процессор
    return "mps" if torch.backends.mps.is_available() else "cpu"


def ms_per_prediction(predict, x_row, repeats=10) -> float:
    """Среднее время предсказания ОДНОЙ заявки (как в проде), мс."""
    predict(x_row)  # прогрев
    t = time.perf_counter()
    for _ in range(repeats):
        predict(x_row)
    return (time.perf_counter() - t) / repeats * 1000


def main():
    from tabicl import TabICLClassifier

    tr, te = split(load_prepared())              # тот же out-of-time сплит, что у основной модели
    # Подвыборки: train до 10 000 (из 2012–2015), test 5 000 (из 2016–2017), обе стратифицированно
    X_tr, _, y_tr, _ = train_test_split(tr[FEATURES], tr["target"], train_size=N_TRAIN, stratify=tr["target"], random_state=SEED)
    X_te, _, y_te, _ = train_test_split(te[FEATURES], te["target"], train_size=N_TEST, stratify=te["target"], random_state=SEED)
    device = pick_device()
    print(f"Устройство: {device}; train={len(X_tr)}, test={len(X_te)}")

    # CatBoost на той же подвыборке — для честного сравнения
    cb = CatBoostClassifier(**catboost_params(iterations=300))   # на 10 тыс. строк 1500 деревьев переобучаются
    cb.fit(X_tr, y_tr)
    p_cb = cb.predict_proba(X_te)[:, 1]

    # TabICL: purpose кодируем числом (0..3), пропуски заполняем медианой train
    def enc(X):
        X = X.copy()
        X["purpose"] = X["purpose"].map({p: i for i, p in enumerate(PURPOSES)})
        return X.astype(float).fillna(med)
    med = X_tr.drop(columns=CAT_FEATURES).median()
    try:
        clf = TabICLClassifier(device=device, random_state=SEED)
        clf.fit(enc(X_tr).values, y_tr.values)
        p_ti = clf.predict_proba(enc(X_te).values)[:, 1]
    except Exception as e:  # например, веса недоступны без логина/лицензии HuggingFace
        raise SystemExit(f"TabICL не запустился: {type(e).__name__}: {e}\n"
                         f"Если нужен логин/лицензия HuggingFace — это решает человек, обходить не будем.")

    one = X_te.iloc[[0]]
    one_f = enc(one).values
    res = {
        "model": "TabICL", "n_train": len(X_tr), "n_test": len(X_te),
        "roc_auc": round(roc_auc_score(y_te, p_ti), 4),
        "gini": round(2 * roc_auc_score(y_te, p_ti) - 1, 4),
        "catboost_roc_auc_same_split": round(roc_auc_score(y_te, p_cb), 4),
        "mean_pd": round(float(p_ti.mean()), 4),
        "actual_default_rate": round(float(y_te.mean()), 4),
        "ms_per_prediction": round(ms_per_prediction(clf.predict_proba, one_f), 2),
        "catboost_ms_per_prediction": round(ms_per_prediction(cb.predict_proba, one), 2),
        "catboost_mean_pd": round(float(p_cb.mean()), 4),
        "device": device,
    }
    print(json.dumps(res, ensure_ascii=False, indent=1))
    path = ART_DIR / "metrics.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m["challenger"] = res
    path.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
