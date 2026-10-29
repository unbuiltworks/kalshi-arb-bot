"""Bounded READ-ONLY diagnostic for PMUS market-data websocket shape.

Subscribes only to documented SUBSCRIPTION_TYPE_MARKET_DATA (1) for a few
venue-reported live sports markets and prints structural keys/counts only.
No order/private endpoints, mutations, or credential values are logged.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter

import websockets

from src import pmus_sports_collector as col
from src import pmus_ws_evidence_recorder as rec


def _scan(obj, counts: Counter) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            counts[str(k)] += 1
            _scan(v, counts)
    elif isinstance(obj, list):
        for v in obj:
            _scan(v, counts)


def _live_slugs(limit: int = 3) -> list[str]:
    events, diag = col.fetch_live_events(col.http_get_json)
    targets = col.live_event_targets(events, ())
    slugs = []
    for t in targets:
        slug = str(t.get("slug") or "")
        if slug and slug not in slugs:
            slugs.append(slug)
        if len(slugs) >= limit:
            break
    print("PMUS_WS_BOOK_DIAG " + json.dumps({
        "stage": "discovery", "live_events": diag.get("v1_live_events"),
        "slugs": slugs, "read_only": True,
    }, separators=(",", ":")), flush=True)
    return slugs


async def main() -> int:
    if not rec.has_credentials():
        print("PMUS_WS_BOOK_DIAG " + json.dumps({
            "verdict": "NO_CREDENTIALS", "read_only": True,
        }), flush=True)
        return 2
    slugs = _live_slugs()
    if not slugs:
        print("PMUS_WS_BOOK_DIAG " + json.dumps({
            "verdict": "NO_LIVE_SLUGS", "read_only": True,
        }), flush=True)
        return 3
    headers = rec.auth_headers(os.environ[rec.KEY_ENV].strip(),
                               os.environ[rec.SECRET_ENV].strip())
    frame = {"subscribe": {
        "request_id": "book-diag",
        "subscription_type": rec.SUB_MARKET_DATA,
        "market_slugs": slugs,
    }}
    total = 0
    with_bids = with_offers = with_best_bid = with_best_ask = 0
    key_counts: Counter = Counter()
    async with websockets.connect(rec.WS_URL, additional_headers=headers,
                                  max_size=8 * 1024 * 1024) as ws:
        await ws.send(json.dumps(rec.guard_outbound(frame)))
        deadline = asyncio.get_running_loop().time() + 25.0
        while total < 50 and asyncio.get_running_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if isinstance(msg, dict) and "heartbeat" in msg:
                await ws.send(json.dumps(rec.guard_outbound({"heartbeat": {}})))
                continue
            total += 1
            c = Counter()
            _scan(msg, c)
            key_counts.update(c)
            with_bids += int(c["bids"] > 0)
            with_offers += int(c["offers"] > 0)
            with_best_bid += int(c["bestBid"] > 0 or c["best_bid"] > 0)
            with_best_ask += int(c["bestAsk"] > 0 or c["best_ask"] > 0)
    print("PMUS_WS_BOOK_DIAG " + json.dumps({
        "verdict": "COMPLETE", "read_only": True,
        "subscription_type": rec.SUB_MARKET_DATA,
        "messages": total,
        "messages_with_bids": with_bids,
        "messages_with_offers": with_offers,
        "messages_with_best_bid": with_best_bid,
        "messages_with_best_ask": with_best_ask,
        "observed_shape_keys": {k: key_counts[k] for k in (
            "market_data", "marketData", "market_data_lite", "marketDataLite",
            "bids", "offers", "bestBid", "bestAsk", "best_bid", "best_ask")
            if key_counts[k]},
    }, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
