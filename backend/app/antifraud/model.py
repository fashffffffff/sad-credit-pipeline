"""Модель антифрода: стандартизация + PCA (эмбеддинг), ближайшие соседи среди эталонов схем и IsolationForest.

Как это читать «на пальцах»:
  1. Заявка превращается в вектор из ~19 чисел (features.py).
  2. Числа приводятся к одной шкале (StandardScaler) и сжимаются PCA до 8 осей — это и есть «эмбеддинг»:
     похожие по поведению заявки оказываются рядом.
  3. Среди эталонных заявок (обычные + три схемы из attacks.py) ищем 15 ближайших и смотрим, кто среди них:
     если большинство — из схемы «кредитная фабрика», то и эта заявка на неё похожа.
  4. IsolationForest, обученный только на обычных заявках, добавляет «необычность» — на случай, если схема новая.
fraud_score (0..1) = 0.75 * доля «мошеннических» соседей + 0.25 * необычность.
"""

import math
import random
from functools import lru_cache

import numpy as np
from sklearn.decomposition import PCA
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from backend.app.antifraud.attacks import SCHEME_RU, SCHEMES, Event, Ids, build_stream
from backend.app.antifraud.features import FEATURE_LABELS, FEATURE_NAMES, build_vector
from backend.app.antifraud.graph import FraudGraph, Node

K = 15
N_COMPONENTS = 8
KNN_WEIGHT, ISO_WEIGHT = 0.75, 0.25
MAX_ISOLATED_SCORE = 0.5  # ниже порога 0.6: изолированная заявка не может быть помечена


def event_to_node(event: Event, idx: int) -> Node:
    """Событие эталона -> узел графа. Отпечатки здесь — просто строки с номерами (реальных данных в эталонах нет)."""
    fps = {"phone": f"t{event.phone}", "passport": f"p{event.person}"}
    if event.device is not None:  # приложение прислало id устройства
        fps["device"] = f"d{event.device}"
    return Node(f"ref{idx}", event.ts, fps, amount=event.amount, income=event.income, term=event.term,
                n_loans=event.n_loans, known_months=event.known_months,
                channel="app" if event.device is not None else "nodevice")


def vectors_for(events: list[Event]) -> np.ndarray:
    """Прогоняет поток событий через НАСТОЯЩИЙ граф (тот же код, что и в проде) и собирает векторы."""
    graph, rows = FraudGraph(), []
    for i, event in enumerate(events):
        node = event_to_node(event, i)
        graph.add(node)
        vec = build_vector(graph, node)
        rows.append([vec[name] for name in FEATURE_NAMES])
    return np.array(rows, dtype=float)


class FraudModel:
    def __init__(self, events: list[Event]):
        X = vectors_for(events)
        self.labels = np.array([e.label for e in events])
        self.scaler = StandardScaler().fit(X)
        Z = self.scaler.transform(X)
        self.pca = PCA(n_components=N_COMPONENTS, random_state=0).fit(Z)
        E = self.pca.transform(Z)
        self.embeddings = E
        self.knn = NearestNeighbors(n_neighbors=K).fit(E)
        normal = self.labels == "normal"
        self.iso = IsolationForest(n_estimators=100, random_state=0).fit(E[normal])
        raw = -self.iso.score_samples(E[normal])
        self.iso_lo, self.iso_hi = float(np.quantile(raw, 0.5)), float(np.quantile(raw, 0.99))
        # центры классов в стандартизованном пространстве — для объяснений
        self.centroids = {c: Z[self.labels == c].mean(axis=0) for c in set(self.labels)}
        # чтобы редкие схемы не тонули среди тысяч обычных заявок, голос класса делим на корень из его размера
        sizes = {c: int((self.labels == c).sum()) for c in set(self.labels)}
        self.class_weight = {c: 1 / np.sqrt(n) for c, n in sizes.items()}

    def score_vector(self, vec: dict[str, float]) -> dict:
        x = np.array([[vec[name] for name in FEATURE_NAMES]])
        z = self.scaler.transform(x)
        e = self.pca.transform(z)
        _, idx = self.knn.kneighbors(e)
        votes: dict[str, float] = {}
        for i in idx[0]:
            label = self.labels[i]
            votes[label] = votes.get(label, 0.0) + self.class_weight[label]
        total = sum(votes.values())
        fraud_votes = {c: v for c, v in votes.items() if c != "normal"}
        fraud_share = sum(fraud_votes.values()) / total
        pattern = str(max(fraud_votes, key=fraud_votes.get)) if fraud_votes else None
        similarity = fraud_votes[pattern] / total if pattern else 0.0
        raw = float(-self.iso.score_samples(e)[0])
        anomaly = min(max((raw - self.iso_lo) / max(self.iso_hi - self.iso_lo, 1e-6), 0.0), 1.0)
        score = min(1.0, KNN_WEIGHT * fraud_share + ISO_WEIGHT * anomaly)
        if vec["log_comp_size"] < math.log1p(1.5):
            # заявка без единой связи (одна в своей группе): одни лишь атрибуты, например короткая история, — не повод
            # подозревать человека. Чтобы не наказывать честных, такие заявки никогда не помечаем
            score = min(score, MAX_ISOLATED_SCORE)
        return {"score": float(score), "pattern": pattern, "similarity": float(similarity), "anomaly": float(anomaly),
                "contributions": self._explain(z[0], vec, pattern)}

    def _explain(self, z: np.ndarray, vec: dict, pattern: str | None) -> list[dict]:
        """Топ-3 признака: чем сильнее заявка отличается от «обычной» В СТОРОНУ найденной схемы, тем больше вклад."""
        if pattern is None:
            return []
        normal, scheme = self.centroids["normal"], self.centroids[pattern]
        contrib = (z - normal) * (scheme - normal)
        positive = np.clip(contrib, 0, None)
        total = positive.sum()
        if total <= 0:
            return []
        out = []
        for i in np.argsort(-positive)[:3]:
            if positive[i] > 0:
                name = FEATURE_NAMES[i]
                out.append({"feature": name, "label_ru": FEATURE_LABELS[name], "value": human_value(name, float(vec[name])),
                            "share": round(float(positive[i] / total), 3)})
        return out


@lru_cache(maxsize=1)
def reference_model() -> FraudModel:
    """Обучается один раз на процесс (~1–2 с) на синтетических эталонах; зерно фиксировано, результат воспроизводим."""
    rng = random.Random(20250301)
    events = build_stream(rng, Ids(), 0.0, n_normal=5000, groups={s: 160 for s in SCHEMES})
    return FraudModel(events)


__all__ = ["FraudModel", "reference_model", "SCHEME_RU", "vectors_for", "event_to_node"]


def human_value(name: str, value: float) -> float:
    """Значение признака в понятных единицах для объяснения (в модель идут логарифмы).

    log1p-признаки возвращаем к исходному числу; паузу — из секунд в минуты.
    """
    if name == "log_gap_device":
        return round(math.expm1(value) / 60, 1)
    if name in ("log_comp_size", "log_comp_growth", "log_comp_passports"):
        return round(math.expm1(value))
    if name == "log_income_to_amount":
        return round(math.exp(value), 2)
    if name == "amount_norm":  # в модель идёт доля от 5 млн ₽
        return round(value * 5_000_000)
    if name == "term_norm":    # доля от 84 месяцев
        return round(value * 84)
    return round(value, 3)

