"""Read-only preflight for the Gemini l5-threshold q=1 probe."""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
from typing import Any

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base5
from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1_v3 as v5
from src import gemini_short_crypto_btc15m_maker_canary_q1 as base15
from src import gemini_short_crypto_btc15m_inventory_maker_canary_q1_v3 as v15
from src import gemini_short_crypto_inventory_local_budget as budget
from src import gemini_short_crypto_local_budget as local_base
from src.gemini_l5_threshold_model import plan_threshold_quotes
from src.short_crypto_incentive_snapshot import fetch_snapshot


async def _horizon(name: str) -> dict[str, Any]:
    if name == "5m":
        event = await base5.discover_btc5m()
        fresh = v5.rest_fresh_top
    elif name == "15m":
        event = await base15.discover_btc15m()
        fresh = v15.rest_fresh_top
    else:
        raise ValueError(name)
    if event is None:
        return {"horizon": name, "market": None, "actionable_live_q1": False}
    bid, bid_size, ask, ask_size = await fresh(event.symbol)
    plan = plan_threshold_quotes(bid, ask, price_increment=event.price_increment)
    return {
        "horizon": name,
        "market": event.ticker,
        "symbol": event.symbol,
        "seconds_to_expiry": event.seconds_to_expiry,
        "quantity_minimum": str(event.quantity_minimum),
        "quantity_increment": str(event.quantity_increment),
        "price_increment": str(event.price_increment),
        "yes_bid": str(bid),
        "yes_bid_size": str(bid_size),
        "yes_ask": str(ask),
        "yes_ask_size": str(ask_size),
        "yes_mid": str(plan.yes_mid),
        "no_mid": str(plan.no_mid),
        "favored_side": plan.favored_side,
        "favored_probability": str(plan.favored_probability),
        "shadow_target_units": plan.shadow_target_units,
        "live_units": 1,
        "quote_skew_ticks": plan.quote_skew_ticks,
        "planned_yes_quote": str(plan.yes_quote),
        "planned_no_quote": str(plan.no_quote),
        "planned_pair_edge": str(plan.modeled_edge),
        "yes_rebate_eligible": plan.yes_rebate_eligible,
        "no_rebate_eligible": plan.no_rebate_eligible,
        "q1_modeled_rebate_rounded_total": str(plan.q1_yes_rebate_rounded + plan.q1_no_rebate_rounded),
        "actionable_live_q1": plan.actionable_live_q1,
    }


async def main_async() -> None:
    terms_task = asyncio.create_task(base5._gemini_terms_ok())
    available_task = asyncio.create_task(base5._gemini_available_usd())
    incentive_task = asyncio.create_task(fetch_snapshot())
    horizons = await asyncio.gather(_horizon("5m"), _horizon("15m"))
    terms, available, incentives = await asyncio.gather(terms_task, available_task, incentive_task)

    local = local_base.local_open_state()
    clean_account: dict[str, Any]
    try:
        snap = await budget.assert_clean_account()
        clean_account = {"clean": True, **snap}
    except Exception as exc:
        clean_account = {"clean": False, "error_type": type(exc).__name__, "error": str(exc)[:240]}

    out = {
        "type": "GEMINI_L5_THRESHOLD_Q1_PREFLIGHT",
        "order_entry": False,
        "mutation": False,
        "terms_ok": bool(terms),
        "available_usd": str(D(str(available))),
        "local_open_state_present": local is not None,
        "account": clean_account,
        "horizons": horizons,
        "incentives": incentives,
    }
    print(json.dumps(out, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    asyncio.run(main_async())
