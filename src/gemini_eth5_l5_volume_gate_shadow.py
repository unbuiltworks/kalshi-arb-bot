"""Read-only ETH5 5-minute L5 shadow with a causal flow gate.

Purpose: test the hypothesis that the existing L5 mechanism works materially better
when entered only in high-flow Gemini 5-minute windows.

Data sources are public only:
- active-event discovery
- public REST trade history to seed an already-started 5m window
- public WebSocket bookTicker + trade streams thereafter

No API credentials, balances, account endpoints, order placement or cancellation.
The original L5 virtual inventory engine is allowed to quote/fill only while the
causal flow gate is open. A gate close cancels virtual resting quotes but preserves
virtual inventory so later analysis cannot erase residual risk.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import time
from typing import Any

import httpx
import websockets

from src.gemini_eth5_forward_tape_shadow import discover_forward
from src.gemini_l5_tape_dynamic_shadow import MarketState, WS_URL, _parse_book_ticker, _parse_trade, _write
from src.gemini_l5_threshold_shadow import Event, book
from src.gemini_l5_volume_gate import FiveMinuteVolumeGate, VolumeGateConfig, threshold_grid

REST = "https://api.gemini.com"
WINDOW_MS = 300_000
ZERO = D("0")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ws_time_ms(message: dict[str, Any]) -> int:
    raw = message.get("E")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return int(time.time() * 1000)
    # Gemini WS event time is currently nanoseconds. Keep compatibility with ms/s.
    if value > 10**17:
        return value // 1_000_000
    if value > 10**11:
        return value
    return value * 1000


def _trade_id(message: dict[str, Any]) -> str | None:
    raw = message.get("t")
    return str(raw) if raw is not None else None


@dataclass
class FlowL5State:
    event: Event
    gate_config: VolumeGateConfig
    l5: MarketState = field(init=False)
    gate: FiveMinuteVolumeGate = field(init=False)
    contract_volume: D = ZERO
    cash_turnover_usd: D = ZERO
    trade_count: int = 0
    seen_trade_ids: set[str] = field(default_factory=set)
    latest_bid: D | None = None
    latest_ask: D | None = None
    history_seed_count: int = 0
    history_seed_truncated_possible: bool = False
    gate_open_events: int = 0
    gate_close_events: int = 0
    samples: int = 0
    last_decision: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.l5 = MarketState(self.event)
        self.gate = FiveMinuteVolumeGate(self.gate_config)

    @property
    def window_start_ms(self) -> int:
        return self.event.expiry_ms - WINDOW_MS

    def in_window(self, ts_ms: int) -> bool:
        return self.window_start_ms <= ts_ms < self.event.expiry_ms

    def add_trade(self, *, ts_ms: int, trade_id: str | None, price: D, quantity: D) -> bool:
        if not self.in_window(ts_ms):
            return False
        if trade_id is not None and trade_id in self.seen_trade_ids:
            return False
        price, quantity = D(str(price)), D(str(quantity))
        if not (ZERO < price < D("1")) or quantity <= ZERO:
            return False
        if trade_id is not None:
            self.seen_trade_ids.add(trade_id)
        self.contract_volume += quantity
        self.cash_turnover_usd += price * quantity
        self.trade_count += 1
        return True

    def cancel_virtual_quotes(self) -> None:
        # Preserve inventory/ledger. Only resting quote intent is removed.
        self.l5.yes_quote = None
        self.l5.no_quote = None

    def evaluate_gate(self, observed_at: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        before = self.gate.gate_open
        decision = self.gate.observe(
            volume_contracts=self.contract_volume,
            seconds_to_expiry=self.event.seconds_to_expiry,
        )
        after = decision.gate_open
        emitted: list[dict[str, Any]] = []
        if after and not before:
            self.gate_open_events += 1
            if self.latest_bid is not None and self.latest_ask is not None:
                emitted.extend(self.l5.on_book(self.latest_bid, self.latest_ask, observed_at))
        elif before and not after:
            self.gate_close_events += 1
            self.cancel_virtual_quotes()

        row = {
            "type": "GEMINI_ETH5_L5_VOLUME_GATE_SAMPLE",
            "observed_at": observed_at,
            "ticker": self.event.ticker,
            "symbol": self.event.symbol,
            "asset": "ETH",
            "horizon": "5m",
            **decision.as_dict(),
            **threshold_grid(
                volume_contracts=decision.volume_contracts,
                projected_final_contracts=decision.projected_final_contracts,
            ),
            "cash_turnover_usd": str(self.cash_turnover_usd),
            "trade_count": self.trade_count,
            "history_seed_count": self.history_seed_count,
            "history_seed_truncated_possible": self.history_seed_truncated_possible,
            "virtual_quote_active": bool(self.l5.yes_quote is not None or self.l5.no_quote is not None),
            "classification": "SHADOW_NOT_REALIZED",
            "read_only": True,
            "order_entry": False,
            "credentials_loaded": False,
        }
        self.samples += 1
        self.last_decision = row
        return row, emitted

    def summary(self) -> dict[str, Any]:
        return {
            "ticker": self.event.ticker,
            "symbol": self.event.symbol,
            "expiry_ms": self.event.expiry_ms,
            "contract_volume_observed": str(self.contract_volume),
            "cash_turnover_usd_observed": str(self.cash_turnover_usd),
            "trade_count": self.trade_count,
            "history_seed_count": self.history_seed_count,
            "history_seed_truncated_possible": self.history_seed_truncated_possible,
            "gate_open_events": self.gate_open_events,
            "gate_close_events": self.gate_close_events,
            "gate_samples": self.samples,
            "last_gate": self.last_decision,
            "l5": self.l5.summary(),
        }


async def seed_public_history(http: httpx.AsyncClient, state: FlowL5State) -> None:
    now_ms = int(time.time() * 1000)
    if now_ms < state.window_start_ms:
        return
    response = await http.get(
        f"{REST}/v1/trades/{state.event.symbol}",
        params={"timestamp": state.window_start_ms, "limit_trades": 500},
        headers={"Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("unexpected Gemini public trade-history payload")
    state.history_seed_count = len(payload)
    state.history_seed_truncated_possible = len(payload) >= 500
    for trade in payload:
        if not isinstance(trade, dict):
            continue
        try:
            ts_ms = int(trade.get("timestampms"))
            price = D(str(trade.get("price")))
            quantity = D(str(trade.get("amount")))
        except (TypeError, ValueError, ArithmeticError):
            continue
        tid = trade.get("tid")
        state.add_trade(
            ts_ms=ts_ms,
            trade_id=str(tid) if tid is not None else None,
            price=price,
            quantity=quantity,
        )


async def capture(
    *,
    seconds: float,
    sample_s: float,
    discover_s: float,
    max_seconds_to_expiry: float,
    output: Path,
    summary_output: Path | None,
    gate_config: VolumeGateConfig,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if summary_output:
        summary_output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    states: dict[str, FlowL5State] = {}
    subscribed: set[str] = set()
    last_discover = 0.0
    last_sample = 0.0
    stats = {
        "discoveries": 0,
        "subscriptions": 0,
        "messages": 0,
        "book_tickers": 0,
        "trades": 0,
        "in_window_trades": 0,
        "history_seeds": 0,
        "errors": 0,
        "gate_samples": 0,
        "shadow_fills": 0,
    }

    def build_summary(status: str) -> dict[str, Any]:
        return {
            "type": "GEMINI_ETH5_L5_VOLUME_GATE_SHADOW_SUMMARY_V1",
            "status": status,
            "observed_at": _iso(),
            "classification": "READ_ONLY_SHADOW_NOT_REALIZED",
            "order_entry": False,
            "credentials_loaded": False,
            "realized_pnl": None,
            "seconds_requested": seconds,
            "sample_s": sample_s,
            "discover_s": discover_s,
            "max_seconds_to_expiry": max_seconds_to_expiry,
            "gate_config": {
                "warmup_seconds": gate_config.warmup_seconds,
                "velocity_window_seconds": gate_config.velocity_window_seconds,
                "enter_volume_contracts": str(gate_config.enter_volume_contracts),
                "enter_projected_contracts": str(gate_config.enter_projected_contracts),
                "enter_velocity_contracts_per_second": str(gate_config.enter_velocity_contracts_per_second),
                "exit_projected_contracts": str(gate_config.exit_projected_contracts),
                "exit_velocity_contracts_per_second": str(gate_config.exit_velocity_contracts_per_second),
            },
            "stats": stats,
            "markets": [state.summary() for state in sorted(states.values(), key=lambda s: s.event.expiry_ms)],
            "warnings": [
                "Contract volume is share quantity; cash_turnover_usd is price x quantity and is logged separately.",
                "A 500-row REST history seed is marked possibly truncated; continuous WebSocket capture avoids that startup limitation.",
                "Tape-touch fills are optimistic because public trades do not reveal queue position.",
                "No shadow P&L is realized P&L.",
            ],
        }

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-eth5-volume-gate-shadow/1.0"}) as http:
        with output.open("w", encoding="utf-8") as fh:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2**20) as ws:
                _write(fh, {
                    "type": "GEMINI_ETH5_L5_VOLUME_GATE_SHADOW_START",
                    "observed_at": _iso(),
                    "classification": "READ_ONLY_SHADOW_NOT_REALIZED",
                    "order_entry": False,
                    "credentials_loaded": False,
                })
                while time.monotonic() < deadline:
                    now_mono = time.monotonic()
                    if now_mono - last_discover >= discover_s or not subscribed:
                        try:
                            events = await discover_forward(http, max_seconds_to_expiry=max_seconds_to_expiry)
                            stats["discoveries"] += 1
                            desired: list[str] = []
                            for event in events.values():
                                key = event.symbol.upper()
                                if key not in states:
                                    state = FlowL5State(event=event, gate_config=gate_config)
                                    states[key] = state
                                    try:
                                        await seed_public_history(http, state)
                                        stats["history_seeds"] += 1
                                    except Exception as exc:
                                        stats["errors"] += 1
                                        _write(fh, {"type":"HISTORY_SEED_ERROR","observed_at":_iso(),"ticker":event.ticker,"symbol":event.symbol,"error_type":type(exc).__name__,"error":str(exc)[:240]})
                                    try:
                                        bid, _, ask, _ = await book(http, event.symbol)
                                        state.latest_bid, state.latest_ask = bid, ask
                                    except Exception as exc:
                                        # Future windows can legitimately have incomplete books.
                                        _write(fh, {"type":"BOOK_SEED_PENDING","observed_at":_iso(),"ticker":event.ticker,"symbol":event.symbol,"error_type":type(exc).__name__,"error":str(exc)[:240]})
                                desired.extend([f"{event.symbol}@bookTicker", f"{event.symbol}@trade"])
                            new_streams = [stream for stream in desired if stream not in subscribed]
                            if new_streams:
                                request_id = f"eth5-flow-{stats['subscriptions'] + 1}"
                                await ws.send(json.dumps({"id": request_id, "method": "SUBSCRIBE", "params": new_streams}))
                                subscribed.update(new_streams)
                                stats["subscriptions"] += len(new_streams)
                                _write(fh, {"type":"WS_SUBSCRIBE_SENT","observed_at":_iso(),"id":request_id,"streams":new_streams})
                            last_discover = now_mono
                        except Exception as exc:
                            stats["errors"] += 1
                            _write(fh, {"type":"DISCOVERY_ERROR","observed_at":_iso(),"error_type":type(exc).__name__,"error":str(exc)[:240]})

                    timeout = min(max(0.05, sample_s / 2), max(0.05, deadline - time.monotonic()))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        raw = None

                    if raw is not None:
                        try:
                            message = json.loads(raw)
                        except Exception:
                            message = None
                        if isinstance(message, dict):
                            stats["messages"] += 1
                            symbol = str(message.get("s") or "").upper()
                            state = states.get(symbol)
                            if state is not None:
                                observed_at = _iso()
                                parsed_book = _parse_book_ticker(message)
                                if parsed_book is not None:
                                    stats["book_tickers"] += 1
                                    state.latest_bid, state.latest_ask = parsed_book
                                    if state.gate.gate_open:
                                        for row in state.l5.on_book(*parsed_book, observed_at):
                                            _write(fh, row)
                                            if row.get("type") == "SHADOW_FILL":
                                                stats["shadow_fills"] += 1

                                parsed_trade = _parse_trade(message)
                                if parsed_trade is not None and "t" in message:
                                    stats["trades"] += 1
                                    price, quantity, buyer_maker = parsed_trade
                                    # Only a quote that existed before this trade can fill.
                                    gate_was_open = state.gate.gate_open
                                    if gate_was_open:
                                        for row in state.l5.on_trade(price, quantity, buyer_maker, observed_at):
                                            _write(fh, row)
                                            if row.get("type") == "SHADOW_FILL":
                                                stats["shadow_fills"] += 1
                                    added = state.add_trade(
                                        ts_ms=_ws_time_ms(message),
                                        trade_id=_trade_id(message),
                                        price=price,
                                        quantity=quantity,
                                    )
                                    if added:
                                        stats["in_window_trades"] += 1
                                        _write(fh, {
                                            "type":"PUBLIC_TRADE_FLOW","observed_at":observed_at,"ticker":state.event.ticker,"symbol":state.event.symbol,
                                            "price":str(price),"quantity":str(quantity),"buyer_maker":buyer_maker,
                                            "cumulative_contract_volume":str(state.contract_volume),"cumulative_cash_turnover_usd":str(state.cash_turnover_usd),
                                        })

                    now_mono = time.monotonic()
                    if now_mono - last_sample >= sample_s:
                        observed_at = _iso()
                        for state in states.values():
                            seconds_left = state.event.seconds_to_expiry
                            if not 0 < seconds_left <= 300:
                                continue
                            try:
                                gate_row, emitted = state.evaluate_gate(observed_at)
                                _write(fh, gate_row)
                                stats["gate_samples"] += 1
                                for row in emitted:
                                    row["gate_transition"] = "OPEN"
                                    _write(fh, row)
                                    if row.get("type") == "SHADOW_FILL":
                                        stats["shadow_fills"] += 1
                            except Exception as exc:
                                stats["errors"] += 1
                                state.cancel_virtual_quotes()
                                _write(fh, {"type":"GATE_ERROR_FAIL_CLOSED","observed_at":observed_at,"ticker":state.event.ticker,"symbol":state.event.symbol,"error_type":type(exc).__name__,"error":str(exc)[:240]})
                        last_sample = now_mono

                summary = build_summary("COMPLETE")
                _write(fh, summary)

    if summary_output:
        summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=3600)
    parser.add_argument("--sample-s", type=float, default=5.0)
    parser.add_argument("--discover-s", type=float, default=10.0)
    parser.add_argument("--max-seconds-to-expiry", type=float, default=650.0)
    parser.add_argument("--output", type=Path, default=Path("/tmp/gemini_eth5_l5_volume_gate_shadow.jsonl"))
    parser.add_argument("--summary-output", type=Path, default=Path("/tmp/gemini_eth5_l5_volume_gate_shadow_summary.json"))
    parser.add_argument("--enter-volume-contracts", type=D, default=D("150"))
    parser.add_argument("--enter-projected-contracts", type=D, default=D("500"))
    parser.add_argument("--enter-velocity", type=D, default=D("1.50"))
    parser.add_argument("--exit-projected-contracts", type=D, default=D("300"))
    parser.add_argument("--exit-velocity", type=D, default=D("0.50"))
    args = parser.parse_args()

    cfg = VolumeGateConfig(
        enter_volume_contracts=args.enter_volume_contracts,
        enter_projected_contracts=args.enter_projected_contracts,
        enter_velocity_contracts_per_second=args.enter_velocity,
        exit_projected_contracts=args.exit_projected_contracts,
        exit_velocity_contracts_per_second=args.exit_velocity,
    )
    asyncio.run(capture(
        seconds=max(0.0, args.seconds),
        sample_s=max(1.0, args.sample_s),
        discover_s=max(2.0, args.discover_s),
        max_seconds_to_expiry=max(300.0, args.max_seconds_to_expiry),
        output=args.output,
        summary_output=args.summary_output,
        gate_config=cfg,
    ))


if __name__ == "__main__":
    main()
