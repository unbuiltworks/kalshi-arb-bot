"""Pure shadow policy for a directional Gemini 30-49c maker rebate buyer.

The rebate is always reported separately from independent fair-value edge. A
rebate never converts a negative-fair-value entry into an actionable trade for
any fair-value policy. ``band_only`` exists only as a comparator.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D, ROUND_DOWN
from typing import Mapping

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_l5_fair_value import FairValueEstimate

LOW = D("0.30")
HIGH = D("0.49")
DEFAULT_QTY = D("10")
DEFAULT_TAKER_RATE = D("0.07")


@dataclass(frozen=True)
class RebateBuyerDecision:
    policy: str
    side: str | None
    quote_price: D | None
    fair_probability: D | None
    quantity: D
    maker_fee_usd: D
    fair_value_edge_before_rebate_usd: D
    projected_rebate_usd: D
    projected_combined_ev_usd: D
    actionable: bool
    reason: str


def projected_maker_rebate(
    *, price: D, quantity: D, rebate_rate: D, taker_rate: D = DEFAULT_TAKER_RATE
) -> D:
    """Model Gemini's published rebate formula, floored to whole cents."""
    p = D(str(price))
    q = D(str(quantity))
    rate = D(str(rebate_rate))
    taker = D(str(taker_rate))
    if not (D("0") < p < D("1")) or q <= 0 or rate < 0 or taker < 0:
        raise ValueError("invalid rebate inputs")
    raw = rate * taker * q * p * (D("1") - p)
    return raw.quantize(D("0.01"), rounding=ROUND_DOWN)


def _candidate_rows(
    *, estimate: FairValueEstimate, yes_bid: D, yes_ask: D, quantity: D
) -> list[dict[str, D | str]]:
    yes = D(str(yes_bid))
    ask = D(str(yes_ask))
    q = D(str(quantity))
    no = D("1") - ask
    rows: list[dict[str, D | str]] = []
    for side, price, fair in (
        ("YES", yes, estimate.probability_up),
        ("NO", no, estimate.probability_down),
    ):
        if LOW <= price <= HIGH:
            fee = gemini_predictions_maker_fee(price, q)
            trade_edge = (fair - price) * q - fee
            rows.append(
                {
                    "side": side,
                    "price": price,
                    "fair": fair,
                    "maker_fee": fee,
                    "trade_edge": trade_edge,
                }
            )
    return rows


def decide(
    *,
    policy: str,
    estimate: FairValueEstimate,
    yes_bid: D,
    yes_ask: D,
    rebate_rate: D,
    min_edge_per_contract: D = D("0"),
    quantity: D = DEFAULT_QTY,
    taker_rate: D = DEFAULT_TAKER_RATE,
) -> RebateBuyerDecision:
    """Pick at most one 30-49c maker-buy side.

    For fair-value policies, the independent after-maker-fee edge must clear the
    configured floor *before* the projected rebate is considered. ``band_only``
    deliberately skips that gate so it can quantify the naive rebate-farming
    hypothesis as a comparator, not as a recommended live policy.
    """
    q = D(str(quantity))
    floor = D(str(min_edge_per_contract)) * q
    rows = _candidate_rows(estimate=estimate, yes_bid=yes_bid, yes_ask=yes_ask, quantity=q)
    if not rows:
        return RebateBuyerDecision(
            policy=policy, side=None, quote_price=None, fair_probability=None,
            quantity=q, maker_fee_usd=D("0"), fair_value_edge_before_rebate_usd=D("0"),
            projected_rebate_usd=D("0"), projected_combined_ev_usd=D("0"),
            actionable=False, reason="Neither maker-buy side is inside 30-49c.",
        )

    best = max(rows, key=lambda row: D(str(row["trade_edge"])))
    price = D(str(best["price"]))
    trade_edge = D(str(best["trade_edge"]))
    rebate = projected_maker_rebate(
        price=price, quantity=q, rebate_rate=rebate_rate, taker_rate=taker_rate
    )
    band_only = policy == "band_only"
    actionable = band_only or trade_edge >= floor
    return RebateBuyerDecision(
        policy=policy,
        side=str(best["side"]) if actionable else None,
        quote_price=price,
        fair_probability=D(str(best["fair"])),
        quantity=q,
        maker_fee_usd=D(str(best["maker_fee"])),
        fair_value_edge_before_rebate_usd=trade_edge,
        projected_rebate_usd=rebate,
        projected_combined_ev_usd=trade_edge + rebate,
        actionable=actionable,
        reason=(
            "Band-only comparator; rebate does not validate the directional trade."
            if band_only
            else (
                "Independent fair value clears the configured after-fee edge floor before rebate."
                if actionable
                else "Independent fair value does not clear the edge floor; projected rebate cannot rescue it."
            )
        ),
    )


def policy_matrix(
    *, estimate: FairValueEstimate, yes_bid: D, yes_ask: D, rebate_rate: D,
    quantity: D = DEFAULT_QTY, taker_rate: D = DEFAULT_TAKER_RATE,
) -> Mapping[str, RebateBuyerDecision]:
    floors = {
        "band_only": D("0"),
        "fair_0c": D("0"),
        "fair_0_5c": D("0.005"),
        "fair_1c": D("0.01"),
        "fair_2c": D("0.02"),
    }
    return {
        name: decide(
            policy=name,
            estimate=estimate,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            rebate_rate=rebate_rate,
            min_edge_per_contract=floor,
            quantity=quantity,
            taker_rate=taker_rate,
        )
        for name, floor in floors.items()
    }
