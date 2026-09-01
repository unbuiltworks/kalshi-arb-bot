"""Resilient read-only Gemini l5 dynamic inventory shadow.

V3 preserves the v2 strategy/fill model while fixing measurement reliability:
- reconnect public Gemini WebSockets after service restarts/transient disconnects;
- resubscribe active markets while preserving virtual inventory;
- attach contract identity to every emitted market row, including SHADOW_FILL;
- atomically checkpoint per-market inventory summaries during the run;
- never load credentials or submit/cancel orders.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
from typing import Any

import httpx
import websockets

from src.gemini_l5_tape_dynamic_shadow import (
    WS_URL,
    MarketState,
    _iso,
    _parse_book_ticker,
    _parse_trade,
    _write,
)
from src.gemini_l5_threshold_shadow import book, discover


def enrich_market_row(row: dict[str, Any], state: MarketState) -> dict[str, Any]:
    enriched = dict(row)
    enriched.setdefault("ticker", state.event.ticker)
    enriched.setdefault("symbol", state.event.symbol)
    enriched.setdefault("asset", state.event.asset)
    enriched.setdefault("horizon", state.event.horizon)
    return enriched


def build_summary(
    *,
    stats: dict[str, int],
    states: dict[str, MarketState],
    seconds: float,
    discover_s: float,
    status: str,
) -> dict[str, Any]:
    return {
        "type": "GEMINI_L5_TAPE_DYNAMIC_SHADOW_SUMMARY_V3",
        **stats,
        "status": status,
        "updated_at": _iso(),
        "seconds_requested": seconds,
        "discover_s": discover_s,
        "markets": len(states),
        "markets_detail": [state.summary() for state in states.values()],
        "read_only": True,
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "classification": "SHADOW_NOT_REALIZED",
        "tape_touch_warning": "Optimistic: public trade at quote ignores queue position.",
        "strict_warning": "Conservative: requires top of book to move fully through resting quote.",
    }


def write_checkpoint(path: Path | None, summary: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


async def capture(
    *,
    seconds: float,
    discover_s: float,
    output: Path,
    summary_output: Path | None = None,
    checkpoint_s: float = 5.0,
    reconnect_s: float = 1.0,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    stats: dict[str, int] = {
        "messages": 0,
        "book_tickers": 0,
        "trades": 0,
        "errors": 0,
        "discoveries": 0,
        "subscriptions": 0,
        "rest_seeds": 0,
        "control_messages": 0,
        "subscription_errors": 0,
        "ws_connections": 0,
        "ws_disconnects": 0,
        "ws_reconnects": 0,
        "checkpoints": 0,
    }
    states: dict[str, MarketState] = {}
    last_checkpoint = 0.0

    def checkpoint(status: str, *, force: bool = False) -> None:
        nonlocal last_checkpoint
        now = time.monotonic()
        if not force and now - last_checkpoint < max(1.0, checkpoint_s):
            return
        summary = build_summary(
            stats=stats,
            states=states,
            seconds=seconds,
            discover_s=discover_s,
            status=status,
        )
        write_checkpoint(summary_output, summary)
        last_checkpoint = now
        stats["checkpoints"] += 1

    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-l5-tape-shadow/3.0"},
    ) as http:
        with output.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                subscribed: set[str] = set()
                last_discover = 0.0
                try:
                    async with websockets.connect(
                        WS_URL,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        max_size=2**20,
                    ) as ws:
                        stats["ws_connections"] += 1
                        if stats["ws_connections"] > 1:
                            stats["ws_reconnects"] += 1
                        _write(
                            fh,
                            {
                                "type": "WS_CONNECTED",
                                "observed_at": _iso(),
                                "connection_number": stats["ws_connections"],
                            },
                        )

                        while time.monotonic() < deadline:
                            now = time.monotonic()
                            if now - last_discover >= discover_s or not subscribed:
                                try:
                                    events = await discover(http)
                                    stats["discoveries"] += 1
                                    desired_streams: list[str] = []
                                    for event in events.values():
                                        key = event.symbol.upper()
                                        if key not in states:
                                            state = MarketState(event)
                                            states[key] = state
                                            try:
                                                bid, _, ask, _ = await book(http, event.symbol)
                                                for row in state.on_book(bid, ask, _iso()):
                                                    _write(fh, enrich_market_row(row, state))
                                                stats["rest_seeds"] += 1
                                            except Exception as exc:
                                                stats["errors"] += 1
                                                _write(
                                                    fh,
                                                    {
                                                        "type": "REST_SEED_ERROR",
                                                        "observed_at": _iso(),
                                                        "ticker": event.ticker,
                                                        "symbol": event.symbol,
                                                        "asset": event.asset,
                                                        "horizon": event.horizon,
                                                        "error_type": type(exc).__name__,
                                                        "error": str(exc)[:240],
                                                    },
                                                )
                                        desired_streams.extend(
                                            [f"{event.symbol}@bookTicker", f"{event.symbol}@trade"]
                                        )
                                    new_streams = [s for s in desired_streams if s not in subscribed]
                                    if new_streams:
                                        request_id = f"{stats['ws_connections']}-{stats['subscriptions'] + 1}"
                                        await ws.send(
                                            json.dumps(
                                                {
                                                    "id": request_id,
                                                    "method": "SUBSCRIBE",
                                                    "params": new_streams,
                                                }
                                            )
                                        )
                                        _write(
                                            fh,
                                            {
                                                "type": "WS_SUBSCRIBE_SENT",
                                                "observed_at": _iso(),
                                                "id": request_id,
                                                "streams": new_streams,
                                            },
                                        )
                                        subscribed.update(new_streams)
                                        stats["subscriptions"] += len(new_streams)
                                    last_discover = now
                                except Exception as exc:
                                    stats["errors"] += 1
                                    _write(
                                        fh,
                                        {
                                            "type": "SHADOW_DISCOVERY_ERROR",
                                            "observed_at": _iso(),
                                            "error_type": type(exc).__name__,
                                            "error": str(exc)[:240],
                                        },
                                    )

                            checkpoint("RUNNING")
                            timeout = min(1.0, max(0.05, deadline - time.monotonic()))
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                            except asyncio.TimeoutError:
                                continue

                            try:
                                message = json.loads(raw)
                            except Exception:
                                stats["errors"] += 1
                                _write(
                                    fh,
                                    {
                                        "type": "WS_NON_JSON",
                                        "observed_at": _iso(),
                                        "raw": str(raw)[:500],
                                    },
                                )
                                continue
                            if not isinstance(message, dict):
                                continue

                            stats["messages"] += 1
                            symbol = str(message.get("s") or "").upper()
                            state = states.get(symbol)
                            if state is None:
                                stats["control_messages"] += 1
                                status = message.get("status")
                                if isinstance(status, int) and status >= 400:
                                    stats["subscription_errors"] += 1
                                _write(
                                    fh,
                                    {
                                        "type": "WS_CONTROL",
                                        "observed_at": _iso(),
                                        "message": message,
                                    },
                                )
                                continue

                            observed_at = _iso()
                            emitted: list[dict[str, Any]] = []
                            trade = _parse_trade(message)
                            if trade is not None and "t" in message:
                                stats["trades"] += 1
                                emitted.extend(state.on_trade(*trade, observed_at))
                                emitted.append(
                                    {
                                        "type": "PUBLIC_TRADE",
                                        "observed_at": observed_at,
                                        "ticker": state.event.ticker,
                                        "symbol": state.event.symbol,
                                        "asset": state.event.asset,
                                        "horizon": state.event.horizon,
                                        "price": str(trade[0]),
                                        "quantity": str(trade[1]),
                                        "buyer_maker": trade[2],
                                        "trade_id": message.get("t"),
                                    }
                                )
                            else:
                                book_tick = _parse_book_ticker(message)
                                if book_tick is not None:
                                    stats["book_tickers"] += 1
                                    emitted.extend(state.on_book(*book_tick, observed_at))
                            for row in emitted:
                                _write(fh, enrich_market_row(row, state))

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stats["ws_disconnects"] += 1
                    stats["errors"] += 1
                    _write(
                        fh,
                        {
                            "type": "WS_DISCONNECTED_RETRYING",
                            "observed_at": _iso(),
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:500],
                            "retry_in_s": reconnect_s,
                        },
                    )
                    checkpoint("RECONNECTING", force=True)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(max(0.05, reconnect_s), remaining))

            final = build_summary(
                stats=stats,
                states=states,
                seconds=seconds,
                discover_s=discover_s,
                status="COMPLETED",
            )
            write_checkpoint(summary_output, final)
            print(json.dumps(final, indent=2, sort_keys=True), flush=True)
            return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=3600.0)
    parser.add_argument("--discover-s", type=float, default=10.0)
    parser.add_argument("--checkpoint-s", type=float, default=5.0)
    parser.add_argument("--reconnect-s", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/gemini-l5-tape-dynamic-shadow-v3.jsonl"),
    )
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()
    asyncio.run(
        capture(
            seconds=args.seconds,
            discover_s=args.discover_s,
            output=args.output,
            summary_output=args.summary_output,
            checkpoint_s=args.checkpoint_s,
            reconnect_s=args.reconnect_s,
        )
    )


if __name__ == "__main__":
    main()
