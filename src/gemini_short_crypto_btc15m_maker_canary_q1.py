"""BTC15m transport/refdata adapter for the Gemini q=1 maker canary stack.

Reuses the already-live-tested Gemini private order/fill/cancel transport and fee
model from BTC5m, while discovering only BTC 15-minute prediction contracts.
"""
from __future__ import annotations

import time
import re
from typing import Any, Mapping

import httpx

from src import gemini_short_crypto_btc5m_maker_canary_q1 as b5

D = b5.D
ONE = b5.ONE
REST = b5.REST
Event = b5.Event
pair_model = b5.pair_model
GeminiMakerSession = b5.GeminiMakerSession
GeminiTradingSession = b5.GeminiTradingSession
_gemini_terms_ok = b5._gemini_terms_ok
_gemini_available_usd = b5._gemini_available_usd
_clean_id = b5._clean_id
_find_row = b5._find_row
_order_by_client = b5._order_by_client
_cancel_maker = b5._cancel_maker
_reconcile_pair = b5._reconcile_pair
fresh_top = b5.fresh_top
EVENT_RE = re.compile(r"^BTC15M(\d{10})$")
MIN_SECONDS_TO_EXPIRY = 300.0


def _iso_ms(value: Any) -> int | None:
    return b5._iso_ms(value)


async def discover_btc15m() -> Event | None:
    found: list[Event] = []
    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-btc15m-canary/1.0"},
    ) as http:
        offset = 0
        while offset < 5000:
            response = await http.get(
                f"{REST}/v1/prediction-markets/events",
                params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                if not EVENT_RE.match(ticker):
                    continue
                for contract in row.get("contracts") or []:
                    if not isinstance(contract, Mapping):
                        continue
                    symbol = str(contract.get("instrumentSymbol") or "").strip()
                    state = str(contract.get("marketState") or contract.get("status") or "").lower()
                    if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                        continue
                    expiry = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                    if expiry is None or expiry <= int(time.time() * 1000):
                        continue
                    found.append(Event(
                        ticker=ticker,
                        symbol=symbol,
                        expiry_ms=expiry,
                        quantity_minimum=D(str(contract.get("quantityMinimum") or "1")),
                        quantity_increment=D(str(contract.get("quantityIncrement") or "1")),
                        price_increment=D(str(contract.get("priceIncrement") or "0.01")),
                    ))
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total = int((pagination or {}).get("total") or len(rows))
            offset += len(rows)
            if not rows or offset >= total:
                break
    eligible = [e for e in found if e.seconds_to_expiry >= MIN_SECONDS_TO_EXPIRY]
    return min(eligible, key=lambda e: e.expiry_ms) if eligible else None
