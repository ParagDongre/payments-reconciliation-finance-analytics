-- Schema for the reconciliation lab (SQLite).
-- Amounts are stored as INTEGER paise (1 INR = 100 paise) to avoid floating-point error.
-- Raw source values are preserved in the *_raw tables; typed rows live in payments/settlements.

DROP VIEW IF EXISTS v_recon_latest;

CREATE TABLE IF NOT EXISTS payments (
    row_id                    INTEGER PRIMARY KEY,   -- 1-based position in source CSV (traceability)
    transaction_id            TEXT,
    merchant_id               TEXT,
    transaction_timestamp     TEXT,
    gross_amount_paise        INTEGER,
    currency                  TEXT,
    payment_method            TEXT,
    payment_status            TEXT,
    expected_settlement_date  TEXT,
    processing_fee_paise      INTEGER,
    tax_on_fee_paise          INTEGER,
    expected_net_paise        INTEGER,
    source_reference          TEXT,
    is_duplicate_source       INTEGER NOT NULL DEFAULT 0,  -- 1 = repeat of an earlier transaction_id
    validation_errors         TEXT                          -- semicolon-separated, empty when valid
);

CREATE TABLE IF NOT EXISTS settlements (
    row_id                INTEGER PRIMARY KEY,
    settlement_id         TEXT,
    transaction_id        TEXT,
    merchant_id           TEXT,
    settlement_timestamp  TEXT,
    settled_amount_paise  INTEGER,
    settlement_reference  TEXT,
    settlement_status     TEXT,
    validation_errors     TEXT
);

CREATE TABLE IF NOT EXISTS rejected_records (
    source        TEXT NOT NULL,      -- 'payments' or 'settlements'
    row_id        INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    raw_record    TEXT NOT NULL,      -- original row as JSON, untouched
    PRIMARY KEY (source, row_id)
);

-- One row per reconciled payment (record_type='payment') or per settlement row that could not be
-- tied to a valid payment (record_type='settlement'). Denormalised on purpose so every dashboard
-- page and report reads the same auditable output.
CREATE TABLE IF NOT EXISTS reconciliation_results (
    result_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    record_type          TEXT NOT NULL,
    transaction_id       TEXT,
    payment_row_id       INTEGER,
    settlement_row_ids   TEXT,
    settlement_ids       TEXT,
    settlement_references TEXT,
    merchant_id          TEXT,
    payment_method       TEXT,
    payment_status       TEXT,
    transaction_date     TEXT,
    gross_amount_paise   INTEGER,
    expected_settlement_date TEXT,
    settlement_date      TEXT,
    recon_status         TEXT NOT NULL,
    reason               TEXT NOT NULL,
    is_exception         INTEGER NOT NULL,   -- 1 = needs analyst attention
    expected_net_paise   INTEGER,
    settled_paise        INTEGER,
    variance_paise       INTEGER,            -- settled - expected
    exposure_paise       INTEGER,            -- financial exposure of the exception (>= 0)
    delay_days           INTEGER,
    age_days             INTEGER,
    ageing_bucket        TEXT,
    run_id               TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id        TEXT PRIMARY KEY,
    started_at    TEXT,
    finished_at   TEXT,
    status        TEXT,
    summary_json  TEXT
);

CREATE INDEX IF NOT EXISTS ix_pay_txn ON payments(transaction_id);
CREATE INDEX IF NOT EXISTS ix_set_txn ON settlements(transaction_id);
CREATE INDEX IF NOT EXISTS ix_set_ref ON settlements(settlement_reference);
CREATE INDEX IF NOT EXISTS ix_res_status ON reconciliation_results(recon_status);
