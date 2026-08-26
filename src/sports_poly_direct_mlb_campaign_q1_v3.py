"""Maker-frontier wrapper for the participation-gated Polymarket MLB q1 canary.

This deliberately preserves the proven v2 reservation/transport/reconciliation path
while replacing only candidate pricing and maker rest behavior:
- any participation-certified ladder may qualify if a post-only q1 quote can preserve
  >=3c using the audited current Polymarket US fee schedule;
- the current maker bid may be zero; the bot may seed the first safe bid;
- near-touch is a ranking signal only, never an admission gate;
- the quote is the highest reported-tick price strictly below the ask that preserves
  the 3c floor against the current q1 hedge;
- maker orders rest sticky for materially longer while hedge economics remain safe.

Settlement classification remains LIVE RULE-RISK because the existing MLB v2 layer
still owns participation/LFMP certification, durable exposure reservation and the
first-filled-pair review halt.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_FLOOR
import os
from typing import Any, Mapping

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_poly_direct_mlb_campaign_q1_v2 as v2
from src.sports_poly_current_fees import maker_then_taker_locked_profit
from src.sports_poly_mlb_participation_gate import maker_tick_for_pair
from src.sports_poly_structural_monotone import fetch_books
from src.strict_mlb_prop_adapters import _poly_identity

D = Decimal
ONE = D("1")
TARGET = D("0.03")
STICKY_REST_SECONDS = max(30.0, min(180.0, float(os.getenv("SPORTS_POLY_MLB_STICKY_REST_SECONDS", "60"))))
STICKY_POLL_INTERVAL_SECONDS = max(0.25, min(2.0, float(os.getenv("SPORTS_POLY_MLB_STICKY_POLL_INTERVAL_SECONDS", "0.50"))))


def _exact_fee_row(maker_price: D, taker_price: D) -> dict[str, D]:
    return maker_then_taker_locked_profit(maker_price=maker_price, taker_price=taker_price, quantity=1)


def _exact_locked(maker_price: D, taker_price: D) -> D:
    return _exact_fee_row(D(str(maker_price)), D(str(taker_price)))["locked_profit"]


def _floor_to_tick(value: D, tick: D) -> D:
    if tick <= 0:
        raise ValueError("tick must be positive")
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _highest_safe_post_only_quote(*, maker_ask: D, hedge_ask: D, tick: D) -> D | None:
    """Return the highest q1 post-only tick preserving the exact >=3c floor."""
    maker_ask = D(str(maker_ask))
    hedge_ask = D(str(hedge_ask))
    tick = D(str(tick))
    if tick <= 0 or maker_ask <= 0 or hedge_ask <= 0 or hedge_ask >= ONE:
        return None
    candidate = _floor_to_tick(min(maker_ask - tick, ONE - tick), tick)
    # At current Polymarket q1 fees this loop is short, but keep it generic for
    # cent-rounding and future tick grids. The domain is bounded to [0, 1].
    while candidate > 0:
        if _exact_locked(candidate, hedge_ask) >= TARGET:
            return candidate
        candidate -= tick
    return None


def _score_frontiers(
    feed: Mapping[str, Mapping[str, Any]],
    books: Mapping[str, Any],
    pairs: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for low_slug, high_slug in pairs:
        low = feed.get(low_slug)
        high = feed.get(high_slug)
        lb = books.get(low_slug)
        hb = books.get(high_slug)
        if low is None or high is None or lb is None or hb is None:
            continue
        li = _poly_identity(low)
        hi = _poly_identity(high)
        if li is None or hi is None:
            continue
        lf, lp, lt, ld, lx = li
        hf, hp, ht, hd, hx = hi
        if (lf, " ".join(lp.lower().split()), set(lt), ld) != (hf, " ".join(hp.lower().split()), set(ht), hd):
            continue
        if lx >= hx:
            continue

        directions = (
            ("MAKER_LOW_HEDGE_HIGH", low_slug, "YES", lb, high_slug, "NO", hb),
            ("MAKER_HIGH_HEDGE_LOW", high_slug, "NO", hb, low_slug, "YES", lb),
        )
        for direction, maker_slug, maker_side, maker_book, hedge_slug, hedge_side, hedge_book in directions:
            try:
                maker_bid = D(str(v4._bid(maker_book, maker_side)))
                maker_ask = D(str(v4._ask(maker_book, maker_side)[0]))
                hedge_ask, hedge_depth = v4._ask(hedge_book, hedge_side)
                hedge_ask = D(str(hedge_ask))
                hedge_depth = D(str(hedge_depth))
                seed = {
                    "direction": direction,
                    "low_slug": low_slug,
                    "high_slug": high_slug,
                    "maker_slug": maker_slug,
                    "maker_side": maker_side,
                    "hedge_slug": hedge_slug,
                    "hedge_side": hedge_side,
                }
                tick = D(str(maker_tick_for_pair(feed, seed)))
                quote = _highest_safe_post_only_quote(maker_ask=maker_ask, hedge_ask=hedge_ask, tick=tick)
            except Exception:
                continue
            if hedge_depth < ONE or quote is None or quote <= 0 or quote >= maker_ask:
                continue
            fee_row = _exact_fee_row(quote, hedge_ask)
            locked = fee_row["locked_profit"]
            if locked < TARGET:
                continue
            gap_to_ask = maker_ask - quote
            improves_bid = quote > maker_bid
            queue_ahead_gap = max(D("0"), maker_bid - quote)
            rows.append(
                {
                    **seed,
                    "maker_price": maker_bid,
                    "maker_ask": maker_ask,
                    "maker_spread": maker_ask - maker_bid,
                    "executable_maker_quote": quote,
                    "maker_gap_to_ask": gap_to_ask,
                    "improves_bid": improves_bid,
                    "queue_mode": "ZERO_OBSERVED_QUEUE" if improves_bid else "BEHIND_OR_JOINING_DISPLAYED_BID",
                    "queue_ahead_gap": queue_ahead_gap,
                    "hedge_ask": hedge_ask,
                    "hedge_depth": hedge_depth,
                    "locked_profit": locked,
                    "quote_locked_profit": locked,
                    "maker_rebate": fee_row["maker_rebate"],
                    "taker_fee": fee_row["taker_fee"],
                    "reported_maker_tick": tick,
                    "near_touch": gap_to_ask <= tick,
                    "low_threshold": lx,
                    "high_threshold": hx,
                    "family": lf,
                    "player": lp,
                    "rule_classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
                    "guaranteed_arbitrage": False,
                }
            )

    rows.sort(
        key=lambda r: (
            not bool(r.get("improves_bid")),
            D(str(r.get("queue_ahead_gap") or "0")),
            D(str(r.get("maker_gap_to_ask") or "1")),
            -D(str(r.get("hedge_depth") or "0")),
            -D(str(r.get("quote_locked_profit") or "0")),
        )
    )
    return rows


async def _fresh_ranked(
    feed: Mapping[str, Mapping[str, Any]],
    pairs: list[tuple[str, str]],
    *,
    require_near_touch: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # require_near_touch is intentionally ignored: near-touch now ranks, never gates.
    slugs = {slug for pair in pairs for slug in pair}
    books, health = await fetch_books(slugs, concurrency=6)
    return _score_frontiers(feed, books, pairs), health


async def _persistent_row(
    feed: Mapping[str, Mapping[str, Any]],
    pair: tuple[str, str],
    expected: Mapping[str, Any],
) -> dict[str, Any] | None:
    first, _ = await _fresh_ranked(feed, [pair])
    if not first:
        return None
    a = first[0]
    identity = ("maker_slug", "hedge_slug", "maker_side", "hedge_side")
    if any(str(a.get(k)) != str(expected.get(k)) for k in identity):
        return None
    await asyncio.sleep(max(0.0, v2.PERSIST_SECONDS))
    second, _ = await _fresh_ranked(feed, [pair])
    if not second:
        return None
    b = second[0]
    if any(str(b.get(k)) != str(a.get(k)) for k in identity):
        return None
    return b


def _exact_maker_quote(book: Any, side: str, hedge_ask: D) -> D | None:
    try:
        maker_ask = D(str(v4._ask(book, side)[0]))
        return _highest_safe_post_only_quote(maker_ask=maker_ask, hedge_ask=D(str(hedge_ask)), tick=D(str(v4.TICK)))
    except Exception:
        return None


async def run_once() -> dict[str, Any]:
    """Run v2 safety/ledger transport with maker-frontier pricing monkeypatches."""
    prior_fresh = v2._fresh_ranked
    prior_persistent = v2._persistent_row
    prior_locked = v4._locked
    prior_maker_quote = v4._maker_quote
    prior_attempt = v4._attempt

    async def _sticky_attempt(client: Any, row: Mapping[str, Any], feed: Mapping[str, Mapping[str, Any]], attempt_no: int) -> dict[str, Any]:
        prior_poll_seconds = v4.POLL_SECONDS
        prior_poll_interval = v4.POLL_INTERVAL_SECONDS
        prior_edge_recheck = v4.EDGE_RECHECK_SECONDS
        v4.POLL_SECONDS = STICKY_REST_SECONDS
        v4.POLL_INTERVAL_SECONDS = STICKY_POLL_INTERVAL_SECONDS
        v4.EDGE_RECHECK_SECONDS = 1.0
        try:
            return await prior_attempt(client, row, feed, attempt_no)
        finally:
            v4.POLL_SECONDS = prior_poll_seconds
            v4.POLL_INTERVAL_SECONDS = prior_poll_interval
            v4.EDGE_RECHECK_SECONDS = prior_edge_recheck

    v2._fresh_ranked = _fresh_ranked
    v2._persistent_row = _persistent_row
    v4._locked = _exact_locked
    v4._maker_quote = _exact_maker_quote
    v4._attempt = _sticky_attempt
    try:
        return await v2.run_once()
    finally:
        v2._fresh_ranked = prior_fresh
        v2._persistent_row = prior_persistent
        v4._locked = prior_locked
        v4._maker_quote = prior_maker_quote
        v4._attempt = prior_attempt


if __name__ == "__main__":
    asyncio.run(run_once())
