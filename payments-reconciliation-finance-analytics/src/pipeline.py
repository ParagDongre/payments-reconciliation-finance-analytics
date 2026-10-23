"""End-to-end reporting pipeline: load -> validate -> store -> SQL checks -> reconcile -> KPIs -> reports.

Runnable from the command line (scripts/run_pipeline.py) and from the Streamlit app.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import config
from src import database as db
from src.ingestion import load_and_validate
from src.kpis import compute_kpis
from src.reconciliation import reconcile
from src.reporting import generate_reports
from src.validation import SchemaError

log = logging.getLogger("pipeline")


def setup_logging(logs_dir: Path | None = None) -> None:
    """Log to console and logs/pipeline.log (idempotent)."""
    root = logging.getLogger()
    if any(getattr(h, "_recon", False) for h in root.handlers):
        return
    d = Path(logs_dir) if logs_dir else config.LOGS_DIR
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in (logging.StreamHandler(), logging.FileHandler(d / "pipeline.log", encoding="utf-8")):
        h.setFormatter(fmt)
        h._recon = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(logging.INFO)


def _sql_cross_checks(conn, asof: str) -> dict[str, int]:
    """Run independent SQL reconciliation queries; these are compared with the Python results."""
    p = {"asof": asof, "grace": config.SETTLEMENT_GRACE_DAYS}
    return {
        "sql_unsettled": len(db.run_query(conn, "sql_unsettled_payments", p)),
        "sql_settlement_only": len(db.run_query(conn, "sql_settlement_only")),
        "sql_duplicate_settlements": len(db.run_query(conn, "sql_duplicate_settlements")),
        "sql_amount_variance": len(db.run_query(conn, "sql_amount_variance")),
        "sql_late_settlements": len(db.run_query(conn, "sql_late_settlements", p)),
        "sql_failed_with_settlement": len(db.run_query(conn, "sql_failed_with_settlement")),
    }


def run_pipeline(payments_csv: Path | str | None = None, settlements_csv: Path | str | None = None,
                 db_path: Path | str | None = None, reports_dir: Path | str | None = None,
                 asof: str | None = None) -> dict:
    """Run the full pipeline and return a run summary dict (also stored in pipeline_runs)."""
    setup_logging()
    run_id = uuid.uuid4().hex[:12]
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    asof = asof or config.GENERATION_ASOF_DATE
    summary: dict = {"run_id": run_id, "status": "FAILED", "asof_date": asof}
    conn = None
    try:
        log.info("Run %s started", run_id)
        data = load_and_validate(payments_csv, settlements_csv)
        results = reconcile(data.payments, data.settlements, asof=asof)
        conn = db.connect(db_path)
        db.replace_load(conn, data.payments, data.settlements, data.rejected, results, run_id)
        counts = db.table_counts(conn)

        checks = _sql_cross_checks(conn, asof)
        by = results["recon_status"].value_counts().to_dict()
        agree = {
            "unsettled": (checks["sql_unsettled"], by.get("Unsettled", 0)),
            "settlement_only": (checks["sql_settlement_only"], by.get("Settlement Only", 0)),
            "duplicate_settlement": (checks["sql_duplicate_settlements"], by.get("Duplicate Settlement", 0)),
        }
        for name, (sql_n, py_n) in agree.items():
            if sql_n != py_n:
                log.warning("SQL vs Python mismatch for %s: sql=%d python=%d", name, sql_n, py_n)
        kpis = compute_kpis(conn)
        meta = generate_reports(conn, reports_dir)
        summary.update({
            "status": "COMPLETED",
            "input_payment_rows": data.input_payment_rows,
            "input_settlement_rows": data.input_settlement_rows,
            "processed_payment_rows": counts["payments"],
            "processed_settlement_rows": counts["settlements"],
            "rejected_or_quarantined_rows": counts["rejected_records"],
            "reconciliation_records": counts["reconciliation_results"],
            "status_counts": {k: int(v) for k, v in by.items()},
            "outstanding_exceptions": kpis["outstanding_count"],
            "sql_vs_python_checks": {k: {"sql": a, "python": b, "agree": a == b} for k, (a, b) in agree.items()},
            "sql_check_counts": checks,
            "reports": {k: v["row_count"] for k, v in meta["reports"].items()},
            "output_dir": str(Path(reports_dir) if reports_dir else config.REPORTS_DIR),
        })
        log.info("Run %s completed", run_id)
    except SchemaError as exc:
        summary["error"] = f"Input problem: {exc}"
        log.error("Run %s failed: %s", run_id, exc)
    except Exception as exc:  # keep the failure in the audit log, then re-raise nothing to the caller
        summary["error"] = f"{type(exc).__name__}: {exc}"
        log.exception("Run %s failed", run_id)
    finally:
        if conn is not None:
            try:
                db.record_run(conn, run_id, started, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                              summary["status"], json.dumps(summary, default=str))
            finally:
                conn.close()
    return summary
