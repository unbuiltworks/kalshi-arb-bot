"""Event-driven, single-attempt Kalshi/Polymarket sports live service.

Discovery and books reuse the production shadow scanner's normalized identities,
but execution is restricted to an explicit rule-approval file. The service is
safe to deploy disarmed: without ENABLE_SPORTS_KP_LIVE=YES and a one-use ARM
file it can only log qualified opportunities.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws
from src.sports_kp_execution import (
    DEFAULT_ARM_TOKEN,
    DEFAULT_MAX_BOOK_AGE_MS,
    DEFAULT_MAX_QUOTE_SKEW_MS,
    DEFAULT_MIN_PROFIT,
    KPExecutionPlan,
    RuleApproval,
    build_kp_plan,
)
from src.sports_kp_live_coordinator import execute_after_fresh_revalidation
from src.strict_mlb_prop_snapshot import TopBook
from src.three_venue_mlb_hot import HotUniverse, discover_hot_universe
from src.three_venue_pricing_core import PricedQuote, make_priced_quote
from src.universal_prediction_quant import LockOpportunity, find_cross_venue_locks

logger = logging.getLogger("kalshi_arb.sports_kp_live_service")
D = Decimal


@dataclass
class CandidateState:
    first_seen_ms: int
    fingerprint: str


def _load_approvals(path: Path) -> dict[str, RuleApproval]:
    if not path.exists():
        return {}
    body = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, RuleApproval] = {}
    for raw in body.get("approvals", []):
        approval = RuleApproval.from_mapping(raw)
        if not approval.approved_for_execution:
            continue
        approval.validate()
        if approval.settlement_key in out:
            raise ValueError(f"duplicate approval for {approval.settlement_key}")
        out[approval.settlement_key] = approval
    return out


def _book_poly(q: Any) -> TopBook:
    return TopBook(q.yes_ask, q.yes_depth, q.no_ask, q.no_depth)


def _book_kalshi(q: Any) -> TopBook:
    return TopBook(q.yes_ask, q.yes_depth, q.no_ask, q.no_depth)


async def _kp_rows(
    key: str,
    universe: HotUniverse,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
) -> list[PricedQuote]:
    hot = universe.contracts[key]
    pside = hot.sides.get("polymarket")
    kside = hot.sides.get("kalshi")
    if pside is None or kside is None or hot.kalshi_fee_multiplier is None or not poly.connected:
        return []
    pq = poly.quote(pside.venue_market_id, max_age_ms=DEFAULT_MAX_BOOK_AGE_MS)
    kq = await kalshi.quote(kside.venue_market_id, max_age_ms=DEFAULT_MAX_BOOK_AGE_MS)
    if pq is None or kq is None:
        return []
    prow = make_priced_quote(
        pside, _book_poly(pq), received_ms=pq.received_ms,
        depth_source="polymarket_websocket_top_book", category="sports_mlb_game",
    )
    krow = make_priced_quote(
        kside, _book_kalshi(kq), received_ms=kq.received_ms,
        kalshi_fee_multiplier=hot.kalshi_fee_multiplier,
        depth_source="kalshi_websocket_top_book", category="sports_mlb_game",
    )
    return [row for row in (prow, krow) if row is not None]


def _best_lock(rows: list[PricedQuote]) -> LockOpportunity | None:
    locks = find_cross_venue_locks(
        [row.quote for row in rows],
        min_unit_profit=DEFAULT_MIN_PROFIT,
        max_quote_skew_ms=DEFAULT_MAX_QUOTE_SKEW_MS,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    )
    return locks[0] if locks else None


def _fingerprint(lock: LockOpportunity) -> str:
    """Fingerprint the executable economics, not timestamp/skew telemetry.

    Persistence should survive a second market-data update when the same exact
    legs and all-in prices remain available. Quote skew is still enforced by the
    lock finder and final execution gate, but changing receive timestamps must
    not reset the 10 ms persistence timer by itself.
    """
    stable = {
        "settlement_key": lock.settlement_key,
        "yes_venue": lock.yes_venue,
        "yes_market_id": lock.yes_market_id,
        "no_venue": lock.no_venue,
        "no_market_id": lock.no_market_id,
        "unit_cost": str(lock.unit_cost),
        "unit_profit": str(lock.unit_profit),
        "max_quantity": str(lock.max_quantity),
    }
    return json.dumps(stable, sort_keys=True, separators=(",", ":"))


async def _fresh_plan(
    key: str,
    universe: HotUniverse,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
    approval: RuleApproval,
) -> KPExecutionPlan:
    rows = await _kp_rows(key, universe, poly, kalshi)
    if len(rows) != 2:
        raise PermissionError("fresh K/P books unavailable")
    lock = _best_lock(rows)
    if lock is None:
        raise PermissionError("fresh after-fee lock disappeared")
    return build_kp_plan(lock, rows, approval)


async def _next_update(poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    tasks = [asyncio.create_task(poly.updates.get()), asyncio.create_task(kalshi.updates.get())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def run(args: argparse.Namespace) -> int:
    approvals = _load_approvals(Path(args.approvals))
    logger.info("SPORTS_KP_LIVE_START %s", json.dumps({
        "approved_contracts": len(approvals),
        "live_env_gate": os.getenv("ENABLE_SPORTS_KP_LIVE", "NO").strip().upper() == "YES",
        "arm_token_present": Path(args.arm_token).is_file(),
        "one_attempt_cap": 1,
        "min_unit_profit": str(DEFAULT_MIN_PROFIT),
        "max_quote_skew_ms": DEFAULT_MAX_QUOTE_SKEW_MS,
        "max_final_book_age_ms": DEFAULT_MAX_BOOK_AGE_MS,
        "order_entry": False if not approvals else "GATED",
    }, separators=(",", ":")))

    async with httpx.AsyncClient(timeout=30.0) as http:
        universe = await discover_hot_universe(http)
    executable_keys = {
        key for key, approval in approvals.items()
        if key in universe.contracts
        and {"kalshi", "polymarket"}.issubset(universe.contracts[key].sides)
        and universe.contracts[key].sides["polymarket"].venue_market_id == approval.polymarket_slug
        and universe.contracts[key].sides["kalshi"].venue_market_id == approval.kalshi_ticker
    }
    logger.info("SPORTS_KP_APPROVAL_JOIN %s", json.dumps({
        "hot_contracts": len(universe.contracts),
        "approved_contracts": len(approvals),
        "joined_execution_contracts": len(executable_keys),
        "joined_keys": sorted(executable_keys),
    }, separators=(",", ":")))

    if not executable_keys:
        while True:
            await asyncio.sleep(3600)

    poly_slugs = sorted({universe.contracts[k].sides["polymarket"].venue_market_id for k in executable_keys})
    kalshi_tickers = sorted({universe.contracts[k].sides["kalshi"].venue_market_id for k in executable_keys})
    poly = EventingPolyBookStore()
    kalshi = EventingKalshiCrossBooks(kalshi_tickers)

    async with httpx.AsyncClient(timeout=8.0) as seed_http:
        sem = asyncio.Semaphore(20)
        poly.notifications_enabled = False
        try:
            await asyncio.gather(*(_fetch_poly_book(seed_http, poly, slug, sem) for slug in poly_slugs))
        finally:
            poly.notifications_enabled = True

    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"], timeout=10.0,
    )
    poly_task = asyncio.create_task(run_poly_ws(client, poly, poly_slugs), name="sports_kp_poly_ws")
    kalshi_task = asyncio.create_task(kalshi.run(), name="sports_kp_kalshi_ws")
    tasks = [poly_task, kalshi_task]
    try:
        deadline = time.monotonic() + args.startup_timeout_seconds
        while time.monotonic() < deadline:
            if any(task.done() for task in tasks):
                raise RuntimeError("market-data task exited during startup")
            if poly.connected and kalshi.initialized_count == len(kalshi_tickers):
                break
            await asyncio.sleep(0.025)
        else:
            raise RuntimeError("K/P live service startup timed out")

        logger.info("SPORTS_KP_LIVE_READY %s", json.dumps({
            "joined_execution_contracts": len(executable_keys),
            "poly_books": poly.quote_count,
            "kalshi_books": kalshi.initialized_count,
            "live_env_gate": os.getenv("ENABLE_SPORTS_KP_LIVE", "NO").strip().upper() == "YES",
            "arm_token_present": Path(args.arm_token).is_file(),
        }, separators=(",", ":")))

        by_market: dict[tuple[str, str], set[str]] = {}
        for key in executable_keys:
            hot = universe.contracts[key]
            for venue in ("polymarket", "kalshi"):
                side = hot.sides[venue]
                by_market.setdefault((venue, side.venue_market_id), set()).add(key)

        states: dict[str, CandidateState] = {}
        while True:
            if poly_task.done() or kalshi_task.done():
                raise RuntimeError("live market-data task exited")
            trigger = await _next_update(poly, kalshi)
            for key in by_market.get((trigger.venue, trigger.market_id), set()):
                rows = await _kp_rows(key, universe, poly, kalshi)
                lock = _best_lock(rows) if len(rows) == 2 else None
                if lock is None:
                    states.pop(key, None)
                    continue
                try:
                    plan = build_kp_plan(lock, rows, approvals[key])
                except Exception as exc:
                    states.pop(key, None)
                    logger.warning("SPORTS_KP_PLAN_REJECTED %s", json.dumps({
                        "settlement_key": key, "reason": f"{type(exc).__name__}:{str(exc)[:200]}"
                    }, separators=(",", ":")))
                    continue

                fp = _fingerprint(lock)
                now_ms = int(time.time() * 1000)
                state = states.get(key)
                if state is None or state.fingerprint != fp:
                    states[key] = CandidateState(now_ms, fp)
                    continue
                persistence_ms = now_ms - state.first_seen_ms
                if persistence_ms < args.min_persistence_ms:
                    continue

                armed = (
                    os.getenv("ENABLE_SPORTS_KP_LIVE", "NO").strip().upper() == "YES"
                    and Path(args.arm_token).is_file()
                )
                logger.info("SPORTS_KP_QUALIFIED %s", json.dumps({
                    "settlement_key": key,
                    "persistence_ms": persistence_ms,
                    "unit_profit": str(plan.unit_profit),
                    "quote_skew_ms": plan.quote_skew_ms,
                    "armed": armed,
                    "plan": plan.as_dict(),
                }, separators=(",", ":"), default=str))
                if not armed:
                    states[key] = CandidateState(now_ms, fp)
                    continue

                async def provider(key=key, approval=approvals[key]):
                    return await _fresh_plan(key, universe, poly, kalshi, approval)

                result = await execute_after_fresh_revalidation(
                    plan, provider,
                    arm_token=Path(args.arm_token),
                    live_requested=True,
                )
                logger.info("SPORTS_KP_LIVE_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
                return 70 if result.get("hard_halt_required") else 0
    finally:
        for task in tasks:
            task.cancel()
        await kalshi.close()
        try:
            client.close()
        except Exception:
            pass
        await asyncio.gather(*tasks, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--approvals", default="config/sports_kp_execution_approvals.json")
    p.add_argument("--arm-token", default=str(DEFAULT_ARM_TOKEN))
    p.add_argument("--startup-timeout-seconds", type=float, default=20.0)
    p.add_argument("--min-persistence-ms", type=int, default=10)
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
