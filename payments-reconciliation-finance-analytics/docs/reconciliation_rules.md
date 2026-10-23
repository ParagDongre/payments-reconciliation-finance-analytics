# Reconciliation Rules

**Simulation on synthetic data. Not a production payment system.**

## Money assumptions
- Currency is **INR**; every amount has 2 decimal places.
- Amounts are parsed with `Decimal` and stored/compared as **integer paise**. Floats are used only for chart display.
- Rounding is **ROUND_HALF_UP** to the paisa, applied once to each computed fee and each tax.
- Fee = gross x method rate (UPI 0%, card 2.00%, netbanking 1.90%, wallet 1.50%). Tax on fee = fee x 18% GST.
- Expected net = gross - fee - tax on fee, for successful payments; 0 for failed/reversed.
- Amount tolerance defaults to **0.00** (exact match). It is configurable (`config.AMOUNT_TOLERANCE`).

## Join keys
Payments and settlements are joined on `transaction_id`. `settlement_reference` is used to describe duplicates.
Only settlement rows that are valid and have `settlement_status = 'settled'` are "active" and used for matching.

## Status precedence (first rule that applies wins)
| # | Status | Condition |
|---|--------|-----------|
| 1 | Data Quality Error | Payment is a duplicate source row, or has any validation failure. Invalid settlement rows are also reported here. |
| 2 | Failed or Reversed | `payment_status` is failed/reversed. It is an *exception* only if a settlement exists (possible wrong payout). |
| 3 | Duplicate Settlement | More than one active settlement for the transaction. |
| 4 | Amount Mismatch | One active settlement whose amount differs from expected net by more than the tolerance. |
| 5 | Delayed Settlement | Amount correct, but settled later than expected date + grace days (default 1). |
| 6 | Unsettled | No active settlement and the expected date is more than the grace period before the as-of date. |
| - | Not Yet Due | No settlement, not yet past due. Informational, not an exception. |
| 7 | Matched | One on-time settlement for exactly the expected net amount. |
| - | Settlement Only | Valid settlement row whose transaction_id is not in payments. |

Examples: a duplicate settlement that is also late is reported as Duplicate Settlement; a settlement that is both short and late is an Amount Mismatch (its delay is still recorded in `delay_days`).

## Exposure (financial impact)
| Status | Exposure |
|--------|----------|
| Unsettled | expected net owed |
| Amount Mismatch | absolute variance |
| Duplicate Settlement | settled total - expected net |
| Settlement Only | settlement amount |
| Failed or Reversed (with settlement) | settled total |
| Data Quality Error | expected net if known (duplicate source rows: 0) |
| Delayed Settlement | 0 (timing only) |

## Ageing
- Unsettled: age = as-of date - expected settlement date.
- Delayed: age = delay days.
- Buckets: 0-3, 4-7, 8-14, 15-30, 30+ days.
- The as-of date is fixed (`2026-07-15`) so results are reproducible; pass `--asof` to change it.

## Auditability
Every result row stores the reason text, supporting row ids, settlement ids/references, expected, settled, variance and exposure. Source values are never modified; invalid rows go to `rejected_records` with the original raw record.
