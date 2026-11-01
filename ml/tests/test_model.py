"""Тесты модели. Нужны артефакты: uv run python -m ml.train, затем python -m ml.make_pool"""
import json
import time

import pandas as pd
import pytest

from ml.credit_model import artifacts_dir, load_model


@pytest.fixture(scope="module")
def model():
    return load_model()


@pytest.fixture(scope="module")
def histories():
    d = artifacts_dir()
    pool = pd.read_parquet(d / "bki_pool.parquet")
    demo = json.loads((d / "bki_demo.json").read_text(encoding="utf-8"))
    return {k: pool[pool["id"] == i].drop(columns="id").to_dict("records") for k, i in demo.items()}


def test_demo_good_above_700(model, histories):
    assert model.predict_history(histories["good"]).score > 700


def test_demo_bad_is_rejected(model, histories):
    bad = model.predict_history(histories["bad"])
    assert bad.score < model.metrics["thresholds"]["review"]
    assert bad.features["has_current_overdue"] == 1


def test_borderline_in_review_zone(model, histories):
    s = model.predict_history(histories["borderline"]).score
    assert 610 <= s <= 670


def test_contributions_sum_to_score(model, histories):
    for loans in histories.values():
        p = model.predict_history(loans)
        total = p.base_score + sum(c.points for c in p.contributions)
        assert abs(min(850, max(300, total)) - p.score) < 1.0   # до обрезки 300..850; остаток — округление
        pts = [abs(c.points) for c in p.contributions]
        assert pts == sorted(pts, reverse=True)
        assert {c.feature for c in p.contributions} == set(model.features)
        assert p.features == {c.feature: c.value for c in p.contributions}


def test_empty_history_does_not_fail(model):
    # Пустая история (нет кредитов) считается как «все нули»; backend такие заявки (found=false)
    # всё равно отправляет на ручную проверку.
    p = model.predict_history([])
    assert 300 <= p.score <= 850 and 0 < p.pd < 1


def test_whatif_more_overdue_lowers_score(model, histories):
    base = model.predict_history(histories["good"]).features
    worse = dict(base, n_overdue_90_plus=base["n_overdue_90_plus"] + 2, has_current_overdue=1)
    assert model.predict(worse).score < model.predict(base).score


def test_specs_and_codes(model):
    assert [s["feature"] for s in model.feature_specs] == model.features
    for s in model.feature_specs:
        assert {"label_ru", "hint_ru", "kind", "min", "max", "step", "default"} <= set(s)
        assert s["min"] <= s["default"] <= s["max"]
    assert set(model.paym_codes) == {"0", "1", "2", "3"}
    assert all(c["color"] in ("ok", "warn", "bad", "none") for c in model.paym_codes.values())


def test_metrics(model):
    m = model.metrics
    assert m["roc_auc"] >= 0.70 and m["roc_auc"] > m["baseline_logreg"]["roc_auc"]
    assert abs(m["gini"] - (2 * m["roc_auc"] - 1)) < 1e-3
    assert m["thresholds"] == {"approve": 680, "review": 600}
    assert m["scale"]["base_points"] == 600 and 20 <= m["scale"]["pdo"] <= 60 and m["scale"]["base_odds"] > 0
    for key in ("ks", "roc_curve", "feature_importance", "score_distribution", "decision_rates_test",
                "calibration", "drift", "challenger", "comparison", "trained_at"):
        assert key in m
    r = m["decision_rates_test"]
    assert 0.6 <= r["approved"] <= 0.7001 and r["manual_review"] <= 0.30 and r["rejected"] >= 0.03
    assert m["decision_rates_test"]["bad_rate_approved"] < 0.035
    assert m["drift"]["score_psi"] >= 0 and len(m["drift"]["features_psi"]) == len(model.features)
    assert {c["dataset"] for c in m["comparison"]} == {"Альфа-Банк (РФ)", "Lending Club (США)"}


def test_predict_history_is_fast(model, histories):
    loans = histories["bad"]
    model.predict_history(loans)
    t = time.perf_counter()
    for _ in range(20):
        model.predict_history(loans)
    assert (time.perf_counter() - t) / 20 < 0.05


def test_stop_rules(model):
    m = model.metrics
    rules = m["stop_rules"]
    base = sum(m["score_distribution"]["bad"]) / (sum(m["score_distribution"]["bad"]) + sum(m["score_distribution"]["good"]))
    assert 1 <= len(rules) <= 3
    for r in rules:
        assert {"code", "feature", "op", "value", "text_ru", "share", "default_rate"} <= set(r)
        assert r["feature"] in model.features and r["op"] == ">="
        assert r["share"] <= 0.05 and r["default_rate"] >= 3 * base * 0.95   # >= ~3× средней
        assert r["feature"] != "has_current_overdue"     # этот флаг по данным риска не несёт
