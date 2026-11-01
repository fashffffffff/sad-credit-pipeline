"""Подготовка выборки Lending Club 2007–2020Q3 (формулы — docs/CONTRACTS.md, раздел 4).

CSV на 1.7 ГБ читаем один раз, только нужные колонки, и сохраняем готовую выборку
в prepared.parquet — повторные запуски читают её за секунды.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd

from ml.lending_club.scoring import DEFAULT_PURPOSE, FEATURES

DEFAULT_CSV = Path(__file__).parent / "data" / "lending-club" / "loans_2007_2020Q3.csv"

# «Курс» долларовых сумм в рубли. Медианный годовой доход в Lending Club ≈ 65 тыс. $,
# то есть ≈ 5.4 тыс. $ в месяц. У нашего типичного заёмщика ≈ 60–70 тыс. ₽/мес,
# поэтому множитель 12: 5.4 × 12 ≈ 65 тыс. ₽. Отношения (платёж/доход, долг/доход)
# от курса не зависят — он нужен лишь для правдоподобных monthly_income и loan_amount.
RATE = 12

# ЗАПРЕЩЁННЫЕ колонки (утечка будущего) — мы их даже не читаем:
#  * total_pymnt, total_rec_*, recoveries, last_pymnt_*, out_prncp, last_fico_* и т.п. —
#    появляются уже ПОСЛЕ выдачи, по ним статус кредита почти известен заранее;
#  * int_rate и grade/sub_grade — это решение самого Lending Club: ставку и грейд он назначил
#    своей моделью, то есть они уже содержат чужой скоринг (и сами зависят от тех же данных).
#    Модель с ними «угадывает» ответ банка, а не оценивает риск; при этом при выдаче нашим
#    конвейером этих значений ещё нет — их определяет как раз наша модель.
USECOLS = [
    "issue_d", "loan_status", "term", "emp_length", "annual_inc", "loan_amnt", "installment",
    "purpose", "fico_range_low", "fico_range_high", "open_acc", "total_bal_ex_mort", "revol_util",
    "delinq_2yrs", "num_tl_90g_dpd_24m", "earliest_cr_line", "inq_last_6mths",
]

# Цели кредита приводим к четырём нашим категориям (как в анкете)
PURPOSE_MAP = {"debt_consolidation": "refinance", "credit_card": "refinance",
               "car": "auto", "educational": "education"}

BAD = {"Charged Off", "Default", "Does not meet the credit policy. Status:Charged Off"}
GOOD = {"Fully Paid", "Does not meet the credit policy. Status:Fully Paid"}
# Всё остальное (Current, Late, In Grace Period…) — исход ещё неизвестен, такие кредиты выбрасываем.

LAST_MONTH = 2020 * 12 + 9                      # сентябрь 2020 — дата выгрузки датасета
YEARS = (2012, 2017)                           # нужные годы выдачи (train 2012–15, test 2016–17)


def csv_path() -> Path:
    return Path(os.environ.get("LENDING_CLUB_CSV", DEFAULT_CSV))


def prepare(csv: Path | None = None) -> pd.DataFrame:
    """CSV -> таблица: issue_d, issue_year, 14 признаков, target (1 = не вернул)."""
    csv = Path(csv) if csv else csv_path()
    raw = pd.read_csv(csv, usecols=USECOLS, low_memory=False)
    raw = raw.dropna(subset=["issue_d", "loan_status", "term"])   # в конце файла есть мусорные строки

    # Целевая переменная: 1 = Charged Off/Default, 0 = Fully Paid
    status = raw["loan_status"]
    raw = raw[status.isin(BAD | GOOD)].copy()
    raw["target"] = raw["loan_status"].isin(BAD).astype(int)

    issue = pd.to_datetime(raw["issue_d"], format="%b-%Y")
    term = raw["term"].str.extract(r"(\d+)")[0].astype(int)
    # «Созревшие» когорты: кредит должен был успеть закончиться до выгрузки. Иначе у свежих
    # выдач остаются только быстрые дефолты, и доля плохих искусственно занижается/искажается.
    end_month = issue.dt.year * 12 + issue.dt.month + term          # номер месяца окончания кредита
    keep = (end_month <= LAST_MONTH) & issue.dt.year.between(*YEARS)
    raw, issue, term = raw[keep], issue[keep], term[keep]

    inc = raw["annual_inc"]
    monthly = inc / 12
    fico = (raw["fico_range_low"] + raw["fico_range_high"]) / 2
    f = pd.DataFrame(index=raw.index)
    # доход режем сверху по 99.9-му перцентилю: в данных есть миллионные «зарплаты»-опечатки
    f["monthly_income"] = (monthly * RATE).clip(upper=(monthly * RATE).quantile(0.999))
    # emp_length: «< 1 year» -> 0, «10+ years» -> 120 месяцев; пропуск остаётся пропуском
    years = raw["emp_length"].str.extract(r"(\d+)")[0].astype(float)
    years = years.where(~raw["emp_length"].str.startswith("<", na=False), 0)
    f["employment_months"] = years * 12
    f["loan_amount"] = raw["loan_amnt"] * RATE
    f["loan_term_months"] = term
    f["purpose"] = raw["purpose"].map(PURPOSE_MAP).fillna(DEFAULT_PURPOSE)   # остальное -> consumer
    # Отношения: обрезаем сверху, чтобы единичные выбросы (доход ~0) не ломали модель
    f["payment_to_income"] = (raw["installment"] / monthly.where(monthly > 0)).clip(upper=3)
    # FICO 300..850 линейно -> 1..999, затем /1000: так же БКИ отдаёт bureau_score/1000
    f["bureau_score"] = ((fico - 300) / 550 * 998 + 1) / 1000
    f["open_credit_lines"] = raw["open_acc"]
    f["total_debt_to_income"] = (raw["total_bal_ex_mort"] / inc.where(inc > 0)).clip(upper=10)
    util = raw["revol_util"]
    if util.dtype == object or str(util.dtype).startswith("str"):
        util = util.str.rstrip("%").astype(float)
    f["revolving_utilization"] = (util / 100).clip(0, 3)
    f["delinq_2y"] = raw["delinq_2yrs"]
    f["overdue_90_plus"] = raw["num_tl_90g_dpd_24m"]
    first = pd.to_datetime(raw["earliest_cr_line"], format="%b-%Y")
    f["credit_history_months"] = ((issue.dt.year - first.dt.year) * 12 + (issue.dt.month - first.dt.month))
    f["inquiries_6m"] = raw["inq_last_6mths"]
    f["issue_year"] = issue.dt.year
    f["target"] = raw["target"]
    return f[FEATURES + ["issue_year", "target"]].reset_index(drop=True)


def load_prepared() -> pd.DataFrame:
    cache = csv_path().parent / "prepared.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    if not csv_path().exists():
        raise SystemExit(f"Нет файла {csv_path()}. Задайте LENDING_CLUB_CSV.")
    df = prepare()
    df.to_parquet(cache)
    return df
