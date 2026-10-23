"""Standard finance CSV reports. Each is built from a named SQL query, monetary columns are
converted from integer paise to 2-decimal INR strings, and a metadata JSON accompanies the set."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from src.database import run_query
from src.kpis import date_bounds
from src.money import paise_to_str

log = logging.getLogger(__name__)

# name -> (sql query name, definition)
REPORTS: dict[str, tuple[str, str]] = {
    "daily_transaction_summary": ("daily_transactions",
        "Per transaction date: valid non-duplicate payments, gross value, expected net, settled value and exception count."),
    "merchant_summary": ("merchant_summary",
        "Per merchant: transactions, gross, expected net, settled, matched, exceptions, exposure and match rate (successful payments)."),
    "settlement_reconciliation_summary": ("settlement_reconciliation_summary",
        "Per reconciliation status: record count, expected net, settled, variance (settled - expected) and exposure."),
    "outstanding_exception_report": ("report_outstanding_exceptions",
        "Unresolved exceptions (all except Delayed Settlement) sorted by financial exposure, with reason."),
    "amount_mismatch_report": ("report_amount_mismatch",
        "Single-settlement payments whose settled amount differs from expected net; variance = settled - expected."),
    "settlement_ageing_report": ("report_settlement_ageing",
        "Unsettled items (age = as-of date - expected date) and delayed settlements (age = delay days), with ageing bucket."),
    "data_quality_report": ("dq_rejected_records",
        "Source rows quarantined for validation failures or duplication, with reasons and the original raw record."),
}


def paise_to_inr_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Replace every `*_paise` integer column with a `*_inr` 2-decimal string column."""
    out = df.copy()
    for col in [c for c in out.columns if c.endswith("_paise")]:
        out[col[:-6] + "_inr"] = out[col].map(lambda v: "" if pd.isna(v) else paise_to_str(int(v)))
        out = out.drop(columns=col)
    return out


def build_report(conn: sqlite3.Connection, name: str, start: str, end: str) -> pd.DataFrame:
    """Build one report as a DataFrame (INR strings instead of paise)."""
    if name not in REPORTS:
        raise KeyError(f"Unknown report: {name}")
    params = {"start": start, "end": end, "high_value": 0}
    df = run_query(conn, REPORTS[name][0], params)
    for col in ("age_days", "delay_days"):   # pandas turns nullable ints into floats; restore whole numbers
        if col in df.columns:
            df[col] = df[col].astype("Int64")
    return paise_to_inr_columns(df)


def generate_reports(conn: sqlite3.Connection, out_dir: Path | str | None = None,
                     start: str | None = None, end: str | None = None,
                     generated_at: datetime | None = None) -> dict:
    """Write all standard reports plus report_metadata.json; return the metadata dict."""
    out = Path(out_dir) if out_dir else config.REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    lo, hi = date_bounds(conn)
    start, end = start or lo or "1900-01-01", end or hi or "9999-12-31"
    stamp = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    meta = {"generated_at": stamp, "reporting_period": {"start": start, "end": end}, "currency": config.CURRENCY,
            "note": "Synthetic data. Amounts in INR with 2 decimals. Period filters transaction_date.",
            "reports": {}}
    for name, (_, definition) in REPORTS.items():
        df = build_report(conn, name, start, end)
        path = out / f"{name}.csv"
        df.to_csv(path, index=False)
        meta["reports"][name] = {"file": path.name, "row_count": int(len(df)), "definition": definition}
        log.info("Wrote %s (%d rows)", path.name, len(df))
    (out / "report_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
