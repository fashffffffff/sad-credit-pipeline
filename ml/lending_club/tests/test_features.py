"""Проверка подготовки признаков на маленьком CSV с известным ответом."""
import pandas as pd

from ml.lending_club.features import RATE, prepare

COLS = ["issue_d", "loan_status", "term", "emp_length", "annual_inc", "loan_amnt", "installment",
        "purpose", "fico_range_low", "fico_range_high", "open_acc", "total_bal_ex_mort", "revol_util",
        "delinq_2yrs", "num_tl_90g_dpd_24m", "earliest_cr_line", "inq_last_6mths"]


def test_prepare(tmp_path):
    base = ["Jan-2014", "Fully Paid", " 36 months", "10+ years", 120000.0, 10000.0, 300.0,
            "credit_card", 780, 784, 5, 24000.0, "50.0%", 1, 2, "Jan-2004", 3]
    rows = [
        base,
        ["Jan-2014", "Charged Off", " 36 months", "< 1 year", 120000.0, 10000.0, 300.0, "car",
         780, 784, 5, 24000.0, "50.0%", 1, 2, "Jan-2004", 3],
        ["Jan-2014", "Current", " 36 months"] + base[3:],            # исход неизвестен -> выбросить
        ["Jan-2017", "Fully Paid", " 60 months"] + base[3:],         # не созрел (до 2022) -> выбросить
        ["Jan-2011", "Fully Paid", " 36 months"] + base[3:],         # вне нужных лет -> выбросить
    ]
    path = tmp_path / "loans.csv"
    pd.DataFrame(rows, columns=COLS).to_csv(path, index=False)
    f = prepare(path)
    assert list(f["target"]) == [0, 1]
    a, b = f.iloc[0], f.iloc[1]
    assert a["monthly_income"] == 10000 * RATE and a["loan_amount"] == 10000 * RATE
    assert a["employment_months"] == 120 and b["employment_months"] == 0
    assert a["purpose"] == "refinance" and b["purpose"] == "auto"
    assert abs(a["payment_to_income"] - 0.03) < 1e-9
    assert abs(a["bureau_score"] - ((782 - 300) / 550 * 998 + 1) / 1000) < 1e-9
    assert a["total_debt_to_income"] == 0.2 and a["revolving_utilization"] == 0.5
    assert a["credit_history_months"] == 120 and a["issue_year"] == 2014
