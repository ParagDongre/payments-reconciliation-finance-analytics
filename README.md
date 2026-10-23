# Payments Reconciliation & Finance Reporting Automation Lab

A portfolio project that simulates how a payments company reconciles payment transactions against settlement records, investigates discrepancies, validates data and automates finance reporting.

> **Simulation only.** All data is synthetic (fixed seed). This is not a production payment-processing system, is not connected to any payment gateway, bank or real merchant, and is not deployed anywhere.

## Business problem
Finance teams must prove that every successful payment was paid out to the merchant exactly once, for the right amount, on time. Gaps (missing, duplicate, short, late or orphan settlements) are cash and compliance risk. This lab automates the match, classifies each gap with a reason and a financial exposure, and produces the reports an analyst would hand to finance leadership.

## Scope and limitations
- Single currency (INR), one settlement per payment expected, join on `transaction_id`.
- Ageing uses a fixed as-of date (2026-07-15) for reproducibility.
- No refunds, chargebacks, partial/multi-part settlements, or multi-currency FX.
- Rule-based explanations are the core. The optional LLM hook is an interface only (no provider bundled).

## Architecture and data flow
```
scripts/generate_data.py -> data/payments.csv, settlements.csv, anomaly_manifest.csv
        |
src/ingestion.py + validation.py   (typed rows in paise, errors recorded, nothing dropped)
        |
src/reconciliation.py              (status precedence, reasons, variance, exposure, ageing)
        |
src/database.py -> SQLite          (atomic replace-load: idempotent)
        |
sql/*.sql via run_query            (cross-checks, KPIs, trends, DQ checks, reports)
        |
src/kpis.py, reporting.py, exception_analysis.py
        |
CSV reports (reports/)  and  Streamlit dashboard (app.py)
```
Ingestion is **idempotent**: each run replaces payments, settlements, rejected records and results in one transaction, so repeated runs never duplicate rows and a failed run leaves the previous load intact. `pipeline_runs` is an append-only audit log.

## Technology stack
Python 3.11+, Pandas, NumPy, SQLite, SQL, Streamlit, Plotly, Pytest, `logging`.

## Setup (Windows PowerShell)
```powershell
cd payments-reconciliation-finance-analytics
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
pip install -r requirements.txt
```

## Run
```powershell
python scripts/generate_data.py      # synthetic CSVs + anomaly manifest
python scripts/run_pipeline.py       # validate, load, reconcile, KPIs, 7 CSV reports, run summary
streamlit run app.py                 # dashboard
python -m pytest                     # tests (offline)
```
The dashboard can also generate data and run the pipeline from its empty state.

## Dashboard pages
Executive Overview, Reconciliation Explorer, Exception Management (CSV export), Finance Reports (downloads), Data Quality, Trends and Analysis. All read the same `reconciliation_results` table and SQL KPI definitions.

## KPI definitions (sample)
- **Gross value**: sum of gross_amount, unique valid payments.
- **Expected net** = gross - processing fee - tax on fee (successful only).
- **Actual settled**: sum of settled amounts received for those payments.
- **Match rate** = Matched / (Matched + Delayed + Mismatch + Duplicate + Unsettled).
- **Outstanding exposure**: money at risk across unresolved exceptions.

Full formulas: `docs/business_metrics.md`.

## Reconciliation rules
Precedence: Data Quality Error > Failed or Reversed > Duplicate Settlement > Amount Mismatch > Delayed Settlement > Unsettled > Matched; unmatched settlements are Settlement Only. Money is compared in integer paise with ROUND_HALF_UP fees. See `docs/reconciliation_rules.md`.

## Sample exception scenarios
| Scenario | Example reason produced |
|----------|-------------------------|
| Unsettled | "No settlement found 120 days after expected date 2026-03-17; expected net INR 18956.91 outstanding." |
| Amount mismatch | "Settled INR 1235.00 vs expected net INR 1256.23: under-settled by INR 21.23." |
| Duplicate | "2 settlement rows (...) share one settlement_reference; total INR 37298.44 vs expected INR 18649.22, excess INR 18649.22." |

## Example SQL
```sql
-- Duplicate settlements (GROUP BY / HAVING)
SELECT transaction_id, COUNT(*) AS rows_, SUM(settled_amount_paise) - MAX(settled_amount_paise) AS excess_paise
FROM settlements WHERE settlement_status='settled' AND validation_errors=''
GROUP BY transaction_id HAVING COUNT(*) > 1;
```
More in `sql/` and `docs/sql_explanations.md`.

## AI assistance and privacy
Rule-based explanations (what was detected, impact, supporting records, next steps) need no API key. An optional hook (`exception_analysis.ai_rephrase`) can rephrase an explanation only if `RECON_AI_ENABLED=1`, `RECON_AI_API_KEY` is set in the environment and you pass a provider function. Keys are never stored or logged. The model never decides a status. Data here is synthetic; do not enable external AI with real data.

## Screenshots
Not included. To capture your own: run `streamlit run app.py`, open each page, and save screenshots to `docs/screenshots/` (e.g. `executive_overview.png`, `exception_management.png`), then link them here.

## Known limitations and future work
Partial/split settlements and refunds, FX, configurable per-merchant SLAs, incremental (non-replace) loads, scheduled runs, auth on the dashboard, a bundled LLM provider adapter.
