"""Read-only Gemini 5-minute crypto L5 policy matrix.

This accelerates the volume-gated L5 experiment by evaluating multiple policies on
one live public market-data stream across BTC/ETH/SOL/XRP 5-minute contracts.

Policies:
- BASELINE_ALWAYS: original L5 behavior without a flow gate.
- FLOW_GATED_STATIC: original L5 behavior only while the causal USD-volume gate is open.
- FLOW_GATED_TIERED: same gate, but WARM/HOT/VERY_HOT alter queue aggression and
  inventory target caps while preserving a one-cent modeled pair floor.

All fills are shadow evidence only. No credentials, balances, orders, cancels, or
account endpoints are used. Final window volume is reported only after the fact; it
never controls an earlier decision.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

import httpx
import websockets

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_l5_tape_dynamic_shadow import MarketState, WS_URL, _parse_book_ticker, _parse_trade, _write
from src.gemini_l5_threshold_shadow import Event, book
from src.gemini_l5_volume_gate import FiveMinuteVolumeGate, VolumeGateConfig, threshold_grid

REST = "https://api.gemini.com"
ONE = D("1")
ZERO = D("0")
PAIR_FLOOR = D("0.01")
CRYPTO5_RE = re.compile(r"^(BTC|ETH|SOL|XRP)05M\d{10}$")
POLICIES = ("BASELINE_ALWAYS", "FLOW_GATED_STATIC", "FLOW_GATED_TIERED")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def events_from_rows_with_volume(
    rows: list[Any],
    *,
    now_ms: int,
    max_seconds_to_expiry: float,
) -> tuple[dict[str, Event], dict[str, D]]:
    events: dict[str, Event] = {}
    volumes: dict[str, D] = {}
    max_ms = int(max_seconds_to_expiry * 1000)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        match = CRYPTO5_RE.match(ticker)
        if not match:
            continue
        asset = match.group(1)
        try:
            volume = D(str(row.get("volume") or "0"))
        except (ArithmeticError, ValueError):
            volume = ZERO
        if volume < ZERO:
            volume = ZERO
        for contract in row.get("contracts") or []:
            if not isinstance(contract, Mapping):
                continue
            symbol = str(contract.get("instrumentSymbol") or "").strip()
            state = str(contract.get("marketState") or contract.get("status") or "").lower()
            if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                continue
            expiry_ms = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
            if expiry_ms is None:
                continue
            remaining = expiry_ms - now_ms
            if remaining <= 0 or remaining > max_ms:
                continue
            event = Event(
                asset=asset,
                horizon="5m",
                ticker=ticker,
                symbol=symbol,
                expiry_ms=expiry_ms,
                price_increment=D(str(contract.get("priceIncrement") or "0.01")),
            )
            key = symbol.upper()
            events[key] = event
            volumes[key] = volume
    return events, volumes


async def discover_crypto5_with_volume(
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
    return events_from_rows_with_volume(
        rows_all,
        now_ms=int(time.time() * 1000),
        max_seconds_to_expiry=max_seconds_to_expiry,
    )


def _pair_edge(yes_quote: D, no_quote: D) -> D:
    return ONE - yes_quote - no_quote - gemini_predictions_maker_fee(yes_quote, ONE) - gemini_predictions_maker_fee(no_quote, ONE)


def apply_tier_policy(state: MarketState, *, bid: D, ask: D, tier: str) -> dict[str, Any]:
    """Alter only the newly resting shadow quote; never alter filled inventory.

    WARM: best bid, max target 1.
    HOT: original one-tick L5 skew, max target 2.
    VERY_HOT: up to two ticks toward the ask, max target 3, only if the modeled
    matched pair still keeps at least PAIR_FLOOR after maker fees.
    COLD: no new resting quote.
    """
    bid, ask = D(str(bid)), D(str(ask))
    tick = state.event.price_increment
    caps = {"COLD": D("0"), "WARM": D("1"), "HOT": D("2"), "VERY_HOT": D("3")}
    cap = caps.get(tier, D("0"))
    state.target_units = min(state.target_units, cap)
    favored = state.favored_side
    if cap <= ZERO or favored is None or state.target_units <= ZERO:
        state.yes_quote = None
        state.no_quote = None
        return {"tier": tier, "target_cap": str(cap), "effective_skew_ticks": 0, "quote": None}

    effective_ticks = 1 if tier in {"HOT", "VERY_HOT"} else 0
    if favored == "YES":
        baseline = bid
        quote = baseline if tier == "WARM" else (state.yes_quote or baseline)
        if tier == "VERY_HOT":
            candidate = min(bid + D("2") * tick, ask - tick)
            no_anchor = ONE - ask
            if candidate > quote and candidate < ask and _pair_edge(candidate, no_anchor) >= PAIR_FLOOR:
                quote = candidate
                effective_ticks = int((quote - baseline) / tick)
        state.yes_quote = quote
        state.no_quote = None
    else:
        baseline = ONE - ask
        no_ask = ONE - bid
        quote = baseline if tier == "WARM" else (state.no_quote or baseline)
        if tier == "VERY_HOT":
            candidate = min(baseline + D("2") * tick, no_ask - tick)
            yes_anchor = bid
            if candidate > quote and candidate < no_ask and _pair_edge(yes_anchor, candidate) >= PAIR_FLOOR:
                quote = candidate
                effective_ticks = int((quote - baseline) / tick)
        state.no_quote = quote
        state.yes_quote = None

    return {
        "tier": tier,
        "favored_side": favored,
        "target_cap": str(cap),
        "effective_target_units": str(state.target_units),
        "effective_skew_ticks": effective_ticks,
        "yes_quote": str(state.yes_quote) if state.yes_quote is not None else None,
        "no_quote": str(state.no_quote) if state.no_quote is not None else None,
    }


def _tag(rows: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    for row in rows:
        row["policy"] = policy
    return rows


@dataclass
class MatrixMarket:
    event: Event
    gate_config: VolumeGateConfig
    baseline: MarketState = field(init=False)
    gated_static: MarketState = field(init=False)
    gated_tiered: MarketState = field(init=False)
    gate: FiveMinuteVolumeGate = field(init=False)
    public_volume_usd: D = ZERO
    latest_bid: D | None = None
    latest_ask: D | None = None
    current_tier: str = "COLD"
    gate_open_events: int = 0
    gate_close_events: int = 0
    gate_samples: int = 0
    max_observed_volume_usd: D = ZERO

    def __post_init__(self) -> None:
        self.baseline = MarketState(self.event)
        self.gated_static = MarketState(self.event)
        self.gated_tiered = MarketState(self.event)
        self.gate = FiveMinuteVolumeGate(self.gate_config)

    def update_volume(self, volume: D) -> None:
        volume = D(str(volume))
        if volume < ZERO:
            raise ValueError("public event volume cannot be negative")
        self.public_volume_usd = volume
        self.max_observed_volume_usd = max(self.max_observed_volume_usd, volume)

    @staticmethod
    def _clear_quotes(state: MarketState) -> None:
        state.yes_quote = None
        state.no_quote = None

    def on_book(self, bid: D, ask: D, observed_at: str) -> list[dict[str, Any]]:
        self.latest_bid, self.latest_ask = D(str(bid)), D(str(ask))
        emitted = _tag(self.baseline.on_book(bid, ask, observed_at), "BASELINE_ALWAYS")
        if self.gate.gate_open:
            emitted.extend(_tag(self.gated_static.on_book(bid, ask, observed_at), "FLOW_GATED_STATIC"))
            tier_rows = self.gated_tiered.on_book(bid, ask, observed_at)
            profile = apply_tier_policy(self.gated_tiered, bid=bid, ask=ask, tier=self.current_tier)
            for row in tier_rows:
                row["tier_profile"] = profile
            emitted.extend(_tag(tier_rows, "FLOW_GATED_TIERED"))
        return emitted

    def on_trade(self, price: D, quantity: D, buyer_maker: bool, observed_at: str) -> list[dict[str, Any]]:
        emitted = _tag(self.baseline.on_trade(price, quantity, buyer_maker, observed_at), "BASELINE_ALWAYS")
        if self.gate.gate_open:
            emitted.extend(_tag(self.gated_static.on_trade(price, quantity, buyer_maker, observed_at), "FLOW_GATED_STATIC"))
            emitted.extend(_tag(self.gated_tiered.on_trade(price, quantity, buyer_maker, observed_at), "FLOW_GATED_TIERED"))
        return emitted

    def evaluate_gate(self, observed_at: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        before = self.gate.gate_open
        decision = self.gate.observe(volume_usd=self.public_volume_usd, seconds_to_expiry=self.event.seconds_to_expiry)
        self.current_tier = decision.tier
        after = decision.gate_open
        emitted: list[dict[str, Any]] = []
        if after and not before:
            self.gate_open_events += 1
            if self.latest_bid is not None and self.latest_ask is not None:
                emitted.extend(_tag(self.gated_static.on_book(self.latest_bid, self.latest_ask, observed_at), "FLOW_GATED_STATIC"))
                tier_rows = self.gated_tiered.on_book(self.latest_bid, self.latest_ask, observed_at)
                profile = apply_tier_policy(self.gated_tiered, bid=self.latest_bid, ask=self.latest_ask, tier=self.current_tier)
                for row in tier_rows:
                    row["tier_profile"] = profile
                emitted.extend(_tag(tier_rows, "FLOW_GATED_TIERED"))
        elif before and not after:
            self.gate_close_events += 1
            self._clear_quotes(self.gated_static)
            self._clear_quotes(self.gated_tiered)
        elif after and self.latest_bid is not None and self.latest_ask is not None:
            # Tier changes can alter target/queue aggression even without a new book tick.
            profile = apply_tier_policy(self.gated_tiered, bid=self.latest_bid, ask=self.latest_ask, tier=self.current_tier)
            emitted.append({
                "type": "TIER_POLICY_REFRESH",
                "observed_at": observed_at,
                "ticker": self.event.ticker,
                "symbol": self.event.symbol,
                "policy": "FLOW_GATED_TIERED",
                "tier_profile": profile,
            })
        self.gate_samples += 1
        row = {
            "type": "GEMINI_CRYPTO5_L5_POLICY_MATRIX_GATE",
            "observed_at": observed_at,
            "ticker": self.event.ticker,
            "symbol": self.event.symbol,
            "asset": self.event.asset,
            "horizon": "5m",
            **decision.as_dict(),
            **threshold_grid(volume_usd=decision.volume_usd, projected_final_volume_usd=decision.projected_final_volume_usd),
            "classification": "SHADOW_NOT_REALIZED",
            "read_only": True,
            "order_entry": False,
            "credentials_loaded": False,
        }
        return row, emitted

    def summary(self) -> dict[str, Any]:
        return {
            "asset": self.event.asset,
            "ticker": self.event.ticker,
            "symbol": self.event.symbol,
            "expiry_ms": self.event.expiry_ms,
            "max_public_event_volume_usd_observed": str(self.max_observed_volume_usd),
            "last_public_event_volume_usd": str(self.public_volume_usd),
            "gate_open_events": self.gate_open_events,
            "gate_close_events": self.gate_close_events,
            "gate_samples": self.gate_samples,
            "last_tier": self.current_tier,
            "policies": {
                "BASELINE_ALWAYS": self.baseline.summary(),
                "FLOW_GATED_STATIC": self.gated_static.summary(),
                "FLOW_GATED_TIERED": self.gated_tiered.summary(),
            },
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
    states: dict[str, MatrixMarket] = {}
    subscribed: set[str] = set()
    last_discover = 0.0
    last_sample = 0.0
    stats = {"discoveries":0,"subscriptions":0,"messages":0,"book_tickers":0,"trades":0,"errors":0,"gate_samples":0,"shadow_fills":0,"public_volume_updates":0}

    def build_summary(status: str) -> dict[str, Any]:
        return {
            "type": "GEMINI_CRYPTO5_L5_POLICY_MATRIX_SUMMARY_V1",
            "status": status,
            "observed_at": _iso(),
            "classification": "READ_ONLY_SHADOW_NOT_REALIZED",
            "assets": ["BTC", "ETH", "SOL", "XRP"],
            "policies": list(POLICIES),
            "order_entry": False,
            "credentials_loaded": False,
            "realized_pnl": None,
            "seconds_requested": seconds,
            "stats": stats,
            "markets": [state.summary() for state in sorted(states.values(), key=lambda s: (s.event.expiry_ms, s.event.asset))],
            "warnings": [
                "All fills and P&L are shadow only; queue position is not observable.",
                "BASELINE_ALWAYS is a counterfactual control, not an armed strategy.",
                "Final observed volume is diagnostic only and never used for earlier gate decisions.",
                "Tiered policy changes only new resting quote intent; virtual filled inventory is preserved.",
            ],
        }

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent":"arb-gemini-crypto5-policy-matrix/1.0"}) as http:
        with output.open("w", encoding="utf-8") as fh:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2**20) as ws:
                _write(fh, {"type":"GEMINI_CRYPTO5_L5_POLICY_MATRIX_START_V1","observed_at":_iso(),"read_only":True,"order_entry":False,"credentials_loaded":False,"policies":list(POLICIES)})
                while time.monotonic() < deadline:
                    now_mono = time.monotonic()
                    if now_mono - last_discover >= discover_s or not subscribed:
                        try:
                            events, volumes = await discover_crypto5_with_volume(http, max_seconds_to_expiry=max_seconds_to_expiry)
                            stats["discoveries"] += 1
                            desired: list[str] = []
                            for key, event in events.items():
                                if key not in states:
                                    states[key] = MatrixMarket(event=event, gate_config=gate_config)
                                    try:
                                        bid, _, ask, _ = await book(http, event.symbol)
                                        states[key].latest_bid, states[key].latest_ask = bid, ask
                                        for row in _tag(states[key].baseline.on_book(bid, ask, _iso()), "BASELINE_ALWAYS"):
                                            _write(fh, row)
                                    except Exception as exc:
                                        _write(fh, {"type":"BOOK_SEED_PENDING","observed_at":_iso(),"asset":event.asset,"ticker":event.ticker,"symbol":event.symbol,"error_type":type(exc).__name__,"error":str(exc)[:240]})
                                states[key].update_volume(volumes.get(key, ZERO))
                                stats["public_volume_updates"] += 1
                                desired.extend([f"{event.symbol}@bookTicker", f"{event.symbol}@trade"])
                            new_streams = [stream for stream in desired if stream not in subscribed]
                            if new_streams:
                                request_id = f"crypto5-matrix-{stats['subscriptions'] + 1}"
                                await ws.send(json.dumps({"id":request_id,"method":"SUBSCRIBE","params":new_streams}))
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
                                    for row in state.on_book(*parsed_book, observed_at):
                                        _write(fh, row)
                                        if row.get("type") == "SHADOW_FILL":
                                            stats["shadow_fills"] += 1
                                parsed_trade = _parse_trade(message)
                                if parsed_trade is not None and "t" in message:
                                    stats["trades"] += 1
                                    for row in state.on_trade(*parsed_trade, observed_at):
                                        _write(fh, row)
                                        if row.get("type") == "SHADOW_FILL":
                                            stats["shadow_fills"] += 1

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
                                    _write(fh, row)
                            except Exception as exc:
                                stats["errors"] += 1
                                MatrixMarket._clear_quotes(state.gated_static)
                                MatrixMarket._clear_quotes(state.gated_tiered)
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
    parser.add_argument("--seconds", type=float, default=1200)
    parser.add_argument("--sample-s", type=float, default=5.0)
    parser.add_argument("--discover-s", type=float, default=5.0)
    parser.add_argument("--max-seconds-to-expiry", type=float, default=650.0)
    parser.add_argument("--output", type=Path, default=Path("/tmp/gemini_crypto5_l5_policy_matrix.jsonl"))
    parser.add_argument("--summary-output", type=Path, default=Path("/tmp/gemini_crypto5_l5_policy_matrix_summary.json"))
    args = parser.parse_args()
    asyncio.run(capture(
        seconds=max(0.0, args.seconds),
        sample_s=max(1.0, args.sample_s),
        discover_s=max(2.0, args.discover_s),
        max_seconds_to_expiry=max(300.0, args.max_seconds_to_expiry),
        output=args.output,
        summary_output=args.summary_output,
        gate_config=VolumeGateConfig(),
    ))


if __name__ == "__main__":
    main()
