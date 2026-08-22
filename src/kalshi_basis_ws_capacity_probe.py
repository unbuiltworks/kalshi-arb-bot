"""Read-only Kalshi API/WS capacity probe for the basis canary.

This module has no order path. It first reads the account's current documented API
usage tier/budgets, then opens exactly one authenticated Kalshi WebSocket, subscribes
to the current BTC 15-minute orderbook, requires an actual snapshot, logs success, and
exits. It is used only to prove API capacity before any new basis-risk live arm.

The probe is intentionally rerunnable after account-tier or socket-capacity changes,
including after pausing non-core read-only Kalshi WebSocket consumers.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import websockets

from src import gemini_kalshi_rollover_optimizer as rollover
from src.gemini_kalshi_predictive_hot import predicted_pair

hot = rollover.hot
logger = logging.getLogger("kalshi_arb.kalshi_basis_ws_capacity_probe")
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"


async def account_limits(private_key) -> None:
    path = "/trade-api/v2/account/limits"
    headers = hot.auth_headers(private_key, "GET", path, hot.API_KEY_ID)
    async with httpx.AsyncClient(timeout=5.0) as http:
        response = await http.get(f"{KALSHI_REST}/account/limits", headers=headers)
    response.raise_for_status()
    body = response.json()
    read = body.get("read") or {}
    write = body.get("write") or {}
    grants = body.get("grants") or []
    logger.warning(
        "KALSHI_API_LIMITS usage_tier=%s read_refill=%s read_capacity=%s write_refill=%s write_capacity=%s grants=%s read_only=true order_entry=false",
        body.get("usage_tier"),
        read.get("refill_rate"),
        read.get("bucket_capacity"),
        write.get("refill_rate"),
        write.get("bucket_capacity"),
        len(grants),
    )


async def probe() -> None:
    pair = predicted_pair("BTC")
    private_key = hot.load_private_key()
    await account_limits(private_key)
    headers = hot.auth_headers(private_key, "GET", hot.KALSHI_WS_SIGN_PATH, hot.API_KEY_ID)
    logger.warning(
        "KALSHI_BASIS_WS_CAPACITY_PROBE_START ticker=%s read_only=true order_entry=false sockets=1",
        pair.kalshi_ticker,
    )
    async with websockets.connect(
        hot.KALSHI_WS,
        open_timeout=8.0,
        **hot._ws_header_kw(headers),
    ) as ws:
        await ws.send(json.dumps(
            rollover._subscribe_payload(9901, [pair.kalshi_ticker]),
            separators=(",", ":"),
        ))
        subscribed = False
        async with asyncio.timeout(10.0):
            async for raw in ws:
                payload = json.loads(raw)
                typ = str(payload.get("type") or "")
                if typ == "error":
                    raise RuntimeError(f"Kalshi subscription error: {str(payload.get('msg'))[:240]}")
                if typ == "subscribed":
                    subscribed = True
                    logger.info(
                        "KALSHI_BASIS_WS_CAPACITY_PROBE_SUBSCRIBED ticker=%s read_only=true order_entry=false",
                        pair.kalshi_ticker,
                    )
                    continue
                if typ == "orderbook_snapshot":
                    msg = payload.get("msg") or {}
                    ticker = str(msg.get("market_ticker") or "")
                    if ticker != pair.kalshi_ticker:
                        continue
                    logger.warning(
                        "KALSHI_BASIS_WS_CAPACITY_PROBE_SUCCESS ticker=%s subscribed=%s snapshot=true read_only=true order_entry=false",
                        pair.kalshi_ticker,
                        str(subscribed).lower(),
                    )
                    return
        raise TimeoutError("Kalshi WS connected but no current-market orderbook snapshot arrived")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(probe())


if __name__ == "__main__":
    main()
