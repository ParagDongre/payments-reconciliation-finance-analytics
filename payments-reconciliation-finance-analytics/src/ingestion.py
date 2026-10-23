"""Load source CSVs, validate them and package the result for database loading."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import config
from src.data_generator import PAYMENT_COLUMNS, SETTLEMENT_COLUMNS
from src.validation import (load_csv, rejected_from, validate_payments, validate_settlements)

log = logging.getLogger(__name__)


@dataclass
class IngestedData:
    """Validated source data plus raw input counts."""
    payments: pd.DataFrame
    settlements: pd.DataFrame
    rejected: pd.DataFrame
    input_payment_rows: int
    input_settlement_rows: int


def load_and_validate(payments_csv: Path | str | None = None,
                      settlements_csv: Path | str | None = None) -> IngestedData:
    """Read both source files, validate every row and build the quarantine table.

    Raises SchemaError for missing files or missing required columns. Empty (header-only)
    files are valid and yield empty frames.
    """
    raw_p = load_csv(payments_csv or config.PAYMENTS_CSV, PAYMENT_COLUMNS)
    raw_s = load_csv(settlements_csv or config.SETTLEMENTS_CSV, SETTLEMENT_COLUMNS)
    pay = validate_payments(raw_p)
    stl = validate_settlements(raw_s)
    rejected = rejected_from(pay, stl, raw_p, raw_s)
    log.info("Loaded %d payment rows, %d settlement rows; %d quarantined",
             len(raw_p), len(raw_s), len(rejected))
    return IngestedData(pay, stl, rejected, len(raw_p), len(raw_s))
