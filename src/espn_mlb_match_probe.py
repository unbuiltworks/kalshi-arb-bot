"""Read-only diagnostic for ESPN/Kalshi MLB physical-game matching."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path

import httpx

import src.kalshi_mlb_sharp_ev_backtest as bt
import src.espn_mlb_closing_ev_backtest as eb
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern


async def run() -> dict:
    start_date = date(2026, 3, 25)
    end_date = date(2026, 8, 24)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True) as http:
        archived, recent = await asyncio.gather(
            eb._page_markets_compat(http, historical=True),
            eb._page_markets_compat(http, historical=False),
        )
        by_ticker = {}
        for market in archived + recent:
            ticker = str(market.get("ticker") or "")
            if ticker and (ticker not in by_ticker or market.get("_source_tier") == "live"):
                by_ticker[ticker] = market
        original = bt.normalize_kalshi_mlb
        bt.normalize_kalshi_mlb = normalize_kalshi_mlb_eastern
        try:
            sides = bt._kalshi_sides(by_ticker.values(), start_date, end_date)
        finally:
            bt.normalize_kalshi_mlb = original
        days = sorted({datetime.fromtimestamp(s.start_ts, tz=timezone.utc).date() for s in sides})[:3]
        allowed = set(days)
        sides = [s for s in sides if datetime.fromtimestamp(s.start_ts, tz=timezone.utc).date() in allowed]
        by_event = defaultdict(list)
        for side in sides:
            by_event[side.event_key].append(side)
        sharp, stats = await eb._load_espn_closes(http, days)

    pair_matches = 0
    within_5m = 0
    within_1h = 0
    within_6h = 0
    samples = []
    for row in sharp:
        candidates = by_event.get(row.event_key, [])
        if not candidates:
            continue
        pair_matches += 1
        nearest = min(candidates, key=lambda s: abs(s.start_ts - row.start_ts))
        skew = nearest.start_ts - row.start_ts
        a = abs(skew)
        within_5m += int(a <= 300)
        within_1h += int(a <= 3600)
        within_6h += int(a <= 21600)
        if len(samples) < 30:
            samples.append({
                "event_key": list(row.event_key),
                "espn_start": datetime.fromtimestamp(row.start_ts, tz=timezone.utc).isoformat(),
                "kalshi_start": datetime.fromtimestamp(nearest.start_ts, tz=timezone.utc).isoformat(),
                "skew_seconds_kalshi_minus_espn": skew,
                "kalshi_ticker": nearest.ticker,
            })

    kalshi_unique = sorted({(s.event_key, s.start_ts) for s in sides}, key=lambda x: x[1])
    return {
        "days": [d.isoformat() for d in days],
        "espn": stats,
        "kalshi_sides": len(sides),
        "kalshi_unique_physical_keys": len(kalshi_unique),
        "exact_team_pair_matches": pair_matches,
        "within_5m": within_5m,
        "within_1h": within_1h,
        "within_6h": within_6h,
        "pair_match_samples": samples,
        "espn_sample": [{
            "event_key": list(s.event_key),
            "start": datetime.fromtimestamp(s.start_ts, tz=timezone.utc).isoformat(),
        } for s in sharp[:20]],
        "kalshi_sample": [{
            "event_key": list(k),
            "start": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        } for k, ts in kalshi_unique[:20]],
    }


if __name__ == "__main__":
    result = asyncio.run(run())
    out = Path("research/results/espn_mlb_match_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, separators=(",", ":")))
