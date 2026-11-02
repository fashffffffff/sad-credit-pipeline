"""Тесты ядра: 5 сценариев кейса (T2-1…T2-5) и все ветки правил."""

from dataclasses import replace
from pathlib import Path

import pytest

from credit_core import (PRODUCTS, REASONS, Application, BkiResult, compute_dti, counter_offers, decide_rows,
                         eligible_products, evaluate, explain, monthly_payment, read_csv, summary,
                         validate_for_api, validate_fields, write_decisions)
from credit_core import emulation

CSV = Path(__file__).resolve().parents[2] / "data" / "case2" / "applications.csv"
GOOD_BKI = BkiResult(status="SUCCESS", score=720, delinquency=False, requests_90d=1)


@pytest.fixture(scope="module")
def pairs():
    return decide_rows(read_csv(CSV.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def by_id(pairs):
    return {d.app_id: d for _, d in pairs}


def base(**kw) -> Application:
    app = Application(app_id="app_9001", client_id="cli_9001", passport_hash="0123456789abcdef",
                      age=32, income=120_000, employment="employed", family="married", amount=500_000, term=24)
    return replace(app, **kw)


# --- 5 обязательных сценариев (03_тесты.md) ---

def test_t2_1_approved(by_id):
    d = by_id["app_0001"]
    assert (d.decision, d.reason) == ("APPROVED", "")
    assert d.score == 90 and d.score >= 60
    assert round(d.dti, 2) == 0.21 and round(d.monthly_payment) == 24962
    assert (d.amount_approved, d.term_approved, d.rate) == (500_000, 24, 0.18)


def test_t2_2_refused_bki(by_id):
    d = by_id["app_0006"]
    assert (d.decision, d.reason) == ("REFUSED", "BKI_DELINQUENCY")
    assert d.score == 35 and d.amount_approved is None


def test_t2_3_missing_data_never_auto_approved(by_id):
    d = by_id["app_0004"]
    assert (d.decision, d.reason) == ("NEED_MORE_DATA", "MISSING_INCOME")
    assert d.amount_approved is None and d.score is None


def test_t2_4_low_income(by_id):
    assert (by_id["app_0002"].decision, by_id["app_0002"].reason) == ("REFUSED", "LOW_INCOME")


def test_t2_5_high_load_discrepancy_explained(pairs, by_id):
    d = by_id["app_0005"]
    assert (d.decision, d.reason) == ("REFUSED", "AMOUNT_LIMIT")
    assert round(d.dti, 2) == 0.39
    row = next(r for r, x in pairs if x.app_id == "app_0005")
    assert "0,39" in explain(row, d)


# --- Вся выборка ---

def test_csv_agreement_and_every_discrepancy_explained(pairs):
    s = summary(pairs)
    assert s["total"] == 100 and s["matched_csv"] == 91
    diffs = sorted(d.app_id for r, d in pairs if explain(r, d))
    assert diffs == ["app_0005", "app_0007", "app_0009", "app_0012",
                     "app_0019", "app_0025", "app_0045", "app_0064", "app_0070"]


def test_every_csv_reason_is_in_catalog(pairs):
    csv_reasons = {r["reason"] for r, _ in pairs if r["reason"]}
    assert csv_reasons <= set(REASONS)
    assert all(REASONS[c]["in_csv"] for c in csv_reasons)
    assert REASONS["EXTERNAL_TIMEOUT"]["category"] == "Нужны данные"


def test_dti_column_matches_formula(pairs):
    for row, d in pairs:
        if row["dti"]:
            assert f"{d.dti:.2f}" == f"{float(row['dti']):.2f}", row["app_id"]


def test_decisions_csv_format(pairs):
    text = write_decisions([d for _, d in pairs])
    lines = text.splitlines()
    assert lines[0] == "app_id,decision,reason,score,dti,amount_approved,term_approved"
    assert lines[1] == "app_0001,APPROVED,,90,0.21,500000,24"
    assert len(lines) == 101


# --- Отдельные правила ---

def test_rules_1_to_5_need_no_external_services():
    assert evaluate(base(age=75)).reason == "AGE_OUT_OF_RANGE"
    assert evaluate(base(employment="unemployed")).reason == "INVALID_EMPLOYMENT"
    assert evaluate(base(income=None)).reason == "MISSING_INCOME"
    assert evaluate(base(income=25_000, amount=50_000)).reason == "LOW_INCOME"
    assert evaluate(base(income=40_000, amount=900_000, term=12)).reason == "HIGH_DTI"
    local = evaluate(base())
    assert local.needs_external and local.score is None and len(local.breakdown) == 4


def test_age_checked_before_missing_income():
    d = evaluate(base(age=17, income=None))
    assert (d.decision, d.reason) == ("REFUSED", "AGE_OUT_OF_RANGE")


def test_timeout_is_never_refusal():
    for bki, smev in ((BkiResult(status="TIMEOUT"), "MATCH"), (GOOD_BKI, "TIMEOUT"), (BkiResult(status="ERROR"), "TIMEOUT")):
        d = evaluate(base(), bki=bki, smev=smev)
        assert (d.decision, d.reason) == ("NEED_MORE_DATA", "EXTERNAL_TIMEOUT")


def test_bki_threshold_550_and_delinquency():
    assert evaluate(base(), bki=BkiResult(score=549), smev="MATCH").reason == "BKI_DELINQUENCY"
    assert evaluate(base(), bki=BkiResult(score=550), smev="MATCH").decision == "APPROVED"
    assert evaluate(base(), bki=BkiResult(score=800, delinquency=True), smev="MATCH").reason == "BKI_DELINQUENCY"


def test_amount_limit_and_smev_mismatch():
    assert evaluate(base(amount=1_300_000, term=60), bki=GOOD_BKI, smev="MATCH").reason == "AMOUNT_LIMIT"
    assert evaluate(base(), bki=GOOD_BKI, smev="MISMATCH").reason == "SMEV_MISMATCH"


def test_low_score_goes_to_manual_review_not_refusal():
    d = evaluate(base(income=45_000, employment="student", age=20, family="single", amount=100_000, term=36),
                 bki=BkiResult(score=560), smev="MATCH")
    assert d.score == 10 + 5 + 10 + 10 + 0
    assert (d.decision, d.reason) == ("MANUAL_REVIEW", "LOW_SCORE")


def test_fraud_flag_goes_to_manual_review():
    d = evaluate(base(), bki=GOOD_BKI, smev="MATCH", fraud_flagged=True)
    assert (d.decision, d.reason) == ("MANUAL_REVIEW", "FRAUD_SUSPECT")


def test_score_capped_to_zero():
    # 0 (доход) + 0 (unemployed) + 5 (65 лет) + 10 (single) − 20 (БКИ) = −5 → 0
    d = evaluate(base(income=35_000, employment="unemployed", age=65, family="single", amount=50_000, term=12),
                 bki=BkiResult(score=450), smev="MATCH")
    assert d.reason == "INVALID_EMPLOYMENT"
    assert d.score == 0 and d.score_capped_to_zero


def test_score_and_dti_present_for_early_refusals(by_id):
    d = by_id["app_0002"]
    assert d.score == 25 and round(d.dti, 2) == 0.72


def test_trace_stops_at_triggered_rule():
    d = evaluate(base(), bki=GOOD_BKI, smev="MISMATCH")
    assert [t["n"] for t in d.trace] == list(range(1, 10))
    assert d.trace[-1]["passed"] is False and all(t["passed"] for t in d.trace[:-1])


# --- Деньги, продукты, проверки ---

def test_annuity_formula():
    assert round(monthly_payment(800_000, 48, 0.18)) == 23500
    assert round(compute_dti(1_000_000, 36, 200_000, 0.18), 2) == 0.18


def test_products_loaded_from_csv():
    assert set(PRODUCTS) == {"prod_standard", "prod_premium", "prod_student", "prod_pension", "prod_express"}
    assert PRODUCTS["prod_standard"].rate == 0.18
    ids = [p.product_id for p in eligible_products(age=20, income=50_000)]
    assert ids[0] == "prod_student" and "prod_premium" not in ids and "prod_pension" not in ids


def test_api_validation():
    assert validate_for_api(base()) == []
    assert validate_for_api(base(age=71))[0].code == "AGE_OUT_OF_RANGE"
    assert validate_for_api(base(family="divorced"))[0].code == "INVALID_FAMILY"
    assert validate_for_api(base(passport_hash="XYZ"))[0].code == "INVALID_PASSPORT_HASH"
    err = validate_for_api(base(product_id="prod_student", age=32))[0]
    assert err.code == "PRODUCT_MISMATCH" and "18–25" in err.message
    codes = {e.code for e in validate_fields(base(age=None, term=None))}
    assert codes == {"MISSING_AGE", "MISSING_TERM"}


def test_bad_format_in_batch_is_need_more_data():
    d = evaluate(base(employment="freelancer"), bki=GOOD_BKI, smev="MATCH")
    assert (d.decision, d.reason) == ("NEED_MORE_DATA", "INVALID_EMPLOYMENT")


# --- Встречные предложения ---

def test_counter_offers_are_approved_by_same_engine():
    app = base(income=60_000, amount=800_000, term=48, employment="self-employed", family="single", age=28)
    bki = BkiResult(score=610)
    d = evaluate(app, bki=bki, smev="MATCH")
    offers = counter_offers(app, bki, "MATCH", d.reason)
    assert offers, "для AMOUNT_LIMIT должно быть предложение"
    for o in offers:
        check = evaluate(replace(app, product_id=o["product_id"], amount=o["amount"], term=o["term"]), bki=bki, smev="MATCH")
        assert check.decision == "APPROVED"
        assert o["amount"] <= 600_000 and o["amount"] % 10_000 == 0


def test_counter_offer_longer_term_for_high_dti():
    app = base(income=50_000, amount=500_000, term=12)
    d = evaluate(app, bki=GOOD_BKI, smev="MATCH")
    assert d.reason == "HIGH_DTI"
    offers = counter_offers(app, GOOD_BKI, "MATCH", d.reason)
    assert any(o["amount"] == 500_000 and o["term"] > 12 for o in offers)


def test_no_offers_for_unfixable_reasons():
    assert counter_offers(base(), GOOD_BKI, "MISMATCH", "SMEV_MISMATCH") == []


# --- Эмуляторы (те же формулы проверяет Go-тест на паритет) ---

def test_emulator_formulas_follow_spec():
    seen = set()
    for i in range(200):
        r = emulation.bki_score(f"{i:016x}")
        seen.add((r.score, r.delinquency))
        assert r.requests_90d == 1
    assert seen == {(600, False), (620, False), (640, False), (660, False), (680, False), (700, False),
                    (450, False), (500, False), (550, False), (400, True)}
    statuses = [emulation.smev_status(f"cli_{i:04d}") for i in range(1000)]
    assert 0.05 < statuses.count("MISMATCH") / 1000 < 0.15
