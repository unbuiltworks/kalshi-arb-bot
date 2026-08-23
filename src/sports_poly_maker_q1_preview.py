"""Authenticated, mutation-free preflight for strict Polymarket MLB maker->hedge q1.

Selects the richest current MLB_PLAYER_PROP maker trigger from the existing census,
re-retrieves both exact markets, re-parses canonical same-game/player/stat identities,
checks active trading/minimum quantity, account balance, and previews both the
post-only maker and FOK hedge payloads.  This module NEVER creates/cancels orders.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
import os
from typing import Any, Mapping

from polymarket_us import PolymarketUS

from src.sports_poly_maker_trigger_census import run_once as census
from src.strict_mlb_prop_adapters import _poly_identity

D = Decimal
ONE = D("1")


def _native_to_yes_price(side: str, native_price: Any) -> D:
    p = D(str(native_price))
    return p if side == "YES" else ONE - p


def _payload(slug: str, side: str, native_price: Any, *, maker: bool) -> dict[str, Any]:
    yes_px = _native_to_yes_price(side, native_price)
    return {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "YES" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": str(yes_px), "currency": "USD"},
        "quantity": 1,
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL" if maker else "TIME_IN_FORCE_FILL_OR_KILL",
        "participateDontInitiate": bool(maker),
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": not maker,
        **({"maxBlockTime": "2"} if not maker else {}),
    }


def _market_ok(m: Mapping[str, Any]) -> bool:
    if m.get("active") is False or m.get("closed") is True or m.get("archived") is True:
        return False
    if m.get("orderBookEnabled") is False:
        return False
    return D(str(m.get("minimumTradeQty") or "1")) <= ONE


def _certify(low: Mapping[str, Any], high: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    li = _poly_identity(low)
    hi = _poly_identity(high)
    if li is None or hi is None:
        raise PermissionError("strict MLB identity parse failed")
    lf, lp, lt, ld, lx = li
    hf, hp, ht, hd, hx = hi
    if (lf, lp.lower(), set(lt), ld) != (hf, hp.lower(), set(ht), hd):
        raise PermissionError("same-game/player/stat identity mismatch")
    if not lx < hx:
        raise PermissionError("thresholds are not strictly monotone")
    # Require matching structured event parent when the API provides one.
    le = str(low.get("eventId") or low.get("event_id") or low.get("eventSlug") or "")
    he = str(high.get("eventId") or high.get("event_id") or high.get("eventSlug") or "")
    if le and he and le != he:
        raise PermissionError("parent event mismatch")
    if not _market_ok(low) or not _market_ok(high):
        raise PermissionError("one exact market is not currently tradeable at q1")
    evidence = {
        "family": lf,
        "player": lp,
        "teams": sorted(lt),
        "game_date": ld,
        "low_threshold": str(lx),
        "high_threshold": str(hx),
        "low_slug": str(low.get("slug") or ""),
        "high_slug": str(high.get("slug") or ""),
        "parent_event": le or he or None,
        "identity": "YES(lower threshold)+NO(higher threshold)>=1",
    }
    fp = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return fp, evidence


async def run_once() -> dict[str, Any]:
    snap = await census()
    candidates = [r for r in (snap.get("top_candidates") or []) if r.get("kind") == "MLB_PLAYER_PROP"]
    if not candidates:
        out = {"status": "NO_STRICT_MLB_MAKER_CANDIDATE", "order_entry": False}
        print("SPORTS_POLY_MAKER_Q1_PREFLIGHT " + json.dumps(out, separators=(",", ":")), flush=True)
        return out
    row = candidates[0]
    client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=8.0)
    try:
        low, high, balances = await asyncio.gather(
            asyncio.to_thread(client.markets.retrieve_by_slug, row["low_slug"]),
            asyncio.to_thread(client.markets.retrieve_by_slug, row["high_slug"]),
            asyncio.to_thread(client.account.balances),
        )
        fp, evidence = _certify(low, high)
        if row["direction"] == "MAKER_LOW_HEDGE_HIGH":
            maker_slug, hedge_slug = row["low_slug"], row["high_slug"]
        else:
            maker_slug, hedge_slug = row["high_slug"], row["low_slug"]
        maker = _payload(maker_slug, row["maker_buy_side"], row["maker_price_current_best_bid"], maker=True)
        hedge = _payload(hedge_slug, row["hedge_buy_side"], row["hedge_ask"], maker=False)
        maker_preview, hedge_preview = await asyncio.gather(
            asyncio.to_thread(client.orders.preview, maker),
            asyncio.to_thread(client.orders.preview, hedge),
        )
        out = {
            "status": "READY_STRICT_MLB_MAKER_Q1_PREVIEW",
            "locked_profit_after_hypothetical_fill": row["locked_profit_after_hypothetical_maker_fill"],
            "direction": row["direction"],
            "maker_slug": maker_slug,
            "hedge_slug": hedge_slug,
            "rule_fingerprint_sha256": fp,
            "rule_evidence": evidence,
            "balance_response_present": bool(balances),
            "maker_preview_ok": bool(maker_preview),
            "hedge_preview_ok": bool(hedge_preview),
            "order_entry": False,
            "realized": False,
        }
        print("SPORTS_POLY_MAKER_Q1_PREFLIGHT " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
