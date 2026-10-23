"""Source-file loading and row-level validation.

Nothing is dropped silently: every row is kept, annotated with `validation_errors`
(semicolon-separated reasons, empty when valid) and typed money columns in integer paise.
Invalid or duplicate rows are later written to `rejected_records` with their reasons.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from src.data_generator import PAYMENT_COLUMNS, SETTLEMENT_COLUMNS
from src.money import parse_amount, to_paise

log = logging.getLogger(__name__)

TXN_RE = re.compile(r"^TXN\d{8}$")
MERCHANT_RE = re.compile(r"^MID\d{3}$")


class SchemaError(ValueError):
    """Raised when a source file is missing, unreadable or lacks required columns."""


def load_csv(path: Path | str, expected_columns: list[str]) -> pd.DataFrame:
    """Read a source CSV as strings (no type coercion) and check its columns."""
    p = Path(path)
    if not p.exists():
        raise SchemaError(f"Input file not found: {p}")
    try:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError as exc:
        raise SchemaError(f"Input file is empty (no header row): {p}") from exc
    missing = [c for c in expected_columns if c not in df.columns]
    if missing:
        raise SchemaError(f"{p.name} is missing required columns: {', '.join(missing)}")
    extra = [c for c in df.columns if c not in expected_columns]
    if extra:
        log.warning("%s has unexpected extra columns (ignored): %s", p.name, extra)
    return df[expected_columns].copy()


def _valid_ts(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return True
    except (ValueError, TypeError):
        return False


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _money_field(raw: str, name: str, errors: list[str], *, positive: bool) -> int | None:
    """Parse one money field; append an error and return None if invalid."""
    amount = parse_amount(raw)
    if amount is None:
        errors.append(f"{name} missing or non-numeric")
        return None
    if positive and amount <= 0:
        errors.append(f"{name} must be > 0")
        return None
    if not positive and amount < 0:
        errors.append(f"{name} must be >= 0")
        return None
    return to_paise(amount)


def validate_payments(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate payment rows; returns a typed frame with `row_id`, paise columns and errors."""
    seen: dict[str, int] = {}
    out = []
    for pos, rec in enumerate(raw.to_dict("records"), start=1):
        errs: list[str] = []
        txn = rec["transaction_id"].strip()
        if not txn:
            errs.append("transaction_id missing")
        elif not TXN_RE.match(txn):
            errs.append("transaction_id has invalid format")
        if not rec["merchant_id"].strip():
            errs.append("merchant_id missing")
        elif not MERCHANT_RE.match(rec["merchant_id"].strip()):
            errs.append("merchant_id has invalid format")
        if not _valid_ts(rec["transaction_timestamp"]):
            errs.append("transaction_timestamp missing or invalid")
        gross = _money_field(rec["gross_amount"], "gross_amount", errs, positive=True)
        if rec["currency"] != config.CURRENCY:
            errs.append(f"currency must be {config.CURRENCY}")
        if rec["payment_method"] not in config.VALID_PAYMENT_METHODS:
            errs.append("payment_method invalid")
        if rec["payment_status"] not in config.VALID_PAYMENT_STATUSES:
            errs.append("payment_status invalid")
        if not _valid_date(rec["expected_settlement_date"]):
            errs.append("expected_settlement_date missing or invalid")
        fee = _money_field(rec["processing_fee"], "processing_fee", errs, positive=False)
        tax = _money_field(rec["tax_on_fee"], "tax_on_fee", errs, positive=False)
        net = _money_field(rec["expected_net_settlement"], "expected_net_settlement", errs, positive=False)
        if not rec["source_reference"].strip():
            errs.append("source_reference missing")
        if (None not in (gross, fee, tax, net) and rec["payment_status"] == "success"
                and net != gross - fee - tax):
            errs.append("expected_net_settlement != gross_amount - processing_fee - tax_on_fee")
        is_dup = 0
        if txn:
            if txn in seen:
                is_dup = 1
            else:
                seen[txn] = pos
        out.append({
            "row_id": pos, "transaction_id": txn, "merchant_id": rec["merchant_id"].strip(),
            "transaction_timestamp": rec["transaction_timestamp"], "gross_amount_paise": gross,
            "currency": rec["currency"], "payment_method": rec["payment_method"],
            "payment_status": rec["payment_status"], "expected_settlement_date": rec["expected_settlement_date"],
            "processing_fee_paise": fee, "tax_on_fee_paise": tax, "expected_net_paise": net,
            "source_reference": rec["source_reference"], "is_duplicate_source": is_dup,
            "validation_errors": "; ".join(errs),
            "first_row_id": seen.get(txn) if is_dup else None,
        })
    cols = ["row_id", "transaction_id", "merchant_id", "transaction_timestamp", "gross_amount_paise", "currency",
            "payment_method", "payment_status", "expected_settlement_date", "processing_fee_paise",
            "tax_on_fee_paise", "expected_net_paise", "source_reference", "is_duplicate_source",
            "validation_errors", "first_row_id"]
    df = pd.DataFrame(out, columns=cols)
    for c in ("gross_amount_paise", "processing_fee_paise", "tax_on_fee_paise", "expected_net_paise", "first_row_id"):
        df[c] = df[c].astype("Int64")
    return df


def validate_settlements(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate settlement rows; returns a typed frame with `row_id`, paise column and errors."""
    seen_ids: set[str] = set()
    out = []
    for pos, rec in enumerate(raw.to_dict("records"), start=1):
        errs: list[str] = []
        sid = rec["settlement_id"].strip()
        if not sid:
            errs.append("settlement_id missing")
        elif sid in seen_ids:
            errs.append("duplicate settlement_id")
        seen_ids.add(sid)
        txn = rec["transaction_id"].strip()
        if not txn:
            errs.append("transaction_id missing")
        elif not TXN_RE.match(txn):
            errs.append("transaction_id has invalid format")
        if not rec["merchant_id"].strip():
            errs.append("merchant_id missing")
        elif not MERCHANT_RE.match(rec["merchant_id"].strip()):
            errs.append("merchant_id has invalid format")
        if not _valid_ts(rec["settlement_timestamp"]):
            errs.append("settlement_timestamp missing or invalid")
        amt = _money_field(rec["settled_amount"], "settled_amount", errs, positive=True)
        if not rec["settlement_reference"].strip():
            errs.append("settlement_reference missing")
        if rec["settlement_status"] not in config.VALID_SETTLEMENT_STATUSES:
            errs.append("settlement_status invalid")
        out.append({
            "row_id": pos, "settlement_id": sid, "transaction_id": txn,
            "merchant_id": rec["merchant_id"].strip(), "settlement_timestamp": rec["settlement_timestamp"],
            "settled_amount_paise": amt, "settlement_reference": rec["settlement_reference"].strip(),
            "settlement_status": rec["settlement_status"], "validation_errors": "; ".join(errs),
        })
    df = pd.DataFrame(out, columns=["row_id", "settlement_id", "transaction_id", "merchant_id", "settlement_timestamp",
                                    "settled_amount_paise", "settlement_reference", "settlement_status", "validation_errors"])
    df["settled_amount_paise"] = df["settled_amount_paise"].astype("Int64")
    return df


def rejected_from(payments: pd.DataFrame, settlements: pd.DataFrame,
                  raw_payments: pd.DataFrame, raw_settlements: pd.DataFrame) -> pd.DataFrame:
    """Build the quarantine table (source, row_id, reason, raw_record) from validated frames."""
    import json
    recs = []
    for _, r in payments[(payments["validation_errors"] != "") | (payments["is_duplicate_source"] == 1)].iterrows():
        reasons = [r["validation_errors"]] if r["validation_errors"] else []
        if r["is_duplicate_source"] == 1:
            reasons.append(f"duplicate source record of row {int(r['first_row_id'])}")
        recs.append(("payments", int(r["row_id"]), "; ".join(reasons),
                     json.dumps(raw_payments.iloc[int(r["row_id"]) - 1].to_dict())))
    for _, r in settlements[settlements["validation_errors"] != ""].iterrows():
        recs.append(("settlements", int(r["row_id"]), r["validation_errors"],
                     json.dumps(raw_settlements.iloc[int(r["row_id"]) - 1].to_dict())))
    return pd.DataFrame(recs, columns=["source", "row_id", "reason", "raw_record"])
