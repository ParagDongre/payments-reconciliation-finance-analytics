-- KPI and trend queries over reconciliation_results (single source of truth for the app and reports).
-- Window parameters: :start and :end are inclusive ISO dates applied to transaction_date.
-- Canonical payments only: duplicate source rows are Data Quality Errors and are excluded from value KPIs.

-- name: kpi_summary
-- Conditional aggregation producing every executive KPI in one pass.
--   gross value      = sum(gross_amount) of valid, non-duplicate payments
--   successful       = payment_status 'success' among those
--   expected net     = gross - processing fee - tax on fee, for successful payments
--   actual settled   = sum of settled amounts on those payments (duplicates included: cash really paid)
SELECT
  SUM(CASE WHEN r.record_type = 'payment' THEN 1 ELSE 0 END)                                           AS payment_rows,
  SUM(CASE WHEN r.record_type = 'payment' AND r.recon_status <> 'Data Quality Error' THEN 1 ELSE 0 END) AS total_transactions,
  COALESCE(SUM(CASE WHEN r.record_type = 'payment' AND r.recon_status <> 'Data Quality Error' THEN r.gross_amount_paise END), 0) AS gross_value_paise,
  SUM(CASE WHEN r.payment_status = 'success' AND r.recon_status <> 'Data Quality Error' THEN 1 ELSE 0 END) AS successful_count,
  COALESCE(SUM(CASE WHEN r.payment_status = 'success' AND r.recon_status <> 'Data Quality Error' THEN r.gross_amount_paise END), 0) AS successful_value_paise,
  COALESCE(SUM(CASE WHEN r.payment_status = 'success' AND r.recon_status <> 'Data Quality Error' THEN r.expected_net_paise END), 0) AS expected_net_paise,
  COALESCE(SUM(CASE WHEN r.record_type = 'payment' AND r.recon_status <> 'Data Quality Error' THEN r.settled_paise END), 0)       AS settled_paise,
  SUM(CASE WHEN r.recon_status = 'Matched' THEN 1 ELSE 0 END)                                          AS matched_count,
  COALESCE(SUM(CASE WHEN r.recon_status = 'Matched' THEN r.settled_paise END), 0)                      AS matched_value_paise,
  SUM(CASE WHEN r.recon_status IN ('Matched','Delayed Settlement','Amount Mismatch','Duplicate Settlement','Unsettled')
            THEN 1 ELSE 0 END)                                                                          AS reconcilable_count,
  SUM(CASE WHEN r.is_exception = 1 THEN 1 ELSE 0 END)                                                  AS exception_count,
  SUM(CASE WHEN r.is_exception = 1 AND r.recon_status <> 'Delayed Settlement' THEN 1 ELSE 0 END)       AS outstanding_count,
  COALESCE(SUM(CASE WHEN r.is_exception = 1 THEN r.exposure_paise END), 0)                             AS exception_exposure_paise,
  COALESCE(SUM(CASE WHEN r.recon_status = 'Amount Mismatch' THEN ABS(r.variance_paise) END), 0)        AS mismatch_abs_paise,
  COALESCE(SUM(CASE WHEN r.recon_status = 'Amount Mismatch' THEN r.variance_paise END), 0)             AS mismatch_net_paise,
  SUM(CASE WHEN r.recon_status = 'Delayed Settlement' THEN 1 ELSE 0 END)                               AS delayed_count,
  SUM(CASE WHEN r.delay_days IS NOT NULL THEN 1 ELSE 0 END)                                            AS single_settled_count,
  SUM(CASE WHEN r.delay_days > :grace THEN 1 ELSE 0 END)                                               AS late_count
FROM reconciliation_results r
WHERE (r.transaction_date BETWEEN :start AND :end) OR (r.transaction_date IS NULL AND :include_undated = 1);

-- name: kpi_fees
-- Fees and tax on fees are kept separate from gross and net so the bridge gross - fee - tax = net is visible.
SELECT COALESCE(SUM(processing_fee_paise), 0) AS fee_paise,
       COALESCE(SUM(tax_on_fee_paise), 0)     AS tax_paise
FROM payments
WHERE payment_status = 'success' AND is_duplicate_source = 0 AND validation_errors = ''
  AND date(transaction_timestamp) BETWEEN :start AND :end;

-- name: daily_transactions
-- Date-based daily metrics (valid, non-duplicate payments).
SELECT transaction_date,
       COUNT(*) AS transactions,
       SUM(CASE WHEN payment_status = 'success' THEN 1 ELSE 0 END) AS successful,
       SUM(CASE WHEN payment_status IN ('failed','reversed') THEN 1 ELSE 0 END) AS failed_or_reversed,
       SUM(gross_amount_paise) AS gross_paise,
       SUM(CASE WHEN payment_status = 'success' THEN gross_amount_paise ELSE 0 END) AS successful_gross_paise,
       SUM(COALESCE(expected_net_paise, 0)) AS expected_net_paise,
       SUM(COALESCE(settled_paise, 0)) AS settled_paise,
       SUM(is_exception) AS exceptions
FROM reconciliation_results
WHERE record_type = 'payment' AND recon_status <> 'Data Quality Error'
  AND transaction_date BETWEEN :start AND :end
GROUP BY transaction_date
ORDER BY transaction_date;

-- name: monthly_transactions
-- Monthly roll-up with strftime.
SELECT strftime('%Y-%m', transaction_date) AS month,
       COUNT(*) AS transactions,
       SUM(gross_amount_paise) AS gross_paise,
       SUM(CASE WHEN payment_status = 'success' THEN COALESCE(expected_net_paise, 0) ELSE 0 END) AS expected_net_paise,
       SUM(COALESCE(settled_paise, 0)) AS settled_paise,
       SUM(is_exception) AS exceptions,
       ROUND(100.0 * SUM(CASE WHEN recon_status = 'Matched' THEN 1 ELSE 0 END)
             / NULLIF(SUM(CASE WHEN payment_status = 'success' THEN 1 ELSE 0 END), 0), 2) AS match_rate_pct
FROM reconciliation_results
WHERE record_type = 'payment' AND recon_status <> 'Data Quality Error'
  AND transaction_date BETWEEN :start AND :end
GROUP BY strftime('%Y-%m', transaction_date)
ORDER BY month;

-- name: merchant_summary
-- Merchant-level summary using conditional aggregation.
SELECT merchant_id,
       COUNT(*) AS transactions,
       SUM(CASE WHEN payment_status = 'success' THEN 1 ELSE 0 END) AS successful,
       SUM(gross_amount_paise) AS gross_paise,
       SUM(CASE WHEN payment_status = 'success' THEN COALESCE(expected_net_paise, 0) ELSE 0 END) AS expected_net_paise,
       SUM(COALESCE(settled_paise, 0)) AS settled_paise,
       SUM(CASE WHEN recon_status = 'Matched' THEN 1 ELSE 0 END) AS matched,
       SUM(is_exception) AS exceptions,
       COALESCE(SUM(CASE WHEN is_exception = 1 THEN exposure_paise END), 0) AS exposure_paise,
       ROUND(100.0 * SUM(CASE WHEN recon_status = 'Matched' THEN 1 ELSE 0 END)
             / NULLIF(SUM(CASE WHEN payment_status = 'success' THEN 1 ELSE 0 END), 0), 2) AS match_rate_pct
FROM reconciliation_results
WHERE record_type = 'payment' AND recon_status <> 'Data Quality Error' AND merchant_id IS NOT NULL
  AND transaction_date BETWEEN :start AND :end
GROUP BY merchant_id
ORDER BY gross_paise DESC;

-- name: settlement_trend_daily
-- Settled value by settlement date.
SELECT settlement_date, COUNT(*) AS settlements, SUM(settled_paise) AS settled_paise
FROM reconciliation_results
WHERE settlement_date IS NOT NULL AND settled_paise IS NOT NULL AND recon_status <> 'Data Quality Error'
  AND settlement_date BETWEEN :start AND :end
GROUP BY settlement_date
ORDER BY settlement_date;

-- name: exception_trend_monthly
-- Exceptions per transaction month by type.
SELECT strftime('%Y-%m', transaction_date) AS month, recon_status, COUNT(*) AS exceptions,
       COALESCE(SUM(exposure_paise), 0) AS exposure_paise
FROM reconciliation_results
WHERE is_exception = 1 AND transaction_date BETWEEN :start AND :end
GROUP BY strftime('%Y-%m', transaction_date), recon_status
ORDER BY month, recon_status;

-- name: delay_distribution
-- Settlement delay (days after expected date) for single settlements.
SELECT delay_days, COUNT(*) AS records
FROM reconciliation_results
WHERE delay_days IS NOT NULL AND recon_status IN ('Matched', 'Delayed Settlement')
  AND transaction_date BETWEEN :start AND :end
GROUP BY delay_days
ORDER BY delay_days;

-- name: settlement_reconciliation_summary
-- Count, expected, settled value and exposure per reconciliation status.
SELECT recon_status, COUNT(*) AS records,
       COALESCE(SUM(expected_net_paise), 0) AS expected_net_paise,
       COALESCE(SUM(settled_paise), 0) AS settled_paise,
       COALESCE(SUM(variance_paise), 0) AS variance_paise,
       COALESCE(SUM(CASE WHEN is_exception = 1 THEN exposure_paise END), 0) AS exposure_paise
FROM reconciliation_results
WHERE transaction_date BETWEEN :start AND :end OR transaction_date IS NULL
GROUP BY recon_status
ORDER BY records DESC;
