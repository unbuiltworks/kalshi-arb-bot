"""Read-only Gemini BTC5 adaptive MOC maker trade-edge diagnostic.

No credentials and no order entry. Uses public discovery/book data only.
For q=1..10 it searches maker-only quote pairs and chooses the largest safely
rescuable clip that preserves >=5c/contract after-fee completed-pair profit under
the unchanged $0.30 absolute first-fill rescue-loss cap.

Important: Gemini's 20c..80c maker-rebate band is metadata, not an execution gate.
Rebates are excluded from modeled P&L. A quote outside the rebate band may still be
valid if its trade-edge and rescue economics are safe.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json
import time
from typing import Any

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_rescue_policy as rescue
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_prediction_book_cache import GeminiTopCache

ONE = D("1")
ZERO = D("0")
MAX_LOSS = D("0.30")
EDGE_PC = D("0.05")
REBATE_MIN = D("0.20")
REBATE_MAX = D("0.80")
WAIT_SECONDS = 360.0
POLL_SECONDS = 3.0


def pair_profit(yes: D, no: D, q: D) -> D:
    return (
        q * (ONE - yes - no)
        - gemini_predictions_maker_fee(yes, q)
        - gemini_predictions_maker_fee(no, q)
    )


def grid(lo: D, hi: D, inc: D):
    x = lo
    while x <= hi + D("0.0000001"):
        yield x
        x += inc


def rebate_eligible(price: D) -> bool:
    return REBATE_MIN <= price <= REBATE_MAX


def best_for_q(*, q: int, snap: Any, inc: D) -> dict[str, Any] | None:
    qty = D(q)
    bid = D(str(snap.bid))
    ask = D(str(snap.ask))
    bid_size = D(str(snap.bid_size))
    ask_size = D(str(snap.ask_size))

    # MOC/maker-only prices may rest at the current bid (YES) or transformed NO
    # bid. Never cross the opposite top; keep at least one tick of separation.
    yes_lo = max(inc, bid)
    yes_hi = min(ONE - inc, ask - inc)
    no_lo = max(inc, ONE - ask)
    no_hi = min(ONE - inc, (ONE - bid) - inc)
    if yes_lo > yes_hi or no_lo > no_hi:
        return None

    # Rescue safety for a first YES fill depends on the YES maker price, not the NO
    # maker price, and vice versa. Precompute those routes once per quote instead of
    # re-running the expensive route search for every quote pair.
    yes_safe: dict[D, Any] = {}
    for yes in grid(yes_lo, yes_hi, inc):
        route = rescue.best_route_after_fill(
            filled_outcome="YES",
            filled_price=yes,
            quantity=qty,
            yes_bid=bid,
            yes_bid_size=bid_size,
            yes_ask=ask,
            yes_ask_size=ask_size,
            max_loss=MAX_LOSS,
            increment=inc,
        )
        if route.safe:
            yes_safe[yes] = route

    no_safe: dict[D, Any] = {}
    for no in grid(no_lo, no_hi, inc):
        route = rescue.best_route_after_fill(
            filled_outcome="NO",
            filled_price=no,
            quantity=qty,
            yes_bid=bid,
            yes_bid_size=bid_size,
            yes_ask=ask,
            yes_ask_size=ask_size,
            max_loss=MAX_LOSS,
            increment=inc,
        )
        if route.safe:
            no_safe[no] = route

    if not yes_safe or not no_safe:
        return None

    best = None
    for yes, yes_route in yes_safe.items():
        for no, no_route in no_safe.items():
            profit = pair_profit(yes, no, qty)
            edge_floor = EDGE_PC * qty
            if profit < edge_floor:
                continue

            # Spend excess edge to improve fill probability. First maximize total
            # inward quote aggression, then balance the improvement across both legs.
            yes_improve = yes - yes_lo
            no_improve = no - no_lo
            score = (yes + no, min(yes_improve, no_improve), yes_improve + no_improve)
            worst_loss = max(yes_route.loss_at_current, no_route.loss_at_current)
            row = {
                "q": q,
                "yes_quote": str(yes),
                "no_quote": str(no),
                "pair_profit": str(profit),
                "edge_floor": str(edge_floor),
                "worst_rescue_loss": str(worst_loss),
                "yes_rebate_eligible": rebate_eligible(yes),
                "no_rebate_eligible": rebate_eligible(no),
                "both_rebate_eligible": rebate_eligible(yes) and rebate_eligible(no),
                "rebates_modeled_in_profit": False,
                "yes_fill_route": yes_route.as_dict(),
                "no_fill_route": no_route.as_dict(),
                "score": [str(score[0]), str(score[1]), str(score[2])],
            }
            if best is None or score > best["_score"]:
                best = {"_score": score, **row}

    if best is not None:
        best.pop("_score", None)
    return best


async def main() -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    cache = None
    cache_symbol = None
    cache_stop = None
    cache_task = None
    polls = 0
    evaluated = []

    async def close_cache():
        nonlocal cache, cache_symbol, cache_stop, cache_task
        if cache_stop is not None:
            cache_stop.set()
        if cache_task is not None:
            cache_task.cancel()
            await asyncio.gather(cache_task, return_exceptions=True)
        cache = cache_symbol = cache_stop = cache_task = None

    try:
        while time.monotonic() < deadline:
            polls += 1
            event = await probe.base.discover_btc5m()
            if event is None or not (
                probe.MIN_SECONDS_TO_EXPIRY <= event.seconds_to_expiry <= probe.MAX_SECONDS_TO_EXPIRY
            ):
                await asyncio.sleep(POLL_SECONDS)
                continue

            if cache_symbol != event.symbol:
                await close_cache()
                cache = GeminiTopCache(event.symbol, stale_ms=1000)
                cache_symbol = event.symbol
                cache_stop = asyncio.Event()
                cache_task = asyncio.create_task(cache.run(cache_stop))

            try:
                snap = await cache.wait_fresh(timeout=4.0, max_age_ms=1000)
            except Exception as exc:
                evaluated.append(
                    {
                        "ticker": event.ticker,
                        "classification": "DEPTH_WAIT",
                        "error_type": type(exc).__name__,
                    }
                )
                await asyncio.sleep(POLL_SECONDS)
                continue

            rows = []
            for q in range(1, 11):
                row = best_for_q(q=q, snap=snap, inc=event.price_increment)
                if row:
                    rows.append(row)
            best = max(rows, key=lambda r: r["q"], default=None)
            evaluated.append(
                {
                    "ticker": event.ticker,
                    "symbol": event.symbol,
                    "seconds_to_expiry": round(event.seconds_to_expiry, 3),
                    "classification": "TRADE_EDGE_EVALUATED",
                    "book": {
                        "bid": str(snap.bid),
                        "bid_size": str(snap.bid_size),
                        "ask": str(snap.ask),
                        "ask_size": str(snap.ask_size),
                    },
                    "max_safe_q": 0 if best is None else best["q"],
                    "selected": best,
                    "best_by_q": rows,
                }
            )
            if best is not None:
                break
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await close_cache()

    print(
        "V5_3_ADAPTIVE_TRADE_EDGE_DIAG="
        + json.dumps(
            {
                "classification": "READ_ONLY_PUBLIC_NO_CREDENTIALS_NO_ORDER_ENTRY",
                "polls": polls,
                "samples": evaluated[-12:],
                "found_trade_edge_clip": any(s.get("max_safe_q", 0) > 0 for s in evaluated),
                "liquidity_rewards_modeled": False,
                "maker_rebates_modeled": False,
                "rebate_band_is_execution_gate": False,
                "order_entry": False,
                "realized_pnl": 0,
            },
            separators=(",", ":"),
            default=str,
        )
    )
    print("V5_3_ADAPTIVE_TRADE_EDGE_DIAG_COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
