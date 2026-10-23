"""Reproducible synthetic payments and settlements generator.

SIMULATION ONLY. Every record is fabricated with a fixed random seed.

Each intentional anomaly is written to an anomaly manifest so it can be traced:
    scenario, transaction_id, settlement_id, detail
Running the generator twice with the same seed yields byte-identical CSVs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

import config
from src.money import to_paise, paise_to_str, round_money

PAYMENT_COLUMNS = [
    "transaction_id", "merchant_id", "transaction_timestamp", "gross_amount", "currency",
    "payment_method", "payment_status", "expected_settlement_date", "processing_fee",
    "tax_on_fee", "expected_net_settlement", "source_reference",
]
SETTLEMENT_COLUMNS = [
    "settlement_id", "transaction_id", "merchant_id", "settlement_timestamp",
    "settled_amount", "settlement_reference", "settlement_status",
]

# Scenario counts among the 5,000 unique payments (documented in docs/data_dictionary.md)
N_FAILED = 220
N_REVERSED = 80
N_FAILED_WITH_SETTLEMENT = 12     # failed/reversed payments that nonetheless received a settlement
N_UNSETTLED = 220                 # successful, past due, no settlement
N_DELAYED = 200
N_MISMATCH = 120
N_DUPLICATE_SETTLEMENT = 60
N_DQ_PAYMENT = 35                 # payments with invalid/missing fields (settled normally)
N_DUPLICATE_SOURCE = 40           # exact repeat rows of an earlier transaction
N_ORPHAN_SETTLEMENTS = 60         # settlement-only records
N_DQ_SETTLEMENT_MISSING_TXN = 8
N_DQ_SETTLEMENT_BAD_AMOUNT = 6

SETTLEMENT_LAG_DAYS = {"upi": 1, "card": 2, "netbanking": 2, "wallet": 2}


@dataclass
class GeneratedData:
    payments: pd.DataFrame
    settlements: pd.DataFrame
    manifest: pd.DataFrame


def _fee_tax_net(gross_paise: int, method: str, status: str) -> tuple[int, int, int]:
    """Return (fee, tax, net) in paise. Non-successful payments carry no fee and no net."""
    if status != "success":
        return 0, 0, 0
    gross = Decimal(gross_paise) / 100
    fee = round_money(gross * Decimal(config.FEE_RATES[method]))
    tax = round_money(fee * Decimal(config.GST_RATE))
    fee_p, tax_p = to_paise(fee), to_paise(tax)
    return fee_p, tax_p, gross_paise - fee_p - tax_p


def generate(seed: int = config.RANDOM_SEED, n_payments: int = config.N_PAYMENTS) -> GeneratedData:
    """Generate payments, settlements and the anomaly manifest."""
    rng = np.random.default_rng(seed)
    manifest: list[dict] = []

    def note(scenario: str, txn: str = "", stl: str = "", detail: str = "") -> None:
        manifest.append({"scenario": scenario, "transaction_id": txn, "settlement_id": stl, "detail": detail})

    # ---------------------------------------------------------------- payments
    merchants = [f"MID{i:03d}" for i in range(1, config.N_MERCHANTS + 1)]
    weights = 1.0 / np.arange(1, len(merchants) + 1) ** 0.8
    weights /= weights.sum()
    start, end = date.fromisoformat(config.START_DATE), date.fromisoformat(config.END_DATE)
    span = (end - start).days + 1
    methods = np.array(["upi", "card", "netbanking", "wallet"])
    method_p = [0.50, 0.30, 0.12, 0.08]

    day_offsets = np.sort(rng.integers(0, span, n_payments))
    seconds = rng.integers(0, 86400, n_payments)
    gross_paise = np.clip(np.round(rng.lognormal(np.log(1500), 1.1, n_payments), 2), 10, 250000)
    gross_paise = np.round(gross_paise * 100).astype(np.int64)
    merchant_idx = rng.choice(len(merchants), n_payments, p=weights)
    method_idx = rng.choice(4, n_payments, p=method_p)

    status = np.array(["success"] * n_payments, dtype=object)
    order = rng.permutation(n_payments)
    failed_idx = order[:N_FAILED]
    reversed_idx = order[N_FAILED:N_FAILED + N_REVERSED]
    status[failed_idx] = "failed"
    status[reversed_idx] = "reversed"

    rows = []
    for i in range(n_payments):
        ts = datetime.combine(start + timedelta(days=int(day_offsets[i])), datetime.min.time()) + timedelta(seconds=int(seconds[i]))
        method = str(methods[method_idx[i]])
        fee, tax, net = _fee_tax_net(int(gross_paise[i]), method, status[i])
        exp_date = ts.date() + timedelta(days=SETTLEMENT_LAG_DAYS[method])
        merchant = merchants[merchant_idx[i]]
        rows.append({
            "transaction_id": f"TXN{10000001 + i:08d}",
            "merchant_id": merchant,
            "transaction_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "gross_amount": paise_to_str(int(gross_paise[i])),
            "currency": config.CURRENCY,
            "payment_method": method,
            "payment_status": status[i],
            "expected_settlement_date": exp_date.isoformat(),
            "processing_fee": paise_to_str(fee),
            "tax_on_fee": paise_to_str(tax),
            "expected_net_settlement": paise_to_str(net),
            "source_reference": f"ORD-{merchant}-{i + 1:07d}",
        })
    payments = pd.DataFrame(rows, columns=PAYMENT_COLUMNS)

    success_idx = [i for i in rng.permutation(n_payments) if status[i] == "success"]
    cursor = 0

    def take(n: int) -> list[int]:
        nonlocal cursor
        chosen = success_idx[cursor:cursor + n]
        cursor += n
        return chosen

    dq_idx = take(N_DQ_PAYMENT)
    unsettled_idx = set(take(N_UNSETTLED))
    delayed_idx = set(take(N_DELAYED))
    mismatch_idx = set(take(N_MISMATCH))
    dup_stl_idx = set(take(N_DUPLICATE_SETTLEMENT))

    asof = date.fromisoformat(config.GENERATION_ASOF_DATE)
    stl_rows: list[dict] = []
    stl_counter = 1
    ref_counter = 5000000000

    def new_settlement(txn: str, merchant: str, ts: datetime, amount_paise: int, ref: str | None = None) -> dict:
        nonlocal stl_counter, ref_counter
        stl_id = f"STL{stl_counter:08d}"
        stl_counter += 1
        if ref is None:
            ref = f"UTR{ref_counter}"
            ref_counter += 1
        return {
            "settlement_id": stl_id, "transaction_id": txn, "merchant_id": merchant,
            "settlement_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "settled_amount": paise_to_str(amount_paise), "settlement_reference": ref,
            "settlement_status": "settled",
        }

    failed_with_stl = set(rng.choice(np.concatenate([failed_idx, reversed_idx]), N_FAILED_WITH_SETTLEMENT, replace=False).tolist())

    for i in range(n_payments):
        p = rows[i]
        net = to_paise(p["expected_net_settlement"])
        exp_date = date.fromisoformat(p["expected_settlement_date"])
        base_ts = datetime.combine(exp_date, datetime.min.time()) + timedelta(seconds=int(rng.integers(9 * 3600, 18 * 3600)))
        if status[i] != "success":
            if i in failed_with_stl:
                gross = to_paise(p["gross_amount"])
                row = new_settlement(p["transaction_id"], p["merchant_id"], base_ts, gross)
                stl_rows.append(row)
                note("failed_or_reversed_with_settlement", p["transaction_id"], row["settlement_id"],
                     f"{status[i]} payment received a settlement of {row['settled_amount']} (expected net 0.00)")
            else:
                note("failed_or_reversed", p["transaction_id"], "", f"{status[i]} payment, no settlement expected")
            continue
        if i in unsettled_idx:
            note("unsettled", p["transaction_id"], "", "successful payment, settlement deliberately omitted")
            continue
        amount, ts, scenario, detail = net, base_ts, "matched", "settled on time for exact expected net"
        if i in delayed_idx:
            max_delay = (asof - exp_date).days
            delay = int(rng.integers(3, 26))
            delay = max(3, min(delay, max_delay)) if max_delay >= 3 else 0
            ts = base_ts + timedelta(days=delay)
            if delay:
                scenario, detail = "delayed_settlement", f"settled {delay} days after expected date"
        if i in mismatch_idx:
            kind = rng.choice(["short", "over", "rounding"], p=[0.7, 0.2, 0.1])
            if kind == "short":
                delta = -max(1, int(net * rng.uniform(0.01, 0.05)))
            elif kind == "over":
                delta = max(1, int(net * rng.uniform(0.002, 0.02)))
            else:
                delta = int(rng.choice([-1, 1]))
            amount = max(1, net + delta)
            scenario, detail = "amount_mismatch", f"{kind}: settled differs from expected by {delta} paise"
        row = new_settlement(p["transaction_id"], p["merchant_id"], ts, amount)
        stl_rows.append(row)
        if i in dup_stl_idx:
            dup = new_settlement(p["transaction_id"], p["merchant_id"], ts + timedelta(hours=3), amount, ref=row["settlement_reference"])
            stl_rows.append(dup)
            note("duplicate_settlement", p["transaction_id"], dup["settlement_id"],
                 f"second settlement row reuses reference {row['settlement_reference']}")
        elif scenario != "matched" or i in dq_idx:
            pass
        if scenario == "matched" and i not in dq_idx and i not in dup_stl_idx:
            note("matched", p["transaction_id"], row["settlement_id"], detail)
        elif scenario != "matched":
            note(scenario, p["transaction_id"], row["settlement_id"], detail)

    # orphan (settlement-only) records
    for k in range(N_ORPHAN_SETTLEMENTS):
        merchant = merchants[int(rng.integers(0, len(merchants)))]
        ts = datetime.combine(start, datetime.min.time()) + timedelta(days=int(rng.integers(2, span)), seconds=int(rng.integers(0, 86400)))
        txn = f"TXN{90000001 + k:08d}"
        row = new_settlement(txn, merchant, ts, int(rng.integers(10000, 500000)))
        stl_rows.append(row)
        note("settlement_only", txn, row["settlement_id"], "settlement references a transaction absent from payments")

    # invalid settlement rows (standalone, so no payment is affected)
    for k in range(N_DQ_SETTLEMENT_MISSING_TXN):
        ts = datetime.combine(start, datetime.min.time()) + timedelta(days=int(rng.integers(2, span)))
        row = new_settlement("", merchants[k % len(merchants)], ts, int(rng.integers(10000, 300000)))
        stl_rows.append(row)
        note("dq_settlement_missing_transaction_id", "", row["settlement_id"], "settlement has blank transaction_id")
    for k in range(N_DQ_SETTLEMENT_BAD_AMOUNT):
        ts = datetime.combine(start, datetime.min.time()) + timedelta(days=int(rng.integers(2, span)))
        row = new_settlement(f"TXN{91000001 + k:08d}", merchants[k % len(merchants)], ts, 10000)
        row["settled_amount"] = ["abc", "-250.00", "", "0.00", "12,50", "NaN"][k % 6]
        stl_rows.append(row)
        note("dq_settlement_invalid_amount", row["transaction_id"], row["settlement_id"], f"settled_amount={row['settled_amount']!r}")

    # payment data-quality corruption (after settlements were built from the clean values)
    corruptions = ["missing_merchant", "negative_amount", "invalid_status", "invalid_currency",
                   "missing_timestamp", "net_inconsistent", "invalid_method"]
    for k, i in enumerate(dq_idx):
        kind = corruptions[k % len(corruptions)]
        txn = payments.at[i, "transaction_id"]
        if kind == "missing_merchant":
            payments.at[i, "merchant_id"] = ""
        elif kind == "negative_amount":
            payments.at[i, "gross_amount"] = "-" + payments.at[i, "gross_amount"]
        elif kind == "invalid_status":
            payments.at[i, "payment_status"] = "unknown"
        elif kind == "invalid_currency":
            payments.at[i, "currency"] = "XXX"
        elif kind == "missing_timestamp":
            payments.at[i, "transaction_timestamp"] = ""
        elif kind == "net_inconsistent":
            payments.at[i, "expected_net_settlement"] = paise_to_str(to_paise(payments.at[i, "expected_net_settlement"]) + 777)
        elif kind == "invalid_method":
            payments.at[i, "payment_method"] = "cheque"
        note(f"dq_payment_{kind}", txn, "", f"{kind} injected into an otherwise settled payment")

    # exact duplicate source rows appended at the end of the file
    dup_src = rng.choice([i for i in range(n_payments) if i not in set(dq_idx)], N_DUPLICATE_SOURCE, replace=False)
    dup_df = payments.iloc[sorted(dup_src.tolist())].copy()
    for txn in dup_df["transaction_id"]:
        note("duplicate_source_record", txn, "", "exact repeat of an earlier payments row")
    payments = pd.concat([payments, dup_df], ignore_index=True)

    settlements = pd.DataFrame(stl_rows, columns=SETTLEMENT_COLUMNS)
    settlements = settlements.iloc[rng.permutation(len(settlements))].reset_index(drop=True)
    return GeneratedData(payments, settlements, pd.DataFrame(manifest))


def write_csvs(data: GeneratedData, data_dir: Path | None = None) -> dict[str, Path]:
    """Write payments, settlements and manifest CSVs; return their paths."""
    out = Path(data_dir) if data_dir else config.DATA_DIR
    out.mkdir(parents=True, exist_ok=True)
    paths = {"payments": out / "payments.csv", "settlements": out / "settlements.csv", "manifest": out / "anomaly_manifest.csv"}
    data.payments.to_csv(paths["payments"], index=False)
    data.settlements.to_csv(paths["settlements"], index=False)
    data.manifest.to_csv(paths["manifest"], index=False)
    return paths
