"""Keep Gemini crypto market data on one public WebSocket.

Prediction depth RPCs and spot bookTicker streams used by the live scanner are public
market data. The maker shadow has already validated the same public depth transport.
Using an authenticated handshake for this feed needlessly competes with the private
trading session's handshake budget.

This patch changes connection authentication only. The exact depth RPC, spot streams,
request-start timestamps, q=1 sizing, fee/profit, freshness/skew, double-boundary,
survival and staged execution rules are unchanged.
"""
from __future__ import annotations

import websockets

from src import gemini_kalshi_kalshi_rest_book_fallback as market_data

_INSTALLED = False


async def _public_market_data_connect():
    return (
        await websockets.connect(market_data.hot.GEMINI_WS, open_timeout=5.0),
        "PUBLIC_SHARED_WS_DEPTH_RPC_PLUS_SPOT_AUTH_RESERVED_FOR_EXECUTION",
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    market_data._gemini_connect_market_data = _public_market_data_connect
    _INSTALLED = True
    market_data.logger.warning(
        "CRYPTO_GEMINI_PUBLIC_MARKET_DATA_INSTALLED market_data_auth=false "
        "one_public_shared_ws=true private_auth_reserved_for_execution=true "
        "rest_fok_backup=true risk_gates_changed=false"
    )
