"""Read-only Gemini/Kalshi crypto duration inventory, including hourly discovery.

No order/auth transport. The purpose is to establish whether same-asset hourly
markets exist on both venues and expose enough explicit timing metadata to match
windows fail-closed before any maker promotion.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import GEMINI_REST, KALSHI_REST, _gemini_active_crypto, _kalshi_open, _text, _asset_from_text, _dt

logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_duration_inventory")
ASSETS = ("BTC", "ETH", "SOL", "XRP")


def _asset_ticker(text: str) -> str | None:
    u = text.upper()
    for a in ASSETS:
        if re.search(rf"(?:^|[-_]){a}(?:15M|1H|H|60M|[-_]|$)", u) or u.startswith(a):
            return a
    return None


def _minutes_from_text(text: str) -> int | None:
    s = text.lower()
    if re.search(r"\b15\s*(?:minute|min|m)\b|15m", s): return 15
    if re.search(r"\b(?:1\s*(?:hour|hr|h)|60\s*(?:minute|min|m))\b|1h|60m", s): return 60
    return None


def _event_times(row: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    starts = ("startTime", "start_time", "openTime", "open_time", "eventStartTime", "event_start_time")
    ends = ("endTime", "end_time", "closeTime", "close_time", "expirationTime", "expiration_time")
    start = next((_dt(row.get(k)) for k in starts if _dt(row.get(k)) is not None), None)
    end = next((_dt(row.get(k)) for k in ends if _dt(row.get(k)) is not None), None)
    return start, end


def _duration(row: dict[str, Any]) -> int | None:
    explicit = row.get("durationMinutes") or row.get("duration_minutes") or row.get("duration")
    try:
        x = int(explicit)
        if x in {5, 15, 30, 60, 240, 1440}: return x
    except Exception:
        pass
    t = " ".join(str(row.get(k) or "") for k in ("ticker","title","description","subtitle","rules","rules_primary"))
    parsed = _minutes_from_text(t)
    if parsed: return parsed
    start, end = _event_times(row)
    if start and end:
        mins = round((end-start).total_seconds()/60)
        if 0 < mins <= 10080: return mins
    return None


def _gemini_asset(row: dict[str, Any]) -> str | None:
    return _asset_ticker(str(row.get("ticker") or "")) or _asset_ticker(str(row.get("title") or ""))


async def run() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=25.0) as http:
        gemini, kalshi = await asyncio.gather(_gemini_active_crypto(http), _kalshi_open(http))

    gd: dict[str, Counter] = defaultdict(Counter)
    kd: dict[str, Counter] = defaultdict(Counter)
    g_hourly=[]; k_hourly=[]
    unknown_g=[]; unknown_k=[]
    for row in gemini:
        asset = _gemini_asset(row)
        if not asset: continue
        dur = _duration(row)
        gd[asset][str(dur or "unknown")] += 1
        sample = {"ticker":row.get("ticker"),"title":row.get("title"),"duration_minutes":dur,"keys":sorted(row.keys())[:80]}
        if dur == 60: g_hourly.append(sample)
        elif dur is None and len(unknown_g)<12: unknown_g.append(sample)
    for row in kalshi:
        asset = _asset_from_text(row)
        if not asset: continue
        dur = _duration(row)
        kd[asset][str(dur or "unknown")] += 1
        sample={"ticker":row.get("ticker"),"event_ticker":row.get("event_ticker"),"title":row.get("title"),"duration_minutes":dur,"expiration_time":row.get("expiration_time"),"expected_expiration_time":row.get("expected_expiration_time")}
        if dur == 60: k_hourly.append(sample)
        elif dur is None and len(unknown_k)<12: unknown_k.append(sample)

    out={
        "gemini_active_crypto_events":len(gemini),
        "kalshi_open_markets":len(kalshi),
        "gemini_duration_counts":{a:dict(v) for a,v in gd.items()},
        "kalshi_duration_counts":{a:dict(v) for a,v in kd.items()},
        "gemini_hourly_count":len(g_hourly),
        "kalshi_hourly_count":len(k_hourly),
        "gemini_hourly_samples":g_hourly[:12],
        "kalshi_hourly_samples":k_hourly[:12],
        "gemini_unknown_samples":unknown_g,
        "kalshi_unknown_samples":unknown_k,
        "hourly_execution_authorized":False,
        "read_only":True,
        "order_entry":False,
        "realized":False,
    }
    logger.warning("CRYPTO_DURATION_INVENTORY %s",json.dumps(out,separators=(",",":"),default=str))
    return out


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())

if __name__=="__main__": main()
