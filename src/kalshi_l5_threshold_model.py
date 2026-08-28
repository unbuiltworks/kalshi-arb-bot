"""Pure, read-only quote model for l5-style Kalshi 15-minute research.

This is intentionally venue-specific and does not reuse Gemini fee/rebate math.
It answers only: given a two-sided Kalshi binary book, what one-tick passive
quote would the simple 60% favored-side hypothesis suggest?

No order entry lives in this module. Fee/reward economics remain unmodeled unless
an external incentive row is attached by the shadow runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

D = Decimal
ONE = D("1")
DEFAULT_THRESHOLD = D("0.60")
DEFAULT_TICK = D("0.01")


def _shadow_units(probability: D, threshold: D) -> int:
    if probability < threshold:
        return 0
    if probability >= D("0.80"):
        return 3
    if probability >= D("0.70"):
        return 2
    return 1


@dataclass(frozen=True)
class KalshiThresholdPlan:
    yes_bid: D
    yes_ask: D
    yes_mid: D
    no_mid: D
    favored_side: str | None
    favored_probability: D
    threshold: D
    yes_quote: D
    no_quote: D
    quote_skew_ticks: int
    shadow_target_units: int
    pair_cost_if_both_fill: D
    pre_fee_pair_margin: D
    passive: bool


def plan_kalshi_threshold_quotes(
    yes_bid: D,
    yes_ask: D,
    *,
    threshold: D = DEFAULT_THRESHOLD,
    tick: D = DEFAULT_TICK,
) -> KalshiThresholdPlan:
    yes_bid = D(str(yes_bid))
    yes_ask = D(str(yes_ask))
    threshold = D(str(threshold))
    tick = D(str(tick))
    if not (D("0") < yes_bid < yes_ask < ONE):
        raise ValueError("invalid Kalshi YES top of book")
    if tick <= 0:
        raise ValueError("tick must be positive")
    if not (D("0.50") <= threshold < ONE):
        raise ValueError("threshold must be in [0.50, 1)")

    yes_mid = (yes_bid + yes_ask) / D("2")
    no_mid = ONE - yes_mid
    if yes_mid >= threshold:
        favored_side = "YES"
        favored_probability = yes_mid
    elif no_mid >= threshold:
        favored_side = "NO"
        favored_probability = no_mid
    else:
        favored_side = None
        favored_probability = max(yes_mid, no_mid)

    no_bid = ONE - yes_ask
    no_ask = ONE - yes_bid
    yes_quote = yes_bid
    no_quote = no_bid
    skew_ticks = 0

    if favored_side == "YES":
        candidate = min(yes_bid + tick, yes_ask - tick)
        if candidate > yes_bid:
            yes_quote = candidate
            skew_ticks = 1
    elif favored_side == "NO":
        candidate = min(no_bid + tick, no_ask - tick)
        if candidate > no_bid:
            no_quote = candidate
            skew_ticks = 1

    passive = yes_quote < yes_ask and no_quote < no_ask
    pair_cost = yes_quote + no_quote
    return KalshiThresholdPlan(
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        yes_mid=yes_mid,
        no_mid=no_mid,
        favored_side=favored_side,
        favored_probability=favored_probability,
        threshold=threshold,
        yes_quote=yes_quote,
        no_quote=no_quote,
        quote_skew_ticks=skew_ticks,
        shadow_target_units=_shadow_units(favored_probability, threshold) if favored_side else 0,
        pair_cost_if_both_fill=pair_cost,
        pre_fee_pair_margin=ONE - pair_cost,
        passive=passive,
    )
