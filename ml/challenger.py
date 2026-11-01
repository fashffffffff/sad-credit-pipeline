"""Модель-соперник: TabICL (табличная foundation-модель) против CatBoost на агрегатах истории.

Запуск: uv run --group challenger python -m ml.challenger
Обе модели видят ОДНИ И ТЕ ЖЕ подвыборки (train 10 000, test 5 000 из отложенной части).
Результат дописывается в metrics.json -> challenger. В прод эта модель не идёт.
"""
import json
import time

import torch
from catboost import CatBoostClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from ml.credit_model.history import FEATURES
from ml.train import ART_DIR, SEED, catboost_params, load_aggregates, split

N_TRAIN, N_TEST = 10_000, 5_000


def ms_per_prediction(predict, x_row, repeats=10) -> float:
    """Среднее время предсказания ОДНОЙ заявки (как в проде), мс."""
    predict(x_row)  # прогрев
    t = time.perf_counter()
    for _ in range(repeats):
        predict(x_row)
    return (time.perf_counter() - t) / repeats * 1000


def main():
    from tabicl import TabICLClassifier

    tr_all, _ = load_aggregates()
    X_tr, X_ho, y_tr, y_ho = split(tr_all)       # тот же сплит, что у основной модели
    X_tr, _, y_tr, _ = train_test_split(X_tr, y_tr, train_size=N_TRAIN, stratify=y_tr, random_state=SEED)
    X_te, _, y_te, _ = train_test_split(X_ho, y_ho, train_size=N_TEST, stratify=y_ho, random_state=SEED)
    # MPS — видеокарта Mac; иначе процессор
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Устройство: {device}; train={len(X_tr)}, test={len(X_te)}")

    # CatBoost на той же подвыборке; на 10 тыс. строк 1000 деревьев переобучаются, берём 300
    cb = CatBoostClassifier(**catboost_params(iterations=300)).fit(X_tr, y_tr)
    p_cb = cb.predict_proba(X_te)[:, 1]
    try:
        clf = TabICLClassifier(device=device, random_state=SEED)
        clf.fit(X_tr[FEATURES].values, y_tr.values)
        p_ti = clf.predict_proba(X_te[FEATURES].values)[:, 1]
    except Exception as e:  # например, веса недоступны без логина/лицензии HuggingFace
        raise SystemExit(f"TabICL не запустился: {type(e).__name__}: {e}\n"
                         f"Если нужен логин/лицензия HuggingFace — это решает человек, обходить не будем.")
    one = X_te.iloc[[0]]
    auc_ti = roc_auc_score(y_te, p_ti)
    res = {
        "model": "TabICL", "n_train": len(X_tr), "n_test": len(X_te),
        "roc_auc": round(auc_ti, 4), "gini": round(2 * auc_ti - 1, 4),
        "catboost_roc_auc_same_split": round(roc_auc_score(y_te, p_cb), 4),
        "mean_pd": round(float(p_ti.mean()), 4),
        "actual_default_rate": round(float(y_te.mean()), 4),
        "ms_per_prediction": round(ms_per_prediction(clf.predict_proba, one.values), 2),
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
