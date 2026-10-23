"""Monetary helpers. Money is parsed with Decimal and stored/compared as integer paise.

Assumptions (see docs/reconciliation_rules.md):
* Currency is INR; amounts have 2 decimal places.
* Rounding is ROUND_HALF_UP to the paisa, applied once per computed fee/tax.
* Reconciliation compares integer paise, never floats.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import config

_QUANT = Decimal(config.MONEY_QUANT)


def round_money(value: Decimal) -> Decimal:
    """Round a Decimal to 2 places using the project rounding rule."""
    return value.quantize(_QUANT, rounding=config.ROUNDING)


def parse_amount(text: object) -> Decimal | None:
    """Parse a source amount string; return None if it is blank, non-numeric or non-finite."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def to_paise(value: Decimal | str | int) -> int:
    """Convert a rupee amount to integer paise (rounded half-up)."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return int((round_money(d) * 100).to_integral_value())


def paise_to_str(paise: int) -> str:
    """Format integer paise as a 2-decimal rupee string."""
    sign = "-" if paise < 0 else ""
    p = abs(int(paise))
    return f"{sign}{p // 100}.{p % 100:02d}"


def paise_to_rupees(paise: int | None) -> float | None:
    """Float rupees for display/charting only, never for reconciliation logic."""
    return None if paise is None else paise / 100
