"""Run the reconciliation and reporting pipeline: python scripts/run_pipeline.py [options]."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Payments reconciliation pipeline (synthetic data).")
    ap.add_argument("--payments", type=Path, default=config.PAYMENTS_CSV)
    ap.add_argument("--settlements", type=Path, default=config.SETTLEMENTS_CSV)
    ap.add_argument("--db", type=Path, default=config.DB_PATH)
    ap.add_argument("--reports-dir", type=Path, default=config.REPORTS_DIR)
    ap.add_argument("--asof", default=config.GENERATION_ASOF_DATE, help="As-of date (YYYY-MM-DD) for ageing")
    args = ap.parse_args()
    summary = run_pipeline(args.payments, args.settlements, args.db, args.reports_dir, args.asof)
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
