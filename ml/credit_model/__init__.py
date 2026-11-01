"""Кредитная модель для backend: load_model() -> CreditModel.predict_history(loans) -> Prediction."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

from ml.credit_model.history import FEATURES, aggregate
from ml.scoring import clip_score, factor, raw_score

__all__ = ["load_model", "FEATURES", "Prediction", "Contribution", "CreditModel"]


def artifacts_dir() -> Path:
    return Path(os.environ.get("ML_ARTIFACTS_DIR", Path(__file__).resolve().parent.parent / "artifacts"))


@dataclass
class Contribution:
    feature: str
    label_ru: str
    value: float          # значение признака
    points: float         # вклад в баллах: > 0 повышает балл, < 0 понижает


@dataclass
class Prediction:
    pd: float                       # вероятность дефолта 0..1
    score: int                      # балл 300..850
    base_score: float               # балл "среднего клиента"
    contributions: list[Contribution] = field(default_factory=list)
    features: dict = field(default_factory=dict)   # признаки, полученные из истории


class CreditModel:
    def __init__(self, model: CatBoostClassifier, metrics: dict):
        self.model = model
        self.metrics = metrics
        self.base_odds = metrics["scale"]["base_odds"]       # якорь шкалы подобран при обучении
        self.pdo = metrics["scale"]["pdo"]                   # растяжка шкалы (баллов на удвоение шансов)
        self.features = list(FEATURES)
        self.feature_specs = metrics["feature_specs"]
        self.paym_codes = metrics["paym_codes"]
        self._label = {s["feature"]: s["label_ru"] for s in self.feature_specs}
        self._default = {s["feature"]: s["default"] for s in self.feature_specs}

    def predict_history(self, loans: list[dict]) -> Prediction:
        """Главный вход для backend: сырые кредиты из ответа БКИ -> признаки -> балл."""
        return self.predict(aggregate(loans))

    def predict(self, features: dict) -> Prediction:
        """Что-если: признаки dict по model.features (пропущенные берутся по умолчанию — медиана)."""
        row = {f: float(features[f]) if features.get(f) is not None else float(self._default[f])
               for f in self.features}
        x = pd.DataFrame([row], columns=self.features)
        # ShapValues: вклад каждого признака в log-odds ПЛОХОГО исхода; последний столбец — база
        shap = self.model.get_feature_importance(data=_pool(x), type="ShapValues")[0]
        contrib_bad, bias = shap[:-1], shap[-1]
        raw_log_odds = float(contrib_bad.sum() + bias)          # = сырой ответ модели
        score_raw = raw_score(raw_log_odds, self.base_odds, self.pdo)
        # Чем выше риск, тем ниже балл -> знак минус; factor(pdo) переводит log-odds в баллы
        contribs = [Contribution(f, self._label[f], row[f], round(float(-c * factor(self.pdo)), 2))
                    for f, c in zip(self.features, contrib_bad)]
        contribs.sort(key=lambda c: -abs(c.points))
        # Инвариант: base_score + sum(points) == score_raw (до округления и обрезки 300..850)
        return Prediction(pd=float(1 / (1 + np.exp(-raw_log_odds))), score=clip_score(score_raw),
                          base_score=raw_score(float(bias), self.base_odds, self.pdo),
                          contributions=contribs, features=row)


def _pool(x):
    from catboost import Pool
    return Pool(x)


def load_model() -> CreditModel:
    d = artifacts_dir()
    model = CatBoostClassifier()
    model.load_model(str(d / "model.cbm"))
    return CreditModel(model, json.loads((d / "metrics.json").read_text(encoding="utf-8")))
