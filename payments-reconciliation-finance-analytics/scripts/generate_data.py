"""Generate the reproducible synthetic CSVs: python scripts/generate_data.py [--seed N] [--out DIR]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from src.data_generator import generate, write_csvs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic payments/settlements (simulation only).")
    ap.add_argument("--seed", type=int, default=config.RANDOM_SEED)
    ap.add_argument("--out", type=Path, default=config.DATA_DIR)
    args = ap.parse_args()
    data = generate(seed=args.seed)
    paths = write_csvs(data, args.out)
    print(f"payments:    {len(data.payments):>6} rows -> {paths['payments']}")
    print(f"settlements: {len(data.settlements):>6} rows -> {paths['settlements']}")
    print(f"anomaly manifest: {len(data.manifest)} rows -> {paths['manifest']}")
    print(data.manifest["scenario"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
