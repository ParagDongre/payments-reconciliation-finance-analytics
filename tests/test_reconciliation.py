"""Reconciliation rules: matching, missing/orphan/duplicate detection, variance, precedence."""
from decimal import Decimal

from src.money import paise_to_str, parse_amount, round_money, to_paise
from src.exception_analysis import explain_exception, ai_rephrase, AIConfigError
from tests.helpers import pay, run_recon, status_of, stl


def test_exact_match():
    r = run_recon([pay()], [stl()])
    row = r.iloc[0]
    assert row["recon_status"] == "Matched" and row["is_exception"] == 0
    assert row["expected_net_paise"] == 97640 and row["settled_paise"] == 97640 and row["variance_paise"] == 0
    assert "equals expected net" in row["reason"]


def test_missing_settlement_is_unsettled_with_age():
    r = run_recon([pay()], [])
    row = r.iloc[0]
    assert row["recon_status"] == "Unsettled"
    assert row["age_days"] == (__import__("datetime").date(2026, 7, 15) - __import__("datetime").date(2026, 3, 3)).days
    assert row["exposure_paise"] == 97640 and row["ageing_bucket"] == "30+ days"


def test_not_yet_due_is_not_an_exception():
    r = run_recon([pay(ts="2026-07-14 10:00:00", exp="2026-07-15")], [])
    assert r.iloc[0]["recon_status"] == "Not Yet Due" and r.iloc[0]["is_exception"] == 0


def test_settlement_only_detected():
    r = run_recon([pay()], [stl(), stl("STL00000009", "TXN00000099", amount="250.00")])
    row = r[r["transaction_id"] == "TXN00000099"].iloc[0]
    assert row["recon_status"] == "Settlement Only" and row["record_type"] == "settlement"
    assert row["exposure_paise"] == 25000


def test_duplicate_settlement_same_reference():
    r = run_recon([pay()], [stl("STL1", ref="UTR1"), stl("STL2", ref="UTR1")])
    row = r.iloc[0]
    assert row["recon_status"] == "Duplicate Settlement"
    assert row["settled_paise"] == 2 * 97640 and row["exposure_paise"] == 97640 and "share one settlement_reference" in row["reason"]


def test_duplicate_source_record_flagged_and_not_double_counted():
    r = run_recon([pay(), pay()], [stl()])
    assert sorted(r["recon_status"]) == ["Data Quality Error", "Matched"]
    dq = r[r["recon_status"] == "Data Quality Error"].iloc[0]
    assert "Duplicate source record" in dq["reason"]


def test_amount_mismatch_variance_sign_and_size():
    under = run_recon([pay()], [stl(amount="900.00")]).iloc[0]
    over = run_recon([pay()], [stl(amount="980.00")]).iloc[0]
    assert under["recon_status"] == "Amount Mismatch" and under["variance_paise"] == -7640 and under["exposure_paise"] == 7640
    assert over["variance_paise"] == 360 and "over-settled" in over["reason"]


def test_one_paisa_difference_is_a_mismatch_but_tolerance_absorbs_it():
    assert run_recon([pay()], [stl(amount="976.41")]).iloc[0]["recon_status"] == "Amount Mismatch"
    assert run_recon([pay()], [stl(amount="976.41")], tolerance_paise=1).iloc[0]["recon_status"] == "Matched"


def test_failed_and_reversed_without_settlement_are_not_exceptions():
    r = run_recon([pay("TXN00000001", status="failed"), pay("TXN00000002", status="reversed")], [])
    assert set(r["recon_status"]) == {"Failed or Reversed"} and r["is_exception"].sum() == 0
    assert (r["expected_net_paise"] == 0).all()


def test_failed_payment_with_settlement_is_exception():
    r = run_recon([pay(status="failed")], [stl(amount="1000.00")]).iloc[0]
    assert r["recon_status"] == "Failed or Reversed" and r["is_exception"] == 1 and r["exposure_paise"] == 100000


def test_delayed_settlement_amount_ok_but_late():
    r = run_recon([pay()], [stl(ts="2026-03-10 12:00:00")]).iloc[0]
    assert r["recon_status"] == "Delayed Settlement" and r["delay_days"] == 7 and r["exposure_paise"] == 0


def test_within_grace_is_matched():
    r = run_recon([pay()], [stl(ts="2026-03-04 12:00:00")]).iloc[0]   # 1 day late, grace = 1
    assert r["recon_status"] == "Matched" and r["delay_days"] == 1
    assert run_recon([pay()], [stl(ts="2026-03-04 12:00:00")], grace_days=0).iloc[0]["recon_status"] == "Delayed Settlement"


# ------------------------------------------------------------------ precedence
def test_precedence_dq_beats_everything():
    r = run_recon([pay(merchant="")], [stl(amount="1.00")]).iloc[0]
    assert r["recon_status"] == "Data Quality Error"


def test_precedence_failed_beats_duplicate():
    assert run_recon([pay(status="failed")], [stl("S1", ref="R"), stl("S2", ref="R")]).iloc[0]["recon_status"] == "Failed or Reversed"


def test_precedence_duplicate_beats_mismatch_and_delay():
    r = run_recon([pay()], [stl("S1", amount="900.00", ts="2026-04-01 10:00:00"), stl("S2", amount="900.00", ts="2026-04-01 10:00:00")])
    assert r.iloc[0]["recon_status"] == "Duplicate Settlement"


def test_precedence_mismatch_beats_delay():
    r = run_recon([pay()], [stl(amount="900.00", ts="2026-04-01 10:00:00")]).iloc[0]
    assert r["recon_status"] == "Amount Mismatch" and r["delay_days"] == 29


def test_every_result_row_has_a_reason_and_original_rows_are_kept():
    pays = [pay(), pay("TXN00000002", merchant=""), pay("TXN00000003", status="failed")]
    stls = [stl(), stl("STL9", "TXN00000077")]
    r = run_recon(pays, stls)
    # 3 payments (matched, DQ, failed) + 1 settlement-only row; nothing silently dropped
    assert len(r) == 4 and r["reason"].str.len().gt(10).all()
    assert sorted(r["recon_status"]) == ["Data Quality Error", "Failed or Reversed", "Matched", "Settlement Only"]


def test_pending_settlement_row_is_not_active():
    r = run_recon([pay()], [stl(status="pending")]).iloc[0]
    assert r["recon_status"] == "Unsettled" and "non-'settled'" in r["reason"]


def test_invalid_settlement_row_is_quarantined_not_matched():
    r = run_recon([pay()], [stl(amount="abc")])
    assert set(r["recon_status"]) == {"Unsettled", "Data Quality Error"}
    assert "quarantined" in r[r["record_type"] == "payment"].iloc[0]["reason"]


# ------------------------------------------------------------------ money & explanations
def test_monetary_rounding_half_up_not_bankers():
    assert round_money(Decimal("2.675")) == Decimal("2.68")
    assert round_money(Decimal("0.125")) == Decimal("0.13")
    assert to_paise("19.999") == 2000 and paise_to_str(5) == "0.05" and paise_to_str(-1234) == "-12.34"


def test_no_float_error_in_amount_comparison():
    assert to_paise("0.1") + to_paise("0.2") == to_paise("0.3")
    assert parse_amount("12,50") is None and parse_amount("NaN") is None and parse_amount("") is None


def test_explanation_is_deterministic_and_complete():
    row = run_recon([pay()], [stl(amount="900.00")]).iloc[0].to_dict()
    e1, e2 = explain_exception(row), explain_exception(row)
    assert e1 == e2
    assert set(e1) == {"what_was_detected", "financial_impact", "supporting_records", "next_steps"}
    assert "under-paid" in e1["financial_impact"] and "STL00000001" in e1["supporting_records"]


def test_ai_is_off_by_default_and_makes_no_call():
    called = []
    try:
        ai_rephrase({"a": "b"}, provider=lambda p, k: called.append(1) or "x")
        raise AssertionError("expected AIConfigError")
    except AIConfigError:
        pass
    assert not called
