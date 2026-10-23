"""Validation: invalid/missing values, schema errors, empty inputs, quarantine."""
import pandas as pd
import pytest

from src.data_generator import PAYMENT_COLUMNS
from src.ingestion import load_and_validate
from src.validation import SchemaError, load_csv, validate_payments, validate_settlements, rejected_from
from tests.helpers import pay, raw_frames, run_recon, stl, write_csvs


def _errs(df):
    return df.iloc[0]["validation_errors"]


@pytest.mark.parametrize("override,fragment", [
    ({"merchant_id": ""}, "merchant_id missing"),
    ({"transaction_id": ""}, "transaction_id missing"),
    ({"transaction_id": "ABC"}, "transaction_id has invalid format"),
    ({"gross_amount": "-5.00"}, "gross_amount must be > 0"),
    ({"gross_amount": "abc"}, "gross_amount missing or non-numeric"),
    ({"gross_amount": ""}, "gross_amount missing or non-numeric"),
    ({"currency": "USD"}, "currency must be INR"),
    ({"payment_status": "pending"}, "payment_status invalid"),
    ({"payment_method": "cheque"}, "payment_method invalid"),
    ({"transaction_timestamp": "2026-13-45"}, "transaction_timestamp missing or invalid"),
    ({"expected_settlement_date": ""}, "expected_settlement_date missing or invalid"),
    ({"expected_net_settlement": "1.00"}, "expected_net_settlement != gross_amount"),
])
def test_payment_invalid_values_detected(override, fragment):
    rp, _ = raw_frames([pay(**override)], [])
    assert fragment in _errs(validate_payments(rp))


def test_valid_payment_has_no_errors():
    rp, _ = raw_frames([pay()], [])
    assert _errs(validate_payments(rp)) == ""


@pytest.mark.parametrize("override,fragment", [
    ({"transaction_id": ""}, "transaction_id missing"),
    ({"settled_amount": "abc"}, "settled_amount missing or non-numeric"),
    ({"settled_amount": "-250.00"}, "settled_amount must be > 0"),
    ({"settled_amount": "0.00"}, "settled_amount must be > 0"),
    ({"settlement_status": "weird"}, "settlement_status invalid"),
    ({"settlement_reference": ""}, "settlement_reference missing"),
])
def test_settlement_invalid_values_detected(override, fragment):
    _, rs = raw_frames([], [stl(**override)])
    assert fragment in _errs(validate_settlements(rs))


def test_duplicate_settlement_id_flagged():
    _, rs = raw_frames([], [stl("S1"), stl("S1", ref="OTHER")])
    v = validate_settlements(rs)
    assert v.iloc[0]["validation_errors"] == "" and "duplicate settlement_id" in v.iloc[1]["validation_errors"]


def test_duplicate_source_marks_second_row_only():
    rp, _ = raw_frames([pay(), pay("TXN00000002"), pay()], [])
    v = validate_payments(rp)
    assert v["is_duplicate_source"].tolist() == [0, 0, 1] and int(v.iloc[2]["first_row_id"]) == 1


def test_quarantine_keeps_reason_and_raw_record():
    rp, rs = raw_frames([pay(merchant=""), pay("TXN00000002")], [stl(amount="abc")])
    vp, vs = validate_payments(rp), validate_settlements(rs)
    rej = rejected_from(vp, vs, rp, rs)
    assert len(rej) == 2 and set(rej["source"]) == {"payments", "settlements"}
    assert rej["reason"].str.len().gt(0).all() and '"merchant_id": ""' in rej.iloc[0]["raw_record"]


def test_invalid_rows_are_not_dropped():
    r = run_recon([pay(merchant="")], [])
    assert len(r) == 1 and r.iloc[0]["recon_status"] == "Data Quality Error"


# ------------------------------------------------------------------ empty / unexpected input
def test_empty_datasets_header_only(tmp_path):
    p, s = write_csvs([], [], tmp_path)
    data = load_and_validate(p, s)
    assert len(data.payments) == 0 and len(data.settlements) == 0 and data.rejected.empty


def test_empty_datasets_reconcile_to_empty():
    r = run_recon([], [])
    assert len(r) == 0


def test_missing_file_raises_schema_error(tmp_path):
    with pytest.raises(SchemaError, match="not found"):
        load_csv(tmp_path / "nope.csv", PAYMENT_COLUMNS)


def test_zero_byte_file_raises_schema_error(tmp_path):
    f = tmp_path / "empty.csv"
    f.write_text("")
    with pytest.raises(SchemaError, match="empty"):
        load_csv(f, PAYMENT_COLUMNS)


def test_unexpected_schema_names_missing_columns(tmp_path):
    f = tmp_path / "bad.csv"
    pd.DataFrame({"foo": ["1"], "transaction_id": ["TXN00000001"]}).to_csv(f, index=False)
    with pytest.raises(SchemaError, match="missing required columns: merchant_id"):
        load_csv(f, PAYMENT_COLUMNS)


def test_extra_columns_are_ignored(tmp_path):
    p, s = write_csvs([pay()], [stl()], tmp_path)
    df = pd.read_csv(p, dtype=str)
    df["bonus"] = "x"
    df.to_csv(p, index=False)
    assert "bonus" not in load_csv(p, PAYMENT_COLUMNS).columns
