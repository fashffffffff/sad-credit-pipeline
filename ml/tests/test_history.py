"""aggregate (одна заявка, dict) и aggregate_frame (векторно) обязаны давать одинаковые признаки."""
import numpy as np
import pandas as pd
import pytest

from ml.credit_model.history import FEATURES, PAYM_COLS, aggregate, aggregate_frame

RNG = np.random.default_rng(0)
FLAGS = ["is_zero_loans5", "is_zero_loans530", "is_zero_loans3060", "is_zero_loans6090",
         "is_zero_loans90", "is_zero_util", "is_zero_over2limit", "fclose_flag"]


def make_frame(n_apps=200):
    rows = []
    for app in range(n_apps):
        for rn in range(1, int(RNG.integers(1, 12)) + 1):
            r = {"id": app, "rn": rn, "enc_loans_credit_type": int(RNG.integers(0, 6))}
            r.update({f: int(RNG.integers(0, 2)) for f in FLAGS})
            for i, c in enumerate(PAYM_COLS):
                # коды 0..3; в позициях 11, 20, 24 как в выгрузке сдвинуты на +1
                r[c] = int(RNG.choice([0, 0, 0, 1, 2, 3, 3])) + (1 if i in (11, 20, 24) else 0)
            rows.append(r)
    df = pd.DataFrame(rows)
    return df.sample(frac=1, random_state=1).reset_index(drop=True)   # порядок строк не должен влиять


def test_frame_equals_single():
    df = make_frame()
    vec = aggregate_frame(df)
    assert list(vec.columns) == FEATURES
    for app, grp in df.groupby("id"):
        single = aggregate(grp.to_dict("records"))
        for f in FEATURES:
            assert vec.loc[app, f] == pytest.approx(single[f]), (app, f)


def test_empty_history_does_not_fail():
    out = aggregate([])
    assert set(out) == set(FEATURES) and all(v == 0 for v in out.values())
