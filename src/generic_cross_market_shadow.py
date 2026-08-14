"""Generic event-driven Polymarket US <-> Kalshi shadow scanner.

Only explicit registry pairs are eligible. There is no fuzzy matching and no order
entry. The registry can contain partially audited pairs for research, but each log
carries its settlement-status label so a normal-event semantic match is never
mistaken for a fully verified executable arbitrage.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import signal
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import polymarket_kalshi_shadow as calc
from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.polymarket_kalshi_shadow_v2 import _fetch_poly_book, run_poly_ws

logger = logging.getLogger("kalshi_arb.generic_cross_market_shadow")

ALLOWED_SETTLEMENT_STATUSES = {
    "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    "FULL_RULES_AUDITED_SHADOW",
}


@dataclass(frozen=True)
class PairSpec:
    pair_id: str
    category: str
    poly_slug: str
    kalshi_ticker: str
    settlement_status: str
    settlement_basis: str
    settlement_source: str
    audit_note: str


@dataclass
class ActiveSignal:
    opened_ms: int
    fingerprint: tuple[Any, ...]
    payload: dict[str, Any]


class Stats:
    def __init__(self) -> None:
        self.started_ms = int(time.time() * 1000)
        self.triggers = 0
        self.scans = 0
        self.comparable = 0
        self.positive = 0
        self.opens = 0
        self.moves = 0
        self.closes = 0
        self.calc_us: deque[float] = deque(maxlen=20000)
        self.trigger_to_calc_us: deque[float] = deque(maxlen=20000)

    @staticmethod
    def pct(values: deque[float], q: float) -> float | None:
        if not values:
            return None
        rows = sorted(values)
        idx = min(len(rows) - 1, max(0, round((len(rows) - 1) * q)))
        return round(rows[idx], 2)

    def as_dict(self, poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks, active: int) -> dict[str, Any]:
        return {
            "uptime_s": round((int(time.time() * 1000) - self.started_ms) / 1000, 1),
            "triggers": self.triggers,
            "scans": self.scans,
            "comparable": self.comparable,
            "positive_observations": self.positive,
            "opens": self.opens,
            "moves": self.moves,
            "closes": self.closes,
            "active_signals": active,
            "calc_us_p50": self.pct(self.calc_us, 0.50),
            "calc_us_p95": self.pct(self.calc_us, 0.95),
            "trigger_to_calc_us_p50": self.pct(self.trigger_to_calc_us, 0.50),
            "trigger_to_calc_us_p95": self.pct(self.trigger_to_calc_us, 0.95),
            "poly_ws_updates": poly.ws_updates,
            "kalshi_initialized_books": kalshi.initialized_count,
            "poly_queue": poly.updates.qsize(),
            "kalshi_queue": kalshi.updates.qsize(),
            "poly_dropped_updates": poly.dropped_updates,
            "kalshi_dropped_updates": kalshi.dropped_updates,
        }


def load_pairs(path: Path) -> list[PairSpec]:
    raw = json.loads(path.read_text())
    rows = raw.get("pairs", [])
    pairs: list[PairSpec] = []
    seen_ids: set[str] = set()
    seen_poly: set[str] = set()
    seen_kalshi: set[str] = set()
    for row in rows:
        if not row.get("enabled_shadow", False):
            continue
        status = str(row.get("settlement_status") or "")
        if status not in ALLOWED_SETTLEMENT_STATUSES:
            raise ValueError(f"Unsupported settlement_status: {status!r}")
        pair = PairSpec(
            pair_id=str(row["pair_id"]),
            category=str(row["category"]),
            poly_slug=str(row["poly_slug"]),
            kalshi_ticker=str(row["kalshi_ticker"]),
            settlement_status=status,
            settlement_basis=str(row.get("settlement_basis") or ""),
            settlement_source=str(row.get("settlement_source") or ""),
            audit_note=str(row.get("audit_note") or ""),
        )
        if pair.pair_id in seen_ids:
            raise ValueError(f"Duplicate pair_id: {pair.pair_id}")
        if pair.poly_slug in seen_poly:
            raise ValueError(f"Duplicate Polymarket slug: {pair.poly_slug}")
        if pair.kalshi_ticker in seen_kalshi:
            raise ValueError(f"Duplicate Kalshi ticker: {pair.kalshi_ticker}")
        seen_ids.add(pair.pair_id); seen_poly.add(pair.poly_slug); seen_kalshi.add(pair.kalshi_ticker)
        pairs.append(pair)
    if not pairs:
        raise ValueError("No enabled shadow pairs")
    return pairs


def _fingerprint(plan: calc.ShadowPlan) -> tuple[Any, ...]:
    return (
        plan.direction, plan.quantity, str(plan.poly_price), str(plan.kalshi_price), str(plan.profit)
    )


def _task_error(name: str, task: asyncio.Task[Any]) -> RuntimeError:
    if task.cancelled():
        return RuntimeError(f"{name} feed task cancelled")
    exc = task.exception()
    if exc is None:
        return RuntimeError(f"{name} feed task exited unexpectedly")
    return RuntimeError(f"{name} feed task failed: {type(exc).__name__}: {exc}")


def _drain(q: asyncio.Queue[BookUpdate]) -> int:
    n = 0
    while True:
        try:
            q.get_nowait(); n += 1
        except asyncio.QueueEmpty:
            return n


async def _next_update(poly: EventingPolyBookStore, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    pt = asyncio.create_task(poly.updates.get())
    kt = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait({pt, kt}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def _seed_poly(http: httpx.AsyncClient, store: EventingPolyBookStore, slugs: list[str]) -> int:
    sem = asyncio.Semaphore(12)
    store.notifications_enabled = False
    try:
        results = await asyncio.gather(*[_fetch_poly_book(http, store, slug, sem) for slug in slugs])
        return sum(bool(x) for x in results)
    finally:
        store.notifications_enabled = True


async def _scan(
    pair: PairSpec,
    trigger: BookUpdate,
    *,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
    bankroll: Decimal,
    poly_fee: Decimal,
    kalshi_fee: Decimal,
    max_age_ms: int,
    active: dict[str, ActiveSignal],
    stats: Stats,
) -> None:
    started_ns = time.perf_counter_ns()
    now_ms = int(time.time() * 1000)
    pq = poly.quote(pair.poly_slug, max_age_ms=max_age_ms)
    kq = await kalshi.quote(pair.kalshi_ticker, max_age_ms=max_age_ms)
    stats.scans += 1
    plan = None
    if pq is not None and kq is not None and poly.connected:
        stats.comparable += 1
        plan = calc.best_plan(
            pq,
            kq,
            bankroll=bankroll,
            poly_fee_rate=poly_fee,
            kalshi_fee_rate=kalshi_fee,
        )
    ended_ns = time.perf_counter_ns()
    calc_us = (ended_ns - started_ns) / 1000
    trigger_us = (ended_ns - trigger.mono_ns) / 1000
    stats.calc_us.append(calc_us); stats.trigger_to_calc_us.append(trigger_us)

    prior = active.get(pair.pair_id)
    if plan is None or plan.profit <= 0 or pq is None or kq is None:
        if prior is not None:
            stats.closes += 1
            payload = dict(prior.payload)
            payload.update({
                "kind": "CLOSE",
                "ts_ms": now_ms,
                "lifetime_ms": now_ms - prior.opened_ms,
                "close_trigger_venue": trigger.venue,
                "close_trigger_kind": trigger.kind,
            })
            logger.info("GENERIC_SHADOW %s", json.dumps(payload, separators=(",", ":")))
            active.pop(pair.pair_id, None)
        return

    stats.positive += 1
    poly_age = max(0, now_ms - pq.received_ms)
    kalshi_age = max(0, now_ms - kq.received_ms)
    payload = {
        "kind": "OPEN" if prior is None else "MOVE",
        "ts_ms": now_ms,
        "pair_id": pair.pair_id,
        "category": pair.category,
        "settlement_status": pair.settlement_status,
        "settlement_basis": pair.settlement_basis,
        "settlement_source": pair.settlement_source,
        "poly_slug": pair.poly_slug,
        "kalshi_ticker": pair.kalshi_ticker,
        **plan.as_dict(),
        "trigger_venue": trigger.venue,
        "trigger_kind": trigger.kind,
        "poly_age_ms": poly_age,
        "kalshi_age_ms": kalshi_age,
        "cross_receive_skew_ms": abs(pq.received_ms - kq.received_ms),
        "kalshi_exchange_ts_ms": kq.exchange_ts_ms,
        "kalshi_seq": kq.seq,
        "calc_us": round(calc_us, 2),
        "trigger_to_calc_us": round(trigger_us, 2),
        "quality": "FRESH_250MS" if max(poly_age, kalshi_age) <= 250 else "RECENT",
    }
    fp = _fingerprint(plan)
    if prior is None:
        active[pair.pair_id] = ActiveSignal(now_ms, fp, payload)
        stats.opens += 1
        payload["lifetime_ms"] = 0
        logger.info("GENERIC_SHADOW %s", json.dumps(payload, separators=(",", ":")))
    elif prior.fingerprint != fp:
        stats.moves += 1
        payload["lifetime_ms"] = now_ms - prior.opened_ms
        prior.fingerprint = fp; prior.payload = payload
        logger.info("GENERIC_SHADOW %s", json.dumps(payload, separators=(",", ":")))


async def run(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    pairs = load_pairs(config_path)
    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise RuntimeError("Polymarket US websocket credentials are required")

    bankroll = Decimal(args.bankroll)
    poly_fee = Decimal(os.getenv("POLYMARKET_TAKER_FEE_RATE", args.poly_fee_rate))
    kalshi_fee = Decimal(os.getenv("KALSHI_TAKER_FEE_RATE", args.kalshi_fee_rate))
    logger.info("GENERIC_START %s", json.dumps({
        "shadow_only": True,
        "pair_count": len(pairs),
        "bankroll": str(bankroll),
        "max_quote_age_ms": args.max_quote_age_ms,
        "settlement_statuses": sorted({p.settlement_status for p in pairs}),
    }, separators=(",", ":")))

    async with httpx.AsyncClient(timeout=15.0) as http:
        poly = EventingPolyBookStore()
        seeded = await _seed_poly(http, poly, [p.poly_slug for p in pairs])
        live_pairs = [p for p in pairs if poly.quote(p.poly_slug, max_age_ms=60_000) is not None]
        if not live_pairs:
            raise RuntimeError("No configured pairs currently have a two-sided Polymarket book")
        if len(live_pairs) != len(pairs):
            missing = sorted(set(p.pair_id for p in pairs) - set(p.pair_id for p in live_pairs))
            logger.warning("GENERIC_EXCLUDED_NO_POLY_BOOK %s", json.dumps(missing))

        by_poly = {p.poly_slug: p for p in live_pairs}
        by_kalshi = {p.kalshi_ticker: p for p in live_pairs}
        kalshi = EventingKalshiCrossBooks(list(by_kalshi))
        client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
        poly_task = asyncio.create_task(run_poly_ws(client, poly, list(by_poly)), name="generic_poly_ws")
        kalshi_task = asyncio.create_task(kalshi.run(), name="generic_kalshi_ws")

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if poly_task.done(): raise _task_error("Polymarket", poly_task)
            if kalshi_task.done(): raise _task_error("Kalshi", kalshi_task)
            if poly.connected and kalshi.initialized_count == len(live_pairs):
                break
            await asyncio.sleep(0.025)
        else:
            raise RuntimeError(
                f"Generic startup timeout poly_connected={poly.connected} "
                f"kalshi={kalshi.initialized_count}/{len(live_pairs)} seeded={seeded}/{len(pairs)}"
            )

        _drain(poly.updates); _drain(kalshi.updates)
        logger.info("GENERIC_READY %s", json.dumps({
            "configured": len(pairs), "live_pairs": len(live_pairs), "rest_is_signal_source": False
        }, separators=(",", ":")))

        stats = Stats()
        active: dict[str, ActiveSignal] = {}
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try: loop.add_signal_handler(sig, stop.set)
            except NotImplementedError: pass

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(10)
                logger.info("GENERIC_SUMMARY %s", json.dumps(stats.as_dict(poly, kalshi, len(active)), separators=(",", ":")))

        hb = asyncio.create_task(heartbeat())
        try:
            while not stop.is_set():
                ut = asyncio.create_task(_next_update(poly, kalshi))
                st = asyncio.create_task(stop.wait())
                done, _ = await asyncio.wait({ut, st, poly_task, kalshi_task}, return_when=asyncio.FIRST_COMPLETED)
                if poly_task in done:
                    ut.cancel(); st.cancel(); await asyncio.gather(ut, st, return_exceptions=True)
                    raise _task_error("Polymarket", poly_task)
                if kalshi_task in done:
                    ut.cancel(); st.cancel(); await asyncio.gather(ut, st, return_exceptions=True)
                    raise _task_error("Kalshi", kalshi_task)
                if st in done and st.result():
                    ut.cancel(); await asyncio.gather(ut, return_exceptions=True); break
                st.cancel(); await asyncio.gather(st, return_exceptions=True)
                trigger = ut.result(); stats.triggers += 1
                pair = by_poly.get(trigger.market_id) if trigger.venue == "polymarket" else by_kalshi.get(trigger.market_id)
                if pair is not None:
                    await _scan(
                        pair, trigger, poly=poly, kalshi=kalshi, bankroll=bankroll,
                        poly_fee=poly_fee, kalshi_fee=kalshi_fee,
                        max_age_ms=args.max_quote_age_ms, active=active, stats=stats,
                    )
        finally:
            hb.cancel(); poly_task.cancel(); kalshi_task.cancel()
            await kalshi.close()
            try: client.close()
            except Exception: pass
            await asyncio.gather(hb, poly_task, kalshi_task, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/cross_market_shadow_pairs.json")
    p.add_argument("--bankroll", default="50")
    p.add_argument("--max-quote-age-ms", type=int, default=1500)
    p.add_argument("--poly-fee-rate", default="0.05")
    p.add_argument("--kalshi-fee-rate", default="0.07")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
