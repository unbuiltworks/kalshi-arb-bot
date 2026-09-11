#!/usr/bin/env python3
"""Read-only AWS preflight for the bounded Kalshi <-> Polymarket US arb executor.

This script deliberately performs no order entry. It proves that the canonical AWS
credentials can authenticate both venue transports and that the hard q=1 sports
execution defaults have not drifted before any live arming step exists.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys


def fail(message: str) -> None:
    print(json.dumps({"status": "FAIL_CLOSED", "reason": message}, sort_keys=True))
    raise SystemExit(2)


if os.getenv("ENABLE_SPORTS_KP_LIVE", "").strip().upper() == "YES":
    fail("live mode must be disabled for AWS arb preflight")

pmus_key = os.getenv("PMUS_API_KEY_ID", "").strip()
pmus_secret = os.getenv("PMUS_API_SECRET_KEY", "").strip()
if not pmus_key or not pmus_secret:
    fail("canonical PMUS credentials are unavailable")

# The official polymarket-us SDK consumes the same base64 Ed25519 credential but
# uses POLYMARKET_* names. Map aliases only inside this read-only process.
os.environ["POLYMARKET_KEY_ID"] = pmus_key
os.environ["POLYMARKET_SECRET_KEY"] = pmus_secret
os.environ["KALSHI_ENV"] = "prod"

from polymarket_us import PolymarketUS  # noqa: E402
from src.auth import KalshiClient  # noqa: E402
from src.sports_kp_execution import (  # noqa: E402
    DEFAULT_ARM_TOKEN,
    DEFAULT_MAX_BOOK_AGE_MS,
    DEFAULT_MAX_QUOTE_SKEW_MS,
    DEFAULT_MIN_PROFIT,
    KALSHI_PROD_REST,
)

if str(DEFAULT_MIN_PROFIT) != "0.03":
    fail(f"minimum profit gate drifted: {DEFAULT_MIN_PROFIT}")
if int(DEFAULT_MAX_QUOTE_SKEW_MS) != 50:
    fail(f"quote skew gate drifted: {DEFAULT_MAX_QUOTE_SKEW_MS}")
if int(DEFAULT_MAX_BOOK_AGE_MS) != 100:
    fail(f"book age gate drifted: {DEFAULT_MAX_BOOK_AGE_MS}")
if DEFAULT_ARM_TOKEN.exists():
    fail(f"one-use ARM token unexpectedly exists at {DEFAULT_ARM_TOKEN}")


def polymarket_read() -> None:
    client = PolymarketUS(key_id=pmus_key, secret_key=pmus_secret, timeout=5.0)
    try:
        # Authenticated account read only. Never call client.orders.* here.
        body = client.account.balances()
        if not isinstance(body, dict):
            fail("Polymarket US authenticated balance read returned an unexpected type")
    finally:
        client.close()


async def kalshi_read() -> None:
    client = KalshiClient(base_url=KALSHI_PROD_REST)
    try:
        # Authenticated exchange-status read only. Never call client.post/delete here.
        response = await client.get("/exchange/status")
        response.raise_for_status()
        if not isinstance(response.json(), dict):
            fail("Kalshi authenticated exchange-status read returned an unexpected type")
    finally:
        await client.close()


try:
    polymarket_read()
    asyncio.run(kalshi_read())
except SystemExit:
    raise
except Exception as exc:
    fail(f"authenticated read preflight failed: {type(exc).__name__}:{str(exc)[:160]}")

print(
    json.dumps(
        {
            "status": "DISARMED_READ_AUTH_OK",
            "order_entry": False,
            "qty": 1,
            "pmus_auth": True,
            "kalshi_auth": True,
            "min_after_fee_profit": "0.03",
            "max_quote_skew_ms": 50,
            "max_book_age_ms": 100,
            "arm_token_present": False,
        },
        sort_keys=True,
    )
)
