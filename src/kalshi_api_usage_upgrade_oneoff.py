"""One-off official Kalshi Predictions API usage-level upgrade.

No order-entry code exists here. The documented POST grants Advanced API usage when
eligibility is met. We immediately read account limits back and exit.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from src import gemini_kalshi_rollover_optimizer as rollover

hot = rollover.hot
logger = logging.getLogger("kalshi_arb.kalshi_api_usage_upgrade_oneoff")
BASE = "https://external-api.kalshi.com/trade-api/v2"


async def main_async() -> None:
    private_key = hot.load_private_key()
    upgrade_path = "/trade-api/v2/account/api_usage_level/upgrade"
    upgrade_headers = hot.auth_headers(private_key, "POST", upgrade_path, hot.API_KEY_ID)
    async with httpx.AsyncClient(timeout=8.0) as http:
        response = await http.post(f"{BASE}/account/api_usage_level/upgrade", headers=upgrade_headers)
        logger.warning(
            "KALSHI_API_USAGE_UPGRADE status=%s success=%s order_entry=false",
            response.status_code,
            str(response.status_code in {200, 201}).lower(),
        )
        if response.status_code not in {200, 201}:
            raise RuntimeError(
                f"Kalshi API usage upgrade failed status={response.status_code} body={response.text[:240]}"
            )

        limits_path = "/trade-api/v2/account/limits"
        limits_headers = hot.auth_headers(private_key, "GET", limits_path, hot.API_KEY_ID)
        limits = await http.get(f"{BASE}/account/limits", headers=limits_headers)
        limits.raise_for_status()
        body = limits.json()
        read = body.get("read") or {}
        write = body.get("write") or {}
        logger.warning(
            "KALSHI_API_USAGE_UPGRADE_VERIFIED usage_tier=%s read_refill=%s read_capacity=%s write_refill=%s write_capacity=%s grants=%s order_entry=false",
            body.get("usage_tier"),
            read.get("refill_rate"),
            read.get("bucket_capacity"),
            write.get("refill_rate"),
            write.get("bucket_capacity"),
            len(body.get("grants") or []),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
