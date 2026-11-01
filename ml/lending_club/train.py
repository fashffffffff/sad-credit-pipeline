"""Обучение кредитной модели на Lending Club.  Запуск: uv run python -m ml.lending_club.train

LENDING_CLUB_CSV — путь к loans_2007_2020Q3.csv. ML_ARTIFACTS_DIR — куда писать результат
(по умолчанию ml/artifacts).

Схема (out-of-time, как в настоящих банках — тестируем на будущем, которого модель не видела):
  обучение: выдачи 2012–2015 (внутри: подгонка 2012–2014, ранняя остановка по 2015),
  тест: выдачи 2016–2017.
Дальше: логрегрессия и CatBoost -> якорь шкалы BASE_ODDS -> метрики -> model.cbm, metrics.json, reference.json.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ml.lending_club.features import load_prepared
from ml.lending_club.scoring import (CAT_FEATURES, FACTOR, FEATURE_LABELS_RU, FEATURES, THRESHOLDS, raw_score)

ROOT = Path(__file__).parent
ART_DIR = Path(os.environ.get("ML_ARTIFACTS_DIR", ROOT.parent / "artifacts" / "lending_club"))
SEED = 42
TRAIN_YEARS, VAL_YEAR, TEST_YEARS = (2012, 2015), 2015, (2016, 2017)

# Монотонность: больше просрочек -> PD не падает (+1); выше скоринг бюро -> PD не растёт (-1).
# Это бизнес-логика, и защита от случайных «провалов» дерева на шуме.
MONOTONE = {"delinq_2y": 1, "overdue_90_plus": 1, "bureau_score": -1}


def catboost_params(iterations=1500) -> dict:
    """Общие параметры CatBoost (train и challenger). Без auto_class_weights — иначе
    вероятности искажаются и калибровка пропадает. purpose — категориальный признак."""
    return dict(iterations=iterations, depth=6, learning_rate=0.05, random_seed=SEED,
                monotone_constraints=MONOTONE, cat_features=CAT_FEATURES,
                verbose=0, allow_writing_files=False)


def split(df: pd.DataFrame):
    """Разбиение по ВРЕМЕНИ выдачи: train 2012–2015, test 2016–2017."""
    tr = df[df["issue_year"].between(*TRAIN_YEARS)]
    te = df[df["issue_year"].between(*TEST_YEARS)]
    return tr, te


def ks_stat(y, p) -> float:
    """KS = максимальный разрыв между долей пойманных плохих (TPR) и ошибочно отклонённых хороших (FPR)."""
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def sample_curve(fpr, tpr, n=50):
    """Оставляем ~50 точек ROC-кривой для графика на фронтенде."""
    idx = np.unique(np.linspace(0, len(fpr) - 1, n).astype(int))
    return [[round(float(fpr[i]), 4), round(float(tpr[i]), 4)] for i in idx]


def choose_base_odds(raw_log_odds_bad: np.ndarray) -> float:
    """Подбираем якорь шкалы BASE_ODDS при фиксированных порогах 680/600.

    Балл >= 680 <=> ln(шансы хорошего) >= ln(B) + (680-600)/FACTOR. Для доли одобрений a
    нужный порог — (1-a)-квантиль log-odds хорошего, отсюда ln(B). Перебираем a от 60% до 70%
    и берём ту, где ручная проверка ближе всего к 15% (цель 10–20%)."""
    good = -raw_log_odds_bad
    best = None
    for a in np.arange(0.60, 0.701, 0.01):
        ln_b = np.quantile(good, 1 - a) - (THRESHOLDS["approve"] - 600) / FACTOR
        s = 600 + FACTOR * (good - ln_b)
        review = np.mean((s >= THRESHOLDS["review"]) & (s < THRESHOLDS["approve"]))
        if best is None or abs(review - 0.15) < best[0]:
            best = (abs(review - 0.15), float(np.exp(ln_b)))
    return round(best[1], 3)


def psi(expected: np.ndarray, actual: np.ndarray, bins=10) -> float:
    """PSI (Population Stability Index) — насколько распределение баллов «уехало».
    Границы — децили обучающих баллов; PSI < 0.1 стабильно, 0.1–0.25 заметный сдвиг, > 0.25 сильный."""
    edges = np.quantile(expected, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-6, None), np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def calibration_table(p, y):
    """По корзинам PD: средняя предсказанная вероятность vs реальная доля дефолтов."""
    edges = [0, .05, .10, .15, .20, .25, .30, .40, .50, 1.0001]
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() == 0:
            continue
        rows.append({"pd_bin": f"{lo*100:.0f}-{min(hi,1)*100:.0f}%",
                     "predicted": round(float(p[m].mean()), 4),
                     "actual": round(float(y[m].mean()), 4), "n": int(m.sum())})
    return rows


def make_logreg():
    """Базовая логрегрессия: числа -> медиана + индикатор пропуска + масштаб; purpose -> one-hot."""
    num = [f for f in FEATURES if f not in CAT_FEATURES]
    pre = ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler()), num),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES)])
    return make_pipeline(pre, LogisticRegression(max_iter=2000))


def main():
    t0 = time.time()
    df = load_prepared()
    tr, te = split(df)
    X_tr, y_tr, X_te, y_te = tr[FEATURES], tr["target"], te[FEATURES], te["target"].values
    print(f"Train {len(tr)} (2012–15), test {len(te)} (2016–17); "
          f"доля дефолтов train {y_tr.mean():.2%}, test {y_te.mean():.2%}")

    logreg = make_logreg().fit(X_tr, y_tr)
    auc_lr = roc_auc_score(y_te, logreg.predict_proba(X_te)[:, 1])

    # Ранняя остановка по времени: подгоняем на 2012–2014, проверяем на 2015 (позже, чем train),
    # так выбранное число деревьев не «подглядывает» в тест. Затем переобучаем на всём train.
    fit_part, val_part = tr[tr["issue_year"] < VAL_YEAR], tr[tr["issue_year"] == VAL_YEAR]
    probe = CatBoostClassifier(**catboost_params(), early_stopping_rounds=100)
    probe.fit(fit_part[FEATURES], fit_part["target"], eval_set=(val_part[FEATURES], val_part["target"]))
    best_iters = probe.get_best_iteration() + 1
    model = CatBoostClassifier(**catboost_params(iterations=best_iters))
    model.fit(X_tr, y_tr)
    train_seconds = time.time() - t0

    p_te = model.predict_proba(X_te)[:, 1]
    auc = roc_auc_score(y_te, p_te)
    fpr, tpr, _ = roc_curve(y_te, p_te)

    # Якорь шкалы подбираем по тесту (в проде он фиксирован в metrics.json -> scale)
    raw = model.predict(X_te, prediction_type="RawFormulaVal")
    base_odds = choose_base_odds(raw)
    to_score = lambda r: np.clip(600 + FACTOR * (-r - np.log(base_odds)), 300, 850)
    scores = to_score(raw)
    train_scores = to_score(model.predict(X_tr, prediction_type="RawFormulaVal"))
    bins = list(range(300, 851, 25))
    good_hist, _ = np.histogram(scores[y_te == 0], bins=bins)
    bad_hist, _ = np.histogram(scores[y_te == 1], bins=bins)
    appr = scores >= THRESHOLDS["approve"]
    rev = (scores >= THRESHOLDS["review"]) & ~appr
    rates = {"approved": round(float(appr.mean()), 4), "manual_review": round(float(rev.mean()), 4),
             "rejected": round(float((~appr & ~rev).mean()), 4),
             "bad_rate_approved": round(float(y_te[appr].mean()) if appr.any() else 0.0, 4)}
    auc_by_year = {str(y): round(roc_auc_score(y_te[(te["issue_year"] == y).values],
                                               p_te[(te["issue_year"] == y).values]), 4)
                   for y in range(TEST_YEARS[0], TEST_YEARS[1] + 1)}

    imp = model.get_feature_importance()
    importance = sorted(
        ({"feature": f, "label_ru": FEATURE_LABELS_RU[f], "importance": round(float(v), 2)}
         for f, v in zip(FEATURES, imp)), key=lambda d: -d["importance"])

    metrics = {
        "dataset": os.environ.get("ML_DATASET_NAME", "Lending Club 2007–2020Q3 (out-of-time: train 2012–15, test 2016–17)"),
        "n_train": len(tr), "n_test": len(te),
        "roc_auc": round(auc, 4), "gini": round(2 * auc - 1, 4), "ks": round(ks_stat(y_te, p_te), 4),
        "baseline_logreg": {"roc_auc": round(auc_lr, 4), "gini": round(2 * auc_lr - 1, 4)},
        "roc_curve": sample_curve(fpr, tpr),
        "feature_importance": importance,
        "score_distribution": {"bins": bins, "good": good_hist.tolist(), "bad": bad_hist.tolist()},
        "thresholds": THRESHOLDS,
        "scale": {"base_points": 600, "base_odds": base_odds, "pdo": 20},
        "decision_rates_test": rates,
        "calibration": calibration_table(p_te, y_te),
        "auc_by_year": auc_by_year,
        "psi_score_train_vs_test": round(psi(train_scores, scores), 4),
        "best_iterations": best_iters,
        "train_seconds": round(train_seconds, 1),
        "challenger": None,   # заполняет ml.challenger
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # reference.json: квантили и доли пропусков признаков (графики/дрейф)
    num = [f for f in FEATURES if f not in CAT_FEATURES]
    reference = {
        "features": {f: {"mean": float(X_tr[f].mean()), "std": float(X_tr[f].std()),
                         "missing_share": float(X_tr[f].isna().mean()),
                         "quantiles": {str(q): float(X_tr[f].quantile(q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}}
                     for f in num},
        "purpose_share": X_tr["purpose"].value_counts(normalize=True).round(4).to_dict(),
        "default_rate": float(y_tr.mean()),
    }

    ART_DIR.mkdir(exist_ok=True, parents=True)
    model.save_model(str(ART_DIR / "model.cbm"))
    (ART_DIR / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    (ART_DIR / "reference.json").write_text(json.dumps(reference, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== Итог ===")
    print(f"Логрегрессия: AUC={auc_lr:.4f}  Gini={2*auc_lr-1:.4f}")
    print(f"CatBoost:     AUC={auc:.4f}  Gini={2*auc-1:.4f}  KS={metrics['ks']:.4f}  (деревьев {best_iters})")
    print(f"AUC по годам теста: {auc_by_year}; PSI баллов train→test: {metrics['psi_score_train_vs_test']}")
    print(f"Доля дефолтов в тесте {y_te.mean():.3%}; средний PD модели {p_te.mean():.3%}")
    print(f"BASE_ODDS (якорь шкалы) = {base_odds}")
    print(f"Тест: одобрено {rates['approved']:.1%}, ручная {rates['manual_review']:.1%}, "
          f"отказ {rates['rejected']:.1%}; дефолтов среди одобренных {rates['bad_rate_approved']:.2%}")
    print("Важность:", ", ".join(f"{d['feature']}={d['importance']}" for d in importance[:6]), "...")
    print(f"Время обучения: {train_seconds:.0f} с")


if __name__ == "__main__":
    main()
