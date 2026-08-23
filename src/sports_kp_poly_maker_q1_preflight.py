"""Targeted mutation-free Polymarket MLB maker->hedge q1 preflight.

Runs inside the low-request K/P executor service so authenticated Polymarket calls
are not made after the broad 3k-book census.  It scans only strict canonical MLB
player-prop ladders from the already-fetched sports event inventory, stops at a
bounded public-book request count, verifies the richest >=3c monotone pair, then
uses authenticated account + order preview endpoints.  It NEVER creates orders.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
import os
from typing import Any, Mapping

import httpx
from polymarket_us import PolymarketUS

from src.sports_poly_current_fees import maker_then_taker_locked_profit
from src.strict_mlb_prop_adapters import _poly_identity
from src.strict_mlb_prop_snapshot import TopBook, poly_top_book

D = Decimal
ONE = D("1")
TARGET = D("0.03")
BASE = "https://gateway.polymarket.us"
MAX_BOOKS = 120


def _ask(book: TopBook, buy_yes: bool) -> tuple[D, D]:
    return (book.yes_ask, book.yes_depth) if buy_yes else (book.no_ask, book.no_depth)


def _bid(book: TopBook, buy_yes: bool) -> D:
    return ONE - (book.no_ask if buy_yes else book.yes_ask)


def _native_to_yes(side: str, native: Any) -> D:
    p = D(str(native))
    return p if side == "YES" else ONE - p


def _payload(slug: str, side: str, native: Any, *, maker: bool) -> dict[str, Any]:
    return {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "YES" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(_native_to_yes(side, native)), "currency": "USD"},
        "quantity": 1,
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL" if maker else "TIME_IN_FORCE_FILL_OR_KILL",
        "participateDontInitiate": bool(maker),
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": not maker,
        **({"maxBlockTime": "2"} if not maker else {}),
    }


def _strict_pair(a: Mapping[str, Any], b: Mapping[str, Any]) -> tuple[tuple[Any, ...], D, D] | None:
    ia, ib = _poly_identity(a), _poly_identity(b)
    if ia is None or ib is None:
        return None
    af, ap, at, ad, ax = ia
    bf, bp, bt, bd, bx = ib
    if (af, ap.lower(), set(at), ad) != (bf, bp.lower(), set(bt), bd) or ax == bx:
        return None
    lo, hi = (ax, bx) if ax < bx else (bx, ax)
    key = (ad, af, ap.lower(), tuple(sorted(at)))
    return key, lo, hi


def _certify(low: Mapping[str, Any], high: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    pair = _strict_pair(low, high)
    if pair is None:
        raise PermissionError("strict same-game/player/stat identity mismatch")
    key, lo, hi = pair
    li, hi_id = _poly_identity(low), _poly_identity(high)
    assert li is not None and hi_id is not None
    if li[4] != lo or hi_id[4] != hi:
        raise PermissionError("low/high threshold orientation mismatch")
    for m in (low, high):
        if m.get("active") is False or m.get("closed") is True or m.get("archived") is True or m.get("orderBookEnabled") is False:
            raise PermissionError("market not currently tradeable")
        if D(str(m.get("minimumTradeQty") or "1")) > ONE:
            raise PermissionError("market minimum quantity exceeds q1")
    le = str(low.get("eventId") or low.get("event_id") or low.get("eventSlug") or "")
    he = str(high.get("eventId") or high.get("event_id") or high.get("eventSlug") or "")
    if le and he and le != he:
        raise PermissionError("parent event mismatch")
    evidence = {
        "identity_key": list(key),
        "low_threshold": str(lo),
        "high_threshold": str(hi),
        "low_slug": str(low.get("slug") or ""),
        "high_slug": str(high.get("slug") or ""),
        "parent_event": le or he or None,
        "monotone_identity": "YES(lower threshold)+NO(higher threshold)>=1",
    }
    fp = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return fp, evidence


async def run_once(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    ladders: dict[tuple[str, str, str, tuple[str, str], str], list[dict[str, Any]]] = {}
    for event in events:
        event_slug = str(event.get("slug") or event.get("id") or "")
        series = str(event.get("seriesSlug") or event.get("series_slug") or event.get("subcategory") or "").lower()
        if "mlb" not in series and "mlb" not in event_slug.lower():
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ident = _poly_identity(market)
            if ident is None:
                continue
            family, player, teams, game_date, threshold = ident
            if threshold < ONE or threshold != threshold.to_integral_value():
                continue
            key = (game_date, family, "".join(ch for ch in player.lower() if ch.isalnum()), tuple(sorted(teams)), event_slug)
            ladders.setdefault(key, []).append({"market": dict(market), "slug": str(market.get("slug") or ""), "threshold": threshold})

    slugs: list[str] = []
    for ladder in ladders.values():
        for row in sorted(ladder, key=lambda x: x["threshold"]):
            if row["slug"] and row["slug"] not in slugs:
                slugs.append(row["slug"])
            if len(slugs) >= MAX_BOOKS:
                break
        if len(slugs) >= MAX_BOOKS:
            break

    books: dict[str, TopBook] = {}
    async with httpx.AsyncClient(timeout=5.0) as http:
        for slug in slugs:
            try:
                r = await http.get(f"{BASE}/v1/markets/{slug}/book")
                if r.status_code == 200:
                    b = poly_top_book(r.json())
                    if b is not None:
                        books[slug] = b
            except Exception:
                continue

    scored: list[dict[str, Any]] = []
    for key, ladder in ladders.items():
        ordered = sorted([x for x in ladder if x["slug"] in books], key=lambda x: x["threshold"])
        for i, low in enumerate(ordered):
            for high in ordered[i + 1:]:
                lb, hb = books[low["slug"]], books[high["slug"]]
                options = (
                    ("MAKER_LOW_HEDGE_HIGH", "YES", _bid(lb, True), "NO", *_ask(hb, False)),
                    ("MAKER_HIGH_HEDGE_LOW", "NO", _bid(hb, False), "YES", *_ask(lb, True)),
                )
                for direction, ms, mp, hs, hp, hd in options:
                    if mp <= 0 or mp >= ONE or hd < ONE:
                        continue
                    econ = maker_then_taker_locked_profit(maker_price=mp, taker_price=hp, quantity=1)
                    if D(str(econ["locked_profit"])) < TARGET:
                        continue
                    scored.append({
                        "key": key, "low": low, "high": high, "direction": direction,
                        "maker_side": ms, "maker_price": mp, "hedge_side": hs, "hedge_ask": hp,
                        "hedge_depth": hd, "locked_profit": D(str(econ["locked_profit"])),
                    })
    scored.sort(key=lambda r: r["locked_profit"], reverse=True)
    if not scored:
        out = {"status": "NO_TARGETED_MLB_MAKER_Q1", "books_fetched": len(books), "order_entry": False}
        print("SPORTS_KP_POLY_MAKER_Q1_PREFLIGHT " + json.dumps(out, separators=(",", ":")), flush=True)
        return out

    row = scored[0]
    client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=6.0)
    try:
        low, high = await asyncio.gather(
            asyncio.to_thread(client.markets.retrieve_by_slug, row["low"]["slug"]),
            asyncio.to_thread(client.markets.retrieve_by_slug, row["high"]["slug"]),
        )
        fp, evidence = _certify(low, high)
        maker_slug = row["low"]["slug"] if row["direction"] == "MAKER_LOW_HEDGE_HIGH" else row["high"]["slug"]
        hedge_slug = row["high"]["slug"] if row["direction"] == "MAKER_LOW_HEDGE_HIGH" else row["low"]["slug"]
        maker_payload = _payload(maker_slug, row["maker_side"], row["maker_price"], maker=True)
        hedge_payload = _payload(hedge_slug, row["hedge_side"], row["hedge_ask"], maker=False)
        balances, mpv, hpv = await asyncio.gather(
            asyncio.to_thread(client.account.balances),
            asyncio.to_thread(client.orders.preview, maker_payload),
            asyncio.to_thread(client.orders.preview, hedge_payload),
        )
        out = {
            "status": "READY_TARGETED_MLB_MAKER_Q1",
            "books_fetched": len(books),
            "locked_profit_after_hypothetical_fill": str(row["locked_profit"]),
            "maker_slug": maker_slug,
            "maker_side": row["maker_side"],
            "maker_price": str(row["maker_price"]),
            "hedge_slug": hedge_slug,
            "hedge_side": row["hedge_side"],
            "hedge_ask": str(row["hedge_ask"]),
            "hedge_depth": str(row["hedge_depth"]),
            "rule_fingerprint_sha256": fp,
            "rule_evidence": evidence,
            "balance_response_present": bool(balances),
            "maker_preview_ok": bool(mpv),
            "hedge_preview_ok": bool(hpv),
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_KP_POLY_MAKER_Q1_PREFLIGHT " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        client.close()
