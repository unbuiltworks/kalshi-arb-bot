"""Read-only Kalshi WebSocket handshake diagnostic.

Opens one authenticated Trade API WebSocket and logs only non-sensitive HTTP response
metadata when the handshake is rejected. There is no order path.
"""
from __future__ import annotations

import asyncio
import logging

import websockets
from websockets.exceptions import InvalidStatus

from src import gemini_kalshi_rollover_optimizer as rollover

hot = rollover.hot
logger = logging.getLogger("kalshi_arb.kalshi_ws_429_header_probe")


async def probe() -> None:
    private_key = hot.load_private_key()
    headers = hot.auth_headers(private_key, "GET", hot.KALSHI_WS_SIGN_PATH, hot.API_KEY_ID)
    logger.warning(
        "KALSHI_WS_HEADER_PROBE_START host=%s read_only=true order_entry=false sockets=1",
        hot.KALSHI_WS,
    )
    try:
        async with websockets.connect(
            hot.KALSHI_WS,
            open_timeout=8.0,
            **hot._ws_header_kw(headers),
        ):
            logger.warning("KALSHI_WS_HEADER_PROBE_CONNECTED read_only=true order_entry=false")
    except InvalidStatus as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        raw_headers = getattr(response, "headers", None)
        allowed = {
            "retry-after",
            "x-ratelimit-limit",
            "x-ratelimit-remaining",
            "x-ratelimit-reset",
            "ratelimit-limit",
            "ratelimit-remaining",
            "ratelimit-reset",
            "server",
            "date",
        }
        safe = {}
        if raw_headers is not None:
            try:
                for key, value in raw_headers.raw_items():
                    if str(key).lower() in allowed:
                        safe[str(key).lower()] = str(value)
            except Exception:
                pass
        logger.error(
            "KALSHI_WS_HEADER_PROBE_REJECTED status=%s headers=%s read_only=true order_entry=false",
            status,
            safe,
        )
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(probe())


if __name__ == "__main__":
    main()
