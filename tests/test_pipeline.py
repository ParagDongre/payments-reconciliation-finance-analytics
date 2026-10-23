"""Pipeline: idempotent repeat runs, failure handling, and an end-to-end run on generated data."""
import pandas as pd

from src import database as db
from src.data_generator import generate, write_csvs
from src.pipeline import run_pipeline
from tests.helpers import kpi_fixture, write_csvs as write_fixture_csvs


def test_repeat_runs_do_not_duplicate_records(tmp_path):
    p, s = write_fixture_csvs(*kpi_fixture(), tmp_path)
    dbp, rep = tmp_path / "t.db", tmp_path / "rep"
    first = run_pipeline(p, s, dbp, rep, "2026-07-15")
    second = run_pipeline(p, s, dbp, rep, "2026-07-15")
    assert first["status"] == second["status"] == "COMPLETED"
    conn = db.connect(dbp)
    counts = db.table_counts(conn)
    assert counts == {"payments": 8, "settlements": 4, "rejected_records": 2, "reconciliation_results": 8}
    assert conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 2   # audit log appends
    conn.close()


def test_reload_with_changed_input_replaces_data(tmp_path):
    p, s = write_fixture_csvs(*kpi_fixture(), tmp_path)
    dbp = tmp_path / "t.db"
    run_pipeline(p, s, dbp, tmp_path / "rep", "2026-07-15")
    pays, stls = kpi_fixture()
    p2, s2 = write_fixture_csvs(pays[:2], stls[:2], tmp_path)
    run_pipeline(p2, s2, dbp, tmp_path / "rep", "2026-07-15")
    assert db.table_counts(db.connect(dbp))["payments"] == 2


def test_missing_input_fails_gracefully_and_keeps_previous_load(tmp_path):
    p, s = write_fixture_csvs(*kpi_fixture(), tmp_path)
    dbp = tmp_path / "t.db"
    run_pipeline(p, s, dbp, tmp_path / "rep", "2026-07-15")
    bad = run_pipeline(tmp_path / "missing.csv", s, dbp, tmp_path / "rep", "2026-07-15")
    assert bad["status"] == "FAILED" and "not found" in bad["error"]
    assert db.table_counts(db.connect(dbp))["payments"] == 8


def test_wrong_schema_reports_error(tmp_path):
    p, s = write_fixture_csvs(*kpi_fixture(), tmp_path)
    pd.DataFrame({"a": ["1"]}).to_csv(p, index=False)
    out = run_pipeline(p, s, tmp_path / "t.db", tmp_path / "rep", "2026-07-15")
    assert out["status"] == "FAILED" and "missing required columns" in out["error"]


def test_generator_is_reproducible_and_meets_size_requirements():
    a, b = generate(), generate()
    assert a.payments.equals(b.payments) and a.settlements.equals(b.settlements)
    assert len(a.payments) >= 5000 and len(a.settlements) >= 4500
    assert a.payments["merchant_id"].nunique() >= 10
    assert generate(seed=7).payments.equals(a.payments) is False


def test_end_to_end_generated_data_matches_anomaly_manifest(tmp_path):
    data = generate()
    paths = write_csvs(data, tmp_path)
    dbp, rep = tmp_path / "e2e.db", tmp_path / "reports"
    summary = run_pipeline(paths["payments"], paths["settlements"], dbp, rep, "2026-07-15")
    assert summary["status"] == "COMPLETED"
    manifest = data.manifest["scenario"].value_counts().to_dict()
    sc = summary["status_counts"]
    assert sc["Unsettled"] == manifest["unsettled"]
    assert sc["Delayed Settlement"] == manifest["delayed_settlement"]
    assert sc["Amount Mismatch"] == manifest["amount_mismatch"]
    assert sc["Duplicate Settlement"] == manifest["duplicate_settlement"]
    assert sc["Settlement Only"] == manifest["settlement_only"]
    assert sc["Matched"] == manifest["matched"]
    assert summary["rejected_or_quarantined_rows"] == 89
    assert all(c["agree"] for c in summary["sql_vs_python_checks"].values())
    for name, rows in summary["reports"].items():
        assert (rep / f"{name}.csv").exists()
        assert rows == len(pd.read_csv(rep / f"{name}.csv"))
    assert (rep / "report_metadata.json").exists()
