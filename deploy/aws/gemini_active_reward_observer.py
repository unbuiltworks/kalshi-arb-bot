"""Observe Gemini liquidity-reward competition through an active short-crypto cycle.

Public endpoint only. No credentials, account calls, or order entry.

The rewards events endpoint also lists future events, whose qualifying-maker count can
naturally be zero before scoring starts. This observer avoids that trap by using each
row's documented `ends_at` plus the ticker's 5m/15m duration to classify whether the
event is inside its active scoring window at each sample.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import time
from typing import Any, Mapping

import httpx

URL = "https://api.gemini.com/v1/prediction-markets/liquidity-rewards/events"
OBSERVE_SECONDS = 330.0
SAMPLE_SECONDS = 15.0


def _ticker(row: Mapping[str, Any]) -> str:
    return str(row.get("event_ticker") or "").upper()


def _asset(ticker: str) -> str | None:
    if "BTC" in ticker:
        return "BTC"
    if "ETH" in ticker:
        return "ETH"
    return None


def _minutes(ticker: str) -> int | None:
    if "05M" in ticker:
        return 5
    if "15M" in ticker:
        return 15
    return None


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        out = datetime.fromisoformat(text)
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        return out.astimezone(timezone.utc)
    except Exception:
        return None


def _active(row: Mapping[str, Any], now: datetime) -> tuple[bool, float | None]:
    ticker = _ticker(row)
    minutes = _minutes(ticker)
    end = _parse_iso(row.get("ends_at"))
    if minutes is None or end is None:
        return False, None
    seconds_to_end = (end - now).total_seconds()
    return 0.0 < seconds_to_end <= minutes * 60.0, seconds_to_end


async def main() -> None:
    start = time.monotonic()
    deadline = start + OBSERVE_SECONDS
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    request_errors: list[str] = []
    samples = 0
    headers = {"User-Agent": "arb-gemini-active-reward-observer/1.0"}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as http:
        while True:
            now = datetime.now(timezone.utc)
            samples += 1
            try:
                r = await http.get(URL, params={"category": "Crypto", "limit": 100, "offset": 0})
                r.raise_for_status()
                payload = r.json() if r.content else {}
                rows = payload.get("events") or payload.get("data") or []
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    ticker = _ticker(row)
                    asset = _asset(ticker)
                    minutes = _minutes(ticker)
                    if asset not in {"BTC", "ETH"} or minutes not in {5, 15}:
                        continue
                    active, seconds_to_end = _active(row, now)
                    if not active:
                        continue
                    observations[ticker].append({
                        "observed_at_utc": now.isoformat(),
                        "seconds_to_end": None if seconds_to_end is None else round(seconds_to_end, 3),
                        "daily_pool_usd": str(row.get("daily_pool_usd")),
                        "pool_source": row.get("pool_source"),
                        "ends_at": row.get("ends_at"),
                        "qualifying_maker_count": row.get("qualifying_maker_count"),
                        "title": row.get("title"),
                        "asset": asset,
                        "window_minutes": minutes,
                    })
            except Exception as exc:
                request_errors.append(type(exc).__name__ + ":" + str(exc)[:160])
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(SAMPLE_SECONDS)

    events: list[dict[str, Any]] = []
    for ticker in sorted(observations):
        rows = observations[ticker]
        counts = [r.get("qualifying_maker_count") for r in rows if isinstance(r.get("qualifying_maker_count"), int)]
        pools = sorted({r.get("daily_pool_usd") for r in rows})
        sources = sorted({str(r.get("pool_source")) for r in rows})
        events.append({
            "event_ticker": ticker,
            "asset": rows[0]["asset"],
            "window_minutes": rows[0]["window_minutes"],
            "sample_count": len(rows),
            "daily_pool_usd_values": pools,
            "pool_sources": sources,
            "qualifying_maker_count_min": None if not counts else min(counts),
            "qualifying_maker_count_max": None if not counts else max(counts),
            "qualifying_maker_count_last": None if not counts else counts[-1],
            "zero_maker_sample_count": sum(1 for c in counts if c == 0),
            "positive_maker_sample_count": sum(1 for c in counts if c > 0),
            "first_observation": rows[0],
            "last_observation": rows[-1],
        })

    out = {
        "classification": "READ_ONLY_PUBLIC_ACTIVE_WINDOW_REWARD_OBSERVER",
        "observe_seconds": OBSERVE_SECONDS,
        "sample_seconds": SAMPLE_SECONDS,
        "request_sample_count": samples,
        "request_error_count": len(request_errors),
        "request_errors": request_errors[:10],
        "active_short_event_count_observed": len(events),
        "events": events,
        "guardrails": {
            "qualifying_maker_count_is_future_payout_guarantee": False,
            "daily_pool_is_our_expected_reward": False,
            "credentials_read": False,
            "order_entry": False,
            "realized_pnl": 0,
        },
        "order_entry": False,
        "realized_pnl": 0,
    }
    print("GEMINI_ACTIVE_REWARD_OBSERVER=" + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    print("GEMINI_ACTIVE_REWARD_OBSERVER_COMPLETE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
