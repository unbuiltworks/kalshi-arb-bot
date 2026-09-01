"""Pure quote-planning model for the first l5-style Gemini live probe.

This module deliberately contains no network or order-entry code.  It tests the
simple public-behaviour hypothesis we can actually observe: when one outcome's
market-implied probability becomes sufficiently favored, bias the resting maker
quote toward that side while continuing to quote the opposite outcome.

Important limitations:
- the market midpoint is a *signal proxy*, not a claim of private fair value;
- live v1 remains q=1 per outcome and therefore does not reproduce l5 sizing;
- maker rebates/rewards are reported separately and never used to manufacture
  positive trading P&L;
- the favored one-tick skew is accepted only if the conservative matched-pair
  floor still survives after current modeled Gemini maker fees.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

D = Decimal
ONE = D("1")
DEFAULT_THRESHOLD = D("0.60")
DEFAULT_PAIR_FLOOR = D("0.01")
DEFAULT_PRICE_INCREMENT = D("0.01")
REBATE_MIN_PRICE = D("0.20")
REBATE_MAX_PRICE = D("0.80")
DEFAULT_TAKER_RATE = D("0.07")
DEFAULT_REBATE_RATE = D("0.30")


def _floor_cent(value: D) -> D:
    if value < 0:
        raise ValueError("value cannot be negative")
    return (value * D("100")).to_integral_value(rounding=ROUND_FLOOR) / D("100")


def maker_rebate_estimate(
    price: D,
    quantity: D = D("1"),
    *,
    taker_rate: D = DEFAULT_TAKER_RATE,
    rebate_rate: D = DEFAULT_REBATE_RATE,
) -> tuple[D, D]:
    """Return raw and cent-rounded Gemini maker-rebate estimates.

    Public Gemini documentation currently describes maker rebate as
    rebate_rate * taker_rate * contracts * P * (1-P), with rebates rounded down
    to cents.  The caller must still verify the live rate endpoint before using
    this as economic accounting.
    """
    price = D(str(price))
    quantity = D(str(quantity))
    if quantity < 0 or price < 0 or price > ONE:
        raise ValueError("invalid rebate inputs")
    if price < REBATE_MIN_PRICE or price > REBATE_MAX_PRICE:
        return D("0"), D("0")
    raw = rebate_rate * taker_rate * quantity * price * (ONE - price)
    return raw, _floor_cent(raw)


def _pair_economics(yes_quote: D, no_quote: D) -> dict[str, D]:
    yes_fee = gemini_predictions_maker_fee(yes_quote, ONE)
    no_fee = gemini_predictions_maker_fee(no_quote, ONE)
    pair_cost = yes_quote + no_quote
    return {
        "yes_quote": yes_quote,
        "no_quote": no_quote,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "pair_cost": pair_cost,
        "modeled_edge": ONE - pair_cost - yes_fee - no_fee,
    }


@dataclass(frozen=True)
class QuotePlan:
    yes_mid: D
    no_mid: D
    favored_side: str | None
    favored_probability: D
    threshold: D
    shadow_target_units: int
    quote_skew_ticks: int
    yes_quote: D
    no_quote: D
    yes_fee: D
    no_fee: D
    pair_cost: D
    modeled_edge: D
    spread: D
    pair_floor: D
    yes_rebate_eligible: bool
    no_rebate_eligible: bool
    q1_yes_rebate_raw: D
    q1_no_rebate_raw: D
    q1_yes_rebate_rounded: D
    q1_no_rebate_rounded: D
    actionable_live_q1: bool

    def as_model_dict(self) -> dict[str, D]:
        """Compatibility shape expected by the proven inventory canaries."""
        return {
            "yes_quote": self.yes_quote,
            "no_quote": self.no_quote,
            "yes_fee": self.yes_fee,
            "no_fee": self.no_fee,
            "pair_cost": self.pair_cost,
            "modeled_edge": self.modeled_edge,
            "spread": self.spread,
        }


def _scale_units(probability: D) -> int:
    # Shadow-only sizing hypothesis. Live v1 is always hard-capped at q=1.
    if probability >= D("0.80"):
        return 3
    if probability >= D("0.70"):
        return 2
    if probability >= DEFAULT_THRESHOLD:
        return 1
    return 0


def plan_threshold_quotes(
    bid: D,
    ask: D,
    *,
    threshold: D = DEFAULT_THRESHOLD,
    pair_floor: D = DEFAULT_PAIR_FLOOR,
    price_increment: D = DEFAULT_PRICE_INCREMENT,
) -> QuotePlan:
    """Create a conservative l5-style threshold/skew plan from a YES book.

    The signal proxy is the YES midpoint. If YES midpoint >= threshold, YES is
    favored. If NO midpoint >= threshold, NO is favored. The favored maker bid
    is improved by at most one tick, but only when it remains passive and the
    full YES+NO pair still retains the configured after-maker-fee floor.
    """
    bid = D(str(bid))
    ask = D(str(ask))
    threshold = D(str(threshold))
    pair_floor = D(str(pair_floor))
    tick = D(str(price_increment))
    if not (D("0") < bid < ask < ONE):
        raise ValueError("invalid YES top of book")
    if tick <= 0:
        raise ValueError("price increment must be positive")
    if not (D("0.50") <= threshold < ONE):
        raise ValueError("threshold must be in [0.50, 1)")

    yes_mid = (bid + ask) / D("2")
    no_mid = ONE - yes_mid
    if yes_mid >= threshold:
        favored = "YES"
        favored_probability = yes_mid
    elif no_mid >= threshold:
        favored = "NO"
        favored_probability = no_mid
    else:
        favored = None
        favored_probability = max(yes_mid, no_mid)

    baseline_yes = bid
    baseline_no = ONE - ask
    chosen = _pair_economics(baseline_yes, baseline_no)
    skew_ticks = 0

    if favored == "YES":
        max_passive_yes = ask - tick
        candidate_yes = min(bid + tick, max_passive_yes)
        if candidate_yes > baseline_yes:
            candidate = _pair_economics(candidate_yes, baseline_no)
            if candidate["modeled_edge"] >= pair_floor:
                chosen = candidate
                skew_ticks = 1
    elif favored == "NO":
        no_ask = ONE - bid
        max_passive_no = no_ask - tick
        candidate_no = min(baseline_no + tick, max_passive_no)
        if candidate_no > baseline_no:
            candidate = _pair_economics(baseline_yes, candidate_no)
            if candidate["modeled_edge"] >= pair_floor:
                chosen = candidate
                skew_ticks = 1

    yes_quote = chosen["yes_quote"]
    no_quote = chosen["no_quote"]
    yes_eligible = REBATE_MIN_PRICE <= yes_quote <= REBATE_MAX_PRICE
    no_eligible = REBATE_MIN_PRICE <= no_quote <= REBATE_MAX_PRICE
    yes_raw, yes_rounded = maker_rebate_estimate(yes_quote)
    no_raw, no_rounded = maker_rebate_estimate(no_quote)
    actionable = bool(
        favored is not None
        and yes_eligible
        and no_eligible
        and chosen["modeled_edge"] >= pair_floor
    )

    return QuotePlan(
        yes_mid=yes_mid,
        no_mid=no_mid,
        favored_side=favored,
        favored_probability=favored_probability,
        threshold=threshold,
        shadow_target_units=_scale_units(favored_probability) if favored else 0,
        quote_skew_ticks=skew_ticks,
        yes_quote=yes_quote,
        no_quote=no_quote,
        yes_fee=chosen["yes_fee"],
        no_fee=chosen["no_fee"],
        pair_cost=chosen["pair_cost"],
        modeled_edge=chosen["modeled_edge"],
        spread=ask - bid,
        pair_floor=pair_floor,
        yes_rebate_eligible=yes_eligible,
        no_rebate_eligible=no_eligible,
        q1_yes_rebate_raw=yes_raw,
        q1_no_rebate_raw=no_raw,
        q1_yes_rebate_rounded=yes_rounded,
        q1_no_rebate_rounded=no_rounded,
        actionable_live_q1=actionable,
    )
