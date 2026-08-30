"""Public/read-only live-book scan for Gemini crypto liquidity-reward markets.

Joins Gemini's active crypto market catalog to the public liquidity-reward event
list, then samples the live top of book for the nearest expiring markets. No API
keys, private account data, orders, or cancellations are used.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import time
from typing import Any, Mapping

import httpx

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

REST = "https://api.gemini.com"
Q = D("10")
ONE = D("1")
ASSETS = ("BTC", "ETH", "SOL", "XRP")
MAX_MARKETS_TO_SAMPLE = 12


def _iso_ms(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _asset(ticker: str) -> str:
    t = ticker.upper()
    return next((a for a in ASSETS if t.startswith(a)), "OTHER")


def _window(ticker: str) -> int:
    t = ticker.upper()
    if "15M" in t:
        return 15
    if "05M" in t or "5M" in t:
        return 5
    return 0


def _model(bid: D, ask: D) -> dict[str, D]:
    yes = D(str(bid)); no = ONE - D(str(ask))
    yf = gemini_predictions_maker_fee(yes, Q); nf = gemini_predictions_maker_fee(no, Q)
    return {
        "yes_quote": yes,
        "no_quote": no,
        "gross_pair_profit": Q * (ONE - yes - no),
        "modeled_pair_profit_after_fees": Q * (ONE - yes - no) - yf - nf,
        "yes_fee": yf,
        "no_fee": nf,
        "reserve_usd": Q * (yes + no),
        "max_one_side_usd": Q * max(yes, no),
    }


async def _public_json(http: httpx.AsyncClient, path: str, **params: Any) -> Any:
    r = await http.get(f"{REST}{path}", params=params or None)
    r.raise_for_status()
    return r.json() if r.content else {}


async def active_reward_catalog() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True,
                                 headers={"User-Agent":"arb-low-volume-live-scan/1.0"}) as http:
        cfg, reward_payload, market_payload = await asyncio.gather(
            _public_json(http, "/v1/prediction-markets/liquidity-rewards/config"),
            _public_json(http, "/v1/prediction-markets/liquidity-rewards/events", category="Crypto", limit=100, offset=0),
            _public_json(http, "/v1/prediction-markets/events", status="active", category="Crypto", limit=500, offset=0),
        )
    rewards = reward_payload.get("data") or reward_payload.get("events") or reward_payload.get("liquidity_reward_events") or []
    reward_by_ticker = {
        str(r.get("event_ticker") or r.get("eventTicker") or "").upper(): dict(r)
        for r in rewards if isinstance(r, Mapping)
    }
    rows: list[dict[str, Any]] = []
    now_ms = int(time.time()*1000)
    for event in market_payload.get("data") or []:
        if not isinstance(event, Mapping):
            continue
        ticker = str(event.get("ticker") or "").upper()
        reward = reward_by_ticker.get(ticker)
        if reward is None or _asset(ticker) == "OTHER":
            continue
        for contract in event.get("contracts") or []:
            if not isinstance(contract, Mapping):
                continue
            symbol = str(contract.get("instrumentSymbol") or "")
            state = str(contract.get("marketState") or contract.get("status") or "").lower()
            if not symbol.upper().endswith("-UP") or state not in {"open","active"}:
                continue
            expiry_ms = _iso_ms(contract.get("expiryDate") or event.get("expiryDate"))
            if expiry_ms is None or expiry_ms <= now_ms:
                continue
            makers_raw = reward.get("qualifying_maker_count") if "qualifying_maker_count" in reward else reward.get("qualifyingMakerCount")
            pool_raw = reward.get("daily_pool_usd") if "daily_pool_usd" in reward else reward.get("dailyPoolUsd")
            try: makers = int(makers_raw)
            except Exception: makers = -1
            rows.append({
                "ticker": ticker, "symbol": symbol, "asset": _asset(ticker), "window_minutes": _window(ticker),
                "expiry_ms": expiry_ms, "seconds_to_expiry": (expiry_ms-now_ms)/1000.0,
                "daily_pool_usd": str(pool_raw or "0"), "qualifying_maker_count": makers,
            })
    rows.sort(key=lambda r: (r["seconds_to_expiry"], -D(r["daily_pool_usd"])))
    return cfg, rows


async def _sample(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    try:
        bid, bid_q, ask, ask_q = await base.fresh_top(row["symbol"], timeout=3.0)
        model = _model(bid, ask)
        out.update({
            "book_ok": True, "bid": str(bid), "bid_qty": str(bid_q), "ask": str(ask), "ask_qty": str(ask_q),
            "model": {k:str(v) for k,v in model.items()},
            "maker_rebate_band_both_sides": bool(D("0.20") <= model["yes_quote"] <= D("0.80") and D("0.20") <= model["no_quote"] <= D("0.80")),
        })
    except Exception as exc:
        out.update({"book_ok":False,"error_type":type(exc).__name__})
    return out


async def main() -> None:
    cfg, rows = await active_reward_catalog()
    # Sample nearest-expiry markets with asset diversity first, then fill remaining
    # slots by expiry. This avoids a BTC-only snapshot hiding ETH/SOL/XRP options.
    chosen=[]; seen=set()
    for row in rows:
        if row["asset"] not in seen:
            chosen.append(row); seen.add(row["asset"])
        if len(seen) == len(ASSETS): break
    for row in rows:
        if row not in chosen:
            chosen.append(row)
        if len(chosen) >= MAX_MARKETS_TO_SAMPLE: break
    sampled = await asyncio.gather(*(_sample(r) for r in chosen))
    sampled.sort(key=lambda r: (
        not r.get("book_ok",False),
        -(D(r.get("daily_pool_usd") or "0") / D(max(1, int(r.get("qualifying_maker_count",0))+1))),
        r.get("seconds_to_expiry",999999),
    ))
    out={
        "classification":"READ_ONLY_PUBLIC_LOW_VOLUME_LIVE_BOOK_SCAN_NOT_REALIZED",
        "order_entry":False,"credentials_loaded":False,
        "reward_enabled":bool(cfg.get("enabled")),
        "max_spread_cents":str(cfg.get("max_spread_cents") or cfg.get("maxSpreadCents") or ""),
        "active_reward_contract_count":len(rows),
        "sampled":sampled,
        "reward_pool_numbers_are_not_expected_or_realized_payouts":True,
    }
    print("GEMINI_LOW_VOLUME_LIVE_SCAN="+json.dumps(out,separators=(",",":"),default=str))


if __name__ == "__main__":
    asyncio.run(main())
