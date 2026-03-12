"""
Kalshi WebSocket client with auth, subscriptions, and auto-reconnect.

Connects to the Kalshi WS endpoint, authenticates via RSA-PSS signed headers,
subscribes to orderbook_delta and fill channels, and dispatches incoming
messages to registered callbacks. Reconnects with exponential backoff on
disconnect.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from config.settings import (
    API_KEY_ID,
    WS_PING_INTERVAL_S,
    WS_RECONNECT_BASE_S,
    WS_RECONNECT_MAX_S,
    WS_URL,
)
from src.auth import auth_headers, load_private_key

logger = logging.getLogger("kalshi_arb.ws")

# Type alias for async message handlers
MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class KalshiWS:
    """
    Authenticated WebSocket client for Kalshi.

    Usage:
        ws = KalshiWS()
        ws.on_orderbook_snapshot(my_snapshot_handler)
        ws.on_orderbook_delta(my_delta_handler)
        ws.on_fill(my_fill_handler)
        await ws.connect(market_tickers=["TICKER-1", "TICKER-2"])
    """

    def __init__(
        self,
        ws_url: str | None = None,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
    ):
        self._ws_url = ws_url or WS_URL
        self._api_key_id = api_key_id or API_KEY_ID
        self._private_key = load_private_key(private_key_path)
        self._ws: ClientConnection | None = None
        self._running = False
        self._cmd_id = 0
        self._market_tickers: list[str] = []

        # Callbacks by message type
        self._handlers: dict[str, list[MessageHandler]] = {
            "orderbook_snapshot": [],
            "orderbook_delta": [],
            "fill": [],
            "error": [],
        }

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def on_orderbook_snapshot(self, handler: MessageHandler) -> None:
        self._handlers["orderbook_snapshot"].append(handler)

    def on_orderbook_delta(self, handler: MessageHandler) -> None:
        self._handlers["orderbook_delta"].append(handler)

    def on_fill(self, handler: MessageHandler) -> None:
        self._handlers["fill"].append(handler)

    def on_error(self, handler: MessageHandler) -> None:
        self._handlers["error"].append(handler)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _next_cmd_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id

    def _ws_auth_headers(self) -> dict[str, str]:
        """Auth headers for the WS handshake (GET on the WS path)."""
        # Extract path from URL: wss://host/trade-api/ws/v2 -> /trade-api/ws/v2
        from urllib.parse import urlparse

        path = urlparse(self._ws_url).path
        return auth_headers(self._private_key, "GET", path, self._api_key_id)

    async def _open_connection(self) -> ClientConnection:
        """Open an authenticated WebSocket connection."""
        headers = self._ws_auth_headers()
        ws = await websockets.connect(
            self._ws_url,
            additional_headers=headers,
            ping_interval=WS_PING_INTERVAL_S,
        )
        logger.info("WebSocket connected to %s", self._ws_url)
        return ws

    async def _subscribe(self, market_tickers: list[str]) -> None:
        """Send subscription command for orderbook_delta and fill channels."""
        if not self._ws or not market_tickers:
            return

        cmd = {
            "id": self._next_cmd_id(),
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta", "fill"],
                "market_tickers": market_tickers,
            },
        }
        await self._ws.send(json.dumps(cmd))
        logger.info(
            "Subscribed to %d market(s): %s",
            len(market_tickers),
            ", ".join(market_tickers[:5]) + ("..." if len(market_tickers) > 5 else ""),
        )

    async def subscribe(self, market_tickers: list[str]) -> None:
        """
        Subscribe to additional markets on a live connection.

        Also updates the internal ticker list so reconnects re-subscribe.
        """
        new_tickers = [t for t in market_tickers if t not in self._market_tickers]
        if not new_tickers:
            return
        self._market_tickers.extend(new_tickers)
        if self._ws:
            await self._subscribe(new_tickers)

    async def unsubscribe(self, market_tickers: list[str]) -> None:
        """Unsubscribe from markets."""
        if not self._ws or not market_tickers:
            return

        cmd = {
            "id": self._next_cmd_id(),
            "cmd": "unsubscribe",
            "params": {
                "channels": ["orderbook_delta", "fill"],
                "market_tickers": market_tickers,
            },
        }
        await self._ws.send(json.dumps(cmd))
        self._market_tickers = [t for t in self._market_tickers if t not in market_tickers]
        logger.info("Unsubscribed from %d market(s)", len(market_tickers))

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route a parsed message to the appropriate handlers."""
        msg_type = msg.get("type", "")
        handlers = self._handlers.get(msg_type, [])

        if msg_type == "error":
            logger.error("WS error message: %s", msg)

        for handler in handlers:
            try:
                await handler(msg)
            except Exception:
                logger.exception("Handler error for message type '%s'", msg_type)

    # ------------------------------------------------------------------
    # Main loop with reconnect
    # ------------------------------------------------------------------

    async def connect(self, market_tickers: list[str] | None = None) -> None:
        """
        Connect, subscribe, and listen forever with auto-reconnect.

        This is a blocking coroutine — run it as a task.

        Args:
            market_tickers: initial list of tickers to subscribe to
        """
        if market_tickers:
            self._market_tickers = list(market_tickers)

        self._running = True
        backoff = WS_RECONNECT_BASE_S

        while self._running:
            try:
                self._ws = await self._open_connection()
                backoff = WS_RECONNECT_BASE_S  # reset on successful connect

                if self._market_tickers:
                    await self._subscribe(self._market_tickers)

                await self._listen()

            except websockets.ConnectionClosedError as e:
                logger.warning("WS connection closed: %s", e)
            except websockets.InvalidStatusCode as e:
                logger.error("WS handshake rejected (status %s): %s", e.status_code, e)
            except OSError as e:
                logger.error("WS network error: %s", e)
            except Exception:
                logger.exception("Unexpected WS error")
            finally:
                self._ws = None

            if not self._running:
                break

            logger.info("Reconnecting in %.1fs...", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, WS_RECONNECT_MAX_S)

    async def _listen(self) -> None:
        """Read messages from the WebSocket until it closes."""
        assert self._ws is not None
        async for raw in self._ws:
            if not self._running:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Non-JSON WS message: %s", raw[:200])
                continue
            await self._dispatch(msg)

    async def close(self) -> None:
        """Gracefully shut down the WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("WebSocket client closed")
