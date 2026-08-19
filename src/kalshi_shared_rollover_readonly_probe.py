"""Read-only live validation for the one-WS/one-SID Kalshi rollover transport.

No order modules or write endpoints are imported. The probe subscribes to current
BTC/ETH/SOL/XRP books on one SID, dynamically adds the next 15-minute books, verifies
fresh snapshots and contiguous multiplexed sequencing, then removes the current books.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from src import gemini_kalshi_rollover_optimizer as opt
from src.gemini_kalshi_predictive_hot import predicted_pair

hot = opt.hot
logger = logging.getLogger("kalshi_arb.kalshi_shared_rollover_readonly_probe")
ASSETS = ("BTC", "ETH", "SOL", "XRP")


async def _wait_ready(hub: opt.SharedKalshiOrderbookHub, tickers: set[str], timeout_s: float) -> int:
    start = time.perf_counter_ns()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready = set(hub.health()["snapshot_ready_markets"])
        if tickers <= ready:
            return int((time.perf_counter_ns() - start) / 1_000_000)
        await asyncio.sleep(0.01)
    raise TimeoutError(
        f"Kalshi shared rollover probe snapshot timeout missing={sorted(tickers - set(hub.health()['snapshot_ready_markets']))}"
    )


async def main_async() -> None:
    hub = opt.SharedKalshiOrderbookHub()
    queues = {asset: asyncio.Queue(maxsize=20_000) for asset in ASSETS}
    stats = {asset: hot.AssetStats(asset=asset) for asset in ASSETS}

    current = {asset: predicted_pair(asset) for asset in ASSETS}
    common_end = max(pair.window_end.timestamp() for pair in current.values())
    nxt = {
        asset: predicted_pair(asset, now_ts=common_end + 0.001)
        for asset in ASSETS
    }
    current_tickers = {pair.kalshi_ticker for pair in current.values()}
    next_tickers = {pair.kalshi_ticker for pair in nxt.values()}

    initial_start = time.perf_counter_ns()
    await asyncio.gather(*(
        hub.register(current[asset].kalshi_ticker, queues[asset], stats[asset])
        for asset in ASSETS
    ))
    current_ready_ms = await _wait_ready(hub, current_tickers, 10.0)
    current_from_register_ms = int((time.perf_counter_ns() - initial_start) / 1_000_000)

    add_start = time.perf_counter_ns()
    await asyncio.gather(*(
        hub.register(nxt[asset].kalshi_ticker, queues[asset], stats[asset])
        for asset in ASSETS
    ))
    next_ready_ms = await _wait_ready(hub, next_tickers, 10.0)
    next_from_add_ms = int((time.perf_counter_ns() - add_start) / 1_000_000)

    # Observe live deltas with eight books simultaneously active.
    await asyncio.sleep(2.0)
    mid = hub.health()
    if mid["sequence_gaps"] != 0:
        raise RuntimeError(f"shared sequence gap detected: {mid}")
    if mid["connection_count"] != 1:
        raise RuntimeError(f"unexpected reconnect while adding next markets: {mid}")
    if not (current_tickers | next_tickers) <= set(mid["snapshot_ready_markets"]):
        raise RuntimeError(f"not all eight market snapshots are ready: {mid}")

    # Simulate rollover retirement of old books while preserving the same SID/socket.
    await asyncio.gather(*(
        hub.unregister(current[asset].kalshi_ticker, queues[asset], stats[asset])
        for asset in ASSETS
    ))
    await asyncio.sleep(1.0)
    final = hub.health()
    if final["sequence_gaps"] != 0:
        raise RuntimeError(f"sequence gap after deleting old markets: {final}")
    if final["connection_count"] != 1:
        raise RuntimeError(f"socket reconnected during rollover simulation: {final}")
    if set(final["desired_markets"]) != next_tickers:
        raise RuntimeError(f"old markets were not retired cleanly: {final}")

    payload = {
        "status": "PASS",
        "read_only": True,
        "order_entry": False,
        "assets": list(ASSETS),
        "current_window_end_utc": current["BTC"].window_end.isoformat(),
        "next_window_end_utc": nxt["BTC"].window_end.isoformat(),
        "current_snapshot_ready_ms": current_ready_ms,
        "current_from_register_ms": current_from_register_ms,
        "next_snapshot_ready_ms": next_ready_ms,
        "next_from_add_ms": next_from_add_ms,
        "single_websocket": final["connection_count"] == 1,
        "single_sid": final["sid"],
        "sequence_gaps": final["sequence_gaps"],
        "last_seq": final["last_seq"],
        "next_markets_retained": sorted(next_tickers),
        "kalshi_events": {asset: stats[asset].kalshi_events for asset in ASSETS},
    }
    print("KALSHI_SHARED_ROLLOVER_PROBE " + json.dumps(payload, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    asyncio.run(main_async())
