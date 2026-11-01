"""Формулы шкалы: перевод PD в баллы (общие для обучения и прода).

BASE_ODDS («якорь») и PDO («растяжка») подбираются при обучении и лежат в metrics.json -> scale,
поэтому здесь их нет — один источник правды.
"""
import math

# 600 баллов = шансы BASE_ODDS:1 («вернёт / не вернёт»), каждые +PDO баллов = шансы ×2.
BASE_POINTS = 600.0
SCORE_MIN, SCORE_MAX = 300, 850
THRESHOLDS = {"approve": 680, "review": 600}   # фиксированы контрактом


def factor(pdo: float) -> float:
    """Сколько баллов даёт один «ln» шансов: PDO / ln2 (удвоение шансов = +PDO баллов)."""
    return pdo / math.log(2)


def raw_score(log_odds_bad: float, base_odds: float, pdo: float) -> float:
    """Балл до обрезки по log-odds ПЛОХОГО исхода (то, что отдаёт CatBoost)."""
    # log-odds хорошего = -log-odds плохого; балл = 600 + factor * ln(шансы / base_odds)
    return BASE_POINTS + factor(pdo) * (-log_odds_bad - math.log(base_odds))


def clip_score(x: float) -> int:
    return int(round(min(SCORE_MAX, max(SCORE_MIN, x))))
