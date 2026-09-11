"""Read-only census of Gemini Prediction Markets liquidity-reward economics.

Public endpoints only. No credentials, no account calls, no order entry.

The census reports what Gemini publicly exposes for active Crypto reward events:
- configured program fields,
- event-level daily pool where present,
- qualifying-maker counts,
- BTC/ETH short-window subset,
- a deliberately labeled equal-share reference for adding one more maker.

The equal-share figure is NOT a payout forecast. Actual program scoring/allocation can
be non-equal and must be verified from Gemini's program rules and realized credits.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D, InvalidOperation
import json
from typing import Any, Mapping

import httpx

REST = "https://api.gemini.com"
CONFIG = "/v1/prediction-markets/liquidity-rewards/config"
EVENTS = "/v1/prediction-markets/liquidity-rewards/events"


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _dec(value: Any) -> D | None:
    if value in (None, ""):
        return None
    try:
        return D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _ticker(row: Mapping[str, Any]) -> str:
    return str(_first(row, "event_ticker", "eventTicker", "ticker") or "").upper()


def _asset(ticker: str) -> str | None:
    if "BTC" in ticker:
        return "BTC"
    if "ETH" in ticker:
        return "ETH"
    return None


def _window_minutes(ticker: str) -> int | None:
    # Gemini tickers seen in this repo use BTC05M / ETH05M and BTC15M / ETH15M.
    if "05M" in ticker:
        return 5
    if "15M" in ticker:
        return 15
    return None


def _normalize(row: Mapping[str, Any]) -> dict[str, Any]:
    ticker = _ticker(row)
    pool = _dec(_first(row, "daily_pool_usd", "dailyPoolUsd", "daily_pool", "dailyPool"))
    makers = _int(_first(row, "qualifying_maker_count", "qualifyingMakerCount"))
    equal_share = None
    if pool is not None and makers is not None and makers >= 0:
        equal_share = pool / D(makers + 1)
    return {
        "event_ticker": ticker,
        "asset": _asset(ticker),
        "window_minutes": _window_minutes(ticker),
        "daily_pool_usd": None if pool is None else str(pool),
        "qualifying_maker_count": makers,
        "equal_share_if_one_more_maker_usd_reference_only": None if equal_share is None else str(equal_share),
        "raw_status": _first(row, "status", "event_status", "eventStatus"),
    }


async def main() -> None:
    headers = {"User-Agent": "arb-gemini-reward-economics-census/1.0"}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as http:
        cfg_r, ev_r = await asyncio.gather(
            http.get(REST + CONFIG),
            http.get(REST + EVENTS, params={"category": "Crypto", "limit": 100, "offset": 0}),
        )
    cfg_r.raise_for_status()
    ev_r.raise_for_status()
    config = cfg_r.json() if cfg_r.content else {}
    payload = ev_r.json() if ev_r.content else {}
    rows = payload.get("data") or payload.get("events") or payload.get("liquidity_reward_events") or []
    normalized = [_normalize(r) for r in rows if isinstance(r, Mapping)]
    short = [r for r in normalized if r["asset"] in {"BTC", "ETH"} and r["window_minutes"] in {5, 15}]

    def _pool_sum(items: list[dict[str, Any]]) -> D:
        total = D("0")
        for r in items:
            p = _dec(r.get("daily_pool_usd"))
            if p is not None:
                total += p
        return total

    zero_maker = [r for r in short if r.get("qualifying_maker_count") == 0]
    known_makers = [r for r in short if isinstance(r.get("qualifying_maker_count"), int)]
    out = {
        "classification": "READ_ONLY_PUBLIC_GEMINI_REWARD_ECONOMICS_CENSUS",
        "program_enabled": bool(config.get("enabled")),
        "config": config,
        "crypto_reward_event_count": len(normalized),
        "btc_eth_5m_15m_event_count": len(short),
        "btc_eth_5m_15m_daily_pool_usd_total_reported": str(_pool_sum(short)),
        "zero_qualifying_maker_short_event_count": len(zero_maker),
        "zero_qualifying_maker_short_daily_pool_usd_total_reported": str(_pool_sum(zero_maker)),
        "known_maker_count_short_event_count": len(known_makers),
        "short_events": short,
        "interpretation_guardrails": {
            "equal_share_reference_is_expected_payout": False,
            "rewards_counted_as_trade_profit": False,
            "rewards_counted_as_realized_pnl": False,
            "order_entry": False,
            "credentials_read": False,
        },
        "order_entry": False,
        "realized_pnl": 0,
    }
    print("GEMINI_REWARD_ECONOMICS_CENSUS=" + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    print("GEMINI_REWARD_ECONOMICS_CENSUS_COMPLETE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
