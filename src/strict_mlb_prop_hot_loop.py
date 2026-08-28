"""Cached hot-loop MLB prop scanner.

Read-only. Keeps exact cross-venue discovery and participation state cached so the
latency-critical loop spends its time repricing already-cleared counterparts rather
than rediscovering the same slate before every batch. Uses the existing conservative
fee/capped-sizing logic and v3's rate-safe request helper. No auth/order entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v3 as v3
from src import strict_mlb_prop_snapshot_v4 as v4
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_hot_loop")
base._fetch_poly_mlb = _fetch_poly_mlb_search

DISCOVERY_REFRESH_S = 300.0
PARTICIPATION_REFRESH_S = 15.0
BATCH_SIZE = 60
MIN_PREFERRED_NET = Decimal("0.03")


class HotState:
    def __init__(self) -> None:
        self.candidates: list[base.StrictCandidate] = []
        self.cleared: list[base.StrictCandidate] = []
        self.proofs: dict[str, Any] = {}
        self.assessments: dict[str, Any] = {}
        self.series_meta: dict[str, dict[str, Any]] = {}
        self.discovery_at = 0.0
        self.participation_at = 0.0
        self.cursor = 0


async def refresh_discovery(http: httpx.AsyncClient, state: HotState) -> None:
    poly_markets, kalshi_events = await asyncio.gather(
        base._fetch_poly_mlb(http), base._fetch_kalshi_events(http)
    )
    state.candidates = base.discover_strict_candidates(poly_markets, kalshi_events)
    meta: dict[str, dict[str, Any]] = {}
    for series in sorted(set(base.FAMILY_SERIES.values())):
        body, error = await v3._fetch_optional(http, f"{base.KALSHI}/series/{series}")
        if body is not None and error is None:
            meta[series] = dict(body.get("series") or {})
    state.series_meta = meta
    state.discovery_at = time.monotonic()
    state.cursor = 0


async def refresh_participation(http: httpx.AsyncClient, state: HotState) -> None:
    if not state.candidates:
        state.cleared = []
        return
    games = await fetch_game_boxscores(http, {c.game_date for c in state.candidates})
    proofs = {
        c.key: participation_proof(
            family=c.family,
            player_name=c.player,
            poly_slug=c.poly_slug,
            games=games,
        )
        for c in state.candidates
    }
    assessments = {
        c.key: base.assess_rule_equivalence(c.family, proofs[c.key])
        for c in state.candidates
    }
    state.proofs = proofs
    state.assessments = assessments
    state.cleared = [c for c in state.candidates if assessments[c.key].settlement_equivalent_now]
    state.participation_at = time.monotonic()
    if state.cursor >= max(1, len(state.cleared)):
        state.cursor = 0


def next_batch(state: HotState) -> list[base.StrictCandidate]:
    ordered = v3._stable_order(state.cleared)
    n = len(ordered)
    if not n:
        return []
    size = min(BATCH_SIZE, n)
    start = state.cursor % n
    end = start + size
    if end <= n:
        batch = ordered[start:end]
    else:
        batch = ordered[start:] + ordered[: end - n]
    state.cursor = (start + size) % n
    return batch


async def price_batch(http: httpx.AsyncClient, state: HotState, batch: list[base.StrictCandidate]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(v3.PRICE_CONCURRENCY)

    async def one(c: base.StrictCandidate) -> dict[str, Any]:
        async with semaphore:
            poly_body, poly_error = await v3._fetch_optional(
                http, f"{base.POLY}/v1/markets/{c.poly_slug}/book"
            )
            kalshi_body, kalshi_error = await v3._fetch_optional(
                http,
                f"{base.KALSHI}/markets/{c.kalshi_ticker}/orderbook",
                params={"depth": 1},
            )
        proof = state.proofs.get(c.key)
        assessment = state.assessments.get(c.key)
        if poly_body is None or kalshi_body is None or assessment is None:
            return {
                "poly_slug": c.poly_slug,
                "kalshi_ticker": c.kalshi_ticker,
                "player": c.player,
                "family": c.family,
                "threshold": str(c.threshold),
                "fetch_error": ";".join(x for x in (poly_error, kalshi_error) if x) or "book_missing",
                "probe_1usd": None,
                "probe_5usd": None,
                "preferred_live_probe": False,
            }
        pbook = base.poly_top_book(poly_body)
        kbook = base.kalshi_top_book(kalshi_body)
        series = base.FAMILY_SERIES[c.family]
        meta = state.series_meta.get(series) or {}
        fee_type = str(meta.get("fee_type") or "")
        multiplier = base._d(meta.get("fee_multiplier"))
        probe1 = probe5 = None
        if (
            pbook is not None
            and kbook is not None
            and fee_type in {"quadratic", "quadratic_with_maker_fees"}
            and multiplier is not None
            and Decimal("0") <= multiplier <= Decimal("100")
        ):
            probe1 = v4.best_capped_plan(
                pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("1")
            )
            probe5 = v4.best_capped_plan(
                pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("5")
            )
        preferred = bool(
            assessment.settlement_equivalent_now
            and probe5
            and Decimal(probe5["conservative_locked_profit"]) >= MIN_PREFERRED_NET
        )
        return {
            "family": c.family,
            "poly_slug": c.poly_slug,
            "kalshi_ticker": c.kalshi_ticker,
            "player": c.player,
            "threshold": str(c.threshold),
            "rule_status": assessment.status.value,
            "participation_proof": proof_dict(proof),
            "settlement_equivalent_now": assessment.settlement_equivalent_now,
            "probe_1usd": probe1,
            "probe_5usd": probe5,
            "preferred_live_probe": preferred,
            "fetch_error": None,
        }

    return await asyncio.gather(*(one(c) for c in batch)) if batch else []


async def run() -> None:
    state = HotState()
    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            now = time.monotonic()
            if not state.candidates or now - state.discovery_at >= DISCOVERY_REFRESH_S:
                await refresh_discovery(http, state)
            now = time.monotonic()
            if not state.proofs or now - state.participation_at >= PARTICIPATION_REFRESH_S:
                await refresh_participation(http, state)

            batch = next_batch(state)
            started = time.monotonic()
            rows = await price_batch(http, state, batch)
            elapsed = time.monotonic() - started
            positive1 = [r for r in rows if r.get("probe_1usd")]
            positive5 = [r for r in rows if r.get("probe_5usd")]
            ready = [r for r in rows if r.get("preferred_live_probe")]
            failures = [r for r in rows if r.get("fetch_error")]

            def profit(r: dict[str, Any]) -> Decimal:
                return Decimal(r["probe_5usd"]["conservative_locked_profit"])

            packet = {
                "ts": time.time(),
                "total_strict_candidates": len(state.candidates),
                "participation_cleared_candidates": len(state.cleared),
                "batch_size": len(batch),
                "batch_elapsed_s": round(elapsed, 3),
                "next_cursor": state.cursor,
                "probe_1usd_positive_pairs": len(positive1),
                "probe_5usd_positive_pairs": len(positive5),
                "preferred_live_probe_pairs": len(ready),
                "fetch_failures": len(failures),
                "top_live_probes": sorted(positive5, key=profit, reverse=True)[:10],
                "ready_probe_packets": sorted(ready, key=profit, reverse=True)[:10],
                "discovery_cache_age_s": round(time.monotonic() - state.discovery_at, 1),
                "participation_cache_age_s": round(time.monotonic() - state.participation_at, 1),
                "read_only": True,
                "order_entry": False,
            }
            logger.info("STRICT_MLB_PROP_HOT_LOOP %s", json.dumps(packet, separators=(",", ":")))
            print(json.dumps(packet, separators=(",", ":")), flush=True)
            await asyncio.sleep(0.25)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
