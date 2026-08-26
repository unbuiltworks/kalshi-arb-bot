"""v3 bounded q1 Polymarket MLB maker campaign.

Cycles through the strongest strict relay candidates one at a time. Each attempt:
- refreshes exact books;
- improves the maker quote toward the ask only as far as the >=3c after-fee floor permits;
- previews maker + FOK hedge;
- rests one post-only q1 maker for <=4 seconds;
- cancels it before trying the next candidate if unfilled;
- on a fill, immediately revalidates and FOK-hedges q1, otherwise flattens.

At most 8 maker attempts per startup. No simultaneous relay makers.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
from typing import Any, Mapping

from polymarket_us import PolymarketUS
from polymarket_us.errors import NotFoundError

from src.sports_kp_poly_relay_live_q1 import (
    D, ONE, TARGET, TAKER_FEE_FLOOR, _amount, _ask, _bid, _books,
    _feed_market_map, _locked, _payload, _score, _tradeable_exact, _unwrap_order,
)
from src.sports_kp_poly_relay_live_q1_v2 import _actual_order_id, _created_order, _resolve_open_order_id

MAX_ATTEMPTS = 8
TICK = D("0.01")
POLL_SECONDS = 4.0


def _maker_quote(book: Any, side: str, hedge_ask: D) -> D | None:
    bid = _bid(book, side)
    ask, _ = _ask(book, side)
    max_for_floor = ONE - hedge_ask - TAKER_FEE_FLOOR - TARGET
    # Stay one tick below the contra ask so participateDontInitiate remains maker.
    improved = min(ask - TICK, max_for_floor)
    px = max(bid, improved)
    if px <= 0 or px >= ask or _locked(px, hedge_ask) < TARGET:
        return None
    return px


async def _cancel_scoped(client: PolymarketUS, oid: str, slug: str) -> None:
    try:
        await asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug})
    except Exception:
        await asyncio.to_thread(client.orders.cancel_all, {"slugs": [slug]})


async def _attempt(client: PolymarketUS, row: Mapping[str, Any], feed: Mapping[str, Mapping[str, Any]], attempt_no: int) -> dict[str, Any]:
    maker_slug, hedge_slug = str(row["maker_slug"]), str(row["hedge_slug"])
    latest = await _books({maker_slug, hedge_slug})
    mb, hb = latest.get(maker_slug), latest.get(hedge_slug)
    if mb is None or hb is None:
        return {"status": "SKIP_BOOK_MISSING", "attempt": attempt_no}
    hedge_ask, hedge_depth = _ask(hb, str(row["hedge_side"]))
    if hedge_depth < ONE:
        return {"status": "SKIP_HEDGE_DEPTH", "attempt": attempt_no}
    maker_px = _maker_quote(mb, str(row["maker_side"]), hedge_ask)
    if maker_px is None:
        return {"status": "SKIP_FLOOR", "attempt": attempt_no}

    low_resp, high_resp = await asyncio.gather(
        asyncio.to_thread(client.markets.retrieve_by_slug, row["low_slug"]),
        asyncio.to_thread(client.markets.retrieve_by_slug, row["high_slug"]),
    )
    _tradeable_exact(feed[row["low_slug"]], feed[row["high_slug"]], low_resp, high_resp)
    maker_payload = _payload(maker_slug, str(row["maker_side"]), maker_px, maker=True)
    hedge_payload = _payload(hedge_slug, str(row["hedge_side"]), hedge_ask, maker=False)
    await asyncio.gather(
        asyncio.to_thread(client.orders.preview, {"request": maker_payload}),
        asyncio.to_thread(client.orders.preview, {"request": hedge_payload}),
    )

    created = await asyncio.to_thread(client.orders.create, maker_payload)
    oid = await _resolve_open_order_id(client, maker_slug, _actual_order_id(created))
    maker_order: Mapping[str, Any] = _created_order(created)
    if not oid:
        await asyncio.to_thread(client.orders.cancel_all, {"slugs": [maker_slug]})
        return {"status": "UNKNOWN_MAKER_ID_SCOPED_CANCEL", "attempt": attempt_no, "order_entry": True}

    loops = max(1, int(POLL_SECONDS / 0.25))
    for _ in range(loops):
        try:
            maker_order = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, oid))
        except NotFoundError:
            response = await asyncio.to_thread(client.orders.list, {"slugs": [maker_slug]})
            matching = [o for o in response.get("orders") or [] if isinstance(o, Mapping) and str(o.get("id") or "") == oid]
            if matching:
                maker_order = matching[0]
        cum = D(str(maker_order.get("cumQuantity") or 0))
        state = str(maker_order.get("state") or "")
        if cum >= ONE or state in {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED"}:
            break
        await asyncio.sleep(0.25)

    maker_cum = D(str(maker_order.get("cumQuantity") or 0))
    if maker_cum < ONE:
        await _cancel_scoped(client, oid, maker_slug)
        # Verify this slug is clear before rotating.
        open_resp = await asyncio.to_thread(client.orders.list, {"slugs": [maker_slug]})
        still_open = [o for o in open_resp.get("orders") or [] if isinstance(o, Mapping)]
        if still_open:
            await asyncio.to_thread(client.orders.cancel_all, {"slugs": [maker_slug]})
        return {"status": "MAKER_NOT_FILLED_CANCELED", "attempt": attempt_no, "maker_order_id": oid,
                "maker_slug": maker_slug, "maker_price": str(maker_px), "hypothetical_locked": str(_locked(maker_px, hedge_ask)),
                "order_entry": True, "realized": False}

    mavg_yes = _amount(maker_order.get("avgPx"))
    maker_native = (mavg_yes if str(row["maker_side"]) == "YES" else ONE - mavg_yes) if mavg_yes is not None else maker_px

    # Filled maker: exact hedge revalidation, then immediate FOK or flatten.
    hedge_book = (await _books({hedge_slug})).get(hedge_slug)
    if hedge_book is None:
        close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": maker_slug, "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
        return {"status": "MAKER_FILLED_FLATTENED_NO_HEDGE_BOOK", "attempt": attempt_no, "maker_order_id": oid,
                "close_response_present": bool(close), "order_entry": True, "realized": False}
    hpx, hdepth = _ask(hedge_book, str(row["hedge_side"]))
    if hdepth < ONE or _locked(maker_native, hpx) < TARGET:
        close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": maker_slug, "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
        return {"status": "MAKER_FILLED_FLATTENED_EDGE_GONE", "attempt": attempt_no, "maker_order_id": oid,
                "close_response_present": bool(close), "order_entry": True, "realized": False}

    hedge_payload = _payload(hedge_slug, str(row["hedge_side"]), hpx, maker=False)
    await asyncio.to_thread(client.orders.preview, {"request": hedge_payload})
    hcreated = await asyncio.to_thread(client.orders.create, hedge_payload)
    hid = _actual_order_id(hcreated)
    horder = _created_order(hcreated)
    hcum = D(str(horder.get("cumQuantity") or 0))
    if hid and hcum < ONE:
        try:
            horder = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, hid))
            hcum = D(str(horder.get("cumQuantity") or 0))
        except NotFoundError:
            pass
    if hcum < ONE:
        close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": maker_slug, "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
        return {"status": "HEDGE_NOT_CONFIRMED_MAKER_FLATTENED", "attempt": attempt_no, "maker_order_id": oid, "hedge_order_id": hid,
                "close_response_present": bool(close), "order_entry": True, "realized": False}

    try:
        maker_order = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, oid))
    except NotFoundError:
        pass
    if hid:
        try:
            horder = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, hid))
        except NotFoundError:
            pass
    mavg_yes, havg_yes = _amount(maker_order.get("avgPx")), _amount(horder.get("avgPx"))
    if mavg_yes is None or havg_yes is None:
        return {"status": "BOTH_FILLED_RECONCILIATION_PENDING", "attempt": attempt_no, "maker_order_id": oid, "hedge_order_id": hid,
                "order_entry": True, "realized": False}
    mcost = mavg_yes if str(row["maker_side"]) == "YES" else ONE - mavg_yes
    hcost = havg_yes if str(row["hedge_side"]) == "YES" else ONE - havg_yes
    mfee = _amount(maker_order.get("commissionNotionalTotalCollected")) or D("0")
    hfee = _amount(horder.get("commissionNotionalTotalCollected")) or D("0")
    net = ONE - mcost - hcost - mfee - hfee
    return {"status": "BOTH_FILLED_RECONCILED", "attempt": attempt_no, "maker_order_id": oid, "hedge_order_id": hid,
            "maker_slug": maker_slug, "hedge_slug": hedge_slug, "actual_net_locked_profit": str(net),
            "actual_total_commission": str(mfee + hfee), "order_entry": True, "realized": bool(net > D("0"))}


async def run_once() -> dict[str, Any]:
    feed = await _feed_market_map()
    books = await _books(set(feed))
    scored = _score(feed, books)
    if not scored:
        out = {"status": "NO_EXACT_RELAY_Q1", "order_entry": False}
        print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V3 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=6.0)
    try:
        balances = await asyncio.to_thread(client.account.balances)
        first = scored[0]
        if os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") != "1":
            out = {"status": "READY_CAMPAIGN_Q1", "candidates": len(scored),
                   "best_locked": str(first["locked_profit"]), "best_hedge_depth": str(first["hedge_depth"]),
                   "balance_response_present": bool(balances), "max_attempts": MAX_ATTEMPTS, "order_entry": False, "realized": False}
            print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V3 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        attempts: list[dict[str, Any]] = []
        for idx, row in enumerate(scored[:MAX_ATTEMPTS], start=1):
            try:
                result = await _attempt(client, row, feed, idx)
            except Exception as exc:
                # Always clear the selected maker slug before rotating after an exception.
                try:
                    await asyncio.to_thread(client.orders.cancel_all, {"slugs": [row["maker_slug"]]})
                except Exception:
                    pass
                result = {"status": "ATTEMPT_ERROR_SCOPED_CANCEL", "attempt": idx, "error_type": type(exc).__name__, "order_entry": True, "realized": False}
            attempts.append(result)
            if result.get("status") not in {"MAKER_NOT_FILLED_CANCELED", "SKIP_BOOK_MISSING", "SKIP_HEDGE_DEPTH", "SKIP_FLOOR"}:
                break
        out = {"status": "CAMPAIGN_COMPLETE", "attempts": attempts, "attempt_count": len(attempts),
               "realized": any(bool(a.get("realized")) for a in attempts)}
        print("SPORTS_POLY_RELAY_CAMPAIGN_Q1_V3 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
