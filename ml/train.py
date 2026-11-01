"""Обучение модели на кредитных историях Альфа-Банка.  Запуск: uv run python -m ml.train

ALFA_DIR — папка data_for_competition (по умолчанию ml/data/alfa/data_for_competition).
ML_ARTIFACTS_DIR — куда писать результат (по умолчанию ml/artifacts).

Идея: заявка = список её кредитов (до 50 строк). history.aggregate_frame превращает строки
в ~16 понятных признаков на заявку; на них учатся логрегрессия и CatBoost.
У test нет меток, поэтому качество меряем на отложенных 20% train, а test используем
только для дрейфа (PSI): он идёт по времени ПОСЛЕ train — это проверка на будущем.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml.credit_model.history import FEATURE_META, FEATURES, PAYM_CODES, aggregate_frame
from ml.metrics_utils import calibration_table, choose_scale, ks_stat, psi, sample_curve
from ml.stop_rules import find_stop_rules
from ml.scoring import THRESHOLDS, factor

ROOT = Path(__file__).parent
ART_DIR = Path(os.environ.get("ML_ARTIFACTS_DIR", ROOT / "artifacts"))
SEED = 42
DEFAULT_ALFA = ROOT / "data" / "alfa" / "data_for_competition"

# Монотонность: больше просрочек -> PD не падает (бизнес-логика и защита от шума)
MONOTONE = {f: 1 for f in FEATURES if f.startswith("n_overdue") or f in (
    "has_current_overdue", "share_bad_months_24", "share_bad_months_6",
    "n_serious_months", "bad_last_month_loans")}


def alfa_dir() -> Path:
    return Path(os.environ.get("ALFA_DIR", DEFAULT_ALFA))


def catboost_params(iterations=1000) -> dict:
    """Параметры CatBoost (общие с challenger). Без auto_class_weights — иначе ломается калибровка."""
    return dict(iterations=iterations, depth=6, learning_rate=0.08, random_seed=SEED,
                monotone_constraints=MONOTONE, verbose=0, allow_writing_files=False)


def _aggregate_folder(folder: Path) -> pd.DataFrame:
    parts = []
    for f in sorted(folder.glob("*.pq"), key=lambda p: int(p.stem.split("_")[-1])):
        df = pd.read_parquet(f)
        small = [c for c in df.columns if c != "id"]
        df = df.astype({c: "int8" for c in small})        # 61 колонка в int8: в 8 раз меньше памяти
        parts.append(aggregate_frame(df))
        print(f"  {f.name}: {len(parts[-1])} заявок", flush=True)
    out = pd.concat(parts)
    assert out.index.is_unique, "одна заявка попала в несколько файлов"
    return out


def load_aggregates() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Признаки train (с меткой) и test. Считаем один раз, дальше кэш parquet."""
    base = alfa_dir()
    cache = base.parent / "features_cache"
    cache.mkdir(exist_ok=True)
    f_tr, f_te = cache / "train.parquet", cache / "test.parquet"
    if not f_tr.exists():
        tr = _aggregate_folder(base / "train_data")
        tr["flag"] = pd.read_csv(base / "train_target.csv").set_index("id")["flag"].reindex(tr.index).values
        tr.to_parquet(f_tr)
    if not f_te.exists():
        _aggregate_folder(base / "test_data").to_parquet(f_te)
    return pd.read_parquet(f_tr), pd.read_parquet(f_te)


def split(tr_all: pd.DataFrame):
    """80/20 со стратификацией (одинаковая доля дефолтов) и фиксированным seed."""
    X, y = tr_all[FEATURES], tr_all["flag"]
    return train_test_split(X, y, test_size=0.2, stratify=y, random_state=SEED)


def feature_specs(X: pd.DataFrame) -> list[dict]:
    """Описание признаков для UI (ползунки): подпись, подсказка, диапазон — по данным."""
    specs = []
    for f in FEATURES:
        label, hint, kind = FEATURE_META[f]
        col = X[f]
        if kind == "bool":
            lo, hi, step = 0, 1, 1
        elif kind == "share":
            lo, hi, step = 0.0, 1.0, 0.05
        else:
            lo, hi, step = 0, int(col.quantile(0.995)), 1      # 99.5% данных, хвост обрезаем
        default = float(col.median())
        specs.append({"feature": f, "label_ru": label, "hint_ru": hint, "kind": kind,
                      "min": lo, "max": hi, "step": step,
                      "default": round(default, 3) if kind in ("share", "float") else int(round(default))})
    return specs


def main():
    t0 = time.time()
    tr_all, te_unl = load_aggregates()
    print(f"Заявок train {len(tr_all)}, test(без меток) {len(te_unl)}; дефолтов {tr_all['flag'].mean():.2%}")
    X_tr, X_ho, y_tr, y_ho = split(tr_all)
    # из 80% ещё 10% — для ранней остановки CatBoost
    X_fit, X_val, y_fit, y_val = train_test_split(X_tr, y_tr, test_size=0.1, stratify=y_tr, random_state=SEED)

    logreg = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(X_tr, y_tr)
    auc_lr = roc_auc_score(y_ho, logreg.predict_proba(X_ho)[:, 1])

    model = CatBoostClassifier(**catboost_params(), early_stopping_rounds=100)
    model.fit(X_fit, y_fit, eval_set=(X_val, y_val))
    train_seconds = time.time() - t0
    p_ho = model.predict_proba(X_ho)[:, 1]
    auc = roc_auc_score(y_ho, p_ho)
    fpr, tpr, _ = roc_curve(y_ho, p_ho)

    # Шкала (PDO и якорь BASE_ODDS) — по отложенной выборке
    raw_ho = model.predict(X_ho, prediction_type="RawFormulaVal")
    pdo, base_odds = choose_scale(raw_ho, verbose=True)
    to_score = lambda r: np.clip(600 + factor(pdo) * (-r - np.log(base_odds)), 300, 850)
    s_ho = to_score(raw_ho)
    yh = y_ho.values
    appr = s_ho >= THRESHOLDS["approve"]
    rev = (s_ho >= THRESHOLDS["review"]) & ~appr
    rates = {"approved": round(float(appr.mean()), 4), "manual_review": round(float(rev.mean()), 4),
             "rejected": round(float((~appr & ~rev).mean()), 4),
             "bad_rate_approved": round(float(yh[appr].mean()), 4)}
    bins = list(range(300, 851, 25))
    good_hist, _ = np.histogram(s_ho[yh == 0], bins=bins)
    bad_hist, _ = np.histogram(s_ho[yh == 1], bins=bins)

    # Дрейф: train (вся обучающая часть) против будущего unlabeled test
    s_tr = to_score(model.predict(X_tr, prediction_type="RawFormulaVal"))
    s_te = to_score(model.predict(te_unl[FEATURES], prediction_type="RawFormulaVal"))
    drift = {"score_psi": round(psi(s_tr, s_te), 4),
             "features_psi": sorted(
                 ({"feature": f, "label_ru": FEATURE_META[f][0], "psi": round(psi(X_tr[f], te_unl[f]), 4)}
                  for f in FEATURES), key=lambda d: -d["psi"])}

    imp = model.get_feature_importance()
    importance = sorted(({"feature": f, "label_ru": FEATURE_META[f][0], "importance": round(float(v), 2)}
                         for f, v in zip(FEATURES, imp)), key=lambda d: -d["importance"])
    lc_path = ART_DIR / "lending_club" / "metrics.json"
    comparison = [{"dataset": "Альфа-Банк (РФ)", "period": "кредитные истории БКИ, 3 млн заявок",
                   "n": len(tr_all), "roc_auc": round(auc, 4), "gini": round(2 * auc - 1, 4), "in_prod": True}]
    if lc_path.exists():
        lc = json.loads(lc_path.read_text(encoding="utf-8"))
        comparison.append({"dataset": "Lending Club (США)", "period": "2007–2020 (выдачи 2012–2017)",
                           "n": lc["n_train"] + lc["n_test"], "roc_auc": lc["roc_auc"],
                           "gini": lc["gini"], "in_prod": False})

    metrics = {
        "dataset": "Альфа-Банк: кредитные истории БКИ (ODS.ai DL Fintech)",
        "n_train": len(X_tr), "n_test": len(X_ho),
        "roc_auc": round(auc, 4), "gini": round(2 * auc - 1, 4), "ks": round(ks_stat(y_ho, p_ho), 4),
        "baseline_logreg": {"roc_auc": round(auc_lr, 4), "gini": round(2 * auc_lr - 1, 4)},
        "roc_curve": sample_curve(fpr, tpr),
        "feature_importance": importance,
        "feature_specs": feature_specs(X_tr),
        "paym_codes": PAYM_CODES,
        "score_distribution": {"bins": bins, "good": good_hist.tolist(), "bad": bad_hist.tolist()},
        "thresholds": THRESHOLDS,
        "scale": {"base_points": 600, "base_odds": base_odds, "pdo": pdo},
        "decision_rates_test": rates,
        "calibration": calibration_table(p_ho, yh, [0, .01, .02, .03, .04, .06, .08, .12, .2, 1.0001]),
        "drift": drift,
        "best_iterations": int(model.get_best_iteration()) + 1,
        "train_seconds": round(train_seconds, 1),
        "stop_rules": find_stop_rules(X_tr, y_tr),
        "challenger": None,   # заполняет ml.challenger
        "comparison": comparison,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    reference = {
        "features": {f: {"mean": float(X_tr[f].mean()), "std": float(X_tr[f].std()),
                         "quantiles": {str(q): float(X_tr[f].quantile(q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}}
                     for f in FEATURES},
        "default_rate": float(y_tr.mean()),
    }
    ART_DIR.mkdir(exist_ok=True, parents=True)
    model.save_model(str(ART_DIR / "model.cbm"))
    (ART_DIR / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    (ART_DIR / "reference.json").write_text(json.dumps(reference, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== Итог ===")
    print(f"Логрегрессия: AUC={auc_lr:.4f}  Gini={2*auc_lr-1:.4f}")
    print(f"CatBoost:     AUC={auc:.4f}  Gini={2*auc-1:.4f}  KS={metrics['ks']:.4f}  (деревьев {metrics['best_iterations']})")
    print(f"Доля дефолтов на отложенной {yh.mean():.3%}; средний PD {p_ho.mean():.3%}")
    print(f"PDO = {pdo}, BASE_ODDS = {base_odds}; одобрено {rates['approved']:.1%}, ручная {rates['manual_review']:.1%}, "
          f"отказ {rates['rejected']:.1%}; дефолтов среди одобренных {rates['bad_rate_approved']:.2%}")
    print(f"Дрейф: PSI балла {drift['score_psi']}; топ признаков:",
          ", ".join(f"{d['feature']}={d['psi']}" for d in drift["features_psi"][:4]))
    print("Важность:", ", ".join(f"{d['feature']}={d['importance']}" for d in importance[:6]), "...")
    print(f"Время обучения: {train_seconds:.0f} с")


if __name__ == "__main__":
    main()
