"""Polymarket-triggered automatic q=1 K/P MLB prop rule-risk lane.

Uses the strict candidate/rule/participation and execution primitives from
`mlb_kp_rule_risk_live_q1`, but removes broad Kalshi polling from the hot path.
Polymarket market-data updates trigger at most one exact Kalshi depth-1 read;
only a provisional >=3c pair enters the two-snapshot final revalidation and live
q=1 executor.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v3 as v3
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict
from src.eventing_books import EventingPolyBookStore
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws
from src.mlb_kp_rule_risk_live_q1 import (
    D,
    MIN_PROFIT,
    KALSHI_BASE,
    MAX_RTT_MS,
    execute,
    live_enabled,
    persistent_plan,
    score_q1,
)

base._fetch_poly_mlb = _fetch_poly_mlb_search
logger = logging.getLogger("kalshi_arb.mlb_kp_rule_risk_event_live_q1")

MAX_TRIGGER_AGE_MS = int(os.getenv("MLB_KP_RULE_RISK_TRIGGER_MAX_AGE_MS", "100"))
KALSHI_MIN_INTERVAL_MS = int(os.getenv("MLB_KP_RULE_RISK_KALSHI_MIN_INTERVAL_MS", "125"))
TICKER_MIN_INTERVAL_MS = int(os.getenv("MLB_KP_RULE_RISK_TICKER_MIN_INTERVAL_MS", "250"))
PARTICIPATION_REFRESH_S = float(os.getenv("MLB_KP_RULE_RISK_PARTICIPATION_REFRESH_S", "10"))
SUMMARY_S = float(os.getenv("MLB_KP_RULE_RISK_SUMMARY_S", "10"))
POLY_GATEWAY = "https://gateway.polymarket.us"


def _top_book(quote: Any) -> base.TopBook:
    return base.TopBook(
        yes_ask=D(str(quote.yes_ask)), yes_depth=D(str(quote.yes_depth)),
        no_ask=D(str(quote.no_ask)), no_depth=D(str(quote.no_depth)),
    )


class ExactKalshiLimiter:
    """Bound global + per-ticker exact Kalshi reads without queueing stale work."""
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_global_ms = 0.0
        self._last_ticker_ms: dict[str, float] = {}
        self.allowed = 0
        self.global_suppressed = 0
        self.ticker_suppressed = 0

    async def allow(self, ticker: str) -> bool:
        async with self._lock:
            now = time.monotonic() * 1000.0
            if now - self._last_global_ms < KALSHI_MIN_INTERVAL_MS:
                self.global_suppressed += 1
                return False
            if now - self._last_ticker_ms.get(ticker, 0.0) < TICKER_MIN_INTERVAL_MS:
                self.ticker_suppressed += 1
                return False
            self._last_global_ms = now
            self._last_ticker_ms[ticker] = now
            self.allowed += 1
            return True


async def _seed_poly(slugs: list[str], store: EventingPolyBookStore) -> int:
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=8.0) as http:
        results = await asyncio.gather(*[_fetch_poly_book(http, store, slug, sem) for slug in slugs])
    return sum(bool(x) for x in results)


async def _exact_kalshi_book(http: httpx.AsyncClient, ticker: str) -> tuple[base.TopBook | None, float, int | None]:
    started = time.perf_counter()
    try:
        response = await http.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook", params={"depth": 1})
        rtt = (time.perf_counter() - started) * 1000.0
        if response.status_code != 200 or rtt > MAX_RTT_MS:
            return None, rtt, response.status_code
        return base.kalshi_top_book(response.json()), rtt, response.status_code
    except Exception:
        return None, (time.perf_counter() - started) * 1000.0, None


async def _startup() -> tuple[list[base.StrictCandidate], dict[str, D]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_markets, kalshi_events = await asyncio.gather(base._fetch_poly_mlb(http), base._fetch_kalshi_events(http))
        candidates = base.discover_strict_candidates(poly_markets, kalshi_events)
        series_meta: dict[str, D] = {}
        for series in sorted(set(base.FAMILY_SERIES.values())):
            body, error = await v3._fetch_optional(http, f"{KALSHI_BASE}/series/{series}")
            if body is not None and error is None:
                mult = base._d((body.get("series") or {}).get("fee_multiplier"))
                if mult is not None:
                    series_meta[series] = mult
    return candidates, series_meta


async def run() -> int:
    candidates, series_meta = await _startup()
    if not candidates:
        raise RuntimeError("no strict K/P MLB prop candidates")

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise RuntimeError("Polymarket market-data credentials missing")

    by_poly: dict[str, list[base.StrictCandidate]] = {}
    for candidate in candidates:
        by_poly.setdefault(candidate.poly_slug, []).append(candidate)
    slugs = sorted(by_poly)

    store = EventingPolyBookStore(max_queue=50000)
    seeded = await _seed_poly(slugs, store)
    poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
    poly_task = asyncio.create_task(run_poly_ws(poly_client, store, slugs))
    limiter = ExactKalshiLimiter()

    cleared: dict[str, dict[str, Any]] = {}
    stats = {
        "poly_events": 0, "stale_poly_events": 0, "uncleared_events": 0,
        "exact_kalshi_reads": 0, "kalshi_200": 0, "kalshi_429": 0,
        "provisional_positive": 0, "final_positive": 0, "executions": 0,
    }
    last_participation = 0.0
    last_summary = 0.0

    logger.info("MLB_KP_RULE_RISK_EVENT_START %s", json.dumps({
        "live": live_enabled(), "q": 1, "min_profit": str(MIN_PROFIT),
        "strict_candidates": len(candidates), "poly_slugs": len(slugs), "poly_seeded": seeded,
        "trigger_max_age_ms": MAX_TRIGGER_AGE_MS,
        "kalshi_min_interval_ms": KALSHI_MIN_INTERVAL_MS,
        "ticker_min_interval_ms": TICKER_MIN_INTERVAL_MS,
        "order_entry": live_enabled(),
        "classification": "LIVE_RULE_RISK_PROBE_NOT_GUARANTEED_ARB" if live_enabled() else "RULE_RISK_SHADOW",
    }, separators=(",", ":")))

    async with httpx.AsyncClient(timeout=4.0) as http:
        try:
            while True:
                now = time.monotonic()
                if now - last_participation >= PARTICIPATION_REFRESH_S:
                    games = await fetch_game_boxscores(http, {c.game_date for c in candidates})
                    next_cleared: dict[str, dict[str, Any]] = {}
                    for c in candidates:
                        proof = participation_proof(family=c.family, player_name=c.player, poly_slug=c.poly_slug, games=games)
                        if base.assess_rule_equivalence(c.family, proof).settlement_equivalent_now:
                            next_cleared[c.key] = proof_dict(proof)
                    cleared = next_cleared
                    last_participation = now
                    logger.info("MLB_KP_RULE_RISK_PARTICIPATION strict=%d proven=%d", len(candidates), len(cleared))

                try:
                    update = await asyncio.wait_for(store.updates.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    update = None

                if update is not None and update.venue == "polymarket" and update.kind == "ws":
                    stats["poly_events"] += 1
                    age_ms = int(time.time() * 1000) - update.received_ms
                    if age_ms > MAX_TRIGGER_AGE_MS:
                        stats["stale_poly_events"] += 1
                    else:
                        for candidate in by_poly.get(update.market_id, []):
                            proof = cleared.get(candidate.key)
                            if proof is None:
                                stats["uncleared_events"] += 1
                                continue
                            multiplier = series_meta.get(base.FAMILY_SERIES[candidate.family])
                            if multiplier is None or not await limiter.allow(candidate.kalshi_ticker):
                                continue
                            pquote = store.quote(candidate.poly_slug, max_age_ms=MAX_TRIGGER_AGE_MS)
                            if pquote is None:
                                stats["stale_poly_events"] += 1
                                continue
                            kbook, krtt, status = await _exact_kalshi_book(http, candidate.kalshi_ticker)
                            stats["exact_kalshi_reads"] += 1
                            if status == 200:
                                stats["kalshi_200"] += 1
                            elif status == 429:
                                stats["kalshi_429"] += 1
                            if kbook is None:
                                continue
                            provisional = score_q1(candidate, _top_book(pquote), kbook, kalshi_fee_multiplier=multiplier, kalshi_rtt_ms=krtt, proof=proof)
                            if provisional is None or provisional.locked_profit < MIN_PROFIT:
                                continue
                            stats["provisional_positive"] += 1
                            logger.warning("MLB_KP_RULE_RISK_PROVISIONAL %s", json.dumps(provisional.as_dict(), separators=(",", ":"), default=str))

                            # Final gate intentionally ignores the trigger quote and does two fresh
                            # direct P+K reads with the original <=100ms/<=50ms/persistence rules.
                            final_plan = await persistent_plan(http, candidate, multiplier, proof)
                            if final_plan is None:
                                continue
                            stats["final_positive"] += 1
                            logger.warning("MLB_KP_RULE_RISK_EXECUTABLE %s", json.dumps(final_plan.as_dict(), separators=(",", ":"), default=str))
                            if not live_enabled():
                                continue
                            report, hard_halt = await execute(final_plan, http, multiplier)
                            stats["executions"] += 1
                            logger.warning("MLB_KP_RULE_RISK_EXECUTION_RESULT %s", json.dumps(report, separators=(",", ":"), default=str))
                            # A q=1 live run is intentionally one-shot whether filled, rejected,
                            # or hard-halted; Railway restart does not silently spend another probe.
                            return 70 if hard_halt else 0

                now = time.monotonic()
                if now - last_summary >= SUMMARY_S:
                    logger.info("MLB_KP_RULE_RISK_EVENT_SUMMARY %s", json.dumps({
                        **stats,
                        "live": live_enabled(), "strict_candidates": len(candidates),
                        "participation_proven": len(cleared), "poly_connected": store.connected,
                        "poly_ws_updates": store.ws_updates, "poly_books": store.quote_count,
                        "limiter_allowed": limiter.allowed,
                        "limiter_global_suppressed": limiter.global_suppressed,
                        "limiter_ticker_suppressed": limiter.ticker_suppressed,
                    }, separators=(",", ":")))
                    last_summary = now
        finally:
            poly_task.cancel()
            await asyncio.gather(poly_task, return_exceptions=True)
            try:
                poly_client.close()
            except Exception:
                pass


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
