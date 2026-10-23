"""SQLite access: schema, named-query loader and idempotent loading.

Loading behaviour (idempotent replace-load): each pipeline run replaces the contents of
`payments`, `settlements`, `rejected_records` and `reconciliation_results` inside ONE transaction,
so re-running with the same input yields identical tables with no duplicated rows. A failure
rolls back and leaves the previous load intact. `pipeline_runs` is an append-only audit log.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd

import config

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)
_SQL_FILES = ["reconciliation_queries.sql", "kpi_queries.sql", "data_quality_queries.sql"]
_registry: dict[str, str] | None = None


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection (creating parent folders) with row access by name."""
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist."""
    conn.executescript((config.SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    conn.commit()


def load_queries(sql_dir: Path | None = None) -> dict[str, str]:
    """Parse `-- name: query_name` blocks from the sql/ files into a registry."""
    global _registry
    if _registry is not None and sql_dir is None:
        return _registry
    reg: dict[str, str] = {}
    for fname in _SQL_FILES:
        text = ((sql_dir or config.SQL_DIR) / fname).read_text(encoding="utf-8")
        parts = _NAME_RE.split(text)
        for name, body in zip(parts[1::2], parts[2::2]):
            if name in reg:
                raise ValueError(f"Duplicate query name {name!r} in {fname}")
            reg[name] = body.strip().rstrip(";")
    if sql_dir is None:
        _registry = reg
    return reg


def run_query(conn: sqlite3.Connection, name: str, params: dict | None = None) -> pd.DataFrame:
    """Execute a named query from sql/ and return a DataFrame."""
    reg = load_queries()
    if name not in reg:
        raise KeyError(f"Unknown SQL query: {name}")
    return pd.read_sql_query(reg[name], conn, params=params or {})


def _clean(v):
    """Convert pandas NA/NaN to None and numpy ints to Python ints for sqlite."""
    if v is None or v is pd.NA:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _insert(conn: sqlite3.Connection, table: str, df: pd.DataFrame, columns: list[str]) -> None:
    if df.empty:
        return
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
    conn.executemany(sql, [[_clean(v) for v in row] for row in df[columns].itertuples(index=False, name=None)])


PAYMENT_DB_COLS = ["row_id", "transaction_id", "merchant_id", "transaction_timestamp", "gross_amount_paise", "currency",
                   "payment_method", "payment_status", "expected_settlement_date", "processing_fee_paise",
                   "tax_on_fee_paise", "expected_net_paise", "source_reference", "is_duplicate_source", "validation_errors"]
SETTLEMENT_DB_COLS = ["row_id", "settlement_id", "transaction_id", "merchant_id", "settlement_timestamp",
                      "settled_amount_paise", "settlement_reference", "settlement_status", "validation_errors"]


def replace_load(conn: sqlite3.Connection, payments: pd.DataFrame, settlements: pd.DataFrame,
                 rejected: pd.DataFrame, results: pd.DataFrame, run_id: str) -> None:
    """Atomically replace all loaded data with this run's data (idempotent)."""
    from src.reconciliation import RESULT_COLUMNS
    init_schema(conn)
    try:
        with conn:  # single transaction: commit on success, rollback on error
            for table in ("reconciliation_results", "rejected_records", "settlements", "payments"):
                conn.execute(f"DELETE FROM {table}")
            _insert(conn, "payments", payments, PAYMENT_DB_COLS)
            _insert(conn, "settlements", settlements, SETTLEMENT_DB_COLS)
            _insert(conn, "rejected_records", rejected, ["source", "row_id", "reason", "raw_record"])
            res = results.copy()
            res["run_id"] = run_id
            _insert(conn, "reconciliation_results", res, RESULT_COLUMNS + ["run_id"])
    except sqlite3.Error:
        log.exception("Database load failed; transaction rolled back")
        raise


def record_run(conn: sqlite3.Connection, run_id: str, started: str, finished: str, status: str, summary_json: str) -> None:
    """Append an entry to the pipeline run audit log."""
    init_schema(conn)
    with conn:
        conn.execute("INSERT OR REPLACE INTO pipeline_runs VALUES (?,?,?,?,?)",
                     (run_id, started, finished, status, summary_json))


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row counts for the main tables (used in run summaries and the UI)."""
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("payments", "settlements", "rejected_records", "reconciliation_results")}


def has_results(db_path: Path | str | None = None) -> bool:
    """True if the database exists and holds reconciliation results."""
    path = Path(db_path) if db_path else config.DB_PATH
    if not path.exists():
        return False
    conn = connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM reconciliation_results").fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()
