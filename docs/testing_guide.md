# Testing Guide

Run everything offline from the project root:

```powershell
python -m pytest
```

| File | Covers |
|------|--------|
| tests/test_reconciliation.py | Matching, missing/orphan/duplicate detection, variance, failed/reversed, delays, precedence, rounding, explanations, AI off by default |
| tests/test_validation.py | Invalid/missing values, quarantine, empty files, bad schemas |
| tests/test_kpis.py | Hand-computed KPIs on a fixed 8-row fixture, date filters, empty data, SQL vs Python agreement |
| tests/test_reporting.py | CSV files, metadata, INR formatting, period filter, empty DB |
| tests/test_pipeline.py | Idempotent repeat runs, failure handling, reproducible generator, end-to-end run checked against the anomaly manifest |

Fixtures are in `tests/helpers.py` (small, known results). No test calls an external API or uses a real `data/` file; each uses `tmp_path`.
