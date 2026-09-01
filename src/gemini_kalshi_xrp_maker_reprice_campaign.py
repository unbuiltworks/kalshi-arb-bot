"""Bounded cancel/reprice campaign for the proven XRP maker canary.

This is a thin orchestration layer over ``gemini_kalshi_xrp_maker_triggered_canary``.
It deliberately reuses that module's already-tested atomic <=50ms paired-book feed,
partial-fill fee math, $1 Command Center reservation, first-fill freeze, exact
fractional Kalshi FOK hedge, flatten paths, and settlement-risk labeling.

The only new behavior is fill pursuit:
- discover/certify one viable XRP 15m pair once per campaign (avoids catalog/API churn);
- skip an expiring current window and select the next window with >90s remaining;
- run short quote rounds with a unique durable trade id for each round;
- each round recomputes the highest partial-safe maker quote from fresh atomic books;
- only post when that safe quote is at/above the current Gemini bid;
- after a confirmed zero-fill cancel, immediately start a fresh round and reprice;
- any fill, ambiguous cancel, rejected order, hedge/flatten event, or hard halt stops
  the entire campaign; there is never more than one resting maker order at a time.

This lane remains threshold-dominance INDEX/RULE RISK, not guaranteed arbitrage.
Successful two-leg execution remains FILLED_NOT_SETTLED until settlement authority.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Iterable

from src import gemini_kalshi_xrp_maker_live_canary as canary
from src import gemini_kalshi_xrp_maker_triggered_canary as triggered

MAX_ROUNDS = max(1, min(int(os.getenv("CRYPTO_MAKER_REPRICE_MAX_ROUNDS", "12")), 24))
CAMPAIGN_SECONDS = max(10.0, min(float(os.getenv("CRYPTO_MAKER_REPRICE_SECONDS", "120")), 300.0))
ROUND_REST_SECONDS = max(1.0, min(float(os.getenv("CRYPTO_MAKER_REPRICE_REST_SECONDS", "4")), 15.0))
ROUND_WAIT_SECONDS = max(1.0, min(float(os.getenv("CRYPTO_MAKER_REPRICE_WAIT_SECONDS", "8")), 30.0))
INTER_ROUND_SECONDS = max(0.05, min(float(os.getenv("CRYPTO_MAKER_REPRICE_PAUSE_SECONDS", "0.25")), 2.0))

SAFE_ZERO_FILL_STATUSES = {
    "NO_FILLABLE_PARTIAL_SAFE_AT_BID_WINDOW",
    "MAKER_CANCELED_ZERO_FILL",
}


def emit(stage: str, **fields: Any) -> None:
    print(
        "CRYPTO_XRP_MAKER_REPRICE "
        + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str),
        flush=True,
    )


def round_trade_id(root_trade_id: str, round_number: int) -> str:
    base = str(root_trade_id).strip()[:72]
    return f"{base}-r{int(round_number):02d}"[:80]


def safe_to_continue(result: dict[str, Any]) -> bool:
    status = str(result.get("status") or "")
    if status not in SAFE_ZERO_FILL_STATUSES:
        return False
    if status == "MAKER_CANCELED_ZERO_FILL":
        cancel = result.get("cancel") or {}
        if not bool(cancel.get("ok")):
            return False
        if str(result.get("filled_quantity") or "0") not in {"", "0", "0.0", "0.00"}:
            return False
    return not bool(result.get("realized"))


def select_viable_pair(pairs: Iterable[Any], *, now_ts: float) -> Any | None:
    viable = sorted(
        (
            p
            for p in pairs
            if getattr(p, "asset", None) == triggered.ASSET
            and p.window_end.timestamp() - now_ts > triggered.MIN_SECONDS_TO_EXPIRY
        ),
        key=lambda p: p.window_end,
    )
    return viable[0] if viable else None


async def discover_next_viable() -> tuple[Any, Any, Any, Any]:
    """Discover once, choosing the next window that is not inside the no-new-risk cutoff."""
    async with triggered.httpx.AsyncClient(timeout=15.0) as http:
        pairs = await triggered._discover_pairs(http, 30.0)
        pair = select_viable_pair(pairs, now_ts=time.time())
        if pair is None:
            raise RuntimeError("no viable XRP 15m pair beyond expiry cutoff")
        fee_meta = await triggered.base.fetch_series_fee_metadata(
            http, triggered.base.ASSET_SERIES[triggered.ASSET]
        )
        market = await triggered._kalshi_market(http, pair.kalshi_ticker)
    geometry = await triggered.dominance.fetch_pair_geometry(pair, triggered.ASSET)
    return pair, fee_meta, market, geometry


async def run() -> dict[str, Any]:
    root_trade = canary.trade_id()
    armed = canary.live_armed()
    out: dict[str, Any] = {
        "mode": "LIVE_REPRICE_CAMPAIGN" if armed else "READ_ONLY_PREFLIGHT",
        "armed": armed,
        "root_trade_id": root_trade,
        "max_rounds": MAX_ROUNDS,
        "campaign_seconds": CAMPAIGN_SECONDS,
        "round_rest_seconds": ROUND_REST_SECONDS,
        "round_wait_seconds": ROUND_WAIT_SECONDS,
        "classification": "LIVE_THRESHOLD_DOMINANCE_INDEX_RISK_NOT_GUARANTEED_ARBITRAGE",
        "guaranteed_arbitrage": False,
        "realized": False,
        "order_entry": False,
    }

    # Discover and certify exactly once. Every quote round still uses fresh atomic
    # Gemini-triggered Kalshi books and recomputes economics from those books.
    cached_discovery = await discover_next_viable()
    pair = cached_discovery[0]
    out["window_end_utc"] = pair.window_end.isoformat()

    async def cached_discover_once():
        return cached_discovery

    original_discover = triggered.discover_once
    original_trade_id = canary.trade_id
    original_rest = triggered.REST_SECONDS
    original_wait = triggered.WAIT_SECONDS
    triggered.discover_once = cached_discover_once
    triggered.REST_SECONDS = ROUND_REST_SECONDS
    triggered.WAIT_SECONDS = ROUND_WAIT_SECONDS

    started = time.monotonic()
    rounds: list[dict[str, Any]] = []
    try:
        if not armed:
            canary.trade_id = lambda: round_trade_id(root_trade, 0)
            result = await triggered.run()
            out.update({
                "status": "READ_ONLY_REPRICE_PREFLIGHT",
                "underlying_status": result.get("status"),
                "order_entry": bool(result.get("order_entry")),
                "quote": result.get("quote"),
                "paired_books_examined": result.get("paired_books_examined"),
            })
            emit("RESULT", **out)
            return out

        for round_number in range(1, MAX_ROUNDS + 1):
            if time.monotonic() - started >= CAMPAIGN_SECONDS:
                break
            if pair.window_end.timestamp() - time.time() <= triggered.MIN_SECONDS_TO_EXPIRY:
                out["status"] = "PAIR_TOO_CLOSE_TO_EXPIRY"
                break

            rid = round_trade_id(root_trade, round_number)
            canary.trade_id = lambda rid=rid: rid
            result = await triggered.run()
            row = {
                "round": round_number,
                "trade_id": rid,
                "status": result.get("status"),
                "order_entry": bool(result.get("order_entry")),
                "maker_price": ((result.get("quote") or {}).get("maker_quote")),
                "cancel_reason": result.get("cancel_reason"),
                "filled_quantity": result.get("filled_quantity"),
            }
            rounds.append(row)
            emit("ROUND", **row)
            out["order_entry"] = bool(out["order_entry"] or result.get("order_entry"))

            if not safe_to_continue(result):
                out.update({
                    "status": "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND",
                    "terminal_status": result.get("status"),
                    "terminal_result": result,
                })
                break
            await asyncio.sleep(INTER_ROUND_SECONDS)
        else:
            out["status"] = "MAX_REPRICE_ROUNDS_REACHED"

        if "status" not in out:
            out["status"] = "REPRICE_CAMPAIGN_TIME_LIMIT"
        out["rounds_completed"] = len(rounds)
        out["rounds"] = rounds
        out["realized"] = False
        emit("RESULT", **out)
        return out
    finally:
        triggered.discover_once = original_discover
        canary.trade_id = original_trade_id
        triggered.REST_SECONDS = original_rest
        triggered.WAIT_SECONDS = original_wait


if __name__ == "__main__":
    asyncio.run(run())
