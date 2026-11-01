"""Общие метрики и шкала: KS, ROC-точки, подбор BASE_ODDS, PSI, калибровка."""
import numpy as np
from sklearn.metrics import roc_curve

from ml.scoring import THRESHOLDS


def ks_stat(y, p) -> float:
    """KS = максимальный разрыв между долей пойманных плохих (TPR) и ошибочно отклонённых хороших (FPR)."""
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.max(tpr - fpr))


def sample_curve(fpr, tpr, n=50):
    """Оставляем ~50 точек ROC-кривой для графика на фронтенде."""
    idx = np.unique(np.linspace(0, len(fpr) - 1, n).astype(int))
    return [[round(float(fpr[i]), 4), round(float(tpr[i]), 4)] for i in idx]


def choose_scale(raw_log_odds_bad: np.ndarray, verbose=False) -> tuple[float, float]:
    """Подбираем шкалу (PDO, BASE_ODDS) при фиксированных порогах 680/600.

    Цель: одобрение 60–70%, ручная проверка 15–25%, отказ 8–20%. Для каждого PDO
    (20, 25, … 60) перебираем якорь BASE_ODDS: балл >= 600 <=> ln(шансы хорошего) >= ln(B), балл >= 680
    <=> >= ln(B) + 80/factor. Берём вариант, ближайший к «идеалу» 65/20/15, и при равенстве — с
    меньшим PDO (шкала не растягивается без нужды). Если цель недостижима — ближайший вариант.
    Узкий разброс риска в данных -> растягиваем шкалу (больше PDO), чтобы три зоны были осмысленными."""
    good = -raw_log_odds_bad
    cand = np.quantile(good, np.linspace(0.02, 0.60, 117))      # возможные положения ln(B)
    best = None
    for pdo in range(20, 61, 5):
        f = pdo / np.log(2)
        for ln_b in cand:
            rej = np.mean(good < ln_b)
            app = np.mean(good >= ln_b + (THRESHOLDS["approve"] - 600) / f)
            rev = 1 - rej - app
            # штраф = расстояние до допустимых диапазонов (0, если все три внутри)
            pen = sum(max(0, lo - v, v - hi) for v, lo, hi in
                      [(app, .60, .70), (rev, .15, .25), (rej, .08, .20)])
            dist = abs(app - .65) + abs(rev - .20) + abs(rej - .15)
            key = (round(pen, 4), round(dist, 3), pdo)
            if best is None or key < best[0]:
                best = (key, pdo, float(np.exp(ln_b)))
    if verbose:
        print("Выбор шкалы: штраф, расстояние, PDO =", best[0])
    return best[1], round(best[2], 3)


def psi(expected, actual, bins=10) -> float:
    """PSI (Population Stability Index): насколько распределение «уехало».
    < 0.1 стабильно, 0.1–0.25 заметный сдвиг, > 0.25 сильный. Для признаков с малым числом
    значений корзины = сами значения, иначе децили «ожидаемой» выборки."""
    expected, actual = np.asarray(expected, float), np.asarray(actual, float)
    vals = np.unique(expected)
    if len(vals) <= 12:
        e = np.array([(expected == v).mean() for v in vals])
        a = np.array([(actual == v).mean() for v in vals])
        a[-1] += (~np.isin(actual, vals)).mean()      # новые значения кладём в последнюю корзину
    else:
        edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        e = np.histogram(expected, edges)[0] / len(expected)
        a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-6, None), np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def calibration_table(p, y, edges):
    """По корзинам PD: средняя предсказанная вероятность vs реальная доля дефолтов."""
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() == 0:
            continue
        rows.append({"pd_bin": f"{lo*100:g}-{min(hi,1)*100:g}%",
                     "predicted": round(float(p[m].mean()), 4),
                     "actual": round(float(y[m].mean()), 4), "n": int(m.sum())})
    return rows
