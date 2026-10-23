"""Streamlit dashboard - Payments Reconciliation & Finance Reporting Lab.

SIMULATION ONLY: synthetic data, not a production payment-processing system.
Every page reads the same `reconciliation_results` table and the same SQL/KPI definitions.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import config
from src import database as db
from src.data_generator import generate, write_csvs
from src.exception_analysis import INVESTIGATION_STEPS, ai_privacy_notice, explain_exception
from src.kpis import KPI_DEFINITIONS, compute_kpis, date_bounds
from src.money import paise_to_rupees
from src.pipeline import run_pipeline
from src.reconciliation import ALL_STATUSES
from src.reporting import REPORTS, build_report, generate_reports

st.set_page_config(page_title="Payments Reconciliation Lab", page_icon="💳", layout="wide")

STATUS_LEGEND = {
    "Matched": "One on-time settlement equal to the expected net amount.",
    "Unsettled": "Successful payment, past its expected date, no settlement found.",
    "Not Yet Due": "No settlement yet, but the expected date has not passed (informational).",
    "Settlement Only": "Settlement references a transaction that is not in payments.",
    "Amount Mismatch": "One settlement whose amount differs from the expected net amount.",
    "Duplicate Settlement": "More than one settlement row for a single transaction.",
    "Delayed Settlement": "Correct amount, settled later than expected date + grace days.",
    "Failed or Reversed": "Payment failed/reversed. Exception only if money was settled anyway.",
    "Data Quality Error": "Invalid fields or a duplicate source row; quarantined and excluded from KPIs.",
}


# ----------------------------------------------------------------------------- helpers
def inr(paise) -> str:
    """Format paise as a rupee string for display."""
    return "-" if paise is None or paise != paise else f"₹{paise / 100:,.2f}"


def _mtime() -> float:
    return config.DB_PATH.stat().st_mtime if config.DB_PATH.exists() else 0.0


@st.cache_data(show_spinner=False)
def cached_query(name: str, params_json: str, mtime: float) -> pd.DataFrame:
    conn = db.connect()
    try:
        return db.run_query(conn, name, json.loads(params_json))
    finally:
        conn.close()


def q(name: str, start: str, end: str, **extra) -> pd.DataFrame:
    params = {"start": start, "end": end, "include_undated": 1, "grace": config.SETTLEMENT_GRACE_DAYS,
              "asof": config.GENERATION_ASOF_DATE, "high_value": int(float(config.HIGH_VALUE_THRESHOLD) * 100), **extra}
    return cached_query(name, json.dumps(params, sort_keys=True), _mtime())


@st.cache_data(show_spinner=False)
def cached_kpis(start: str, end: str, mtime: float) -> dict:
    conn = db.connect()
    try:
        return compute_kpis(conn, start, end)
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def load_results(mtime: float) -> pd.DataFrame:
    conn = db.connect()
    try:
        return pd.read_sql_query("SELECT * FROM reconciliation_results", conn)
    finally:
        conn.close()


def with_rupees(df: pd.DataFrame) -> pd.DataFrame:
    """Display copy: convert *_paise columns to float rupees (display only)."""
    out = df.copy()
    for c in [c for c in out.columns if c.endswith("_paise")]:
        out[c[:-6] + "_inr"] = out[c].map(paise_to_rupees)
        out = out.drop(columns=c)
    return out


def run_full_pipeline(regenerate: bool) -> None:
    with st.spinner("Running pipeline..."):
        if regenerate or not config.PAYMENTS_CSV.exists():
            write_csvs(generate())
        summary = run_pipeline()
    st.cache_data.clear()
    if summary["status"] == "COMPLETED":
        st.success(f"Pipeline completed (run {summary['run_id']}): {summary['reconciliation_records']} reconciliation records, "
                   f"{summary['outstanding_exceptions']} outstanding exceptions.")
        st.json(summary, expanded=False)
    else:
        st.error(summary.get("error", "Pipeline failed. See logs/pipeline.log."))


# ----------------------------------------------------------------------------- sidebar
st.sidebar.title("💳 Reconciliation Lab")
st.sidebar.caption("Simulation on synthetic data. Not a production payment system.")

if not db.has_results():
    st.title("Payments Reconciliation & Finance Reporting Lab")
    st.info("No reconciliation results found yet. Generate the synthetic data and run the pipeline to begin.")
    if st.button("Generate synthetic data and run pipeline", type="primary"):
        run_full_pipeline(regenerate=True)
        st.rerun()
    st.code("python scripts/generate_data.py\npython scripts/run_pipeline.py", language="powershell")
    st.stop()

_conn = db.connect()
lo, hi = date_bounds(_conn)
_conn.close()
d_lo, d_hi = date.fromisoformat(lo), date.fromisoformat(hi)
rng = st.sidebar.date_input("Transaction date range", (d_lo, d_hi), min_value=d_lo, max_value=d_hi)
start, end = (rng[0], rng[1]) if isinstance(rng, tuple) and len(rng) == 2 else (d_lo, d_hi)
START, END = start.isoformat(), end.isoformat()

page = st.sidebar.radio("Page", ["Executive Overview", "Reconciliation Explorer", "Exception Management",
                                 "Finance Reports", "Data Quality", "Trends and Analysis"])
st.sidebar.divider()
if st.sidebar.button("Re-run pipeline on current CSVs"):
    run_full_pipeline(regenerate=False)
    st.rerun()
st.sidebar.caption(f"As-of date for ageing: {config.GENERATION_ASOF_DATE} · Currency: {config.CURRENCY}")

results = load_results(_mtime())
results = results[(results["transaction_date"].isna()) | ((results["transaction_date"] >= START) & (results["transaction_date"] <= END))]

# ----------------------------------------------------------------------------- A. Executive Overview
if page == "Executive Overview":
    st.title("Executive Overview")
    k = cached_kpis(START, END, _mtime())
    st.caption(f"Period {START} to {END} (transaction date). Duplicate source rows and invalid rows are excluded from value KPIs.")
    c = st.columns(4)
    c[0].metric("Total transactions", f"{k['total_transactions']:,}")
    c[1].metric("Gross transaction value", inr(k["gross_value_paise"]))
    c[2].metric("Successful payments", f"{k['successful_count']:,}", inr(k["successful_value_paise"]), delta_color="off")
    c[3].metric("Reconciliation match rate", "-" if k["match_rate_pct"] is None else f"{k['match_rate_pct']}%")
    c = st.columns(4)
    c[0].metric("Fees (successful)", inr(k["fees_paise"]))
    c[1].metric("Tax on fees", inr(k["tax_on_fees_paise"]))
    c[2].metric("Expected net settlement", inr(k["expected_net_paise"]))
    c[3].metric("Actual settled value", inr(k["settled_paise"]))
    c = st.columns(4)
    c[0].metric("Matched transactions", f"{k['matched_count']:,}", inr(k["matched_value_paise"]), delta_color="off")
    c[1].metric("Outstanding exceptions", f"{k['outstanding_count']:,}", inr(k["outstanding_exposure_paise"]), delta_color="off")
    c[2].metric("Total amount mismatch (absolute)", inr(k["mismatch_abs_paise"]), f"net {inr(k['mismatch_net_paise'])}", delta_color="off")
    c[3].metric("Settlement delay rate", "-" if k["delay_rate_pct"] is None else f"{k['delay_rate_pct']}%")

    left, right = st.columns(2)
    with left:
        wf = go.Figure(go.Waterfall(
            x=["Successful gross", "Processing fees", "Tax on fees", "Expected net", "Actual settled"],
            measure=["absolute", "relative", "relative", "absolute", "absolute"],
            y=[k["successful_value_paise"] / 100, -k["fees_paise"] / 100, -k["tax_on_fees_paise"] / 100,
               k["expected_net_paise"] / 100, k["settled_paise"] / 100]))
        wf.update_layout(title="From gross to settled (INR)", showlegend=False, height=380)
        st.plotly_chart(wf, width="stretch")
    with right:
        s = q("settlement_reconciliation_summary", START, END)
        fig = px.bar(s, x="recon_status", y="records", title="Records by reconciliation status", text="records")
        fig.update_layout(height=380, xaxis_title="Status", yaxis_title="Records")
        st.plotly_chart(fig, width="stretch")
    with st.expander("KPI definitions"):
        for name, text in KPI_DEFINITIONS.items():
            st.markdown(f"**{name}**: {text}")

# ----------------------------------------------------------------------------- B. Explorer
elif page == "Reconciliation Explorer":
    st.title("Reconciliation Explorer")
    with st.expander("Status legend", expanded=False):
        st.table(pd.DataFrame({"Status": list(STATUS_LEGEND), "Meaning": list(STATUS_LEGEND.values())}))
    f1, f2, f3 = st.columns(3)
    search = f1.text_input("Transaction ID contains")
    merchants = f2.multiselect("Merchant", sorted(results["merchant_id"].dropna().unique()))
    statuses = f3.multiselect("Status", ALL_STATUSES)
    f4, f5 = st.columns(2)
    methods = f4.multiselect("Payment method", sorted(results["payment_method"].dropna().unique()))
    amt_lo, amt_hi = f5.slider("Gross amount (INR)", 0.0, 250000.0, (0.0, 250000.0), step=100.0)
    df = results
    if search:
        df = df[df["transaction_id"].fillna("").str.contains(search.strip(), case=False)]
    if merchants:
        df = df[df["merchant_id"].isin(merchants)]
    if statuses:
        df = df[df["recon_status"].isin(statuses)]
    if methods:
        df = df[df["payment_method"].isin(methods)]
    if (amt_lo, amt_hi) != (0.0, 250000.0):
        g = df["gross_amount_paise"]
        df = df[(g >= amt_lo * 100) & (g <= amt_hi * 100)]
    st.caption(f"{len(df):,} of {len(results):,} records")
    if df.empty:
        st.warning("No records match the current filters.")
    else:
        show = with_rupees(df[["transaction_id", "merchant_id", "transaction_date", "payment_method", "payment_status",
                               "gross_amount_paise", "expected_net_paise", "settled_paise", "variance_paise",
                               "settlement_date", "recon_status", "reason"]])
        st.dataframe(show, width="stretch", height=420, hide_index=True)
        ids = df["transaction_id"].dropna().unique()[:1000]
        pick = st.selectbox("Inspect a transaction (first 1,000 matches)", ids)
        if pick:
            conn = db.connect()
            pay = pd.read_sql_query("SELECT * FROM payments WHERE transaction_id = ?", conn, params=(pick,))
            stl = pd.read_sql_query("SELECT * FROM settlements WHERE transaction_id = ?", conn, params=(pick,))
            conn.close()
            res = results[results["transaction_id"] == pick].iloc[0].to_dict()
            st.subheader(f"{pick}: {res['recon_status']}")
            a, b = st.columns(2)
            a.markdown("**Payment record(s)**")
            a.dataframe(with_rupees(pay), hide_index=True, width="stretch")
            b.markdown("**Settlement record(s)**")
            if stl.empty:
                b.info("No settlement rows for this transaction.")
            else:
                b.dataframe(with_rupees(stl), hide_index=True, width="stretch")
            st.markdown(f"**Why:** {res['reason']}")
            if res["is_exception"]:
                ex = explain_exception(res)
                st.markdown(f"**Financial impact:** {ex['financial_impact']}")
                st.markdown(f"**Supporting records:** {ex['supporting_records']}")
                st.markdown("**Next steps:**\n" + "\n".join(f"- {s}" for s in ex["next_steps"].split(" | ")))

# ----------------------------------------------------------------------------- C. Exceptions
elif page == "Exception Management":
    st.title("Exception Management")
    ex = results[results["is_exception"] == 1]
    types = st.multiselect("Exception types", sorted(ex["recon_status"].unique()),
                           default=[s for s in sorted(ex["recon_status"].unique()) if s != "Delayed Settlement"])
    hv = st.number_input("High-value threshold (INR)", 0.0, 10_000_000.0, float(config.HIGH_VALUE_THRESHOLD), step=5000.0)
    ex = ex[ex["recon_status"].isin(types)]
    if ex.empty:
        st.success("No exceptions for the selected types and period.")
    else:
        c = st.columns(4)
        c[0].metric("Exceptions", f"{len(ex):,}")
        c[1].metric("Financial exposure", inr(int(ex["exposure_paise"].fillna(0).sum())))
        hv_df = ex[ex["exposure_paise"].fillna(0) >= hv * 100]
        c[2].metric("High-value exceptions", f"{len(hv_df):,}", inr(int(hv_df["exposure_paise"].sum())), delta_color="off")
        lo_df = ex[ex["age_days"].fillna(0) >= config.LONG_OUTSTANDING_DAYS]
        c[3].metric(f"Outstanding {config.LONG_OUTSTANDING_DAYS}+ days", f"{len(lo_df):,}")
        g1, g2 = st.columns(2)
        by_type = ex.groupby("recon_status").agg(count=("reason", "size"), exposure_paise=("exposure_paise", "sum")).reset_index()
        g1.plotly_chart(px.bar(with_rupees(by_type), x="recon_status", y="exposure_inr", text="count",
                               title="Exposure by exception type (INR; labels = count)"), width="stretch")
        by_m = (ex.assign(merchant_id=ex["merchant_id"].fillna("UNKNOWN")).groupby("merchant_id")
                .agg(count=("reason", "size"), exposure_paise=("exposure_paise", "sum")).reset_index()
                .sort_values("exposure_paise", ascending=False).head(15))
        g2.plotly_chart(px.bar(with_rupees(by_m), x="merchant_id", y="exposure_inr", text="count",
                               title="Top 15 merchants by exposure (INR)"), width="stretch")
        age = ex[ex["ageing_bucket"].notna()].groupby(["ageing_bucket", "recon_status"]).size().reset_index(name="count")
        if not age.empty:
            order = [b[2] for b in config.AGEING_BUCKETS]
            st.plotly_chart(px.bar(age, x="ageing_bucket", y="count", color="recon_status", title="Ageing of outstanding items",
                                   category_orders={"ageing_bucket": order}), width="stretch")
        st.subheader("Exception records")
        cols = ["recon_status", "transaction_id", "merchant_id", "transaction_date", "expected_net_paise", "settled_paise",
                "variance_paise", "exposure_paise", "age_days", "ageing_bucket", "reason"]
        table = with_rupees(ex[cols].sort_values("exposure_paise", ascending=False))
        st.dataframe(table, width="stretch", height=380, hide_index=True)
        st.download_button("Download filtered exceptions (CSV)", table.to_csv(index=False).encode("utf-8"),
                           file_name="filtered_exceptions.csv", mime="text/csv")
    st.subheader("Investigation steps by category")
    for status, steps in INVESTIGATION_STEPS.items():
        with st.expander(status):
            st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))
    st.caption(ai_privacy_notice())

# ----------------------------------------------------------------------------- D. Reports
elif page == "Finance Reports":
    st.title("Finance Reports")
    st.caption(f"Reporting period {START} to {END}. Amounts in INR (2 decimals). Definitions are shown with each report.")
    conn = db.connect()
    for name, (_, definition) in REPORTS.items():
        df = build_report(conn, name, START, END)
        with st.expander(f"{name.replace('_', ' ').title()} ({len(df):,} rows)"):
            st.caption(definition)
            if df.empty:
                st.info("This report has no rows for the selected period.")
            else:
                st.dataframe(df.head(200), width="stretch", hide_index=True)
            st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), file_name=f"{name}.csv",
                               mime="text/csv", key=f"dl_{name}")
    conn.close()
    if st.button("Write all reports + metadata to the reports/ folder"):
        c2 = db.connect()
        meta = generate_reports(c2, config.REPORTS_DIR, START, END)
        c2.close()
        st.success(f"Wrote {len(meta['reports'])} reports to {config.REPORTS_DIR}")
        st.json(meta, expanded=False)

# ----------------------------------------------------------------------------- E. Data Quality
elif page == "Data Quality":
    st.title("Data Quality")
    conn = db.connect()
    counts = db.table_counts(conn)
    conn.close()
    c = st.columns(4)
    c[0].metric("Payment rows loaded", f"{counts['payments']:,}")
    c[1].metric("Settlement rows loaded", f"{counts['settlements']:,}")
    c[2].metric("Rejected / quarantined", f"{counts['rejected_records']:,}")
    c[3].metric("Reconciliation records", f"{counts['reconciliation_results']:,}")
    st.caption("Nothing is dropped: invalid and duplicate rows stay in the database, are listed below with reasons, and are excluded from KPIs.")
    sections = [("Missing values", "dq_missing_values"), ("Duplicate transactions (source records)", "dq_duplicate_transactions"),
                ("Duplicate settlement references", "dq_duplicate_settlement_references"), ("Invalid amounts", "dq_invalid_amounts"),
                ("Invalid identifiers", "dq_invalid_identifiers"), ("Referential integrity failures", "dq_referential_integrity"),
                ("Merchant consistency (payment vs settlement)", "dq_merchant_consistency")]
    for title, qname in sections:
        df = q(qname, START, END)
        with st.expander(f"{title} ({len(df)} rows)", expanded=False):
            if df.empty:
                st.success("No issues found.")
            else:
                st.dataframe(with_rupees(df), width="stretch", hide_index=True)
    st.subheader("Rejected / quarantined records")
    rej = q("dq_rejected_records", START, END)
    if rej.empty:
        st.success("No rejected records.")
    else:
        src = st.multiselect("Source", sorted(rej["source"].unique()), default=sorted(rej["source"].unique()))
        st.dataframe(rej[rej["source"].isin(src)], width="stretch", height=360, hide_index=True)
        st.download_button("Download quarantine list (CSV)", rej.to_csv(index=False).encode("utf-8"),
                           file_name="rejected_records.csv", mime="text/csv")

# ----------------------------------------------------------------------------- F. Trends
else:
    st.title("Trends and Analysis")
    st.caption(f"Period {START} to {END}.")
    daily = q("daily_transactions", START, END)
    if daily.empty:
        st.warning("No transactions in the selected period.")
    else:
        d = with_rupees(daily)
        st.plotly_chart(px.line(d, x="transaction_date", y=["gross_inr", "expected_net_inr", "settled_inr"],
                                title="Daily gross vs expected net vs settled value (INR, by transaction date)",
                                labels={"value": "INR", "transaction_date": "Transaction date", "variable": "Measure"}),
                        width="stretch")
        st.plotly_chart(px.bar(d, x="transaction_date", y="exceptions", title="Daily exception count (by transaction date)"),
                        width="stretch")
        m = with_rupees(q("monthly_transactions", START, END))
        a, b = st.columns(2)
        a.plotly_chart(px.bar(m, x="month", y=["gross_inr", "settled_inr"], barmode="group",
                              title="Monthly gross vs settled (INR)"), width="stretch")
        b.plotly_chart(px.line(m, x="month", y="match_rate_pct", markers=True, title="Monthly match rate (%)"),
                       width="stretch")
        et = with_rupees(q("exception_trend_monthly", START, END))
        st.plotly_chart(px.bar(et, x="month", y="exceptions", color="recon_status", title="Monthly exceptions by type"),
                        width="stretch")
        st_ = with_rupees(q("settlement_trend_daily", START, END))
        st.plotly_chart(px.area(st_, x="settlement_date", y="settled_inr", title="Settled value by settlement date (INR)"),
                        width="stretch")
        mer = with_rupees(q("merchant_summary", START, END)).head(15)
        st.plotly_chart(px.bar(mer, x="merchant_id", y="gross_inr", color="match_rate_pct",
                               title="Top 15 merchants by gross value (colour = match rate %)"), width="stretch")
        dl = q("delay_distribution", START, END)
        st.plotly_chart(px.bar(dl, x="delay_days", y="records", title="Settlement delay distribution (days after expected date)",
                               labels={"delay_days": "Delay (days)", "records": "Settlements"}), width="stretch")
