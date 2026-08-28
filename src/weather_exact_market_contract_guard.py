"""Fail-closed exact-contract weather market audit from Kalshi Get Market evidence.

Read-only research helper.  Never infer bracket semantics from ticker suffixes or titles.
Certification requires an exact GET /markets/{ticker} request/response binding plus the
numeric strike fields and settlement-rule text returned for that exact contract.
"""
from __future__ import annotations

from math import isfinite
from typing import Any
from urllib.parse import unquote, urlparse

_ALLOWED_API_HOST = "external-api.kalshi.com"
_ALLOWED_STRIKE_TYPES = {"greater", "less", "between"}


def _norm_token(value: Any) -> str | None:
    token = str(value or "").strip().upper()
    return token or None


def _finite_number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _request_ticker_from_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != _ALLOWED_API_HOST:
        return None
    parts = [unquote(part).strip() for part in (parsed.path or "").split("/") if part.strip()]
    # Current public endpoint: /trade-api/v2/markets/{ticker}
    if len(parts) != 4 or [part.lower() for part in parts[:3]] != ["trade-api", "v2", "markets"]:
        return None
    return _norm_token(parts[3])


def audit_exact_weather_market_contract(
    *,
    target_market_ticker: Any,
    target_event_ticker: Any,
    api_request_url: Any,
    api_market: Any,
) -> dict[str, Any]:
    """Bind an exact weather contract to authoritative Kalshi market fields.

    ``api_market`` must be the unmodified market object from Kalshi's Get Market response.
    This guard deliberately does not parse ticker suffixes to reconstruct thresholds.
    """
    blockers: list[str] = []
    target_market = _norm_token(target_market_ticker)
    target_event = _norm_token(target_event_ticker)
    request_ticker = _request_ticker_from_url(api_request_url)

    if not isinstance(api_market, dict):
        api_market = {}
        blockers.append("EXACT_MARKET_API_RESPONSE_REQUIRED")

    response_market = _norm_token(api_market.get("ticker"))
    response_event = _norm_token(api_market.get("event_ticker"))
    if (
        not target_market
        or not target_event
        or request_ticker != target_market
        or response_market != target_market
        or response_event != target_event
    ):
        blockers.append("EXACT_MARKET_API_IDENTITY_MISMATCH")

    strike_type_raw = api_market.get("strike_type")
    strike_type = str(strike_type_raw or "").strip().lower() or None
    floor_strike = _finite_number_or_none(api_market.get("floor_strike"))
    cap_strike = _finite_number_or_none(api_market.get("cap_strike"))

    if strike_type not in _ALLOWED_STRIKE_TYPES:
        blockers.append("EXACT_MARKET_STRIKE_TYPE_UNVERIFIED")
    elif strike_type == "greater" and floor_strike is None:
        blockers.append("EXACT_MARKET_FLOOR_STRIKE_UNVERIFIED")
    elif strike_type == "less" and cap_strike is None:
        blockers.append("EXACT_MARKET_CAP_STRIKE_UNVERIFIED")
    elif strike_type == "between":
        if floor_strike is None or cap_strike is None or floor_strike > cap_strike:
            blockers.append("EXACT_MARKET_RANGE_STRIKES_UNVERIFIED")

    rules_primary = api_market.get("rules_primary")
    rules_secondary = api_market.get("rules_secondary")
    if not isinstance(rules_primary, str) or not rules_primary.strip():
        blockers.append("EXACT_MARKET_PRIMARY_RULES_REQUIRED")
    if not isinstance(rules_secondary, str) or not rules_secondary.strip():
        blockers.append("EXACT_MARKET_SECONDARY_RULES_REQUIRED")

    blockers = sorted(set(blockers))
    return {
        "exact_market_contract_verified": not blockers,
        "blockers": blockers,
        "target_market_ticker": target_market,
        "target_event_ticker": target_event,
        "api_request_ticker": request_ticker,
        "api_response_market_ticker": response_market,
        "api_response_event_ticker": response_event,
        "strike_type": strike_type,
        "floor_strike": floor_strike,
        "cap_strike": cap_strike,
        "rules_primary_present": isinstance(rules_primary, str) and bool(rules_primary.strip()),
        "rules_secondary_present": isinstance(rules_secondary, str) and bool(rules_secondary.strip()),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
