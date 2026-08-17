"""Event-driven, one-shot auto runner for proven same-Kalshi nested structures.

Safety model:
- default mode is paper/read-only;
- at most one execution attempt per process;
- production live guard remains authoritative (>=3c floor, <=6h close, one lot);
- requires the existing CLI + env + one-use arm-token authorization chain;
- only the currently proven greater-strike orientation is accepted here:
  YES(lower strike) + NO(higher strike);
- seed IOC is sent first by execute_seed_first; maker is never sent unless the
  seed fills exactly 1.00 contract;
- the process exits after its first paper GO or live execution attempt.

This module does not remove or weaken any existing live guard.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import logging
import re
import time
from typing import Any

import httpx
import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_btc_ws_shadow import KalshiOrderBook
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata
from src.kalshi_nested_live_probe_guard import D, NestedLiveProbeSpec, validate_spec
from src.kalshi_nested_probe_quality_watch import probe_quality_gate
from src.kalshi_nested_rolling_shadow import Candidate, NY, build_candidates, desired_shadow_quote
from src.kalshi_nested_seed_first_probe import execute_seed_first
from src.kalshi_nested_trade_through_shadow import KALSHI_WS, KALSHI_WS_SIGN_PATH, _ws_header_kw

logger = logging.getLogger("kalshi_arb.kalshi_nested_auto_once")
STRIKE_RE = re.compile(r"-T(-?\d+(?:\.\d+)?)$")
MIN_AUTO_TARGET = D("0.03")
MAX_AUTO_MAKER_IMPROVEMENT = D("0.02")
MAX_AUTO_SEED_COST = D("0.01")
MAX_AUTO_TIME_TO_CLOSE_SECONDS = 6 * 3600


@dataclass
class AttemptLatch:
    """In-process hard stop: exactly one execution attempt can ever be claimed."""
    claimed: bool = False

    def claim(self) -> bool:
        if self.claimed:
            return False
        self.claimed = True
        return True


def strike_from_ticker(ticker: str) -> Decimal:
    match = STRIKE_RE.search(str(ticker))
    if not match:
        raise ValueError(f"cannot parse strike from ticker {ticker!r}")
    return D(match.group(1))


async def fresh_fee_metadata(series: str):
    async with httpx.AsyncClient(timeout=10.0) as http:
        return await fetch_series_fee_metadata(http, series)


def candidate_to_spec(
    *,
    candidate: Candidate,
    quote: Decimal,
    economics: dict[str, Any],
    fee_type: str,
    fee_multiplier: Decimal,
    scheduled_fee_changes: tuple[dict, ...],
    maker_rest_seconds: int,
    now: datetime | None = None,
) -> NestedLiveProbeSpec:
    """Freeze one WS GO into the exact guarded live spec.

    First automatic version deliberately supports only YES(lower)+NO(higher).
    That is the proven ``greater`` nested relationship currently observed on the
    BTC/ETH/SOL daily ladders. Other orientations fail closed until separately
    proven and tested.
    """
    if candidate.maker_side != "yes" or candidate.hedge_side != "no":
        raise ValueError("auto-once supports only YES(lower)+NO(higher)")

    lower = strike_from_ticker(candidate.maker_ticker)
    higher = strike_from_ticker(candidate.hedge_ticker)
    if not lower < higher:
        raise ValueError("auto-once requires maker strike lower than hedge strike")

    if str(economics.get("reason") or "") != "READY":
        raise ValueError("economics are not READY")
    hedge_price = D(str(economics.get("hedge_price") or "-1"))

    spec = NestedLiveProbeSpec(
        event_ticker=candidate.event_ticker,
        event_close=candidate.event_close,
        strike_type="greater",
        lower_ticker=candidate.maker_ticker,
        lower_strike=lower,
        higher_ticker=candidate.hedge_ticker,
        higher_strike=higher,
        maker_ticker=candidate.maker_ticker,
        maker_side="yes",
        maker_cost=D(quote),
        hedge_ticker=candidate.hedge_ticker,
        hedge_side="no",
        hedge_cost=hedge_price,
        fee_type=str(fee_type),
        fee_multiplier=D(fee_multiplier),
        scheduled_fee_changes=scheduled_fee_changes,
        target_profit=D(candidate.target_profit),
        quantity=D("1"),
        maker_rest_seconds=int(maker_rest_seconds),
    )
    validate_spec(spec, now=now)
    return spec


async def auto_once(
    *,
    seconds: float,
    series: list[str],
    target_profit: Decimal,
    max_candidates: int,
    maker_rest_seconds: int,
    live: bool,
) -> dict[str, Any]:
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is not configured")
    if D(target_profit) < MIN_AUTO_TARGET:
        raise ValueError("auto runner target cannot be below 3c")
    if seconds <= 0:
        raise ValueError("seconds must be positive")

    candidates = await build_candidates(
        series=series,
        target_profit=D(target_profit),
        max_candidates=max_candidates,
        max_time_to_close_seconds=MAX_AUTO_TIME_TO_CLOSE_SECONDS,
    )
    if not candidates:
        row = {
            "status": "NO_CANDIDATES",
            "live_requested": bool(live),
            "actual_order_placed": False,
        }
        logger.info("NESTED_AUTO_ONCE_DONE %s", json.dumps(row, separators=(",", ":")))
        return row

    tickers = sorted({ticker for c in candidates for ticker in (c.maker_ticker, c.hedge_ticker)})
    books = {ticker: KalshiOrderBook(ticker) for ticker in tickers}
    snapshots: set[str] = set()
    by_market: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_market.setdefault(candidate.maker_ticker, []).append(candidate)
        by_market.setdefault(candidate.hedge_ticker, []).append(candidate)

    latch = AttemptLatch()
    private_key = load_private_key()
    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    deadline = time.monotonic() + seconds

    logger.info("NESTED_AUTO_ONCE_START %s", json.dumps({
        "live_requested": bool(live),
        "candidates": len(candidates),
        "markets": len(tickers),
        "target_profit": str(target_profit),
        "max_maker_improvement": str(MAX_AUTO_MAKER_IMPROVEMENT),
        "max_seed_cost": str(MAX_AUTO_SEED_COST),
        "max_attempts": 1,
        "max_quantity": "1",
    }, separators=(",", ":")))

    async def evaluate(candidate: Candidate) -> dict[str, Any] | None:
        if candidate.maker_ticker not in snapshots or candidate.hedge_ticker not in snapshots:
            return None
        quote, economics = desired_shadow_quote(
            candidate=candidate,
            maker_book=books[candidate.maker_ticker],
            hedge_book=books[candidate.hedge_ticker],
        )
        ok, reason, detail = probe_quality_gate(
            quote=quote,
            economics=economics,
            target_profit=D(target_profit),
            max_maker_improvement=MAX_AUTO_MAKER_IMPROVEMENT,
            max_seed_cost=MAX_AUTO_SEED_COST,
        )
        if not ok or quote is None:
            return None
        if not latch.claim():
            return None

        base = {
            "status": "GO",
            "event_ticker": candidate.event_ticker,
            "maker_ticker": candidate.maker_ticker,
            "maker_side": candidate.maker_side,
            "maker_quote": str(quote),
            "hedge_ticker": candidate.hedge_ticker,
            "hedge_side": candidate.hedge_side,
            **detail,
            "live_requested": bool(live),
        }

        # Fee metadata is refreshed at the actual GO, not trusted from startup.
        meta = await fresh_fee_metadata(candidate.series)
        spec = candidate_to_spec(
            candidate=candidate,
            quote=D(quote),
            economics=economics,
            fee_type=str(meta.fee_type),
            fee_multiplier=D(meta.fee_multiplier),
            scheduled_fee_changes=tuple(meta.scheduled_changes or []),
            maker_rest_seconds=maker_rest_seconds,
        )

        if not live:
            row = {
                **base,
                "status": "PAPER_GO_EXIT",
                "locked_profit": str(economics.get("locked_profit")),
                "actual_order_placed": False,
                "read_only": True,
            }
            logger.info("NESTED_AUTO_ONCE_PAPER_GO %s", json.dumps(row, separators=(",", ":"), default=str))
            return row

        # execute_seed_first still requires --live + ENABLE...=YES + a one-use
        # arm-token file. It consumes the token immediately before the seed IOC.
        result = await execute_seed_first(spec, cli_live=True)
        row = {
            **base,
            "status": result.status,
            "execution": result.as_dict(),
            "actual_order_placed": result.seed_state is not None,
            "read_only": False,
        }
        logger.info("NESTED_AUTO_ONCE_LIVE_RESULT %s", json.dumps(row, separators=(",", ":"), default=str))
        return row

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
                result = await evaluate(candidate)
                if result is not None:
                    return result

    row = {
        "status": "TIMEOUT_NO_GO",
        "live_requested": bool(live),
        "actual_order_placed": False,
    }
    logger.info("NESTED_AUTO_ONCE_DONE %s", json.dumps(row, separators=(",", ":")))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=1800.0)
    parser.add_argument("--series", nargs="+", default=["KXBTCD", "KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", type=Decimal, default=MIN_AUTO_TARGET)
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--maker-rest-seconds", type=int, default=10)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(auto_once(
        seconds=args.seconds,
        series=[s.upper() for s in args.series],
        target_profit=args.target_profit,
        max_candidates=args.max_candidates,
        maker_rest_seconds=args.maker_rest_seconds,
        live=bool(args.live),
    ))
    print(json.dumps(result, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
