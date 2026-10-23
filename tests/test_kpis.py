"""KPI correctness against hand-computed values, and SQL vs Python agreement.

Fixture (tests/helpers.kpi_fixture), card = 2% fee + 18% GST on fee:
  #1 gross 1000.00 card   net 976.40  settled 976.40         -> Matched
  #2 gross  500.00 upi    net 500.00  settled 500.00         -> Matched
  #3 gross 1000.00 card   net 976.40  no settlement          -> Unsettled
  #4 gross 1000.00 card   net 976.40  settled 900.00         -> Amount Mismatch (-76.40)
  #5 gross 1000.00 failed net 0       none                   -> Failed or Reversed
  #6 gross 1000.00 card   net 976.40  settled 976.40, 7d late -> Delayed
  dup of #1 and an invalid row (#8) -> Data Quality Error, excluded from value KPIs
"""
import pytest

from src.kpis import compute_kpis
from tests.helpers import kpi_fixture, load_db, pay, stl


@pytest.fixture()
def kpis(tmp_path):
    conn = load_db(*kpi_fixture(), tmp_path / "t.db")
    yield compute_kpis(conn), conn
    conn.close()


def test_counts_exclude_duplicates_and_invalid_rows(kpis):
    k, _ = kpis
    assert k["total_transactions"] == 6                 # 8 rows - 1 duplicate - 1 invalid
    assert k["excluded_payment_rows"] == 2
    assert k["successful_count"] == 5


def test_value_kpis(kpis):
    k, _ = kpis
    assert k["gross_value_paise"] == 100000 * 4 + 50000 + 100000   # #1,#3,#4,#5,#6 = 5 x 1000 + 500
    assert k["gross_value_paise"] == 550000
    assert k["successful_value_paise"] == 450000
    assert k["fees_paise"] == 4 * 2000 and k["tax_on_fees_paise"] == 4 * 360
    assert k["expected_net_paise"] == 4 * 97640 + 50000
    assert k["successful_value_paise"] - k["fees_paise"] - k["tax_on_fees_paise"] == k["expected_net_paise"]
    assert k["settled_paise"] == 97640 + 50000 + 90000 + 97640


def test_match_rate_and_exceptions(kpis):
    k, _ = kpis
    assert k["matched_count"] == 2 and k["matched_value_paise"] == 97640 + 50000
    assert k["reconcilable_count"] == 5                      # matched 2 + unsettled + mismatch + delayed
    assert k["match_rate_pct"] == 40.0
    # outstanding = unsettled + mismatch + 2 data-quality rows (duplicate source, invalid); delayed excluded
    assert k["outstanding_count"] == 4
    assert k["delayed_count"] == 1


def test_mismatch_and_delay_kpis(kpis):
    k, _ = kpis
    assert k["mismatch_abs_paise"] == 7640 and k["mismatch_net_paise"] == -7640
    assert k["single_settled_count"] == 4 and k["late_count"] == 1
    assert k["delay_rate_pct"] == 25.0


def test_outstanding_exposure(kpis):
    k, _ = kpis
    # unsettled 976.40 + mismatch 76.40 + DQ(invalid row has net 976.40) + DQ duplicate 0
    assert k["outstanding_exposure_paise"] == 97640 + 7640 + 97640


def test_date_filter_changes_kpis(tmp_path):
    pays = [pay("TXN00000001", ts="2026-01-05 10:00:00", exp="2026-01-07"),
            pay("TXN00000002", ts="2026-02-05 10:00:00", exp="2026-02-07")]
    stls = [stl("S1", "TXN00000001", ts="2026-01-07 10:00:00"), stl("S2", "TXN00000002", ts="2026-02-07 10:00:00")]
    conn = load_db(pays, stls, tmp_path / "d.db")
    assert compute_kpis(conn, "2026-01-01", "2026-01-31")["total_transactions"] == 1
    assert compute_kpis(conn)["total_transactions"] == 2
    conn.close()


def test_kpis_on_empty_results(tmp_path):
    conn = load_db([], [], tmp_path / "e.db")
    k = compute_kpis(conn)
    assert k["total_transactions"] == 0 and k["match_rate_pct"] is None and k["delay_rate_pct"] is None
    conn.close()


def test_sql_queries_agree_with_python_status(tmp_path):
    from src import database as db
    conn = load_db(*kpi_fixture(), tmp_path / "s.db")
    p = {"asof": "2026-07-15", "grace": 1}
    assert db.run_query(conn, "sql_unsettled_payments", p)["transaction_id"].tolist() == ["TXN00000003"]
    v = db.run_query(conn, "sql_amount_variance")
    assert v["transaction_id"].tolist() == ["TXN00000004"] and v.iloc[0]["variance_paise"] == -7640
    assert db.run_query(conn, "sql_late_settlements", p)["delay_days"].tolist() == [7]
    conn.close()


def test_sql_settlement_only_and_duplicates(tmp_path):
    from src import database as db
    pays = [pay()]
    stls = [stl("S1", ref="R"), stl("S2", ref="R"), stl("S3", "TXN00000099", amount="10.00")]
    conn = load_db(pays, stls, tmp_path / "x.db")
    assert db.run_query(conn, "sql_settlement_only")["transaction_id"].tolist() == ["TXN00000099"]
    dup = db.run_query(conn, "sql_duplicate_settlements")
    assert len(dup) == 1 and dup.iloc[0]["excess_paise"] == 97640
    conn.close()
