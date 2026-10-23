-- Reconciliation queries. The app loads each block by its `-- name:` marker (src/database.py).
-- Amounts are INTEGER paise; divide by 100.0 only for display.
-- Parameters use :start / :end (inclusive ISO dates) where noted.

-- name: sql_unsettled_payments
-- LEFT JOIN + missing-record detection: successful, valid payments with no 'settled' settlement row.
-- Why it matters: this is cash the merchant is owed but has not received.
SELECT p.transaction_id, p.merchant_id, p.expected_settlement_date, p.expected_net_paise
FROM payments p
LEFT JOIN settlements s
       ON s.transaction_id = p.transaction_id
      AND s.settlement_status = 'settled'
      AND s.validation_errors = ''
WHERE p.payment_status = 'success'
  AND p.is_duplicate_source = 0
  AND p.validation_errors = ''
  AND s.row_id IS NULL
  AND julianday(:asof) - julianday(p.expected_settlement_date) > :grace
ORDER BY p.expected_net_paise DESC;

-- name: sql_settlement_only
-- Settlement rows whose transaction_id does not exist in payments (referential integrity failure).
-- Why it matters: money was paid out with no supporting payment - a potential leakage/fraud signal.
SELECT s.settlement_id, s.transaction_id, s.merchant_id, s.settled_amount_paise
FROM settlements s
LEFT JOIN payments p ON p.transaction_id = s.transaction_id
WHERE p.row_id IS NULL
  AND s.validation_errors = ''
ORDER BY s.settled_amount_paise DESC;

-- name: sql_duplicate_settlements
-- GROUP BY + HAVING: transactions with more than one active settlement row.
-- Why it matters: duplicates overpay merchants; excess = total settled minus the largest single row.
SELECT s.transaction_id,
       COUNT(*)                                     AS settlement_rows,
       COUNT(DISTINCT s.settlement_reference)       AS distinct_references,
       SUM(s.settled_amount_paise)                  AS total_settled_paise,
       SUM(s.settled_amount_paise) - MAX(s.settled_amount_paise) AS excess_paise
FROM settlements s
INNER JOIN payments p ON p.transaction_id = s.transaction_id AND p.is_duplicate_source = 0
WHERE s.settlement_status = 'settled' AND s.validation_errors = ''
GROUP BY s.transaction_id
HAVING COUNT(*) > 1
ORDER BY excess_paise DESC;

-- name: sql_amount_variance
-- INNER JOIN + settlement variance: single-settlement transactions whose amount differs from expected net.
-- Why it matters: variances usually come from fee/tax calculation errors or partial payouts.
SELECT p.transaction_id, p.merchant_id, p.expected_net_paise, s.settled_amount_paise,
       s.settled_amount_paise - p.expected_net_paise AS variance_paise,
       CASE WHEN s.settled_amount_paise < p.expected_net_paise THEN 'under-settled' ELSE 'over-settled' END AS direction
FROM payments p
INNER JOIN settlements s ON s.transaction_id = p.transaction_id
WHERE p.payment_status = 'success' AND p.is_duplicate_source = 0 AND p.validation_errors = ''
  AND s.settlement_status = 'settled' AND s.validation_errors = ''
  AND s.settled_amount_paise <> p.expected_net_paise
  AND p.transaction_id IN (SELECT transaction_id FROM settlements
                           WHERE settlement_status = 'settled' AND validation_errors = ''
                           GROUP BY transaction_id HAVING COUNT(*) = 1)
ORDER BY ABS(variance_paise) DESC;

-- name: sql_late_settlements
-- Date-based analysis with CASE: settlement lag versus expected date and ageing bucket.
SELECT p.transaction_id, p.merchant_id, p.expected_settlement_date, date(s.settlement_timestamp) AS settled_on,
       CAST(julianday(date(s.settlement_timestamp)) - julianday(p.expected_settlement_date) AS INTEGER) AS delay_days,
       CASE
         WHEN julianday(date(s.settlement_timestamp)) - julianday(p.expected_settlement_date) <= 3  THEN '0-3 days'
         WHEN julianday(date(s.settlement_timestamp)) - julianday(p.expected_settlement_date) <= 7  THEN '4-7 days'
         WHEN julianday(date(s.settlement_timestamp)) - julianday(p.expected_settlement_date) <= 14 THEN '8-14 days'
         ELSE '15+ days' END AS delay_bucket
FROM payments p
INNER JOIN settlements s ON s.transaction_id = p.transaction_id
WHERE p.payment_status = 'success' AND p.is_duplicate_source = 0 AND p.validation_errors = ''
  AND s.settlement_status = 'settled' AND s.validation_errors = ''
  AND julianday(date(s.settlement_timestamp)) - julianday(p.expected_settlement_date) > :grace
ORDER BY delay_days DESC;

-- name: sql_failed_with_settlement
-- Failed/reversed payments that nonetheless have a settlement (should be zero).
SELECT p.transaction_id, p.payment_status, s.settlement_id, s.settled_amount_paise
FROM payments p
INNER JOIN settlements s ON s.transaction_id = p.transaction_id
WHERE p.payment_status IN ('failed', 'reversed') AND p.is_duplicate_source = 0
  AND s.settlement_status = 'settled' AND s.validation_errors = ''
ORDER BY s.settled_amount_paise DESC;

-- name: exception_summary_by_status
-- Conditional aggregation over the reconciliation output: counts and exposure per status.
SELECT recon_status,
       COUNT(*)                                   AS records,
       SUM(is_exception)                          AS exceptions,
       COALESCE(SUM(CASE WHEN is_exception = 1 THEN exposure_paise END), 0) AS exposure_paise,
       COALESCE(SUM(CASE WHEN is_exception = 1 AND exposure_paise >= :high_value THEN 1 ELSE 0 END), 0) AS high_value_exceptions
FROM reconciliation_results
GROUP BY recon_status
ORDER BY exposure_paise DESC;

-- name: exception_summary_by_merchant
-- Which merchants carry the most unresolved exposure.
SELECT COALESCE(merchant_id, 'UNKNOWN') AS merchant_id,
       SUM(is_exception)                                         AS exceptions,
       SUM(CASE WHEN recon_status = 'Unsettled' THEN 1 ELSE 0 END)            AS unsettled,
       SUM(CASE WHEN recon_status = 'Amount Mismatch' THEN 1 ELSE 0 END)      AS mismatches,
       SUM(CASE WHEN recon_status = 'Duplicate Settlement' THEN 1 ELSE 0 END) AS duplicates,
       COALESCE(SUM(CASE WHEN is_exception = 1 THEN exposure_paise END), 0)   AS exposure_paise
FROM reconciliation_results
GROUP BY COALESCE(merchant_id, 'UNKNOWN')
HAVING SUM(is_exception) > 0
ORDER BY exposure_paise DESC;

-- name: report_outstanding_exceptions
-- Unresolved exceptions (Delayed Settlement is excluded: cash arrived, only timing was off).
SELECT recon_status, transaction_id, merchant_id, transaction_date, expected_settlement_date,
       expected_net_paise, settled_paise, variance_paise, exposure_paise, age_days, ageing_bucket, reason
FROM reconciliation_results
WHERE is_exception = 1 AND recon_status <> 'Delayed Settlement'
  AND (transaction_date BETWEEN :start AND :end OR transaction_date IS NULL)
ORDER BY exposure_paise DESC, transaction_id;

-- name: report_amount_mismatch
SELECT transaction_id, merchant_id, transaction_date, expected_net_paise, settled_paise, variance_paise,
       CASE WHEN variance_paise < 0 THEN 'under-settled' ELSE 'over-settled' END AS direction, reason
FROM reconciliation_results
WHERE recon_status = 'Amount Mismatch' AND transaction_date BETWEEN :start AND :end
ORDER BY ABS(variance_paise) DESC, transaction_id;

-- name: report_settlement_ageing
-- Record-level ageing for unsettled and delayed items.
SELECT recon_status, transaction_id, merchant_id, expected_settlement_date, settlement_date,
       expected_net_paise, delay_days, age_days, ageing_bucket
FROM reconciliation_results
WHERE recon_status IN ('Unsettled', 'Delayed Settlement') AND transaction_date BETWEEN :start AND :end
ORDER BY age_days DESC, transaction_id;

-- name: exception_ageing
-- Ageing of outstanding items (unsettled and delayed) by bucket.
SELECT ageing_bucket, recon_status, COUNT(*) AS records, SUM(COALESCE(expected_net_paise, 0)) AS value_paise
FROM reconciliation_results
WHERE ageing_bucket IS NOT NULL
GROUP BY ageing_bucket, recon_status
ORDER BY MIN(age_days), recon_status;
