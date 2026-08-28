"""Fail-closed model-run/quote-time provenance guard for weather SHADOW EV.

A forecast generated after the market quote being scored is lookahead evidence and
must never certify that earlier apparent edge. This helper is intentionally pure,
read-only, and execution-incapable so it can be composed into the weather model
sanity/certification pipeline without adding venue clients or arm state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _parse_aware_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def audit_model_run_quote_time(*, model_run_time: Any, quote_observed_at: Any) -> dict[str, Any]:
    """Require the scored forecast run to exist no later than the scored quote."""
    run_time = _parse_aware_iso(model_run_time)
    quote_time = _parse_aware_iso(quote_observed_at)
    blockers: list[str] = []

    if run_time is None or quote_time is None:
        blockers.append("MODEL_RUN_QUOTE_TIME_PROVENANCE_INVALID")
    elif run_time > quote_time:
        blockers.append("MODEL_RUN_LOOKAHEAD_DETECTED")

    verified = not blockers
    return {
        "model_run_quote_time_verified": verified,
        "model_run_time": run_time.isoformat() if run_time is not None else None,
        "quote_observed_at": quote_time.isoformat() if quote_time is not None else None,
        "blockers": blockers,
        "classification": (
            "MODEL_RUN_QUOTE_TIME_VERIFIED_SHADOW"
            if verified
            else "MODEL_RUN_LOOKAHEAD_RESEARCH_ONLY"
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
