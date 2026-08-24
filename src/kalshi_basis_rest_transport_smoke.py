"""Read-only production smoke for the basis canary's exact Kalshi REST books."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from src import gemini_kalshi_basis_risk_marketdata as marketdata
from src import gemini_kalshi_rollover_optimizer as rollover
from src.gemini_kalshi_predictive_hot import predicted_pair

logger = logging.getLogger("kalshi_arb.kalshi_basis_rest_transport_smoke")
ASSETS = ("BTC", "ETH", "SOL", "XRP")


async def main_async() -> None:
    private_key = rollover.hot.load_private_key()
    rows = []
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
    async with httpx.AsyncClient(timeout=2.0, limits=limits) as http:
        for cycle in range(5):
            pairs = {asset: predicted_pair(asset) for asset in ASSETS}

            async def one(asset: str):
                pair = pairs[asset]
                ticker = pair.kalshi_ticker
                path = f"/trade-api/v2/markets/{ticker}/orderbook"
                headers = rollover.hot.auth_headers(
                    private_key, "GET", path, rollover.hot.API_KEY_ID
                )
                start_ms = int(time.time() * 1000)
                started = time.perf_counter()
                response = await http.get(
                    f"{rollover.readiness.KALSHI_REST}/markets/{ticker}/orderbook",
                    params={"depth": 1},
                    headers=headers,
                )
                rtt_ms = (time.perf_counter() - started) * 1000
                top = None
                if response.status_code == 200:
                    top = marketdata.exact_rest_top(response.json(), ticker, start_ms)
                return {
                    "cycle": cycle,
                    "asset": asset,
                    "ticker": ticker,
                    "status": response.status_code,
                    "rtt_ms": round(rtt_ms, 3),
                    "parsed": top is not None,
                    "quote_ts_mode": "request_start",
                    "read_only": True,
                    "order_entry": False,
                }

            batch = await asyncio.gather(*(one(asset) for asset in ASSETS))
            rows.extend(batch)
            logger.info("KALSHI_BASIS_REST_SMOKE_CYCLE %s", json.dumps(batch, separators=(",", ":")))
            await asyncio.sleep(0.05)

    ok = [row for row in rows if row["status"] == 200 and row["parsed"]]
    under_100 = [row for row in ok if row["rtt_ms"] <= 100.0]
    summary = {
        "requests": len(rows),
        "http_200_and_parsed": len(ok),
        "rtt_le_100ms": len(under_100),
        "max_rtt_ms": max((row["rtt_ms"] for row in rows), default=None),
        "min_rtt_ms": min((row["rtt_ms"] for row in rows), default=None),
        "all_assets": list(ASSETS),
        "read_only": True,
        "order_entry": False,
    }
    logger.warning("KALSHI_BASIS_REST_SMOKE_SUMMARY %s", json.dumps(summary, separators=(",", ":")))
    if len(ok) != len(rows):
        raise RuntimeError("exact REST transport had HTTP/parse failures")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
