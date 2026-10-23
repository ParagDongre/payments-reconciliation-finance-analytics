"""Small, controlled fixtures with known expected results (offline, synthetic)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import database as db
from src.data_generator import PAYMENT_COLUMNS, SETTLEMENT_COLUMNS, _fee_tax_net
from src.money import paise_to_str, to_paise
from src.reconciliation import reconcile
from src.validation import validate_payments, validate_settlements, rejected_from

ASOF = "2026-07-15"


def pay(txn="TXN00000001", merchant="MID001", ts="2026-03-01 10:00:00", gross="1000.00", method="card",
        status="success", exp="2026-03-03", **override) -> dict:
    """One payment row; fee/tax/net derived with the production rules unless overridden."""
    fee, tax, net = _fee_tax_net(to_paise(gross) if float(gross) > 0 else 0, method, status)
    row = {"transaction_id": txn, "merchant_id": merchant, "transaction_timestamp": ts, "gross_amount": gross,
           "currency": "INR", "payment_method": method, "payment_status": status, "expected_settlement_date": exp,
           "processing_fee": paise_to_str(fee), "tax_on_fee": paise_to_str(tax),
           "expected_net_settlement": paise_to_str(net), "source_reference": f"ORD-{txn}"}
    row.update(override)
    return row


def stl(sid="STL00000001", txn="TXN00000001", merchant="MID001", ts="2026-03-03 12:00:00",
        amount="976.40", ref=None, status="settled", **override) -> dict:
    row = {"settlement_id": sid, "transaction_id": txn, "merchant_id": merchant, "settlement_timestamp": ts,
           "settled_amount": amount, "settlement_reference": ref or f"UTR-{sid}", "settlement_status": status}
    row.update(override)
    return row


def raw_frames(pays: list[dict], stls: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    return (pd.DataFrame(pays, columns=PAYMENT_COLUMNS, dtype=str),
            pd.DataFrame(stls, columns=SETTLEMENT_COLUMNS, dtype=str))


def run_recon(pays: list[dict], stls: list[dict], **kw) -> pd.DataFrame:
    rp, rs = raw_frames(pays, stls)
    return reconcile(validate_payments(rp), validate_settlements(rs), asof=kw.pop("asof", ASOF), **kw)


def status_of(results: pd.DataFrame, txn: str) -> str:
    return results[results["transaction_id"] == txn].iloc[0]["recon_status"]


def write_csvs(pays: list[dict], stls: list[dict], folder: Path) -> tuple[Path, Path]:
    rp, rs = raw_frames(pays, stls)
    p, s = folder / "payments.csv", folder / "settlements.csv"
    rp.to_csv(p, index=False)
    rs.to_csv(s, index=False)
    return p, s


def load_db(pays: list[dict], stls: list[dict], db_path: Path):
    """Validate, reconcile and load fixtures into a temp SQLite DB; return an open connection."""
    rp, rs = raw_frames(pays, stls)
    vp, vs = validate_payments(rp), validate_settlements(rs)
    results = reconcile(vp, vs, asof=ASOF)
    conn = db.connect(db_path)
    db.replace_load(conn, vp, vs, rejected_from(vp, vs, rp, rs), results, "testrun")
    return conn


def kpi_fixture() -> tuple[list[dict], list[dict]]:
    """Eight payments with hand-computed KPIs (see tests/test_kpis.py)."""
    pays = [
        pay("TXN00000001"),                                           # matched
        pay("TXN00000002", gross="500.00", method="upi", exp="2026-03-02"),   # matched (UPI, no fee)
        pay("TXN00000003"),                                           # unsettled
        pay("TXN00000004"),                                           # amount mismatch (settled 900.00)
        pay("TXN00000005", status="failed"),                          # failed, no settlement
        pay("TXN00000006"),                                           # delayed 7 days
        pay("TXN00000001"),                                           # duplicate source row of #1
        pay("TXN00000008", gross="-50.00", expected_net_settlement="976.40", processing_fee="20.00", tax_on_fee="3.60"),  # invalid
    ]
    stls = [
        stl("STL00000001", "TXN00000001"),
        stl("STL00000002", "TXN00000002", amount="500.00", ts="2026-03-02 09:00:00"),
        stl("STL00000004", "TXN00000004", amount="900.00"),
        stl("STL00000006", "TXN00000006", ts="2026-03-10 12:00:00"),
    ]
    return pays, stls
