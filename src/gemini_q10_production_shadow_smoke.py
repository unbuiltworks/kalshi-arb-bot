"""Public-only AWS smoke for the q10 production candidate.

No credentials and no order entry. Compares REST ticker timing with Gemini's
low-latency BTCUSD bookTicker WebSocket, probes the exact prediction-market
settlement-reference stream, and asks the public q10 selector for a candidate.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_production_candidate as production
from src import gemini_q10_production_risk as risk
from src import gemini_q10_reference_ws_probe as reference_ws
from src import gemini_q10_spot_ws_guard as spot_ws


async def main() -> None:
    guard = risk.SpotMomentumGuard()
    samples = []
    async with httpx.AsyncClient(timeout=1.5, follow_redirects=True) as http:
        for _ in range(10):
            started = time.monotonic()
            spot = await production._spot(http)
            latency_ms = (time.monotonic() - started) * 1000.0
            toxic = guard.observe(time.monotonic(), spot)
            samples.append({
                "spot": str(spot),
                "latency_ms": round(latency_ms, 3),
                "move_bps": str(guard.current_move_bps()),
                "toxic": bool(toxic),
            })
            await asyncio.sleep(0.2)

    try:
        spot_stream = await spot_ws.probe(duration_s=2.0)
    except Exception as exc:
        spot_stream = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:300]}
    try:
        ref = await reference_ws.probe(max_messages=5, timeout_s=4.0)
    except Exception as exc:
        ref = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:300]}
    found = await probe._candidate()
    out = {
        "classification": "READ_ONLY_PUBLIC_Q10_PRODUCTION_SHADOW_SMOKE",
        "order_entry": False,
        "credentials_loaded": False,
        "rest_spot_samples": samples,
        "spot_ws": spot_stream,
        "reference_ws": ref,
        "candidate": None,
    }
    if found is not None:
        event, reward, model = found
        out["candidate"] = {
            "ticker": event.ticker,
            "symbol": event.symbol,
            "seconds_to_expiry": event.seconds_to_expiry,
            "model": {key: str(value) for key, value in model.items()},
            "qualifying_maker_count": reward["event"].get("qualifying_maker_count"),
        }
    print("Q10_PRODUCTION_SHADOW_SMOKE=" + json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    asyncio.run(main())
