"""Central configuration for the Payments Reconciliation & Finance Analytics Lab.

SIMULATION ONLY: all data is synthetic. This is not a payment-processing system.

All paths are relative to the project root and can be overridden with
environment variables, so nothing is machine-specific.
"""
from __future__ import annotations

import os
from decimal import ROUND_HALF_UP
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _path(env_name: str, default: str) -> Path:
    """Resolve a configurable path, relative to the project root if not absolute."""
    value = Path(os.environ.get(env_name, default))
    return value if value.is_absolute() else PROJECT_ROOT / value


DATA_DIR = _path("RECON_DATA_DIR", "data")
REPORTS_DIR = _path("RECON_REPORTS_DIR", "reports")
LOGS_DIR = _path("RECON_LOGS_DIR", "logs")
SQL_DIR = PROJECT_ROOT / "sql"
DB_PATH = _path("RECON_DB_PATH", "data/recon.db")

PAYMENTS_CSV = DATA_DIR / "payments.csv"
SETTLEMENTS_CSV = DATA_DIR / "settlements.csv"
ANOMALY_LOG_CSV = DATA_DIR / "anomaly_manifest.csv"

# ---------------------------------------------------------------- data generation
RANDOM_SEED = 42
N_PAYMENTS = 5000          # unique payment rows before duplicate source rows are added
N_MERCHANTS = 25
START_DATE = "2026-01-01"
END_DATE = "2026-06-30"
GENERATION_ASOF_DATE = "2026-07-15"   # "today" for the synthetic dataset

# ---------------------------------------------------------------- money rules
CURRENCY = "INR"
MONEY_QUANT = "0.01"       # all amounts held as Decimal with 2 places (paise)
ROUNDING = ROUND_HALF_UP
FEE_RATES = {"upi": "0.0000", "card": "0.0200", "netbanking": "0.0190", "wallet": "0.0150"}
GST_RATE = "0.18"          # tax on processing fee
AMOUNT_TOLERANCE = "0.00"  # exact match required; set e.g. "1.00" to tolerate rounding drift

# ---------------------------------------------------------------- reconciliation rules
SETTLEMENT_GRACE_DAYS = 1      # settled within expected date + grace is on time
HIGH_VALUE_THRESHOLD = "50000.00"
LONG_OUTSTANDING_DAYS = 14
AGEING_BUCKETS = [(0, 3, "0-3 days"), (4, 7, "4-7 days"), (8, 14, "8-14 days"), (15, 30, "15-30 days"), (31, 10**6, "30+ days")]

VALID_PAYMENT_STATUSES = {"success", "failed", "reversed"}
VALID_SETTLEMENT_STATUSES = {"settled", "pending", "reversed"}
VALID_PAYMENT_METHODS = set(FEE_RATES)

# ---------------------------------------------------------------- optional AI (off by default)
# External LLM calls only happen if the user sets BOTH variables explicitly.
AI_ENABLED = os.environ.get("RECON_AI_ENABLED", "0") == "1"
AI_API_KEY_ENV = "RECON_AI_API_KEY"   # key is read from the environment only, never logged
