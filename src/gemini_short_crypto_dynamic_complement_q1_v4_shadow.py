"""V4 SHADOW-ONLY initial-placement candidate for q=1 dynamic complement.

This module has no order-entry wrapper and is intentionally not wired into the live
launcher. It tests the next queue-position hypothesis suggested by the ETH5
front-queue census: after the existing raw >=5c eligibility gate has qualified a
book, spend available edge toward the most aggressive balanced maker prices while
preserving the existing >=2c fee-adjusted completed-pair floor.

Post-first-fill behavior is deliberately out of scope here and remains V2/V3 until
this initial-placement candidate is replay/shadow validated. Rewards/rebates are
excluded from all economics.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any

from src import gemini_short_crypto_dynamic_complement_q1 as v1
from src import gemini_incentive_mm_dynamic_complement as policy

ENTRY_PAIR_EDGE = v1.ENTRY_PAIR_EDGE
FRONT_QUEUE_TARGET_EDGE = v1.FINAL_PAIR_EDGE
FINAL_PAIR_EDGE = v1.FINAL_PAIR_EDGE
MIN_PRICE = v1.MIN_PRICE
MAX_PRICE = v1.MAX_PRICE
ONE = D("1")
CENT = D("0.01")


def front_queue_pair_model(bid: D, ask: D) -> dict[str, Any]:
    """Return maximally aggressive balanced maker quotes preserving >=2c edge.

    Raw eligibility remains a separate >=5c gate in the live architecture. This
    helper only answers where a qualifying pair could be placed without crossing
    the maker-passive ceiling or consuming the hard completed-pair edge floor.
    """
    bid, ask = D(str(bid)), D(str(ask))
    raw = dict(v1.base.pair_model(bid, ask))
    raw_edge = D(str(raw["modeled_edge"]))
    if raw_edge < ENTRY_PAIR_EDGE:
        return {
            "yes_quote": D(str(raw["yes_quote"])),
            "no_quote": D(str(raw["no_quote"])),
            "yes_fee": D(str(raw["yes_fee"])),
            "no_fee": D(str(raw["no_fee"])),
            "pair_cost": D(str(raw["pair_cost"])),
            "modeled_edge": raw_edge,
            "raw_modeled_edge": raw_edge,
            "front_queue_target_edge": FRONT_QUEUE_TARGET_EDGE,
            "quote_action": "INELIGIBLE_RAW_EDGE",
            "quote_reason": "RAW_EDGE_BELOW_5C_GATE",
            "yes_improvement": D("0"),
            "no_improvement": D("0"),
        }

    plan = policy.aggressive_entry_plan(
        yes_bid=bid,
        yes_ask=ask,
        min_entry_edge=FRONT_QUEUE_TARGET_EDGE,
        increment=CENT,
        min_price=MIN_PRICE,
        max_price=MAX_PRICE,
    )
    yes_quote = D(str(plan["yes_quote"]))
    no_quote = D(str(plan["no_quote"]))
    yes_fee = v1.base.gemini_predictions_maker_fee(yes_quote, ONE)
    no_fee = v1.base.gemini_predictions_maker_fee(no_quote, ONE)
    pair_cost = yes_quote + no_quote
    modeled_edge = ONE - pair_cost - yes_fee - no_fee
    if modeled_edge != D(str(plan["final_edge"])):
        raise AssertionError("front-queue model disagrees with policy final edge")
    if modeled_edge < FRONT_QUEUE_TARGET_EDGE:
        raise AssertionError("front-queue initial quote violated 2c hard pair floor")
    return {
        "yes_quote": yes_quote,
        "no_quote": no_quote,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "pair_cost": pair_cost,
        "modeled_edge": modeled_edge,
        "raw_yes_quote": D(str(raw["yes_quote"])),
        "raw_no_quote": D(str(raw["no_quote"])),
        "raw_modeled_edge": raw_edge,
        "front_queue_target_edge": FRONT_QUEUE_TARGET_EDGE,
        "yes_passive_ceiling": D(str(plan["yes_passive_ceiling"])),
        "no_passive_ceiling": D(str(plan["no_passive_ceiling"])),
        "yes_improvement": D(str(plan["yes_improvement"])),
        "no_improvement": D(str(plan["no_improvement"])),
        "quote_action": str(plan["action"]),
        "quote_reason": str(plan["reason"]),
    }
