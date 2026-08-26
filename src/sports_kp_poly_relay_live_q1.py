"""Bounded Polymarket US same-venue MLB maker->hedge q1 live probe.

Fail-closed design:
- strict canonical same-game/player/stat monotone threshold identity from /v1/events;
- exact current books determine every price/depth decision;
- authenticated exact market retrieval + both order previews before mutation;
- one post-only q1 maker at a time, 5s TTL;
- after maker fill, exact hedge book is re-read and must still preserve >=3c using
  a conservative 1c taker-fee allowance; otherwise the maker position is closed;
- FOK q1 hedge only after confirmed maker fill.

Live mutation is gated by SPORTS_POLY_RELAY_LIVE_Q1=1. Without it this is a
mutation-free authenticated preflight.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any, Mapping

import httpx
from polymarket_us import PolymarketUS

from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

D = Decimal
ONE = D("1")
TARGET = D("0.03")
TAKER_FEE_FLOOR = D("0.01")
BASE = "https://gateway.polymarket.us"
MAKER_TTL_SECONDS = D("5")

# Relay only semantic identities from the current broad census. Prices are NEVER
# relayed; every run re-reads exact books. Ordered low-threshold, high-threshold.
RELAY_PAIRS = (
    ("astatc-mlb-min-sd-2026-08-23-k-walbue-gte2", "astatc-mlb-min-sd-2026-08-23-k-walbue-gte3"),
    ("astatc-mlb-chc-sea-2026-08-23-hrr-colyou-gte1", "astatc-mlb-chc-sea-2026-08-23-hrr-colyou-gte2"),
    ("astatc-mlb-cin-az-2026-08-23-hrr-jorlaw-gte1", "astatc-mlb-cin-az-2026-08-23-hrr-jorlaw-gte2"),
    ("astatc-mlb-cle-col-2026-08-23-hrr-natlow-gte2", "astatc-mlb-cle-col-2026-08-23-hrr-natlow-gte3"),
    ("astatc-mlb-cin-az-2026-08-23-hrr-corcar-gte1", "astatc-mlb-cin-az-2026-08-23-hrr-corcar-gte2"),
    ("astatc-mlb-cin-az-2026-08-23-hrr-jjble-gte1", "astatc-mlb-cin-az-2026-08-23-hrr-jjble-gte2"),
    ("astatc-mlb-laa-tex-2026-08-23-tb-jakbur-gte2", "astatc-mlb-laa-tex-2026-08-23-tb-jakbur-gte3"),
    ("astatc-mlb-cle-col-2026-08-23-hrr-wilcas-gte1", "astatc-mlb-cle-col-2026-08-23-hrr-wilcas-gte2"),
    ("astatc-mlb-cle-col-2026-08-23-hrr-micmon-gte1", "astatc-mlb-cle-col-2026-08-23-hrr-micmon-gte2"),
    ("astatc-mlb-cle-col-2026-08-23-hrr-jakmcc-gte1", "astatc-mlb-cle-col-2026-08-23-hrr-jakmcc-gte2"),
    ("astatc-mlb-pit-lad-2026-08-23-hrr-kyltuc-gte1", "astatc-mlb-pit-lad-2026-08-23-hrr-kyltuc-gte2"),
    ("astatc-mlb-pit-lad-2026-08-23-hrr-teoher-gte1", "astatc-mlb-pit-lad-2026-08-23-hrr-teoher-gte2"),
    ("astatc-mlb-atl-mil-2026-08-23-tb-matols-gte3", "astatc-mlb-atl-mil-2026-08-23-tb-matols-gte4"),
)


def _amount(v: Any) -> D | None:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:
        return None


def _unwrap_market(resp: Mapping[str, Any]) -> Mapping[str, Any]:
    market = resp.get("market")
    return market if isinstance(market, Mapping) else resp


def _unwrap_order(resp: Mapping[str, Any]) -> Mapping[str, Any]:
    order = resp.get("order")
    return order if isinstance(order, Mapping) else resp


def _locked(maker_native: D, hedge_native: D) -> D:
    return ONE - maker_native - hedge_native - TAKER_FEE_FLOOR


def _ask(book: TopBook, side: str) -> tuple[D, D]:
    return (book.yes_ask, book.yes_depth) if side == "YES" else (book.no_ask, book.no_depth)


def _bid(book: TopBook, side: str) -> D:
    return ONE - (book.no_ask if side == "YES" else book.yes_ask)


def _yes_price(side: str, native: D) -> D:
    return native if side == "YES" else ONE - native


def _payload(slug: str, side: str, native: D, *, maker: bool) -> dict[str, Any]:
    return {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "YES" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(_yes_price(side, native)), "currency": "USD"},
        "quantity": 1,
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL" if maker else "TIME_IN_FORCE_FILL_OR_KILL",
        "participateDontInitiate": bool(maker),
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": not maker,
        **({"maxBlockTime": "2"} if not maker else {}),
    }


def _strict(low: Mapping[str, Any], high: Mapping[str, Any]) -> bool:
    li, hi = _poly_identity(low), _poly_identity(high)
    if li is None or hi is None:
        return False
    lf, lp, lt, ld, lx = li
    hf, hp, ht, hd, hx = hi
    return (lf, lp.lower(), set(lt), ld) == (hf, hp.lower(), set(ht), hd) and lx < hx


async def _feed_market_map() -> dict[str, dict[str, Any]]:
    horizon = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    wanted = {s for pair in RELAY_PAIRS for s in pair}
    found: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=10.0) as http:
        for page in range(12):
            try:
                r = await http.get(f"{BASE}/v1/events", params={
                    "limit": 100, "offset": page * 100, "active": "true", "closed": "false",
                    "ended": "false", "categories": "sports", "startTimeMax": horizon,
                    "orderBy": "startTime", "orderDirection": "desc",
                })
                r.raise_for_status()
            except Exception:
                break
            body = r.json() if r.content else {}
            rows = body.get("events") or [] if isinstance(body, Mapping) else []
            for event in rows:
                if not isinstance(event, Mapping):
                    continue
                for m in event.get("markets") or []:
                    if not isinstance(m, Mapping):
                        continue
                    slug = str(m.get("slug") or "")
                    if slug in wanted:
                        found[slug] = dict(m)
            if len(found) == len(wanted) or len(rows) < 100:
                break
    return found


async def _books(slugs: set[str]) -> dict[str, TopBook]:
    out: dict[str, TopBook] = {}
    async with httpx.AsyncClient(timeout=4.0) as http:
        sem = asyncio.Semaphore(8)
        async def one(slug: str) -> None:
            async with sem:
                try:
                    r = await http.get(f"{BASE}/v1/markets/{slug}/book")
                    if r.status_code == 200:
                        b = poly_top_book(r.json())
                        if b is not None:
                            out[slug] = b
                except Exception:
                    pass
        await asyncio.gather(*(one(s) for s in slugs))
    return out


def _score(feed: Mapping[str, Mapping[str, Any]], books: Mapping[str, TopBook]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for low_slug, high_slug in RELAY_PAIRS:
        low, high = feed.get(low_slug), feed.get(high_slug)
        lb, hb = books.get(low_slug), books.get(high_slug)
        if low is None or high is None or lb is None or hb is None or not _strict(low, high):
            continue
        for direction, maker_slug, maker_side, maker_px, hedge_slug, hedge_side, hedge_px, hedge_depth in (
            ("MAKER_LOW_HEDGE_HIGH", low_slug, "YES", _bid(lb, "YES"), high_slug, "NO", *_ask(hb, "NO")),
            ("MAKER_HIGH_HEDGE_LOW", high_slug, "NO", _bid(hb, "NO"), low_slug, "YES", *_ask(lb, "YES")),
        ):
            locked = _locked(maker_px, hedge_px)
            if maker_px <= 0 or maker_px >= ONE or hedge_depth < ONE or locked < TARGET:
                continue
            rows.append({
                "direction": direction, "low_slug": low_slug, "high_slug": high_slug,
                "maker_slug": maker_slug, "maker_side": maker_side, "maker_price": maker_px,
                "hedge_slug": hedge_slug, "hedge_side": hedge_side, "hedge_ask": hedge_px,
                "hedge_depth": hedge_depth, "locked_profit": locked,
            })
    rows.sort(key=lambda r: (r["locked_profit"], r["hedge_depth"]), reverse=True)
    return rows


def _tradeable_exact(feed_low: Mapping[str, Any], feed_high: Mapping[str, Any], low_resp: Mapping[str, Any], high_resp: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not _strict(feed_low, feed_high):
        raise PermissionError("strict semantic pair failed")
    low, high = _unwrap_market(low_resp), _unwrap_market(high_resp)
    if str(low.get("slug") or "") != str(feed_low.get("slug") or "") or str(high.get("slug") or "") != str(feed_high.get("slug") or ""):
        raise PermissionError("authenticated exact slug mismatch")
    for m in (low, high):
        if m.get("active") is False or m.get("closed") is True or m.get("archived") is True:
            raise PermissionError("authenticated exact market not tradeable")
    le, he = str(low.get("eventSlug") or ""), str(high.get("eventSlug") or "")
    if le and he and le != he:
        raise PermissionError("authenticated exact parent mismatch")
    return low, high


async def _current_selected(row: Mapping[str, Any]) -> tuple[D, D, D] | None:
    books = await _books({str(row["maker_slug"]), str(row["hedge_slug"])})
    mb, hb = books.get(str(row["maker_slug"])), books.get(str(row["hedge_slug"]))
    if mb is None or hb is None:
        return None
    mp = _bid(mb, str(row["maker_side"]))
    hp, hd = _ask(hb, str(row["hedge_side"]))
    return mp, hp, hd


async def run_once() -> dict[str, Any]:
    feed = await _feed_market_map()
    books = await _books(set(feed))
    scored = _score(feed, books)
    if not scored:
        out = {"status": "NO_EXACT_RELAY_Q1", "relay_markets_found": len(feed), "books_found": len(books), "order_entry": False}
        print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
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
        base = {
            "candidate": {k: str(v) if isinstance(v, D) else v for k, v in row.items()},
            "balance_response_present": bool(balances), "maker_preview_ok": bool(mpv), "hedge_preview_ok": bool(hpv),
        }
        if os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") != "1":
            out = {"status": "READY_EXACT_RELAY_Q1", **base, "order_entry": False, "realized": False}
            print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        # Final pre-submit exact book read; no stale relay prices enter mutation.
        now = await _current_selected(row)
        if now is None:
            raise PermissionError("final exact books unavailable")
        maker_px, hedge_px, hedge_depth = now
        if hedge_depth < ONE or _locked(maker_px, hedge_px) < TARGET:
            out = {"status": "LIVE_ABORT_FINAL_ECONOMICS", **base, "order_entry": False, "realized": False}
            print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out
        maker = _payload(row["maker_slug"], row["maker_side"], maker_px, maker=True)
        hedge_preview = _payload(row["hedge_slug"], row["hedge_side"], hedge_px, maker=False)
        await asyncio.gather(
            asyncio.to_thread(client.orders.preview, {"request": maker}),
            asyncio.to_thread(client.orders.preview, {"request": hedge_preview}),
        )

        created = await asyncio.to_thread(client.orders.create, maker)
        order_id = str(created.get("id") or "")
        if not order_id:
            raise RuntimeError("maker create returned no id")
        maker_order: Mapping[str, Any] = {}
        for _ in range(20):
            detail = await asyncio.to_thread(client.orders.retrieve, order_id)
            maker_order = _unwrap_order(detail)
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
                pass
            out = {"status": "MAKER_Q1_NOT_FILLED_CANCELED", **base, "maker_order_id": order_id, "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        avg_yes = _amount(maker_order.get("avgPx"))
        maker_native_actual = _yes_price(str(row["maker_side"]), avg_yes) if avg_yes is not None and row["maker_side"] == "YES" else None
        if avg_yes is not None:
            maker_native_actual = avg_yes if row["maker_side"] == "YES" else ONE - avg_yes
        if maker_native_actual is None:
            maker_native_actual = maker_px

        # Maker filled. Revalidate hedge immediately. If it no longer preserves the
        # floor, flatten instead of leaving naked sports exposure.
        hb = (await _books({str(row["hedge_slug"])})).get(str(row["hedge_slug"]))
        if hb is None:
            await asyncio.to_thread(client.orders.close_position, {"marketSlug": row["maker_slug"], "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
            out = {"status": "MAKER_FILLED_EMERGENCY_CLOSED_NO_HEDGE_BOOK", **base, "maker_order_id": order_id, "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out
        hedge_now, hedge_depth_now = _ask(hb, str(row["hedge_side"]))
        if hedge_depth_now < ONE or _locked(maker_native_actual, hedge_now) < TARGET:
            close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": row["maker_slug"], "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
            out = {"status": "MAKER_FILLED_EMERGENCY_CLOSED_EDGE_GONE", **base, "maker_order_id": order_id,
                   "close_response_present": bool(close), "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        hedge_order_payload = _payload(row["hedge_slug"], row["hedge_side"], hedge_now, maker=False)
        await asyncio.to_thread(client.orders.preview, {"request": hedge_order_payload})
        hcreated = await asyncio.to_thread(client.orders.create, hedge_order_payload)
        hid = str(hcreated.get("id") or "")
        if not hid:
            await asyncio.to_thread(client.orders.close_position, {"marketSlug": row["maker_slug"], "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
            raise RuntimeError("hedge create returned no id; maker close requested")
        hdetail = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, hid))
        hcum = D(str(hdetail.get("cumQuantity") or 0))
        if hcum < ONE:
            close = await asyncio.to_thread(client.orders.close_position, {"marketSlug": row["maker_slug"], "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC", "synchronousExecution": True, "maxBlockTime": "2"})
            out = {"status": "HEDGE_FOK_NOT_FILLED_MAKER_CLOSED", **base, "maker_order_id": order_id, "hedge_order_id": hid,
                   "close_response_present": bool(close), "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out

        # Reconcile actual order prices and commissions before calling profit realized.
        maker_order = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, order_id))
        hdetail = _unwrap_order(await asyncio.to_thread(client.orders.retrieve, hid))
        mavg_yes, havg_yes = _amount(maker_order.get("avgPx")), _amount(hdetail.get("avgPx"))
        if mavg_yes is None or havg_yes is None:
            out = {"status": "BOTH_FILLED_RECONCILIATION_PENDING", **base, "maker_order_id": order_id, "hedge_order_id": hid,
                   "order_entry": True, "realized": False}
            print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
            return out
        mcost = mavg_yes if row["maker_side"] == "YES" else ONE - mavg_yes
        hcost = havg_yes if row["hedge_side"] == "YES" else ONE - havg_yes
        mfee = _amount(maker_order.get("commissionNotionalTotalCollected")) or D("0")
        hfee = _amount(hdetail.get("commissionNotionalTotalCollected")) or D("0")
        net = ONE - mcost - hcost - mfee - hfee
        out = {"status": "BOTH_FILLED_RECONCILED", **base, "maker_order_id": order_id, "hedge_order_id": hid,
               "actual_net_locked_profit": str(net), "actual_total_commission": str(mfee + hfee),
               "order_entry": True, "realized": bool(net >= D("0"))}
        print("SPORTS_POLY_RELAY_Q1 " + json.dumps(out, separators=(",", ":")), flush=True)
        return out
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
