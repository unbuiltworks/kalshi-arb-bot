"""Read-only WebSocket watcher for a realistic first nested live-probe window.

This does not place orders. It watches the candidate books already used by the
nested rolling shadow and emits NESTED_PROBE_GO_SHADOW only when all of these hold:

- same-event nested economics still lock the requested floor;
- the cheap complementary seed is <= 1c;
- the maker quote improves the live maker bid by <= 2c;
- the maker bid is non-empty;
- both order books have delivered authenticated WebSocket snapshots.

The purpose is to distinguish a plausible 93c->95c / 94c->95c experiment from a
mathematically valid but operationally meaningless 56c->95c jump.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from decimal import Decimal
import json
import logging
import time
from typing import Any

import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.kalshi_nested_maker_shadow import D
from src.kalshi_nested_rolling_shadow import (
    Candidate,
    NY,
    build_candidates,
    desired_shadow_quote,
)
from src.kalshi_nested_trade_through_shadow import (
    KALSHI_WS,
    KALSHI_WS_SIGN_PATH,
    _ws_header_kw,
)

logger = logging.getLogger("kalshi_arb.kalshi_nested_probe_quality_watch")
DEFAULT_MAX_MAKER_IMPROVEMENT = D("0.02")
DEFAULT_MAX_SEED_COST = D("0.01")


def probe_quality_gate(
    *,
    quote: Decimal | None,
    economics: dict[str, Any],
    target_profit: Decimal,
    max_maker_improvement: Decimal = DEFAULT_MAX_MAKER_IMPROVEMENT,
    max_seed_cost: Decimal = DEFAULT_MAX_SEED_COST,
) -> tuple[bool, str, dict[str, str]]:
    """Pure GO/no-GO gate for the first operator-triggered probe."""
    if quote is None or str(economics.get("reason")) != "READY":
        return False, "ECONOMICS_NOT_READY", {}
    try:
        maker_bid = D(str(economics["maker_bid"]))
        seed_cost = D(str(economics["hedge_price"]))
        locked = D(str(economics["locked_profit"]))
    except Exception:
        return False, "MISSING_ECONOMICS", {}
    improvement = D(quote) - maker_bid
    detail = {
        "maker_bid": str(maker_bid),
        "maker_quote": str(quote),
        "maker_improvement": str(improvement),
        "seed_cost": str(seed_cost),
        "locked_profit": str(locked),
    }
    if maker_bid <= 0:
        return False, "EMPTY_MAKER_BID", detail
    if improvement <= 0:
        return False, "NOT_IMPROVING_BID", detail
    if improvement > D(max_maker_improvement):
        return False, "MAKER_IMPROVEMENT_TOO_LARGE", detail
    if seed_cost > D(max_seed_cost):
        return False, "SEED_TOO_EXPENSIVE", detail
    if locked < D(target_profit):
        return False, "LOCKED_PROFIT_BELOW_TARGET", detail
    return True, "GO_SHADOW", detail


def _go_payload(candidate: Candidate, economics: dict[str, Any], detail: dict[str, str]) -> dict[str, Any]:
    seconds_to_close = max(
        0.0,
        (candidate.event_close - datetime.now(NY)).total_seconds(),
    )
    return {
        "series": candidate.series,
        "event_ticker": candidate.event_ticker,
        "event_close": candidate.event_close.isoformat(),
        "seconds_to_close": seconds_to_close,
        "maker_ticker": candidate.maker_ticker,
        "maker_side": candidate.maker_side,
        "seed_ticker": candidate.hedge_ticker,
        "seed_side": candidate.hedge_side,
        "target_profit": str(candidate.target_profit),
        **detail,
        "hedge_size": str(economics.get("hedge_size")),
        "taker_fee": str(economics.get("taker_fee")),
        "classification": "OPERATOR_PROBE_GO_SHADOW_ONLY",
        "actual_order_placed": False,
        "read_only": True,
        "order_entry": False,
    }


async def watch_cycle(
    *,
    cycle_seconds: float,
    series: list[str],
    target_profit: Decimal,
    max_candidates: int,
    max_time_to_close_seconds: float,
    max_maker_improvement: Decimal,
    max_seed_cost: Decimal,
) -> dict[str, Any]:
    candidates = await build_candidates(
        series=series,
        target_profit=target_profit,
        max_candidates=max_candidates,
        max_time_to_close_seconds=max_time_to_close_seconds,
    )
    if not candidates:
        logger.info("NESTED_PROBE_GO_HEARTBEAT %s", json.dumps({
            "ready": 0,
            "candidates": 0,
            "reason": "NO_NEAR_TERM_CANDIDATES",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        await asyncio.sleep(min(cycle_seconds, 10.0))
        return {"candidates": 0, "go_transitions": 0}

    tickers = sorted({t for c in candidates for t in (c.maker_ticker, c.hedge_ticker)})
    books = {ticker: KalshiOrderBook(ticker) for ticker in tickers}
    snapshots: set[str] = set()
    by_market: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_market.setdefault(candidate.maker_ticker, []).append(candidate)
        by_market.setdefault(candidate.hedge_ticker, []).append(candidate)

    ready: set[tuple[str, str, str]] = set()
    go_transitions = 0
    private_key = load_private_key()
    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    deadline = time.monotonic() + cycle_seconds

    logger.info("NESTED_PROBE_GO_CYCLE %s", json.dumps({
        "candidates": len(candidates),
        "markets": len(tickers),
        "events": sorted({c.event_ticker for c in candidates}),
        "target_profit": str(target_profit),
        "max_maker_improvement": str(max_maker_improvement),
        "max_seed_cost": str(max_seed_cost),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))

    def evaluate(candidate: Candidate) -> None:
        nonlocal go_transitions
        key = (candidate.maker_ticker, candidate.maker_side, candidate.hedge_ticker)
        if candidate.maker_ticker not in snapshots or candidate.hedge_ticker not in snapshots:
            return
        quote, econ = desired_shadow_quote(
            candidate=candidate,
            maker_book=books[candidate.maker_ticker],
            hedge_book=books[candidate.hedge_ticker],
        )
        ok, reason, detail = probe_quality_gate(
            quote=quote,
            economics=econ,
            target_profit=target_profit,
            max_maker_improvement=max_maker_improvement,
            max_seed_cost=max_seed_cost,
        )
        if ok and key not in ready:
            ready.add(key)
            go_transitions += 1
            logger.info(
                "NESTED_PROBE_GO_SHADOW %s",
                json.dumps(_go_payload(candidate, econ, detail), separators=(",", ":"), default=str),
            )
        elif not ok and key in ready:
            ready.remove(key)
            logger.info("NESTED_PROBE_GO_CLEARED %s", json.dumps({
                "maker_ticker": candidate.maker_ticker,
                "maker_side": candidate.maker_side,
                "seed_ticker": candidate.hedge_ticker,
                "reason": reason,
                **detail,
                "actual_order_placed": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))

    async with websockets.connect(KALSHI_WS, open_timeout=5.0, **_ws_header_kw(headers)) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta"], "market_tickers": tickers},
        }))
        while time.monotonic() < deadline:
            timeout = min(0.5, max(0.01, deadline - time.monotonic()))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                for candidate in candidates:
                    evaluate(candidate)
                continue
            payload = json.loads(raw)
            if payload.get("type") == "error":
                raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
            if payload.get("type") not in {"orderbook_snapshot", "orderbook_delta"}:
                continue
            msg = payload.get("msg") or {}
            ticker = str(msg.get("market_ticker") or "")
            book = books.get(ticker)
            if book is None:
                continue
            book.apply(payload)
            if payload.get("type") == "orderbook_snapshot":
                snapshots.add(ticker)
            for candidate in by_market.get(ticker, []):
                evaluate(candidate)

    logger.info("NESTED_PROBE_GO_SUMMARY %s", json.dumps({
        "candidates": len(candidates),
        "go_transitions": go_transitions,
        "ready_at_cycle_end": len(ready),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    return {
        "candidates": len(candidates),
        "go_transitions": go_transitions,
        "ready_at_cycle_end": len(ready),
    }


async def rolling_watch(
    *,
    seconds: float,
    refresh_seconds: float,
    series: list[str],
    target_profit: Decimal,
    max_candidates: int,
    max_time_to_close_seconds: float,
    max_maker_improvement: Decimal,
    max_seed_cost: Decimal,
) -> None:
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    if refresh_seconds < 30:
        raise ValueError("refresh_seconds must be >= 30")
    start = time.monotonic()
    cycles = 0
    while seconds <= 0 or time.monotonic() - start < seconds:
        remaining = refresh_seconds if seconds <= 0 else min(
            refresh_seconds,
            max(0.1, seconds - (time.monotonic() - start)),
        )
        if remaining <= 0:
            break
        cycles += 1
        try:
            await watch_cycle(
                cycle_seconds=remaining,
                series=series,
                target_profit=target_profit,
                max_candidates=max_candidates,
                max_time_to_close_seconds=max_time_to_close_seconds,
                max_maker_improvement=max_maker_improvement,
                max_seed_cost=max_seed_cost,
            )
        except Exception as exc:
            logger.exception("NESTED_PROBE_GO_CYCLE_ERROR cycle=%s error=%s", cycles, str(exc)[:300])
            await asyncio.sleep(1.0)
    logger.info("NESTED_PROBE_GO_DONE %s", json.dumps({
        "cycles": cycles,
        "seconds": seconds,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=1800.0)
    parser.add_argument("--refresh-seconds", type=float, default=60.0)
    parser.add_argument("--series", nargs="+", default=["KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--max-candidates", type=int, default=40)
    parser.add_argument("--max-time-to-close-seconds", type=float, default=3600.0)
    parser.add_argument("--max-maker-improvement", type=Decimal, default=DEFAULT_MAX_MAKER_IMPROVEMENT)
    parser.add_argument("--max-seed-cost", type=Decimal, default=DEFAULT_MAX_SEED_COST)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(rolling_watch(
        seconds=args.seconds,
        refresh_seconds=args.refresh_seconds,
        series=[s.upper() for s in args.series],
        target_profit=args.target_profit,
        max_candidates=args.max_candidates,
        max_time_to_close_seconds=args.max_time_to_close_seconds,
        max_maker_improvement=args.max_maker_improvement,
        max_seed_cost=args.max_seed_cost,
    ))


if __name__ == "__main__":
    main()
