"""Public read-only l5-style shadow for Gemini BTC/ETH 5m and 15m markets.

This measurement lane intentionally has no authenticated client and no order-entry
imports. It discovers active short-crypto events, samples Gemini's public REST
book, applies the same 60/70/80 favored-side threshold hypothesis used by the
bounded live probe, and writes a replayable JSONL chronology.

The shadow logs maker fees and modeled maker rebates separately. Neither is
claimed as realized. Liquidity-reward eligibility is only contextual; q<10 cannot
satisfy the currently published minimum-size rule.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

import httpx

from src.gemini_l5_threshold_model import plan_threshold_quotes
from src.gemini_same_venue_crypto_ladder_scan import top as parse_book
from src.short_crypto_incentive_snapshot import fetch_snapshot

REST = "https://api.gemini.com"
EVENT_RE = re.compile(r"^(BTC|ETH)(05M|15M)(\d{10})$")
TARGETS = ("BTC05M", "BTC15M", "ETH05M", "ETH15M")


@dataclass(frozen=True)
class Event:
    asset: str
    horizon: str
    ticker: str
    symbol: str
    expiry_ms: int
    price_increment: D

    @property
    def key(self) -> str:
        return f"{self.asset}-{self.horizon}"

    @property
    def seconds_to_expiry(self) -> float:
        return (self.expiry_ms - int(time.time() * 1000)) / 1000.0


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


async def discover(http: httpx.AsyncClient) -> dict[str, Event]:
    found: dict[str, list[Event]] = {target: [] for target in TARGETS}
    offset = 0
    now_ms = int(time.time() * 1000)
    while offset < 5000:
        response = await http.get(
            f"{REST}/v1/prediction-markets/events",
            params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or [] if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            match = EVENT_RE.match(ticker)
            if not match:
                continue
            asset, duration, _ = match.groups()
            horizon = "5m" if duration == "05M" else "15m"
            target = f"{asset}{duration}"
            for contract in row.get("contracts") or []:
                if not isinstance(contract, Mapping):
                    continue
                symbol = str(contract.get("instrumentSymbol") or "").strip()
                state = str(contract.get("marketState") or contract.get("status") or "").lower()
                if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                    continue
                expiry = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                if expiry is None or expiry <= now_ms:
                    continue
                found[target].append(Event(
                    asset=asset,
                    horizon=horizon,
                    ticker=ticker,
                    symbol=symbol,
                    expiry_ms=expiry,
                    price_increment=D(str(contract.get("priceIncrement") or "0.01")),
                ))
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = int((pagination or {}).get("total") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            break
    return {
        target: min(events, key=lambda event: event.expiry_ms)
        for target, events in found.items()
        if events
    }


async def book(http: httpx.AsyncClient, symbol: str) -> tuple[D, D, D, D]:
    response = await http.get(
        f"{REST}/v1/book/{symbol}",
        params={"limit_bids": 20, "limit_asks": 20},
        headers={"Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    parsed = parse_book(response.json())
    values = (
        parsed.get("yes_bid"), parsed.get("yes_bid_qty"),
        parsed.get("yes_ask"), parsed.get("yes_ask_qty"),
    )
    if not all(isinstance(value, D) for value in values):
        raise RuntimeError(f"incomplete Gemini book for {symbol}")
    bid, bid_size, ask, ask_size = values
    assert isinstance(bid, D) and isinstance(bid_size, D)
    assert isinstance(ask, D) and isinstance(ask_size, D)
    if not (D("0") < bid < ask < D("1")):
        raise RuntimeError(f"invalid Gemini book for {symbol}")
    return bid, bid_size, ask, ask_size


def _reward_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("gemini", {}).get("short_crypto_reward_events", [])
    return {
        str(row.get("event_ticker") or ""): row
        for row in rows
        if str(row.get("event_ticker") or "")
    }


async def capture(*, seconds: float, poll_s: float, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    incentive_snapshot = await fetch_snapshot()
    rewards = _reward_map(incentive_snapshot)
    stats: dict[str, dict[str, int]] = {
        key: {
            "samples": 0, "threshold_samples": 0, "yes_favored_samples": 0,
            "no_favored_samples": 0, "side_switches": 0, "market_rollovers": 0,
            "reward_pool_samples": 0, "errors": 0,
        }
        for key in ("BTC-5m", "BTC-15m", "ETH-5m", "ETH-15m")
    }
    last_side: dict[str, str] = {}
    last_ticker: dict[str, str] = {}
    last_identity: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-l5-shadow/1.0"}) as http:
        with output.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                loop_started = time.monotonic()
                try:
                    events = await discover(http)
                except Exception as exc:
                    print(json.dumps({"type":"GEMINI_L5_SHADOW_DISCOVERY_ERROR","error_type":type(exc).__name__,"error":str(exc)[:240],"read_only":True,"order_entry":False}, separators=(",", ":")), flush=True)
                    await asyncio.sleep(max(0.1, poll_s))
                    continue

                for event in events.values():
                    key = event.key
                    try:
                        bid, bid_size, ask, ask_size = await book(http, event.symbol)
                        plan = plan_threshold_quotes(
                            bid, ask, price_increment=event.price_increment
                        )
                        previous_ticker = last_ticker.get(key)
                        if previous_ticker and previous_ticker != event.ticker:
                            stats[key]["market_rollovers"] += 1
                            last_side.pop(key, None)
                        last_ticker[key] = event.ticker
                        if plan.favored_side:
                            previous_side = last_side.get(key)
                            if previous_side and previous_side != plan.favored_side:
                                stats[key]["side_switches"] += 1
                            last_side[key] = plan.favored_side

                        reward = rewards.get(event.ticker)
                        row = {
                            "type": "GEMINI_L5_THRESHOLD_SHADOW",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "asset": event.asset,
                            "horizon": event.horizon,
                            "ticker": event.ticker,
                            "symbol": event.symbol,
                            "seconds_to_expiry": round(event.seconds_to_expiry, 3),
                            "price_increment": str(event.price_increment),
                            "yes_bid": str(bid), "yes_bid_size": str(bid_size),
                            "yes_ask": str(ask), "yes_ask_size": str(ask_size),
                            "yes_mid": str(plan.yes_mid), "no_mid": str(plan.no_mid),
                            "favored_side": plan.favored_side,
                            "favored_probability": str(plan.favored_probability),
                            "shadow_target_units": plan.shadow_target_units,
                            "shadow_yes_quote": str(plan.yes_quote),
                            "shadow_no_quote": str(plan.no_quote),
                            "quote_skew_ticks": plan.quote_skew_ticks,
                            "pair_cost_if_both_fill": str(plan.pair_cost),
                            "modeled_pair_edge_after_maker_fees": str(plan.modeled_edge),
                            "yes_maker_fee_q1": str(plan.yes_fee),
                            "no_maker_fee_q1": str(plan.no_fee),
                            "yes_rebate_eligible": plan.yes_rebate_eligible,
                            "no_rebate_eligible": plan.no_rebate_eligible,
                            "q1_yes_rebate_raw": str(plan.q1_yes_rebate_raw),
                            "q1_no_rebate_raw": str(plan.q1_no_rebate_raw),
                            "q1_rebate_cent_rounded_total": str(plan.q1_yes_rebate_rounded + plan.q1_no_rebate_rounded),
                            "reward_pool_active": reward is not None,
                            "reward_pool": reward,
                            "liquidity_reward_size_status": "Q1_TO_Q3_BELOW_PUBLISHED_10_CONTRACT_MINIMUM",
                            "fill_status": "NO_FILL_ASSUMED",
                            "trading_pnl_status": "SHADOW_NOT_REALIZED",
                            "rebate_status": "MODELED_NOT_REALIZED",
                            "read_only": True,
                            "order_entry": False,
                        }
                        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
                        fh.flush()
                        stats[key]["samples"] += 1
                        if plan.favored_side:
                            stats[key]["threshold_samples"] += 1
                            stats[key][f"{plan.favored_side.lower()}_favored_samples"] += 1
                        if reward:
                            stats[key]["reward_pool_samples"] += 1
                        identity = f"{event.ticker}:{plan.favored_side}:{plan.yes_quote}:{plan.no_quote}:{bool(reward)}"
                        if last_identity.get(key) != identity:
                            print(json.dumps(row, separators=(",", ":"), default=str), flush=True)
                            last_identity[key] = identity
                    except Exception as exc:
                        stats[key]["errors"] += 1
                        print(json.dumps({"type":"GEMINI_L5_THRESHOLD_SHADOW_ERROR","asset":event.asset,"horizon":event.horizon,"ticker":event.ticker,"error_type":type(exc).__name__,"error":str(exc)[:240],"read_only":True,"order_entry":False}, separators=(",", ":")), flush=True)

                elapsed = time.monotonic() - loop_started
                await asyncio.sleep(max(0.05, poll_s - elapsed))

    summary = {
        "type": "GEMINI_L5_THRESHOLD_SHADOW_SUMMARY",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "seconds_requested": seconds,
        "poll_s": poll_s,
        "series": stats,
        "read_only": True,
        "order_entry": False,
        "realized_pnl": None,
    }
    print(json.dumps(summary, separators=(",", ":")), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/gemini-l5-threshold-shadow.jsonl"))
    args = parser.parse_args()
    asyncio.run(capture(seconds=args.seconds, poll_s=args.poll_s, output=args.output))


if __name__ == "__main__":
    main()
