# SQL Explanations

Queries live in `sql/` and are loaded by name (`-- name:` markers) through `src/database.py::run_query`. The pipeline runs the `sql_*` queries as independent cross-checks against the Python engine, and the KPIs, trends and reports are all SQL.

## reconciliation_queries.sql
| Query | Technique | Business use |
|-------|-----------|--------------|
| sql_unsettled_payments | LEFT JOIN anti-join, date arithmetic | Cash owed to merchants but not paid |
| sql_settlement_only | LEFT JOIN anti-join | Payouts with no payment (leakage signal) |
| sql_duplicate_settlements | INNER JOIN, GROUP BY, HAVING | Overpayment from double posting |
| sql_amount_variance | INNER JOIN, subquery with HAVING | Fee/tax errors and partial payouts |
| sql_late_settlements | CASE buckets, julianday | SLA breaches |
| sql_failed_with_settlement | INNER JOIN | Payouts on failed/reversed payments |
| exception_summary_by_status / _by_merchant | Conditional aggregation | Where exposure sits |
| exception_ageing | GROUP BY bucket | Prioritising old items |
| report_* | Filtered, sorted extracts | The CSV reports |

## kpi_queries.sql
`kpi_summary` (one-pass conditional aggregation of every executive KPI), `kpi_fees`, `daily_transactions`, `monthly_transactions` (strftime), `merchant_summary`, `settlement_trend_daily`, `exception_trend_monthly`, `delay_distribution`, `settlement_reconciliation_summary`.

## data_quality_queries.sql
Missing values (UNION ALL of conditional counts), duplicate transactions and settlement references (GROUP BY/HAVING), invalid amounts and identifiers, referential integrity (LEFT JOIN), merchant consistency (INNER JOIN), and the quarantine list.

Money columns are integer paise; divide by 100 only for display.
