"""v2 bounded Polymarket MLB q1 probe with server order-id extraction.

The create endpoint may return a top-level request/execution id while the actual
order id is nested in executions[].order.id. v2 extracts the real order id and
falls back to the open-orders endpoint before polling/reconciliation.
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
    D, ONE, TARGET, RELAY_PAIRS, _amount, _ask, _books, _feed_market_map,
    _locked, _payload, _score, _tradeable_exact, _unwrap_order,
)


def _actual_order_id(created: Mapping[str, Any]) -> str:
    for ex in created.get("executions") or []:
        if not isinstance(ex, Mapping):
            continue
        order = ex.get("order")
        if isinstance(order, Mapping) and order.get("id"):
            return str(order["id"])
    return str(created.get("id") or "")


def _created_order(created: Mapping[str, Any]) -> Mapping[str, Any]:
    for ex in reversed(created.get("executions") or []):
        if isinstance(ex, Mapping) and isinstance(ex.get("order"), Mapping):
            return ex["order"]
    return {}


async def _resolve_open_order_id(client: PolymarketUS, slug: str, preferred: str) -> str:
    if preferred:
        try:
            await asyncio.to_thread(client.orders.retrieve, preferred)
            return preferred
        except NotFoundError:
            pass
    for _ in range(8):
        response = await asyncio.to_thread(client.orders.list, {"slugs": [slug]})
        for raw in response.get("orders") or []:
            if isinstance(raw, Mapping) and str(raw.get("marketSlug") or "") == slug and raw.get("id"):
                return str(raw["id"])
        await asyncio.sleep(0.15)
    return preferred


async def run_once() -> dict[str, Any]:
    feed = await _feed_market_map()
    books = await _books(set(feed))
    scored = _score(feed, books)
    if not scored:
        out = {"status": "NO_EXACT_RELAY_Q1", "relay_markets_found": len(feed), "books_found": len(books), "order_entry": False}
        print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    row = scored[0]
    low_feed, high_feed = feed[row["low_slug"]], feed[row["high_slug"]]
    client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=6.0)
    try:
        low_resp, high_resp = await asyncio.gather(
            asyncio.to_thread(client.markets.retrieve_by_slug, row["low_slug"]),
            asyncio.to_thread(client.markets.retrieve_by_slug, row["high_slug"]),
        )
        _tradeable_exact(low_feed, high_feed, low_resp, high_resp)
        maker = _payload(row["maker_slug"], row["maker_side"], row["maker_price"], maker=True)
        hedge = _payload(row["hedge_slug"], row["hedge_side"], row["hedge_ask"], maker=False)
        balances, mpv, hpv = await asyncio.gather(
            asyncio.to_thread(client.account.balances),
            asyncio.to_thread(client.orders.preview, {"request": maker}),
            asyncio.to_thread(client.orders.preview, {"request": hedge}),
        )
        base = {"candidate": {k: str(v) if isinstance(v, Decimal) else v for k, v in row.items()},
                "balance_response_present": bool(balances), "maker_preview_ok": bool(mpv), "hedge_preview_ok": bool(hpv)}
        if os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") != "1":
            out = {"status": "READY_EXACT_RELAY_Q1", **base, "order_entry": False, "realized": False}
            print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        # Re-read exact books and recompute the same selected direction immediately.
        latest = await _books({str(row["maker_slug"]), str(row["hedge_slug"])})
        mb, hb = latest.get(str(row["maker_slug"])), latest.get(str(row["hedge_slug"]))
        if mb is None or hb is None:
            raise PermissionError("final exact books unavailable")
        from src.sports_kp_poly_relay_live_q1 import _bid
        maker_px = _bid(mb, str(row["maker_side"]))
        hedge_px, hedge_depth = _ask(hb, str(row["hedge_side"]))
        if hedge_depth < ONE or _locked(maker_px, hedge_px) < TARGET:
            out = {"status": "LIVE_ABORT_FINAL_ECONOMICS", **base, "order_entry": False, "realized": False}
            print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out
        maker = _payload(row["maker_slug"], row["maker_side"], maker_px, maker=True)
        hedge_preview = _payload(row["hedge_slug"], row["hedge_side"], hedge_px, maker=False)
        await asyncio.gather(
            asyncio.to_thread(client.orders.preview, {"request": maker}),
            asyncio.to_thread(client.orders.preview, {"request": hedge_preview}),
        )

        created = await asyncio.to_thread(client.orders.create, maker)
        preferred_id = _actual_order_id(created)
        maker_order = _created_order(created)
        order_id = await _resolve_open_order_id(client, str(row["maker_slug"]), preferred_id)
        if not order_id:
            # Unknown mutation state: do a scoped cancel-all on exactly this relay slug.
            await asyncio.to_thread(client.orders.cancel_all, {"slugs": [row["maker_slug"]]})
            raise RuntimeError("unable to resolve maker order id; scoped cancel-all requested")

        # 5-second max resting window. The real order id is used for every read/cancel.
        for _ in range(20):
            try:
                detail = await asyncio.to_thread(client.orders.retrieve, order_id)
                maker_order = _unwrap_order(detail)
            except NotFoundError:
                response = await asyncio.to_thread(client.orders.list, {"slugs": [row["maker_slug"]]})
                matching = [o for o in response.get("orders") or [] if isinstance(o, Mapping) and str(o.get("id") or "") == order_id]
                if matching:
                    maker_order = matching[0]
            cum = D(str(maker_order.get("cumQuantity") or 0))
            state = str(maker_order.get("state") or "")
            if cum >= ONE or state in {"ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED"}:
                break
            await asyncio.sleep(0.25)

        maker_cum = D(str(maker_order.get("cumQuantity") or 0))
        if maker_cum < ONE:
            try:
                await asyncio.to_thread(client.orders.cancel, order_id, {"marketSlug": row["maker_slug"]})
            except Exception:
                await asyncio.to_thread(client.orders.cancel_all, {"slugs": [row["maker_slug"]]})
            out = {"status": "MAKER_Q1_NOT_FILLED_CANCELED", **base, "maker_order_id": order_id, "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        mavg_yes = _amount(maker_order.get("avgPx"))
        maker_native = (mavg_yes if row["maker_side"] == "YES" else ONE - mavg_yes) if mavg_yes is not None else maker_px

        # Maker is filled. Exact hedge book must still preserve the floor.
        hedge_book = (await _books({str(row["hedge_slug"])})).get(str(row["hedge_slug"]))
        if hedge_book is None:
            close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": row["maker_slug"], "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
            out = {"status": "MAKER_FILLED_EMERGENCY_CLOSED_NO_HEDGE_BOOK", **base, "maker_order_id": order_id, "close_response_present": bool(close), "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out
        hedge_now, hedge_depth_now = _ask(hedge_book, str(row["hedge_side"]))
        if hedge_depth_now < ONE or _locked(maker_native, hedge_now) < TARGET:
            close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": row["maker_slug"], "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
            out = {"status": "MAKER_FILLED_EMERGENCY_CLOSED_EDGE_GONE", **base, "maker_order_id": order_id, "close_response_present": bool(close), "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        hedge_payload = _payload(row["hedge_slug"], row["hedge_side"], hedge_now, maker=False)
        await asyncio.to_thread(client.orders.preview, {"request": hedge_payload})
        hcreated = await asyncio.to_thread(client.orders.create, hedge_payload)
        horder = _created_order(hcreated)
        hid = _actual_order_id(hcreated)
        hcum = D(str(horder.get("cumQuantity") or 0))
        if hid and hcum < ONE:
            try:
                horder = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, hid))
                hcum = D(str(horder.get("cumQuantity") or 0))
            except NotFoundError:
                pass
        if hcum < ONE:
            close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": row["maker_slug"], "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
            out = {"status": "HEDGE_FOK_NOT_CONFIRMED_MAKER_CLOSED", **base, "maker_order_id": order_id, "hedge_order_id": hid,
                   "close_response_present": bool(close), "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        # Use execution order snapshots for immediate fill reconciliation; retrieve if available.
        try:
            maker_order = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, order_id))
        except NotFoundError:
            pass
        if hid:
            try:
                horder = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, hid))
            except NotFoundError:
                pass
        mavg_yes, havg_yes = _amount(maker_order.get("avgPx")), _amount(horder.get("avgPx"))
        if mavg_yes is None or havg_yes is None:
            out = {"status": "BOTH_FILLED_RECONCILIATION_PENDING", **base, "maker_order_id": order_id, "hedge_order_id": hid, "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out
        mcost = mavg_yes if row["maker_side"] == "YES" else ONE - mavg_yes
        hcost = havg_yes if row["hedge_side"] == "YES" else ONE - havg_yes
        mfee = _amount(maker_order.get("commissionNotionalTotalCollected")) or D("0")
        hfee = _amount(horder.get("commissionNotionalTotalCollected")) or D("0")
        net = ONE - mcost - hcost - mfee - hfee
        out = {"status": "BOTH_FILLED_RECONCILED", **base, "maker_order_id": order_id, "hedge_order_id": hid,
               "actual_net_locked_profit": str(net), "actual_total_commission": str(mfee + hfee),
               "order_entry": True, "realized": bool(net > D("0"))}
        print("SPORTS_POLY_RELAY_Q1_V2 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
