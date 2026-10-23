# Data Dictionary

All data is synthetic. Generated with seed 42 (`config.RANDOM_SEED`). Amounts in CSVs are INR with 2 decimals.

## payments.csv
| Field | Meaning |
|-------|---------|
| transaction_id | `TXN` + 8 digits, unique per payment (repeats are duplicate source records) |
| merchant_id | `MID` + 3 digits |
| transaction_timestamp | `YYYY-MM-DD HH:MM:SS` |
| gross_amount | Amount charged to the customer, before fees |
| currency | Always `INR` |
| payment_method | upi, card, netbanking, wallet |
| payment_status | success, failed, reversed |
| expected_settlement_date | Transaction date + 1 day (UPI) or 2 days (others) |
| processing_fee | gross x method rate (0 for non-successful) |
| tax_on_fee | 18% GST on processing_fee |
| expected_net_settlement | gross - processing_fee - tax_on_fee (0 for failed/reversed) |
| source_reference | Merchant order reference |

## settlements.csv
| Field | Meaning |
|-------|---------|
| settlement_id | `STL` + 8 digits |
| transaction_id | Payment the settlement pays out |
| merchant_id | Merchant paid |
| settlement_timestamp | When funds were settled |
| settled_amount | Amount actually settled |
| settlement_reference | Bank reference (`UTR...`); a repeated reference marks a duplicate posting |
| settlement_status | settled, pending, reversed |

## SQLite tables
`payments`, `settlements` (money as integer paise; `validation_errors` text; `is_duplicate_source`), `rejected_records`, `reconciliation_results` (one auditable row per record), `pipeline_runs` (audit log). See `sql/schema.sql`.

## Synthetic anomaly scenarios (also written to `data/anomaly_manifest.csv`)
Counts below are for seed 42 and are asserted by an end-to-end test.

| Scenario | Count | Intent |
|----------|------:|--------|
| matched | 4,065 | Exact net amount, on time |
| unsettled | 220 | Successful payment, settlement omitted |
| delayed_settlement | 200 | Correct amount, 3+ days late |
| amount_mismatch | 120 | Short (70%), over (20%) or 1 paisa rounding (10%) |
| duplicate_settlement | 60 | Second settlement row reusing the same reference |
| settlement_only | 60 | Settlement for a TXN id absent from payments |
| failed_or_reversed | 288 | No settlement expected |
| failed_or_reversed_with_settlement | 12 | Failed/reversed payment that was paid out anyway (deliberate contradiction, flagged as an exception) |
| duplicate_source_record | 40 | Exact repeat payment row |
| dq_payment_* | 35 | Missing merchant, negative amount, invalid status/currency/method, missing timestamp, net inconsistent |
| dq_settlement_missing_transaction_id | 8 | Blank transaction_id |
| dq_settlement_invalid_amount | 6 | Non-numeric, negative, blank or zero amount |

Notes:
- The 35 payments with injected data-quality errors were originally settled normally, so their settlement rows exist. The payment is reported as a Data Quality Error and excluded from KPIs until corrected at source; the settlement row is kept in the database and linked to it in the result's supporting records.
- `Failed or Reversed` in the pipeline summary (300) = 288 with no settlement + 12 paid out anyway.
- Quarantined rows (89) = 35 invalid payments + 40 duplicate source rows + 14 invalid settlements.
