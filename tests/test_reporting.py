"""CSV report generation: files, metadata, row counts, money formatting."""
import json

import pandas as pd

from src.reporting import REPORTS, generate_reports, build_report
from tests.helpers import kpi_fixture, load_db


def test_all_reports_written_with_metadata(tmp_path):
    conn = load_db(*kpi_fixture(), tmp_path / "r.db")
    out = tmp_path / "out"
    meta = generate_reports(conn, out)
    for name in REPORTS:
        assert (out / f"{name}.csv").exists()
        assert meta["reports"][name]["row_count"] == len(pd.read_csv(out / f"{name}.csv"))
        assert meta["reports"][name]["definition"]
    on_disk = json.loads((out / "report_metadata.json").read_text())
    assert on_disk["reporting_period"] == {"start": "2026-03-01", "end": "2026-03-01"}
    assert on_disk["generated_at"] and on_disk["currency"] == "INR"
    conn.close()


def test_report_contents_and_inr_formatting(tmp_path):
    conn = load_db(*kpi_fixture(), tmp_path / "r.db")
    mm = build_report(conn, "amount_mismatch_report", "2026-01-01", "2026-12-31")
    assert len(mm) == 1 and "variance_paise" not in mm.columns
    assert mm.iloc[0]["variance_inr"] == "-76.40" and mm.iloc[0]["direction"] == "under-settled"
    out = build_report(conn, "outstanding_exception_report", "2026-01-01", "2026-12-31")
    assert "Delayed Settlement" not in set(out["recon_status"]) and len(out) == 4
    ageing = build_report(conn, "settlement_ageing_report", "2026-01-01", "2026-12-31")
    assert set(ageing["recon_status"]) == {"Unsettled", "Delayed Settlement"}
    dq = build_report(conn, "data_quality_report", "2026-01-01", "2026-12-31")
    assert len(dq) == 2
    conn.close()


def test_period_filter_limits_rows(tmp_path):
    conn = load_db(*kpi_fixture(), tmp_path / "r.db")
    assert len(build_report(conn, "amount_mismatch_report", "2026-04-01", "2026-04-30")) == 0
    conn.close()


def test_reports_on_empty_database_do_not_fail(tmp_path):
    conn = load_db([], [], tmp_path / "e.db")
    meta = generate_reports(conn, tmp_path / "o")
    assert all(v["row_count"] == 0 for v in meta["reports"].values())
    conn.close()


def test_unknown_report_name_raises(tmp_path):
    import pytest
    conn = load_db([], [], tmp_path / "e.db")
    with pytest.raises(KeyError):
        build_report(conn, "nope", "2026-01-01", "2026-01-02")
    conn.close()
