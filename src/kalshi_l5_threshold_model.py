"""Pure, read-only quote model for l5-style Kalshi 15-minute research.

This is intentionally venue-specific and does not reuse Gemini fee/rebate math.
It answers only: given a two-sided Kalshi binary book, what one-tick passive
quote would the simple 60% favored-side hypothesis suggest?

Kalshi supports market-specific subpenny price ranges. Callers should pass the
market's ``price_ranges`` so the proposed quote advances by the venue's actual
valid step at that price rather than assuming a one-cent tick.

No order entry lives in this module. Fee/reward economics remain unmodeled unless
an external incentive row is attached by the shadow runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

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


def kalshi_price_step(
    price: D,
    price_ranges: Iterable[Mapping[str, Any]] | None,
    *,
    fallback: D = DEFAULT_TICK,
) -> D:
    """Return the active Kalshi step for ``price`` from Market.price_ranges."""
    price = D(str(price))
    fallback = D(str(fallback))
    for row in price_ranges or ():
        try:
            start = D(str(row.get("start")))
            end = D(str(row.get("end")))
            step = D(str(row.get("step")))
        except Exception:
            continue
        if step <= 0:
            continue
        # Price ranges tile [0,1]. Treat the final endpoint as inclusive.
        if start <= price < end or (price == ONE and end == ONE):
            return step
    return fallback


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
    quote_step: D
    shadow_target_units: int
    pair_cost_if_both_fill: D
    pre_fee_pair_margin: D
    passive: bool


def plan_kalshi_threshold_quotes(
    yes_bid: D,
    yes_ask: D,
    *,
    threshold: D = DEFAULT_THRESHOLD,
    tick: D | None = None,
    price_ranges: Iterable[Mapping[str, Any]] | None = None,
) -> KalshiThresholdPlan:
    yes_bid = D(str(yes_bid))
    yes_ask = D(str(yes_ask))
    threshold = D(str(threshold))
    if not (D("0") < yes_bid < yes_ask < ONE):
        raise ValueError("invalid Kalshi YES top of book")
    if tick is not None and D(str(tick)) <= 0:
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
    quote_step = D(str(tick)) if tick is not None else DEFAULT_TICK

    if favored_side == "YES":
        quote_step = D(str(tick)) if tick is not None else kalshi_price_step(yes_bid, price_ranges)
        candidate = yes_bid + quote_step
        if candidate < yes_ask:
            yes_quote = candidate
            skew_ticks = 1
    elif favored_side == "NO":
        quote_step = D(str(tick)) if tick is not None else kalshi_price_step(no_bid, price_ranges)
        candidate = no_bid + quote_step
        if candidate < no_ask:
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
        quote_step=quote_step,
        shadow_target_units=_shadow_units(favored_probability, threshold) if favored_side else 0,
        pair_cost_if_both_fill=pair_cost,
        pre_fee_pair_margin=ONE - pair_cost,
        passive=passive,
    )
