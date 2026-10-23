-- Data-quality validation checks run against the loaded source tables.

-- name: dq_missing_values
-- Conditional aggregation: blank/NULL counts per key payment column.
SELECT 'payments' AS source, 'transaction_id' AS column_name, SUM(CASE WHEN COALESCE(transaction_id,'') = '' THEN 1 ELSE 0 END) AS missing FROM payments
UNION ALL SELECT 'payments', 'merchant_id', SUM(CASE WHEN COALESCE(merchant_id,'') = '' THEN 1 ELSE 0 END) FROM payments
UNION ALL SELECT 'payments', 'transaction_timestamp', SUM(CASE WHEN COALESCE(transaction_timestamp,'') = '' THEN 1 ELSE 0 END) FROM payments
UNION ALL SELECT 'payments', 'gross_amount', SUM(CASE WHEN gross_amount_paise IS NULL THEN 1 ELSE 0 END) FROM payments
UNION ALL SELECT 'payments', 'source_reference', SUM(CASE WHEN COALESCE(source_reference,'') = '' THEN 1 ELSE 0 END) FROM payments
UNION ALL SELECT 'settlements', 'transaction_id', SUM(CASE WHEN COALESCE(transaction_id,'') = '' THEN 1 ELSE 0 END) FROM settlements
UNION ALL SELECT 'settlements', 'settled_amount', SUM(CASE WHEN settled_amount_paise IS NULL THEN 1 ELSE 0 END) FROM settlements
UNION ALL SELECT 'settlements', 'settlement_reference', SUM(CASE WHEN COALESCE(settlement_reference,'') = '' THEN 1 ELSE 0 END) FROM settlements;

-- name: dq_duplicate_transactions
-- Duplicate source records: GROUP BY + HAVING on transaction_id.
SELECT transaction_id, COUNT(*) AS occurrences, GROUP_CONCAT(row_id) AS source_rows
FROM payments
WHERE COALESCE(transaction_id,'') <> ''
GROUP BY transaction_id
HAVING COUNT(*) > 1
ORDER BY occurrences DESC, transaction_id;

-- name: dq_duplicate_settlement_references
-- The same settlement_reference appearing on more than one settlement row.
SELECT settlement_reference, COUNT(*) AS occurrences, GROUP_CONCAT(settlement_id) AS settlement_ids,
       COUNT(DISTINCT transaction_id) AS distinct_transactions
FROM settlements
WHERE COALESCE(settlement_reference,'') <> ''
GROUP BY settlement_reference
HAVING COUNT(*) > 1
ORDER BY occurrences DESC, settlement_reference;

-- name: dq_invalid_amounts
-- Rows rejected for unusable money fields (non-numeric, blank, zero or negative).
SELECT 'payments' AS source, row_id, transaction_id, validation_errors AS reason FROM payments
WHERE validation_errors LIKE '%amount%' OR validation_errors LIKE '%fee%' OR validation_errors LIKE '%expected_net%'
UNION ALL
SELECT 'settlements', row_id, transaction_id, validation_errors FROM settlements
WHERE validation_errors LIKE '%settled_amount%'
ORDER BY source, row_id;

-- name: dq_invalid_identifiers
-- Missing or malformed transaction / merchant identifiers.
SELECT 'payments' AS source, row_id, transaction_id, validation_errors AS reason FROM payments
WHERE validation_errors LIKE '%transaction_id%' OR validation_errors LIKE '%merchant_id%'
UNION ALL
SELECT 'settlements', row_id, transaction_id, validation_errors FROM settlements
WHERE validation_errors LIKE '%transaction_id%' OR validation_errors LIKE '%merchant_id%'
ORDER BY source, row_id;

-- name: dq_referential_integrity
-- Settlement rows (valid format) pointing to no payment, via LEFT JOIN anti-join.
SELECT s.row_id, s.settlement_id, s.transaction_id, s.settled_amount_paise,
       'transaction_id not found in payments' AS reason
FROM settlements s
LEFT JOIN payments p ON p.transaction_id = s.transaction_id
WHERE p.row_id IS NULL AND COALESCE(s.transaction_id,'') <> ''
ORDER BY s.row_id;

-- name: dq_merchant_consistency
-- INNER JOIN: settlement merchant differs from the payment's merchant.
SELECT s.settlement_id, s.transaction_id, p.merchant_id AS payment_merchant, s.merchant_id AS settlement_merchant
FROM settlements s
INNER JOIN payments p ON p.transaction_id = s.transaction_id AND p.is_duplicate_source = 0
WHERE p.merchant_id <> s.merchant_id AND p.merchant_id <> '' AND s.merchant_id <> '';

-- name: dq_rejected_records
-- Quarantined rows with reasons (never silently dropped).
SELECT source, row_id, reason, raw_record
FROM rejected_records
ORDER BY source, row_id;

-- name: dq_summary_by_reason
-- Count of quarantined rows grouped by source (reason text varies, so group by source here).
SELECT source, COUNT(*) AS rejected_rows
FROM rejected_records
GROUP BY source;
