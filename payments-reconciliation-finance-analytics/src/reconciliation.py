"""Reconciliation engine: classify every payment and settlement record.

Precedence for a payment (first rule that applies wins; see docs/reconciliation_rules.md):
    1. Data Quality Error   - duplicate source row, or invalid field values
    2. Failed or Reversed   - payment_status is failed/reversed (a settlement here is an exception)
    3. Duplicate Settlement - more than one active settlement row for the transaction
    4. Amount Mismatch      - one settlement whose amount differs from expected net (beyond tolerance)
    5. Delayed Settlement   - one correct settlement, but later than expected date + grace days
    6. Unsettled            - no active settlement and the expected date has passed
       (Not Yet Due         - no active settlement yet, but not past due: informational)
    7. Matched              - one on-time settlement for exactly the expected net amount
Settlement rows with no payment are 'Settlement Only'; invalid settlement rows are
'Data Quality Error'. All comparisons use integer paise (no floats).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

import pandas as pd

import config
from src.money import paise_to_str, to_paise

MATCHED = "Matched"
UNSETTLED = "Unsettled"
NOT_YET_DUE = "Not Yet Due"
SETTLEMENT_ONLY = "Settlement Only"
AMOUNT_MISMATCH = "Amount Mismatch"
DUPLICATE_SETTLEMENT = "Duplicate Settlement"
DELAYED = "Delayed Settlement"
FAILED_REVERSED = "Failed or Reversed"
DQ_ERROR = "Data Quality Error"

ALL_STATUSES = [MATCHED, UNSETTLED, NOT_YET_DUE, SETTLEMENT_ONLY, AMOUNT_MISMATCH,
                DUPLICATE_SETTLEMENT, DELAYED, FAILED_REVERSED, DQ_ERROR]

RESULT_COLUMNS = [
    "record_type", "transaction_id", "payment_row_id", "settlement_row_ids", "settlement_ids",
    "settlement_references", "merchant_id", "payment_method", "payment_status", "transaction_date",
    "gross_amount_paise", "expected_settlement_date", "settlement_date", "recon_status", "reason",
    "is_exception", "expected_net_paise", "settled_paise", "variance_paise", "exposure_paise",
    "delay_days", "age_days", "ageing_bucket",
]


def ageing_bucket(age_days: int | None) -> str | None:
    """Map an age in days to its configured bucket label."""
    if age_days is None:
        return None
    for lo, hi, label in config.AGEING_BUCKETS:
        if lo <= age_days <= hi:
            return label
    return config.AGEING_BUCKETS[0][2]  # negative age: treat as freshest bucket


def _inr(paise: int | None) -> str:
    return "n/a" if paise is None else f"INR {paise_to_str(int(paise))}"


def _d(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _none(v):
    return None if v is None or (v is pd.NA) or (isinstance(v, float) and pd.isna(v)) else v


def reconcile(payments: pd.DataFrame, settlements: pd.DataFrame,
              asof: date | str | None = None, tolerance_paise: int | None = None,
              grace_days: int | None = None) -> pd.DataFrame:
    """Reconcile validated payments and settlements; return one auditable row per record."""
    asof_d = _d(asof) if isinstance(asof, str) else (asof or _d(config.GENERATION_ASOF_DATE))
    tol = to_paise(config.AMOUNT_TOLERANCE) if tolerance_paise is None else tolerance_paise
    grace = config.SETTLEMENT_GRACE_DAYS if grace_days is None else grace_days

    pay_ids = set(payments["transaction_id"]) if len(payments) else set()
    active: dict[str, list[dict]] = defaultdict(list)    # valid + status 'settled'
    inactive: dict[str, list[dict]] = defaultdict(list)  # valid but pending/reversed
    invalid_by_txn: dict[str, int] = defaultdict(int)
    results: list[dict] = []

    for rec in settlements.to_dict("records"):
        txn = rec["transaction_id"]
        if rec["validation_errors"]:
            invalid_by_txn[txn] += 1
            amt = _none(rec["settled_amount_paise"])
            results.append(_row("settlement", txn, None, [rec], rec["merchant_id"], recon_status=DQ_ERROR,
                                reason=f"Settlement row {rec['row_id']} ({rec['settlement_id'] or 'no id'}) failed validation: "
                                       f"{rec['validation_errors']}. Quarantined, not used for matching.",
                                is_exception=1, settled=amt, exposure=int(amt) if amt else 0))
        elif rec["settlement_status"] == "settled":
            active[txn].append(rec)
        else:
            inactive[txn].append(rec)

    for p in payments.to_dict("records"):
        txn = p["transaction_id"]
        net = _none(p["expected_net_paise"])
        gross = _none(p["gross_amount_paise"])
        base = dict(txn=txn, prow=p["row_id"], merchant=p["merchant_id"], pay=p)
        rows = active.get(txn, []) if txn else []
        settled_total = sum(int(r["settled_amount_paise"]) for r in rows) if rows else None

        if p["is_duplicate_source"] == 1:
            results.append(_row("payment", txn, p["row_id"], [], p["merchant_id"], pay=p, recon_status=DQ_ERROR,
                                reason=f"Duplicate source record: repeats payments row {int(p['first_row_id'])}. "
                                       "Excluded from totals so the transaction is counted once.",
                                is_exception=1, exposure=0))
            continue
        if p["validation_errors"]:
            results.append(_row("payment", txn, p["row_id"], rows, p["merchant_id"], pay=p, recon_status=DQ_ERROR,
                                reason=f"Invalid payment record: {p['validation_errors']}. Excluded from financial KPIs until corrected.",
                                is_exception=1, expected=net, settled=settled_total,
                                exposure=int(net) if net else 0))
            continue

        status = p["payment_status"]
        if status in ("failed", "reversed"):
            if rows:
                results.append(_row("payment", txn, p["row_id"], rows, p["merchant_id"], pay=p, recon_status=FAILED_REVERSED,
                                    reason=f"Payment is {status} (no funds should settle) but {len(rows)} settlement(s) totalling "
                                           f"{_inr(settled_total)} exist. Possible incorrect payout.",
                                    is_exception=1, expected=0, settled=settled_total, variance=settled_total,
                                    exposure=settled_total))
            else:
                results.append(_row("payment", txn, p["row_id"], [], p["merchant_id"], pay=p, recon_status=FAILED_REVERSED,
                                    reason=f"Payment is {status}; no settlement expected and none found. Excluded from settlement matching.",
                                    is_exception=0, expected=0))
            continue

        if len(rows) > 1:
            refs = {r["settlement_reference"] for r in rows}
            shared = "share one settlement_reference" if len(refs) == 1 else "have different references"
            excess = settled_total - net
            results.append(_row("payment", txn, p["row_id"], rows, p["merchant_id"], pay=p, recon_status=DUPLICATE_SETTLEMENT,
                                reason=f"{len(rows)} settlement rows ({', '.join(r['settlement_id'] for r in rows)}) {shared}; "
                                       f"total {_inr(settled_total)} vs expected {_inr(net)}, excess {_inr(excess)}.",
                                is_exception=1, expected=net, settled=settled_total, variance=excess,
                                exposure=max(excess, 0)))
            continue

        if len(rows) == 1:
            r = rows[0]
            amount = int(r["settled_amount_paise"])
            variance = amount - int(net)
            delay = (_d(r["settlement_timestamp"]) - _d(p["expected_settlement_date"])).days
            if abs(variance) > tol:
                direction = "under" if variance < 0 else "over"
                results.append(_row("payment", txn, p["row_id"], rows, p["merchant_id"], pay=p, recon_status=AMOUNT_MISMATCH,
                                    reason=f"Settled {_inr(amount)} vs expected net {_inr(net)}: {direction}-settled by "
                                           f"{_inr(abs(variance))} (tolerance {_inr(tol)}).",
                                    is_exception=1, expected=net, settled=amount, variance=variance,
                                    exposure=abs(variance), delay=delay))
            elif delay > grace:
                results.append(_row("payment", txn, p["row_id"], rows, p["merchant_id"], pay=p, recon_status=DELAYED,
                                    reason=f"Amount matches ({_inr(amount)}) but settled {delay} days after expected "
                                           f"{p['expected_settlement_date']} (grace {grace} day(s)). Timing exception only.",
                                    is_exception=1, expected=net, settled=amount, variance=variance, exposure=0, delay=delay,
                                    age=delay))
            else:
                results.append(_row("payment", txn, p["row_id"], rows, p["merchant_id"], pay=p, recon_status=MATCHED,
                                    reason=f"Single settlement of {_inr(amount)} equals expected net, "
                                           f"{'on time' if delay <= 0 else f'{delay} day(s) after expected date, within grace'}.",
                                    is_exception=0, expected=net, settled=amount, variance=variance, delay=delay))
            continue

        # no active settlement
        age = (asof_d - _d(p["expected_settlement_date"])).days
        note = ""
        if inactive.get(txn):
            note += f" {len(inactive[txn])} non-'settled' settlement row(s) ignored."
        if invalid_by_txn.get(txn):
            note += f" {invalid_by_txn[txn]} invalid settlement row(s) quarantined for this transaction."
        if age <= grace:
            results.append(_row("payment", txn, p["row_id"], [], p["merchant_id"], pay=p, recon_status=NOT_YET_DUE,
                                reason=f"No settlement yet; expected {p['expected_settlement_date']} is not past due as of {asof_d}.{note}",
                                is_exception=0, expected=net))
        else:
            results.append(_row("payment", txn, p["row_id"], [], p["merchant_id"], pay=p, recon_status=UNSETTLED,
                                reason=f"No settlement found {age} days after expected date {p['expected_settlement_date']}; "
                                       f"expected net {_inr(net)} outstanding.{note}",
                                is_exception=1, expected=net, variance=-int(net), exposure=int(net), age=age))

    # settlement-only: valid settlement rows (any status) whose transaction is unknown
    for txn, rows in list(active.items()) + list(inactive.items()):
        if txn not in pay_ids:
            for r in rows:
                amt = int(r["settled_amount_paise"])
                results.append(_row("settlement", txn, None, [r], r["merchant_id"], recon_status=SETTLEMENT_ONLY,
                                    reason=f"Settlement {r['settlement_id']} of {_inr(amt)} references {txn}, which is absent from payments "
                                           "(referential integrity failure).",
                                    is_exception=1, settled=amt, variance=amt, exposure=amt,
                                    settlement_date=r["settlement_timestamp"][:10]))

    df = pd.DataFrame(results, columns=RESULT_COLUMNS)
    for c in ("gross_amount_paise", "expected_net_paise", "settled_paise", "variance_paise", "exposure_paise",
              "delay_days", "age_days", "payment_row_id"):
        df[c] = df[c].astype("Int64")
    return df


def _row(rtype: str, txn: str, prow, srows: list[dict], merchant: str, *, pay: dict | None = None,
         recon_status: str, reason: str, is_exception: int, expected=None, settled=None, variance=None,
         exposure=None, delay=None, age=None, settlement_date=None) -> dict:
    """Assemble one result record."""
    if srows and settlement_date is None:
        settlement_date = min(r["settlement_timestamp"][:10] for r in srows if r["settlement_timestamp"])
    ts = pay["transaction_timestamp"] if pay else ""
    return {
        "record_type": rtype, "transaction_id": txn or None, "payment_row_id": prow,
        "settlement_row_ids": ",".join(str(r["row_id"]) for r in srows) or None,
        "settlement_ids": ",".join(r["settlement_id"] for r in srows) or None,
        "settlement_references": ",".join(sorted({r["settlement_reference"] for r in srows})) or None,
        "merchant_id": merchant or None,
        "payment_method": pay["payment_method"] if pay else None,
        "payment_status": pay["payment_status"] if pay else None,
        "transaction_date": ts[:10] if ts and len(ts) >= 10 and ts[4:5] == "-" else None,
        "gross_amount_paise": _none(pay["gross_amount_paise"]) if pay else None,
        "expected_settlement_date": pay["expected_settlement_date"] if pay else None,
        "settlement_date": settlement_date, "recon_status": recon_status, "reason": reason,
        "is_exception": is_exception, "expected_net_paise": expected, "settled_paise": settled,
        "variance_paise": variance, "exposure_paise": exposure, "delay_days": delay, "age_days": age,
        "ageing_bucket": ageing_bucket(age),
    }
