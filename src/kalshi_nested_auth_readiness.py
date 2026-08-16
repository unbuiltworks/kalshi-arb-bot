"""Authenticated READ-ONLY readiness check for the nested live-probe service.

Performs exactly one signed GET /portfolio/balance.  It never imports the live
executor and never creates/cancels orders.  Output intentionally omits balance
amounts/secrets; it only proves the keypair/auth path works and reports whether the
separate live-enable/token gates are absent or present.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from src.auth import KalshiClient
from src.kalshi_nested_probe_transport import KALSHI_PROD_REST

logger = logging.getLogger("kalshi_arb.kalshi_nested_auth_readiness")


async def check() -> dict:
    client = KalshiClient(base_url=KALSHI_PROD_REST)
    try:
        response = await client.get("/portfolio/balance")
        body = {}
        try:
            body = response.json()
        except Exception:
            pass
        token_path = os.getenv("KALSHI_NESTED_LIVE_ARM_TOKEN_FILE", "").strip()
        report = {
            "auth_http_status": response.status_code,
            "auth_ok": response.status_code == 200,
            "balance_shape_ok": response.status_code == 200 and "balance" in body and "portfolio_value" in body,
            "live_enable_flag": os.getenv("ENABLE_KALSHI_NESTED_LIVE_PROBES", "NO") == "YES",
            "arm_token_path_configured": bool(token_path),
            "arm_token_exists": bool(token_path and Path(token_path).is_file()),
            "request_type": "SIGNED_GET_ONLY",
            "orders_created": 0,
            "orders_canceled": 0,
        }
        logger.info("NESTED_AUTH_READINESS %s", json.dumps(report, separators=(",", ":")))
        if not report["auth_ok"]:
            raise RuntimeError(f"authenticated balance read failed HTTP {response.status_code}")
        return report
    finally:
        await client.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(check())


if __name__ == "__main__":
    main()
