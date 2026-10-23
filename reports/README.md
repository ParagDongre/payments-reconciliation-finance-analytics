# reports/

The pipeline writes seven CSV reports and `report_metadata.json` here (git-ignored):
daily_transaction_summary, merchant_summary, settlement_reconciliation_summary, outstanding_exception_report, amount_mismatch_report, settlement_ageing_report, data_quality_report.

Metadata holds the generation timestamp, reporting period, per-report row counts and definitions. Amounts are INR with 2 decimals.

```powershell
python scripts/run_pipeline.py
```
