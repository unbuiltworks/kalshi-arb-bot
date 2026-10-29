"""Read-only public census of Polymarket US crypto order books.

Purpose: decide whether the L5-style short-duration maker concept can transfer
to the venue we can legally access. Enumerates crypto series -> open markets,
snapshots each book once, and measures structure against the US crypto rewards
program's published qualification target (15,000 resting contracts per side,
$800/day shared pool, 0.30 discount factor -- polymarket.us/rewards, 2026-08-30).

Public GET endpoints only; no credentials; no order entry. One snapshot is a
structural census (what markets exist, what depth rests); it does NOT measure
taker flow -- that requires timed sampling and is out of scope here.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

GATEWAY = "https://gateway.polymarket.us"
CRYPTO_KEYWORDS = ("btc", "bitcoin", "eth", "ethereum", "sol", "solana", "xrp", "doge", "crypto")
TARGET_CONTRACTS_PER_SIDE = 15_000
NEAR_TICKS = D("0.05")  # depth within 5 cents of best counts as "near"


def _levels(rows: Any) -> List[Dict[str, D]]:
    out = []
    for row in rows or []:
        try:
            out.append({"px": D(str(row["px"]["value"])), "qty": D(str(row["qty"]))})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def crypto_series(http: httpx.Client) -> List[Dict[str, Any]]:
    found, offset = [], 0
    while True:
        payload = http.get(f"{GATEWAY}/v1/series", params={"limit": 100, "offset": offset}).json()
        rows = payload.get("series") or []
        if not rows:
            return found
        for row in rows:
            text = f"{row.get('title','')} {row.get('slug','')}".lower()
            if any(key in text for key in CRYPTO_KEYWORDS):
                found.append(row)
        offset += len(rows)
        if offset > 5000:
            return found


def open_markets(http: httpx.Client, series_id: str) -> List[Dict[str, Any]]:
    payload = http.get(
        f"{GATEWAY}/v1/events", params={"series_id": series_id, "limit": 100}
    ).json()
    markets = []
    for event in payload.get("events") or []:
        if event.get("closed"):
            continue
        for market in event.get("markets") or []:
            if market.get("closed") or not market.get("active"):
                continue
            market = dict(market)
            market["_event_ticker"] = event.get("ticker")
            market["_event_end"] = event.get("endDate")
            markets.append(market)
    return markets


def census_market(http: httpx.Client, market: Dict[str, Any]) -> Dict[str, Any]:
    slug = market.get("slug")
    row: Dict[str, Any] = {
        "slug": slug,
        "event": market.get("_event_ticker"),
        "question": market.get("question"),
        "end_date": market.get("endDate") or market.get("_event_end"),
        "tick": market.get("orderPriceMinTickSize"),
        "fee_coefficient": market.get("feeCoefficient"),
    }
    response = http.get(f"{GATEWAY}/v1/markets/{slug}/book")
    if response.status_code != 200:
        row["book_status"] = response.status_code
        return row
    data = (response.json() or {}).get("marketData") or {}
    bids = sorted(_levels(data.get("bids")), key=lambda l: l["px"], reverse=True)
    asks = sorted(_levels(data.get("offers")), key=lambda l: l["px"])
    row["book_status"] = 200
    if bids:
        row["best_bid"] = str(bids[0]["px"])
        row["bid_depth_best"] = str(bids[0]["qty"])
        near = bids[0]["px"] - NEAR_TICKS
        row["bid_depth_near"] = str(sum(l["qty"] for l in bids if l["px"] >= near))
        row["bid_depth_total"] = str(sum(l["qty"] for l in bids))
        row["bid_meets_target_near"] = sum(l["qty"] for l in bids if l["px"] >= near) >= TARGET_CONTRACTS_PER_SIDE
    if asks:
        row["best_ask"] = str(asks[0]["px"])
        row["ask_depth_best"] = str(asks[0]["qty"])
        near = asks[0]["px"] + NEAR_TICKS
        row["ask_depth_near"] = str(sum(l["qty"] for l in asks if l["px"] <= near))
        row["ask_depth_total"] = str(sum(l["qty"] for l in asks))
        row["ask_meets_target_near"] = sum(l["qty"] for l in asks if l["px"] <= near) >= TARGET_CONTRACTS_PER_SIDE
    if bids and asks:
        row["spread"] = str(asks[0]["px"] - bids[0]["px"])
    return row


def run(out_path: Optional[Path] = None) -> Dict[str, Any]:
    with httpx.Client(timeout=20, headers={"User-Agent": "arb-readonly-us-census/1.0"}) as http:
        series = crypto_series(http)
        markets: List[Dict[str, Any]] = []
        for row in series:
            markets.extend(open_markets(http, row["id"]))
        rows = []
        for market in markets:
            rows.append(census_market(http, market))
            time.sleep(0.1)

    both_target = sum(
        1 for r in rows if r.get("bid_meets_target_near") and r.get("ask_meets_target_near")
    )
    spreads = [D(r["spread"]) for r in rows if r.get("spread")]
    payload = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "gateway": GATEWAY,
        "rewards_program_reference": {
            "source": "polymarket.us/rewards as of 2026-08-30 (user-verified)",
            "daily_pool_usd": 800,
            "markets": 50,
            "discount_factor": 0.30,
            "target_contracts_per_side": TARGET_CONTRACTS_PER_SIDE,
        },
        "crypto_series": [
            {"id": s.get("id"), "slug": s.get("slug"), "title": s.get("title")} for s in series
        ],
        "open_active_crypto_markets": len(rows),
        "markets_with_both_sides_at_target_near_best": both_target,
        "median_spread": str(sorted(spreads)[len(spreads) // 2]) if spreads else None,
        "short_duration_updown_markets_found": 0,
        "markets": rows,
    }
    out_path = Path(out_path or Path("analysis/l5/polymarket_us_crypto_census.json"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":  # pragma: no cover
    result = run()
    print(json.dumps({k: v for k, v in result.items() if k != "markets"}, indent=1, default=str))
