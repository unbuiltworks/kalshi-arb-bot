"""Event-driven hot lane for participation-cleared MLB prop counterparts.

READ-ONLY. This process performs one broad discovery / MLB participation pass at
startup, keeps only already-cleared strict Polymarket US <-> Kalshi counterparts,
subscribes to both venues' order-book WebSockets, and recalculates capped $1/$5
conservative arb plans only when one of the two books changes.

It contains no order-entry code and intentionally stays separate from the existing
rotating REST scanner. Use --shard-count/--shard-index to split the hot universe
across isolated workers without changing the existing scanner.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import hashlib
import json
import logging
import os
import signal
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v3 as v3
from src import strict_mlb_prop_snapshot_v4 as v4
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict
from src.eventing_books import EventingPolyBookStore, EventingKalshiCrossBooks
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_hot_shadow")
base._fetch_poly_mlb = _fetch_poly_mlb_search

PREFERRED_NET = Decimal(os.getenv("MLB_HOT_PREFERRED_NET", "0.03"))
MAX_BOOK_AGE_MS = int(os.getenv("MLB_HOT_MAX_BOOK_AGE_MS", "3000"))
SUMMARY_SECONDS = float(os.getenv("MLB_HOT_SUMMARY_SECONDS", "10"))


def _stable_shard(key: str, count: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % count


def _top_book(quote: Any) -> base.TopBook:
    return base.TopBook(
        yes_ask=Decimal(str(quote.yes_ask)),
        yes_depth=Decimal(str(quote.yes_depth)),
        no_ask=Decimal(str(quote.no_ask)),
        no_depth=Decimal(str(quote.no_depth)),
    )


async def _discover_cleared(http: httpx.AsyncClient) -> tuple[list[base.StrictCandidate], dict[str, Any], dict[str, dict[str, Any]]]:
    poly_markets, kalshi_events = await asyncio.gather(
        base._fetch_poly_mlb(http), base._fetch_kalshi_events(http)
    )
    candidates = base.discover_strict_candidates(poly_markets, kalshi_events)
    games = await fetch_game_boxscores(http, {c.game_date for c in candidates})
    proofs: dict[str, Any] = {}
    cleared: list[base.StrictCandidate] = []
    for candidate in candidates:
        proof = participation_proof(
            family=candidate.family,
            player_name=candidate.player,
            poly_slug=candidate.poly_slug,
            games=games,
        )
        proofs[candidate.key] = proof
        if base.assess_rule_equivalence(candidate.family, proof).settlement_equivalent_now:
            cleared.append(candidate)

    series_meta: dict[str, dict[str, Any]] = {}
    for series in sorted(set(base.FAMILY_SERIES.values())):
        body, error = await v3._fetch_optional(http, f"{base.KALSHI}/series/{series}")
        if body is not None and error is None:
            series_meta[series] = dict(body.get("series") or {})
    return cleared, proofs, series_meta


async def _seed_poly(slugs: list[str], store: EventingPolyBookStore) -> int:
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=8.0) as http:
        results = await asyncio.gather(*[_fetch_poly_book(http, store, slug, sem) for slug in slugs])
    return sum(bool(x) for x in results)


async def run(args: argparse.Namespace) -> None:
    if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
        raise SystemExit("invalid shard index/count")

    async with httpx.AsyncClient(timeout=20.0) as http:
        cleared, proofs, series_meta = await _discover_cleared(http)

    shard = [c for c in cleared if _stable_shard(c.key, args.shard_count) == args.shard_index]
    if not shard:
        raise SystemExit("no participation-cleared candidates in this shard")

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise SystemExit("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required for market-data WebSocket")

    poly_store = EventingPolyBookStore(max_queue=50000)
    kalshi_store = EventingKalshiCrossBooks([c.kalshi_ticker for c in shard], max_queue=50000)
    slugs = [c.poly_slug for c in shard]
    poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)

    seeded = await _seed_poly(slugs, poly_store)
    by_poly = {c.poly_slug: c for c in shard}
    by_kalshi = {c.kalshi_ticker: c for c in shard}

    logger.info("MLB_HOT_START %s", json.dumps({
        "read_only": True,
        "order_entry": False,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "cleared_total": len(cleared),
        "hot_candidates": len(shard),
        "poly_seeded": seeded,
        "preferred_net": str(PREFERRED_NET),
        "max_book_age_ms": MAX_BOOK_AGE_MS,
    }, separators=(",", ":")))

    poly_task = asyncio.create_task(run_poly_ws(poly_client, poly_store, slugs))
    kalshi_task = asyncio.create_task(kalshi_store.run())
    stop_task = asyncio.create_task(_wait_for_stop())

    last_summary = time.monotonic()
    events = 0
    comparable = 0
    positive = 0
    preferred = 0
    last_packet_by_key: dict[str, tuple[str, str, str]] = {}

    async def evaluate(candidate: base.StrictCandidate, trigger: str) -> None:
        nonlocal comparable, positive, preferred
        p = poly_store.quote(candidate.poly_slug, max_age_ms=MAX_BOOK_AGE_MS)
        k = await kalshi_store.quote(candidate.kalshi_ticker, max_age_ms=MAX_BOOK_AGE_MS)
        if p is None or k is None:
            return
        comparable += 1
        meta = series_meta.get(base.FAMILY_SERIES[candidate.family]) or {}
        fee_type = str(meta.get("fee_type") or "")
        multiplier = base._d(meta.get("fee_multiplier"))
        if fee_type not in {"quadratic", "quadratic_with_maker_fees"} or multiplier is None:
            return

        pbook = _top_book(p)
        kbook = _top_book(k)
        probe1 = v4.best_capped_plan(pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("1"))
        probe5 = v4.best_capped_plan(pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("5"))
        if probe5 is None:
            return
        positive += 1
        net = Decimal(probe5["conservative_locked_profit"])
        is_preferred = net >= PREFERRED_NET
        if is_preferred:
            preferred += 1

        fingerprint = (
            str(probe5["direction"]),
            str(probe5["poly_price"]),
            str(probe5["kalshi_price"]),
        )
        if last_packet_by_key.get(candidate.key) == fingerprint and not is_preferred:
            return
        last_packet_by_key[candidate.key] = fingerprint

        packet = {
            "label": "MANUAL_PROBE_READY" if is_preferred else "POSITIVE_SHADOW",
            "trigger": trigger,
            "observed_at_ms": int(time.time() * 1000),
            "family": candidate.family,
            "player": candidate.player,
            "threshold": str(candidate.threshold),
            "poly_slug": candidate.poly_slug,
            "kalshi_ticker": candidate.kalshi_ticker,
            "participation_proof": proof_dict(proofs[candidate.key]),
            "settlement_equivalent_now": True,
            "probe_1usd": probe1,
            "probe_5usd": probe5,
            "preferred": is_preferred,
            "read_only": True,
            "order_entry": False,
        }
        logger.warning("MLB_HOT_PACKET %s", json.dumps(packet, separators=(",", ":")))

    try:
        while True:
            if stop_task.done():
                break
            get_poly = asyncio.create_task(poly_store.updates.get())
            get_kalshi = asyncio.create_task(kalshi_store.updates.get())
            done, pending = await asyncio.wait(
                {get_poly, get_kalshi, stop_task},
                timeout=1.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                if task is not stop_task:
                    task.cancel()
            if stop_task in done:
                break
            for task in done:
                if task is stop_task:
                    continue
                update = task.result()
                events += 1
                candidate = by_poly.get(update.market_id) if update.venue == "polymarket" else by_kalshi.get(update.market_id)
                if candidate is not None:
                    await evaluate(candidate, f"{update.venue}:{update.kind}")

            now = time.monotonic()
            if now - last_summary >= SUMMARY_SECONDS:
                logger.info("MLB_HOT_SUMMARY %s", json.dumps({
                    "shard_index": args.shard_index,
                    "hot_candidates": len(shard),
                    "events": events,
                    "poly_ws_updates": poly_store.ws_updates,
                    "poly_books": poly_store.quote_count,
                    "kalshi_initialized": kalshi_store.initialized_count,
                    "comparable_evaluations": comparable,
                    "positive_evaluations": positive,
                    "preferred_evaluations": preferred,
                    "dropped_poly_events": poly_store.dropped_updates,
                    "dropped_kalshi_events": kalshi_store.dropped_updates,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))
                last_summary = now
    finally:
        poly_task.cancel()
        kalshi_task.cancel()
        stop_task.cancel()
        await kalshi_store.close()
        try:
            poly_client.close()
        except Exception:
            pass
        await asyncio.gather(poly_task, kalshi_task, stop_task, return_exceptions=True)


async def _wait_for_stop() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only event-driven MLB prop hot scanner")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
