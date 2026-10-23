"""KPI calculation. All numbers come from SQL over `reconciliation_results` (kpi_queries.sql),
so the dashboard, reports and tests share one definition. Money is returned in integer paise."""
from __future__ import annotations

import sqlite3

import config
from src.database import run_query

KPI_DEFINITIONS = {
    "total_transactions": "Count of unique, valid payment transactions (duplicate source rows and rows with data-quality errors excluded).",
    "gross_value": "Sum of gross_amount over those transactions (all statuses). Before fees and tax.",
    "successful_count": "Unique valid payments with status 'success'.",
    "successful_value": "Sum of gross_amount for successful payments.",
    "fees": "Sum of processing_fee on successful payments.",
    "tax_on_fees": "Sum of tax_on_fee (GST on the processing fee) on successful payments.",
    "expected_net": "Sum of expected_net_settlement = gross - processing fee - tax on fee, successful payments only. Failed/reversed payments contribute 0.",
    "settled_value": "Sum of settled_amount actually received for those payments (includes any excess from duplicate settlements).",
    "matched_count": "Payments with exactly one on-time settlement equal to the expected net amount.",
    "matched_value": "Settled value of matched payments.",
    "match_rate_pct": "Matched / (Matched + Delayed + Amount Mismatch + Duplicate + Unsettled) x 100. Not-yet-due and failed/reversed payments are excluded from the denominator.",
    "outstanding_count": "Unresolved exceptions: every is_exception record except Delayed Settlement (cash received, timing only).",
    "outstanding_exposure": "Financial exposure of outstanding exceptions: unsettled expected net, absolute mismatch, duplicate excess, settlement-only and failed-payment payouts.",
    "mismatch_abs": "Sum of absolute variance on Amount Mismatch payments.",
    "mismatch_net": "Signed sum of (settled - expected) on Amount Mismatch payments (negative = net under-settlement).",
    "delay_rate_pct": "Settlements later than expected date + grace days / all payments with exactly one settlement x 100.",
}


def date_bounds(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """Earliest and latest transaction_date in the results table."""
    row = conn.execute("SELECT MIN(transaction_date), MAX(transaction_date) FROM reconciliation_results").fetchone()
    return row[0], row[1]


def _pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def compute_kpis(conn: sqlite3.Connection, start: str | None = None, end: str | None = None,
                 include_undated: bool = True) -> dict:
    """Compute the executive KPI set for a transaction-date window (inclusive)."""
    lo, hi = date_bounds(conn)
    start = start or lo or "1900-01-01"
    end = end or hi or "9999-12-31"
    params = {"start": start, "end": end, "include_undated": int(include_undated),
              "grace": config.SETTLEMENT_GRACE_DAYS}
    row = run_query(conn, "kpi_summary", params).iloc[0]
    fees = run_query(conn, "kpi_fees", params).iloc[0]
    g = lambda k: int(row[k]) if row[k] == row[k] and row[k] is not None else 0  # noqa: E731  (NaN-safe)
    k = {
        "period_start": start, "period_end": end,
        "total_transactions": g("total_transactions"),
        "gross_value_paise": g("gross_value_paise"),
        "successful_count": g("successful_count"),
        "successful_value_paise": g("successful_value_paise"),
        "fees_paise": int(fees["fee_paise"]), "tax_on_fees_paise": int(fees["tax_paise"]),
        "expected_net_paise": g("expected_net_paise"),
        "settled_paise": g("settled_paise"),
        "matched_count": g("matched_count"), "matched_value_paise": g("matched_value_paise"),
        "reconcilable_count": g("reconcilable_count"),
        "outstanding_count": g("outstanding_count"),
        "outstanding_exposure_paise": g("exception_exposure_paise"),
        "exception_count_all": g("exception_count"),
        "mismatch_abs_paise": g("mismatch_abs_paise"), "mismatch_net_paise": g("mismatch_net_paise"),
        "delayed_count": g("delayed_count"),
        "single_settled_count": g("single_settled_count"), "late_count": g("late_count"),
        "excluded_payment_rows": g("payment_rows") - g("total_transactions"),  # duplicates + invalid rows
    }
    k["match_rate_pct"] = _pct(k["matched_count"], k["reconcilable_count"])
    k["delay_rate_pct"] = _pct(k["late_count"], k["single_settled_count"])
    return k
