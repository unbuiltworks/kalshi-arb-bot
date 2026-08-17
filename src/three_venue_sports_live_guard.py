"""Hard guardrails for the first automated sports live probe.

No network calls or order submission exist here. The first production probe is
intentionally narrower than the research router:
- MLB full-game moneyline only
- pregame only
- exactly one contract on each leg
- minimum 5c recomputed after-fee locked profit
- <=50ms final quote skew
- FOK on Gemini/Kalshi
- explicit normal-completion rule-risk acknowledgement
- independent CLI + environment opt-ins remain required by transport

This module adapts the already-tested Gemini/Kalshi probe payloads to sports without
weakening their exact outbound-economics checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from typing import Any

from src.gemini_kalshi_live_probe_guard import (
    build_leg_payloads,
    recompute_payload_locked_profit,
)

D = Decimal
SPORTS_LIVE_ENV = "ENABLE_THREE_VENUE_SPORTS_LIVE_PROBES"
RULE_RISK_ENV = "ACK_MLB_ABNORMAL_STATE_RULE_RISK"
MAX_QUANTITY = D("1")
MIN_LOCKED_PROFIT = D("0.05")
MAX_QUOTE_SKEW_MS = 50
MIN_SECONDS_TO_START = 30
MAX_SECONDS_TO_START = 24 * 60 * 60


@dataclass(frozen=True)
class SportsProbeIdentity:
    sport: str
    league: str
    market_family: str
    scope: str
    canonical_subject: str
    event_start_ms: int
    normal_completion_equivalent: bool
    fully_rule_equivalent: bool


def sports_live_authorized(cli_live: bool) -> bool:
    return (
        bool(cli_live)
        and os.getenv(SPORTS_LIVE_ENV, "NO") == "YES"
        and os.getenv(RULE_RISK_ENV, "NO") == "YES"
    )


def validate_identity(identity: SportsProbeIdentity, *, now_ms: int) -> None:
    if identity.sport != "baseball" or identity.league != "mlb":
        raise ValueError("first sports live probe is MLB-only")
    if identity.market_family != "moneyline":
        raise ValueError("first sports live probe is full-game moneyline-only")
    if identity.scope != "full_contest":
        raise ValueError("first sports live probe requires full_contest scope")
    if not identity.canonical_subject:
        raise ValueError("missing canonical team subject")
    if not identity.normal_completion_equivalent:
        raise ValueError("normal-completion settlement predicate is not equivalent")
    if identity.fully_rule_equivalent:
        # Full equivalence is acceptable, but currently uncommon across venues.
        pass
    seconds_to_start = (int(identity.event_start_ms) - int(now_ms)) / 1000
    if seconds_to_start < MIN_SECONDS_TO_START:
        raise ValueError("first sports live probe requires >=30s before scheduled start")
    if seconds_to_start > MAX_SECONDS_TO_START:
        raise ValueError("first sports live probe only trades games within 24h")


def validate_probe_economics(*, quantity: Decimal, locked_profit: Decimal, quote_skew_ms: int) -> None:
    if D(quantity) != MAX_QUANTITY:
        raise ValueError("first sports live probe is hard-capped at exactly 1 contract")
    if D(locked_profit) < MIN_LOCKED_PROFIT:
        raise ValueError("first sports live probe requires >=5c after-fee locked profit")
    if int(quote_skew_ms) > MAX_QUOTE_SKEW_MS:
        raise ValueError("final sports quote skew exceeds 50ms")


def build_gemini_kalshi_sports_payloads(
    *,
    identity: SportsProbeIdentity,
    now_ms: int,
    direction: str,
    gemini_symbol: str,
    kalshi_ticker: str,
    gemini_yes_bid: Decimal,
    gemini_yes_ask: Decimal,
    kalshi_yes_bid: Decimal,
    kalshi_yes_ask: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
) -> tuple[dict[str, Any], dict[str, Any], Decimal]:
    """Build and independently reprice exact q=1 Gemini/Kalshi FOK payloads."""
    validate_identity(identity, now_ms=now_ms)
    gemini, kalshi = build_leg_payloads(
        direction=direction,
        gemini_symbol=gemini_symbol,
        kalshi_ticker=kalshi_ticker,
        gemini_yes_bid=D(gemini_yes_bid),
        gemini_yes_ask=D(gemini_yes_ask),
        kalshi_yes_bid=D(kalshi_yes_bid),
        kalshi_yes_ask=D(kalshi_yes_ask),
    )
    locked = recompute_payload_locked_profit(
        gemini,
        kalshi,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    validate_probe_economics(quantity=D("1"), locked_profit=locked, quote_skew_ms=quote_skew_ms)
    return gemini, kalshi, locked


def assert_sports_send_authorized(
    *,
    cli_live: bool,
    identity: SportsProbeIdentity,
    now_ms: int,
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    modeled_locked_profit: Decimal,
    kalshi_fee_multiplier: Decimal,
    quote_skew_ms: int,
) -> Decimal:
    """Last synchronous gate immediately before transport mutation."""
    if not sports_live_authorized(cli_live):
        raise PermissionError(
            "sports live probe disabled; --live + ENABLE_THREE_VENUE_SPORTS_LIVE_PROBES=YES "
            "+ ACK_MLB_ABNORMAL_STATE_RULE_RISK=YES are all required"
        )
    validate_identity(identity, now_ms=now_ms)
    recomputed = recompute_payload_locked_profit(
        gemini_payload,
        kalshi_payload,
        kalshi_fee_multiplier=D(kalshi_fee_multiplier),
    )
    if abs(recomputed - D(modeled_locked_profit)) > D("0.000001"):
        raise ValueError("sports outbound payload economics changed after candidate selection")
    validate_probe_economics(quantity=D("1"), locked_profit=recomputed, quote_skew_ms=quote_skew_ms)
    return recomputed
