"""Read-only ETH5 5-minute L5 shadow gated by public Gemini USD volume.

Purpose: test whether the existing L5 mechanism behaves materially better when it
opens NEW exposure only after a 5-minute Gemini window has demonstrated meaningful
flow. Gemini's documented event ``volume`` field is total trading volume in USD,
which aligns with the volume shown in the product UI.

Public data only:
- active prediction-event REST discovery, including event USD volume
- public WebSocket bookTicker + trade streams

No credentials, balances, account endpoints, orders, or cancellations. A gate close
removes virtual resting quotes but never erases virtual inventory or disables later
rescue/accounting logic in a live integration.
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
from typing import Any, Mapping

import httpx
import websockets

from src.gemini_eth5_forward_tape_shadow import events_from_rows
from src.gemini_l5_tape_dynamic_shadow import MarketState, WS_URL, _parse_book_ticker, _parse_trade, _write
from src.gemini_l5_threshold_shadow import Event, book
from src.gemini_l5_volume_gate import FiveMinuteVolumeGate, VolumeGateConfig, threshold_grid

REST = "https://api.gemini.com"
ZERO = D("0")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def discover_forward_with_volume(
    http: httpx.AsyncClient,
    *,
    max_seconds_to_expiry: float,
) -> tuple[dict[str, Event], dict[str, D]]:
    rows_all: list[Any] = []
    offset = 0
    while offset < 5000:
        response = await http.get(
            f"{REST}/v1/prediction-markets/events",
            params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or [] if isinstance(payload, dict) else []
        rows_all.extend(rows)
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = int((pagination or {}).get("total") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            break

    events = events_from_rows(
        rows_all,
        now_ms=int(time.time() * 1000),
        max_seconds_to_expiry=max_seconds_to_expiry,
    )
    volume_by_ticker: dict[str, D] = {}
    for row in rows_all:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker.startswith("ETH05M"):
            continue
        try:
            volume = D(str(row.get("volume") or "0"))
        except (ArithmeticError, ValueError):
            continue
        if volume >= ZERO:
            volume_by_ticker[ticker] = volume

    volume_by_symbol = {
        symbol.upper(): volume_by_ticker.get(event.ticker.upper(), ZERO)
        for symbol, event in events.items()
    }
    return events, volume_by_symbol


@dataclass
class FlowL5State:
    event: Event
    gate_config: VolumeGateConfig
    l5: MarketState = field(init=False)
    gate: FiveMinuteVolumeGate = field(init=False)
    public_volume_usd: D = ZERO
    ws_contract_volume: D = ZERO
    ws_cash_turnover_usd: D = ZERO
    ws_trade_count: int = 0
    latest_bid: D | None = None
    latest_ask: D | None = None
    gate_open_events: int = 0
    gate_close_events: int = 0
    samples: int = 0
    last_decision: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.l5 = MarketState(self.event)
        self.gate = FiveMinuteVolumeGate(self.gate_config)

    def update_public_volume(self, volume_usd: D) -> None:
        value = D(str(volume_usd))
        if value < ZERO:
            raise ValueError("public event volume cannot be negative")
        self.public_volume_usd = value

    def add_ws_trade(self, price: D, quantity: D) -> None:
        price, quantity = D(str(price)), D(str(quantity))
        if ZERO < price < D("1") and quantity > ZERO:
            self.ws_contract_volume += quantity
            self.ws_cash_turnover_usd += price * quantity
            self.ws_trade_count += 1

    def cancel_virtual_quotes(self) -> None:
        self.l5.yes_quote = None
        self.l5.no_quote = None

    def evaluate_gate(self, observed_at: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        before = self.gate.gate_open
        decision = self.gate.observe(
            volume_usd=self.public_volume_usd,
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
                volume_usd=decision.volume_usd,
                projected_final_volume_usd=decision.projected_final_volume_usd,
            ),
            "ws_contract_volume_observed": str(self.ws_contract_volume),
            "ws_cash_turnover_usd_observed": str(self.ws_cash_turnover_usd),
            "ws_trade_count": self.ws_trade_count,
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
            "public_event_volume_usd_observed": str(self.public_volume_usd),
            "ws_contract_volume_observed": str(self.ws_contract_volume),
            "ws_cash_turnover_usd_observed": str(self.ws_cash_turnover_usd),
            "ws_trade_count": self.ws_trade_count,
            "gate_open_events": self.gate_open_events,
            "gate_close_events": self.gate_close_events,
            "gate_samples": self.samples,
            "last_gate": self.last_decision,
            "l5": self.l5.summary(),
        }


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
        "errors": 0,
        "gate_samples": 0,
        "shadow_fills": 0,
        "public_volume_updates": 0,
    }

    def build_summary(status: str) -> dict[str, Any]:
        return {
            "type": "GEMINI_ETH5_L5_VOLUME_GATE_SHADOW_SUMMARY_V2",
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
                "enter_volume_usd": str(gate_config.enter_volume_usd),
                "enter_projected_volume_usd": str(gate_config.enter_projected_volume_usd),
                "enter_velocity_usd_per_second": str(gate_config.enter_velocity_usd_per_second),
                "exit_projected_volume_usd": str(gate_config.exit_projected_volume_usd),
                "exit_velocity_usd_per_second": str(gate_config.exit_velocity_usd_per_second),
            },
            "stats": stats,
            "markets": [state.summary() for state in sorted(states.values(), key=lambda s: s.event.expiry_ms)],
            "warnings": [
                "Gate input is Gemini's documented public event volume in USD.",
                "WebSocket trade counts are diagnostic only and are not used as the gate denominator.",
                "Tape-touch shadow fills are optimistic because public data does not reveal our queue position.",
                "No shadow P&L is realized P&L.",
            ],
        }

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-eth5-volume-gate-shadow/2.0"}) as http:
        with output.open("w", encoding="utf-8") as fh:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2**20) as ws:
                _write(fh, {
                    "type": "GEMINI_ETH5_L5_VOLUME_GATE_SHADOW_START_V2",
                    "observed_at": _iso(),
                    "classification": "READ_ONLY_SHADOW_NOT_REALIZED",
                    "order_entry": False,
                    "credentials_loaded": False,
                    "gate_input": "PUBLIC_EVENT_VOLUME_USD",
                })
                while time.monotonic() < deadline:
                    now_mono = time.monotonic()
                    if now_mono - last_discover >= discover_s or not subscribed:
                        try:
                            events, volumes = await discover_forward_with_volume(http, max_seconds_to_expiry=max_seconds_to_expiry)
                            stats["discoveries"] += 1
                            desired: list[str] = []
                            for event in events.values():
                                key = event.symbol.upper()
                                if key not in states:
                                    states[key] = FlowL5State(event=event, gate_config=gate_config)
                                    try:
                                        bid, _, ask, _ = await book(http, event.symbol)
                                        states[key].latest_bid, states[key].latest_ask = bid, ask
                                    except Exception as exc:
                                        _write(fh, {"type":"BOOK_SEED_PENDING","observed_at":_iso(),"ticker":event.ticker,"symbol":event.symbol,"error_type":type(exc).__name__,"error":str(exc)[:240]})
                                state = states[key]
                                state.update_public_volume(volumes.get(key, ZERO))
                                stats["public_volume_updates"] += 1
                                desired.extend([f"{event.symbol}@bookTicker", f"{event.symbol}@trade"])

                            new_streams = [stream for stream in desired if stream not in subscribed]
                            if new_streams:
                                request_id = f"eth5-usd-flow-{stats['subscriptions'] + 1}"
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
                                    if state.gate.gate_open:
                                        for row in state.l5.on_trade(price, quantity, buyer_maker, observed_at):
                                            _write(fh, row)
                                            if row.get("type") == "SHADOW_FILL":
                                                stats["shadow_fills"] += 1
                                    state.add_ws_trade(price, quantity)
                                    _write(fh, {
                                        "type":"PUBLIC_WS_TRADE_DIAGNOSTIC","observed_at":observed_at,
                                        "ticker":state.event.ticker,"symbol":state.event.symbol,
                                        "price":str(price),"quantity":str(quantity),"buyer_maker":buyer_maker,
                                        "public_event_volume_usd":str(state.public_volume_usd),
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
    parser.add_argument("--discover-s", type=float, default=5.0)
    parser.add_argument("--max-seconds-to-expiry", type=float, default=650.0)
    parser.add_argument("--output", type=Path, default=Path("/tmp/gemini_eth5_l5_volume_gate_shadow.jsonl"))
    parser.add_argument("--summary-output", type=Path, default=Path("/tmp/gemini_eth5_l5_volume_gate_shadow_summary.json"))
    parser.add_argument("--enter-volume-usd", type=D, default=D("150"))
    parser.add_argument("--enter-projected-volume-usd", type=D, default=D("500"))
    parser.add_argument("--enter-velocity-usd-per-second", type=D, default=D("1.50"))
    parser.add_argument("--exit-projected-volume-usd", type=D, default=D("300"))
    parser.add_argument("--exit-velocity-usd-per-second", type=D, default=D("0.50"))
    args = parser.parse_args()

    cfg = VolumeGateConfig(
        enter_volume_usd=args.enter_volume_usd,
        enter_projected_volume_usd=args.enter_projected_volume_usd,
        enter_velocity_usd_per_second=args.enter_velocity_usd_per_second,
        exit_projected_volume_usd=args.exit_projected_volume_usd,
        exit_velocity_usd_per_second=args.exit_velocity_usd_per_second,
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
