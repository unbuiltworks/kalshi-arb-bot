"""Basis-risk-only Kalshi market-data pacing.

This module changes no trading/risk threshold. It exists because the first armed basis
canary proved the quote hub and the strike lifecycle cache were opening two authenticated
Kalshi WebSockets at startup, while Kalshi returned HTTP 429 to both. The quote hub then
retried every <=5 seconds, preventing any Kalshi book event from reaching the existing
freshness/depth/dominance gates.

For the dedicated settlement-basis process only:
* disable the optional market_lifecycle_v2 socket; exact strikes continue through the
  existing bounded authoritative GET /markets/{ticker} fallback;
* use the same one-socket/one-SID multiplexed orderbook hub, but after an HTTP 429 wait
  at least 30 seconds before the next authenticated handshake.

All downstream quote age, cross-venue skew, persistence, depth, after-fee economics,
dominance, FOK transport, durable reservation and settlement controls are unchanged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_target_lifecycle_cache as targets

logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_marketdata")
DEFAULT_429_COOLDOWN_S = 30.0
_INSTALLED = False


def _cooldown_s() -> float:
    raw = os.getenv("CRYPTO_BASIS_KALSHI_WS_429_COOLDOWN_S", str(DEFAULT_429_COOLDOWN_S))
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_429_COOLDOWN_S
    return max(DEFAULT_429_COOLDOWN_S, min(value, 120.0))


async def _rest_only_lifecycle_placeholder() -> None:
    """Keep target-loop task semantics without opening a second Kalshi WebSocket."""
    logger.warning(
        "CRYPTO_BASIS_RISK_TARGET_TRANSPORT lifecycle_ws=false rest_fallback=true "
        "risk_gates_changed=false"
    )
    await asyncio.Event().wait()


class BasisSharedKalshiOrderbookHub(rollover.SharedKalshiOrderbookHub):
    """Identical shared book hub with conservative HTTP-429 handshake pacing."""

    async def _run(self) -> None:
        private_key = rollover.hot.load_private_key()
        backoff = 0.25
        while True:
            try:
                headers = rollover.hot.auth_headers(
                    private_key,
                    "GET",
                    rollover.hot.KALSHI_WS_SIGN_PATH,
                    rollover.hot.API_KEY_ID,
                )
                async with rollover.websockets.connect(
                    rollover.hot.KALSHI_WS,
                    open_timeout=5.0,
                    **rollover.hot._ws_header_kw(headers),
                ) as ws:
                    self._ws = ws
                    self._sid = None
                    self._last_seq = None
                    self._pending_ops.clear()
                    self._books.clear()
                    self._snapshot_ready.clear()
                    self._subscribed_markets.clear()
                    self._initial_markets = set(self._targets)
                    if not self._initial_markets:
                        raise RuntimeError("shared Kalshi hub connected with no markets")
                    self.connection_count += 1
                    self._connected.set()
                    cid = self._command_id()
                    await self._send(
                        rollover._subscribe_payload(cid, self._initial_markets)
                    )
                    rollover.logger.info(
                        "CRYPTO_ROLLOVER_SHARED_KALSHI_CONNECTED %s",
                        json.dumps(
                            {
                                "connection_count": self.connection_count,
                                "initial_markets": sorted(self._initial_markets),
                                "single_subscription_sid": True,
                                "shared_websocket": True,
                                "basis_429_pacing": True,
                                "order_entry": False,
                            },
                            separators=(",", ":"),
                        ),
                    )
                    backoff = 0.25
                    async for raw in ws:
                        await self._handle(json.loads(raw))
                    raise RuntimeError("shared Kalshi WebSocket ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected.clear()
                self._ws = None
                self._sid = None
                self._last_seq = None
                self._pending_ops.clear()
                self._books.clear()
                self._snapshot_ready.clear()
                self._subscribed_markets.clear()
                is_429 = "HTTP 429" in str(exc).upper()
                sleep_s = max(backoff, _cooldown_s()) if is_429 else backoff
                rollover.logger.warning(
                    "CRYPTO_ROLLOVER_SHARED_KALSHI_RECONNECT type=%s error=%s "
                    "registered=%s gaps=%s backoff_s=%.2f basis_429_pacing=true",
                    type(exc).__name__,
                    str(exc)[:260],
                    len(self._targets),
                    self.sequence_gaps,
                    sleep_s,
                )
                await asyncio.sleep(sleep_s)
                backoff = min(backoff * 2.0, 5.0)


def install() -> None:
    """Patch market-data objects only inside the dedicated basis child process."""
    global _INSTALLED
    if _INSTALLED:
        return
    targets._lifecycle_stream = _rest_only_lifecycle_placeholder
    rollover._HUB = BasisSharedKalshiOrderbookHub()
    _INSTALLED = True
    logger.warning(
        "CRYPTO_BASIS_RISK_MARKETDATA_GUARD_INSTALLED lifecycle_ws=false "
        "shared_orderbook_ws=true kalshi_429_cooldown_s=%.2f risk_gates_changed=false",
        _cooldown_s(),
    )
