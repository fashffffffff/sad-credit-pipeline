"""Стоп-факторы по данным.  Запуск: uv run python -m ml.stop_rules  (дописывает metrics.json -> stop_rules)

Стоп-фактор — жёсткое правило «признак >= значение -> отказ без скоринга». Оно имеет смысл,
только если подпадающая группа ОЧЕНЬ рискованная и небольшая: иначе мы режем много хороших
клиентов. Критерии (на train):
  * доля дефолтов в группе >= 3 × средняя (у нас >= ~10%);
  * в группу попадает <= 5% заявок (и не меньше 0.3%, чтобы правило не было случайным).
Правила выбираются жадно: каждое следующее должно ловить ещё НЕ пойманных предыдущими клиентов
(правила могут частично пересекаться, зато каждое понятно по отдельности). Максимум 3 правила.
"""
import json

import numpy as np
import pandas as pd

from ml.credit_model.history import FEATURES, FEATURE_META

MAX_RULES, MIN_SHARE, MAX_SHARE, LIFT = 3, 0.003, 0.05, 3.0
# Только «плохие» признаки: чем больше значение, тем хуже. Флаги-индикаторы и «хорошие» счётчики исключаем.
CANDIDATES = ["n_overdue_30_60", "n_overdue_60_90", "n_overdue_90_plus", "share_bad_months_24",
              "share_bad_months_6", "n_serious_months", "bad_last_month_loans"]

TEXT = {
    "n_overdue_30_60": "Кредитов с просрочкой 30–60 дней: {v} и больше",
    "n_overdue_60_90": "Кредитов с просрочкой 60–90 дней: {v} и больше",
    "n_overdue_90_plus": "Кредитов с просрочкой 90+ дней: {v} и больше",
    "share_bad_months_24": "Доля месяцев с просрочкой за 2 года {pct}% и выше",
    "share_bad_months_6": "Доля месяцев с просрочкой за полгода {pct}% и выше",
    "n_serious_months": "Месяцев серьёзной просрочки (статус 2): {v} и больше",
    "bad_last_month_loans": "Кредитов с просрочкой в последнем месяце: {v} и больше",
}
CODES = {"n_overdue_30_60": "MANY_OVERDUE_30_60", "n_overdue_60_90": "MANY_OVERDUE_60_90",
         "n_overdue_90_plus": "MANY_OVERDUE_90_PLUS", "share_bad_months_24": "BAD_MONTHS_SHARE_24M",
         "share_bad_months_6": "BAD_MONTHS_SHARE_6M", "n_serious_months": "SEVERE_OVERDUE_MONTHS",
         "bad_last_month_loans": "RECENT_OVERDUE_LOANS"}


def find_stop_rules(X: pd.DataFrame, y: pd.Series) -> list[dict]:
    mean = y.mean()
    covered = np.zeros(len(X), bool)
    rules = []
    for _ in range(MAX_RULES):
        best = None
        for f in CANDIDATES:
            if any(r["feature"] == f for r in rules):
                continue
            col = X[f].to_numpy()
            vals = np.unique(np.round(col, 2)) if f.startswith("share") else np.unique(col)
            for v in vals[vals > 0]:
                m = col >= v
                if not (MIN_SHARE <= m.mean() <= MAX_SHARE):
                    continue
                if y.to_numpy()[m].mean() < LIFT * mean:        # группа сама по себе >= 3× средней
                    continue
                new = m & ~covered                      # кого правило ловит дополнительно к предыдущим
                if new.sum() < 0.001 * len(X):          # правило должно давать что-то новое
                    continue
                gain = y.to_numpy()[new].sum()          # сколько дефолтов поймали дополнительно
                if best is None or gain > best[0]:
                    best = (gain, f, float(v))
        if best is None:
            break
        _, f, v = best
        m = X[f].to_numpy() >= v
        covered |= m
        value = v if f.startswith("share") else int(v)
        rules.append({
            "code": CODES[f], "feature": f, "op": ">=", "value": value,
            "text_ru": TEXT[f].format(v=value, pct=round(v * 100)),
            "share": round(float(m.mean()), 4), "default_rate": round(float(y.to_numpy()[m].mean()), 4)})
    return rules


def holdout_check(rules, X, y):
    """Те же правила на отложенной выборке (не участвовала в подборе) — должны держаться."""
    return [{"code": r["code"], "share": round(float((X[r["feature"]] >= r["value"]).mean()), 4),
             "default_rate": round(float(y[X[r["feature"]] >= r["value"]].mean()), 4)} for r in rules]


def main():
    from ml.train import ART_DIR, load_aggregates, split
    tr, _ = load_aggregates()
    X_tr, X_ho, y_tr, y_ho = split(tr)
    rules = find_stop_rules(X_tr, y_tr)
    for r, h in zip(rules, holdout_check(rules, X_ho, y_ho)):
        print(f"{r['code']}: {r['feature']} >= {r['value']}: train доля {r['share']:.2%}, дефолтов {r['default_rate']:.1%};"
              f" отложенная доля {h['share']:.2%}, дефолтов {h['default_rate']:.1%}")
    union = np.zeros(len(X_tr), bool)
    for r in rules:
        union |= X_tr[r["feature"]].to_numpy() >= r["value"]
    print(f"Вместе: {union.mean():.2%} заявок, дефолтов {y_tr.to_numpy()[union].mean():.1%} (средняя {y_tr.mean():.2%})")
    path = ART_DIR / "metrics.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m["stop_rules"] = rules
    # находка про «текущую просрочку»: сравнение долей дефолтов
    g = tr.groupby("has_current_overdue")["flag"].agg(["mean", "size"])
    m["finding_current_overdue"] = {"share_with_flag": round(float(g.loc[1, "size"] / g["size"].sum()), 4),
                                    "default_rate_with": round(float(g.loc[1, "mean"]), 4),
                                    "default_rate_without": round(float(g.loc[0, "mean"]), 4)}
    path.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
