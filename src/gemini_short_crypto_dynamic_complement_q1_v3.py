"""V3 q=1 dynamic complement: spend excess entry edge for queue position.

V2 owns post-first-fill complement management. V3 changes only the initial maker
quotes: the raw top-of-book pair must still satisfy the existing >=5c live gate,
but when raw edge is >=10c we spend excess edge symmetrically on better maker
prices while preserving at least 10c after maker fees. Raw opportunities with
5-10c edge are left unchanged rather than made worse.

No taker path is introduced. Orders remain BUY LIMIT MOC, q=1 per outcome, one
campaign, with the same V2 post-fill rescue and settlement plumbing.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any

from src import gemini_short_crypto_dynamic_complement_q1 as v1
from src import gemini_short_crypto_dynamic_complement_q1_v2 as v2
from src import gemini_incentive_mm_dynamic_complement as policy

ENTRY_PAIR_EDGE = v1.ENTRY_PAIR_EDGE
INITIAL_TARGET_EDGE = D("0.10")
FINAL_PAIR_EDGE = v1.FINAL_PAIR_EDGE
INITIAL_WAIT_SECONDS = v1.INITIAL_WAIT_SECONDS
RESCUE_SECONDS = v1.RESCUE_SECONDS
REPRICE_INTERVAL_SECONDS = v1.REPRICE_INTERVAL_SECONDS
MAX_REPRICES = v1.MAX_REPRICES
MIN_PRICE = v1.MIN_PRICE
MAX_PRICE = v1.MAX_PRICE
STOP_BEFORE_EXPIRY_SECONDS = v1.STOP_BEFORE_EXPIRY_SECONDS
LIVE_ENV = v1.LIVE_ENV
CANARY_ID_ENV = v1.CANARY_ID_ENV
CANARY_ARM_ENV = v1.CANARY_ARM_ENV
ONE = D("1")
CENT = D("0.01")


def edge_spending_pair_model(bid: D, ask: D) -> dict[str, Any]:
    """Return initial q1 maker economics after safely spending excess raw edge."""
    bid, ask = D(str(bid)), D(str(ask))
    raw = dict(v1.base.pair_model(bid, ask))
    plan = policy.aggressive_entry_plan(
        yes_bid=bid,
        yes_ask=ask,
        min_entry_edge=INITIAL_TARGET_EDGE,
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
        raise AssertionError("edge-spending model disagrees with policy final edge")
    if plan["action"] == "IMPROVE_INITIAL_MAKERS" and modeled_edge < INITIAL_TARGET_EDGE:
        raise AssertionError("edge-spending initial quote violated 10c target reserve")
    return {
        "yes_quote": yes_quote,
        "no_quote": no_quote,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "pair_cost": pair_cost,
        "modeled_edge": modeled_edge,
        "spread": ask - bid,
        "raw_yes_quote": D(str(raw["yes_quote"])),
        "raw_no_quote": D(str(raw["no_quote"])),
        "raw_modeled_edge": D(str(raw["modeled_edge"])),
        "initial_target_edge": INITIAL_TARGET_EDGE,
        "yes_improvement": D(str(plan["yes_improvement"])),
        "no_improvement": D(str(plan["no_improvement"])),
        "quote_action": str(plan["action"]),
        "quote_reason": str(plan["reason"]),
    }


async def run() -> None:
    """Run V2 rescue with V3 initial quote shaping, restoring module state after."""
    original_model = v1.raw_pair_model
    try:
        v1.raw_pair_model = edge_spending_pair_model
        await v2.run()
    finally:
        v1.raw_pair_model = original_model
