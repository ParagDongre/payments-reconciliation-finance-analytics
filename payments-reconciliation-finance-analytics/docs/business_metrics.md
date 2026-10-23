# Business Metrics and KPI Formulas

All KPIs come from `sql/kpi_queries.sql` over `reconciliation_results` and are shared by the dashboard, reports and tests. Money terms are kept separate:

| Term | Definition |
|------|-----------|
| Gross transaction value | Sum of gross_amount over unique valid payments (all statuses) |
| Successful value | Gross of payments with status success |
| Fees / Tax on fees | Processing fee and GST on that fee, successful payments |
| Expected net settlement | Successful gross - fees - tax on fees |
| Actual settled value | Sum of settled amounts on those payments (duplicate excess included, since cash was paid) |

| KPI | Formula |
|-----|---------|
| Total transactions | Unique valid payments (duplicate source rows and invalid rows excluded) |
| Matched count / value | Status = Matched; settled value of those |
| Match rate | Matched / (Matched + Delayed + Amount Mismatch + Duplicate + Unsettled) x 100. Failed/reversed and not-yet-due are excluded from the denominator |
| Outstanding exceptions | All exception rows except Delayed Settlement (cash arrived) |
| Outstanding exposure | Sum of exposure (see reconciliation_rules.md) |
| Total amount mismatch | Absolute sum of (settled - expected) on Amount Mismatch rows; the signed net is shown too |
| Settlement delay rate | Settlements later than expected + grace / payments with exactly one settlement x 100 |

Double-counting guards: duplicate source rows are excluded from value KPIs; failed/reversed payments carry expected net 0; duplicate settlements are one exception per transaction.

Bridge check (tested): successful gross - fees - tax on fees = expected net.
