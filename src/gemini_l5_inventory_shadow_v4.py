"""Resilient read-only online Gemini l5 inventory shadow V4.

V4 changes the objective from favored-side accumulation to portfolio improvement:
- at most one naked q=1 unit per market;
- never add the same side while residual inventory exists;
- only complete a pair if it locks at least the configured edge after modeled
  maker fees;
- block expensive fresh directional entries and late-window openings;
- preserve inventory through WebSocket reconnects and checkpoint continuously.

Public-data only. No credentials, balances, account endpoints, order placement,
order cancellation, or realized P&L.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from decimal import Decimal as D
import json
from pathlib import Path
import time
from typing import Any

import httpx
import websockets

from src.gemini_l5_inventory_replay_v4 import V4Market
from src.gemini_l5_tape_dynamic_shadow import WS_URL, _iso, _parse_book_ticker, _parse_trade, _write
from src.gemini_l5_tape_dynamic_shadow_v3 import write_checkpoint
from src.gemini_l5_threshold_shadow import Event, book, discover


@dataclass
class OnlineMarketState:
    event: Event
    market: V4Market = field(init=False)

    def __post_init__(self) -> None:
        self.market = V4Market(
            ticker=self.event.ticker,
            symbol=self.event.symbol,
            asset=self.event.asset,
            horizon=self.event.horizon,
            price_increment=self.event.price_increment,
        )

    def _fill_rows(
        self,
        *,
        ledger_key: str,
        before: int,
        trade_price: D | None = None,
        observed_at: str,
    ) -> list[dict[str, Any]]:
        ledger = self.market.tape if ledger_key == "tape" else self.market.strict
        rows: list[dict[str, Any]] = []
        pair_index = len(ledger.pairs) - sum(1 for f in ledger.fills[before:] if f.role == "PAIR_COMPLETION")
        for fill in ledger.fills[before:]:
            row: dict[str, Any] = {
                "type": "SHADOW_FILL",
                "observed_at": observed_at,
                "ticker": self.event.ticker,
                "symbol": self.event.symbol,
                "asset": self.event.asset,
                "horizon": self.event.horizon,
                "ledger": ledger.label,
                "side": fill.side,
                "price": fill.price,
                "quantity": "1",
                "maker_fee": fill.fee,
                "evidence": fill.evidence,
                "role": fill.role,
                "read_only": True,
                "order_entry": False,
            }
            if trade_price is not None:
                row["trade_price"] = str(trade_price)
            if fill.role == "PAIR_COMPLETION" and 0 <= pair_index < len(ledger.pairs):
                pair = ledger.pairs[pair_index]
                row["locked_edge_after_fees"] = pair.locked_edge_after_fees
                row["held_side"] = pair.held_side
                row["held_price"] = pair.held_price
                pair_index += 1
            rows.append(row)
        return rows

    @staticmethod
    def _ledger_plan(ledger: Any) -> dict[str, Any]:
        return {
            "quote_side": ledger.quote_side,
            "quote_price": str(ledger.quote_price) if ledger.quote_price is not None else None,
            "quote_mode": ledger.quote_mode,
            "quote_reason": ledger.quote_reason,
            "residual_side": ledger.residual_side,
            "residual_qty": str(ledger.residual_qty),
            "residual_acquisition_cost": str(ledger.residual_acquisition_cost),
            "completed_pairs": len(ledger.pairs),
            "locked_edge_total_after_fees": str(
                sum((D(p.locked_edge_after_fees) for p in ledger.pairs), D("0"))
            ),
        }

    def on_book(self, bid: D, ask: D, observed_at: str) -> list[dict[str, Any]]:
        before_strict = len(self.market.strict.fills)
        self.market.on_book(
            bid=D(str(bid)),
            ask=D(str(ask)),
            seconds_to_expiry=self.event.seconds_to_expiry,
        )
        rows = self._fill_rows(
            ledger_key="strict",
            before=before_strict,
            observed_at=observed_at,
        )
        rows.append(
            {
                "type": "V4_SHADOW_PLAN",
                "observed_at": observed_at,
                "ticker": self.event.ticker,
                "symbol": self.event.symbol,
                "asset": self.event.asset,
                "horizon": self.event.horizon,
                "seconds_to_expiry": round(self.event.seconds_to_expiry, 3),
                "yes_bid": str(bid),
                "yes_ask": str(ask),
                "tape": self._ledger_plan(self.market.tape),
                "strict": self._ledger_plan(self.market.strict),
                "pair_floor_after_fees": str(self.market.tape.pair_floor),
                "max_naked_units_per_market": "1",
                "read_only": True,
                "order_entry": False,
            }
        )
        return rows

    def on_trade(
        self,
        price: D,
        quantity: D,
        buyer_maker: bool,
        observed_at: str,
    ) -> list[dict[str, Any]]:
        before_tape = len(self.market.tape.fills)
        self.market.on_trade(
            price=D(str(price)),
            quantity=D(str(quantity)),
            buyer_maker=buyer_maker,
        )
        return self._fill_rows(
            ledger_key="tape",
            before=before_tape,
            trade_price=D(str(price)),
            observed_at=observed_at,
        )

    def summary(self) -> dict[str, Any]:
        return self.market.summary()


def _aggregate(states: dict[str, OnlineMarketState], ledger_key: str) -> dict[str, Any]:
    ledgers = [
        state.market.tape if ledger_key == "tape" else state.market.strict
        for state in states.values()
    ]
    pairs = [p for ledger in ledgers for p in ledger.pairs]
    locked = sum((D(p.locked_edge_after_fees) for p in pairs), D("0"))
    residual_cost = sum((ledger.residual_acquisition_cost for ledger in ledgers), D("0"))
    return {
        "fills": sum(len(ledger.fills) for ledger in ledgers),
        "markets_with_fills": sum(1 for ledger in ledgers if ledger.fills),
        "completed_pairs": len(pairs),
        "markets_with_completed_pairs": sum(1 for ledger in ledgers if ledger.pairs),
        "locked_edge_total_after_fees": str(locked),
        "average_locked_edge_per_pair_after_fees": (
            str(locked / D(len(pairs))) if pairs else None
        ),
        "residual_markets": sum(1 for ledger in ledgers if ledger.residual_side is not None),
        "residual_qty_total": str(sum((ledger.residual_qty for ledger in ledgers), D("0"))),
        "residual_acquisition_cost_total": str(residual_cost),
        "blocked_expensive_directional": sum(ledger.blocked_expensive_directional for ledger in ledgers),
        "blocked_unprofitable_completion": sum(ledger.blocked_unprofitable_completion for ledger in ledgers),
    }


def build_summary(
    *,
    stats: dict[str, int],
    states: dict[str, OnlineMarketState],
    seconds: float,
    discover_s: float,
    status: str,
) -> dict[str, Any]:
    return {
        "type": "GEMINI_L5_INVENTORY_SHADOW_SUMMARY_V4",
        **stats,
        "status": status,
        "updated_at": _iso(),
        "seconds_requested": seconds,
        "discover_s": discover_s,
        "markets": len(states),
        "markets_detail": [state.summary() for state in states.values()],
        "pair_floor_after_fees": "0.03",
        "max_naked_units_per_market": "1",
        "max_directional_entry_price": "0.80",
        "tape_touch": _aggregate(states, "tape"),
        "strict_trade_through": _aggregate(states, "strict"),
        "read_only": True,
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "classification": "SHADOW_NOT_REALIZED",
        "tape_touch_warning": "Optimistic: public trade at quote ignores queue position.",
        "strict_warning": "Conservative: requires top of book to move fully through resting quote.",
    }


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
    states: dict[str, OnlineMarketState] = {}
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
        headers={"User-Agent": "arb-gemini-l5-inventory-shadow/4.0"},
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
                                            state = OnlineMarketState(event)
                                            states[key] = state
                                            try:
                                                bid, _, ask, _ = await book(http, event.symbol)
                                                for row in state.on_book(bid, ask, _iso()):
                                                    _write(fh, row)
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
                                _write(fh, row)

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
    parser.add_argument("--seconds", type=float, default=1800.0)
    parser.add_argument("--discover-s", type=float, default=10.0)
    parser.add_argument("--checkpoint-s", type=float, default=5.0)
    parser.add_argument("--reconnect-s", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/gemini-l5-inventory-shadow-v4.jsonl"),
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
