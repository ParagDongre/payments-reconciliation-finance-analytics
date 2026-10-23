"""Deterministic, rule-based exception explanations (the core feature, no API key needed).

An optional LLM hook is provided at the bottom. It can only *rephrase* an explanation that SQL/Python
rules already produced - it never decides a reconciliation status - and it refuses to run unless the
user has explicitly enabled it and supplied a provider.
"""
from __future__ import annotations

import os
from typing import Callable, Mapping

import config
from src.money import paise_to_str
from src.reconciliation import (AMOUNT_MISMATCH, DELAYED, DQ_ERROR, DUPLICATE_SETTLEMENT, FAILED_REVERSED,
                                SETTLEMENT_ONLY, UNSETTLED)

INVESTIGATION_STEPS: dict[str, list[str]] = {
    UNSETTLED: [
        "Confirm the payment is captured at the gateway and the settlement cycle has run for the merchant.",
        "Check whether the merchant's settlement is on hold (risk review, KYC, negative balance).",
        "Search the bank/acquirer payout files for the transaction or its source_reference under a different key.",
        "If the payout truly failed, raise a re-settlement request and record the expected release date.",
    ],
    SETTLEMENT_ONLY: [
        "Search payments for the transaction_id with alternate formats or a typo.",
        "Check whether the payment belongs to a different period or a source system not yet loaded.",
        "Confirm the merchant on the settlement; if no payment exists, escalate as a potential unauthorised payout.",
    ],
    AMOUNT_MISMATCH: [
        "Recompute fee and GST for the payment method and compare to the settled amount.",
        "Check for partial settlement, chargeback or refund adjustment netted off the payout.",
        "If over-settled, plan recovery from the merchant's next settlement; if under-settled, schedule a top-up.",
    ],
    DUPLICATE_SETTLEMENT: [
        "Compare settlement_ids, references and timestamps to see whether the same payout was posted twice.",
        "Confirm with the bank whether funds were actually transferred twice.",
        "Reverse or recover the excess amount and fix the upstream job that double-posted.",
    ],
    DELAYED: [
        "Identify the cause (bank holiday, batch failure, merchant hold) and whether other transactions share it.",
        "Check for a delay pattern by merchant or payment method that points to a systemic issue.",
        "Review the SLA impact and any merchant communication needed.",
    ],
    FAILED_REVERSED: [
        "Verify the payment status at the gateway; a settlement for a failed/reversed payment is unexpected.",
        "Check whether a later retry succeeded under a different transaction_id.",
        "If the payout was made in error, initiate recovery from the merchant.",
    ],
    DQ_ERROR: [
        "Review the quarantined source row and the listed validation failures.",
        "Correct the value at source (or confirm it is a harmless repeat) and reload.",
        "Do not include the record in financial totals until it is corrected.",
    ],
}


def _inr(v) -> str:
    return "not available" if v is None or v != v else f"INR {paise_to_str(int(v))}"


def explain_exception(row: Mapping) -> dict[str, str]:
    """Explain one reconciliation result row: what was found, impact, evidence, next steps."""
    status = row["recon_status"]
    txn = row.get("transaction_id") or "(no transaction_id)"
    exposure = row.get("exposure_paise")
    detected = f"{status}: {row['reason']}"
    if status == UNSETTLED:
        impact = f"{_inr(row.get('expected_net_paise'))} owed to the merchant is outstanding for {row.get('age_days')} days."
    elif status == AMOUNT_MISMATCH:
        v = row.get("variance_paise")
        impact = f"Settlement differs from expected by {_inr(abs(int(v)))} ({'merchant under-paid' if v < 0 else 'merchant over-paid'})."
    elif status == DUPLICATE_SETTLEMENT:
        impact = f"Possible overpayment of {_inr(exposure)}."
    elif status == SETTLEMENT_ONLY:
        impact = f"{_inr(exposure)} was paid out with no matching payment."
    elif status == FAILED_REVERSED:
        impact = f"{_inr(exposure)} settled on a payment that should not settle." if exposure else "No financial impact; informational."
    elif status == DELAYED:
        impact = f"No amount difference; cash arrived {row.get('delay_days')} days after the expected date (timing impact only)."
    elif status == DQ_ERROR:
        impact = f"Record excluded from KPIs; value at stake {_inr(exposure)}."
    else:
        impact = "No exception."
    evidence = (f"transaction {txn}; payment_row_id={row.get('payment_row_id')}; "
                f"settlement_ids={row.get('settlement_ids') or 'none'}; "
                f"settlement_references={row.get('settlement_references') or 'none'}; "
                f"merchant={row.get('merchant_id') or 'unknown'}")
    steps = INVESTIGATION_STEPS.get(status, [])
    return {"what_was_detected": detected, "financial_impact": impact,
            "supporting_records": evidence, "next_steps": " | ".join(steps)}


# ----------------------------------------------------------------------------- optional LLM hook
class AIConfigError(RuntimeError):
    """Raised when an external AI call is attempted without explicit configuration."""


def ai_privacy_notice() -> str:
    return ("External AI is OFF by default. This project uses synthetic data only. If you enable it, the "
            "deterministic explanation text for one exception is sent to the provider you configure. "
            "Do not enable it with real merchant or customer data. SQL/Python rules remain the source of truth.")


def ai_rephrase(explanation: Mapping[str, str], provider: Callable[[str, str], str] | None = None) -> str:
    """Optionally rephrase a deterministic explanation with an LLM.

    Requires RECON_AI_ENABLED=1, an API key in the RECON_AI_API_KEY environment variable, and a
    caller-supplied `provider(prompt, api_key) -> str`. The key is never logged or returned.
    """
    if not config.AI_ENABLED:
        raise AIConfigError("AI assistance is disabled. Set RECON_AI_ENABLED=1 to opt in. " + ai_privacy_notice())
    key = os.environ.get(config.AI_API_KEY_ENV)
    if not key:
        raise AIConfigError(f"Set the {config.AI_API_KEY_ENV} environment variable to use AI assistance.")
    if provider is None:
        raise AIConfigError("No provider function supplied; no external call was made.")
    prompt = ("Rewrite this finance exception note for a non-technical manager in 3 sentences. "
              "Do not change any numbers or the status.\n\n" + "\n".join(f"{k}: {v}" for k, v in explanation.items()))
    return provider(prompt, key)
