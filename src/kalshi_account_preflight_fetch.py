"""READ-ONLY fetcher for the Kalshi account preflight. GET only.

Runs where credentials exist (a GitHub Actions job with repository secrets,
or any host with KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM set) and prints
a single JSON line containing NO key material.

There is no order path here and there must never be one: this module issues
GET requests exclusively, and a test asserts that no POST/DELETE/order verb
appears anywhere in its source.
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List

from src.kalshi_account_preflight_readonly import preflight

PROD = "https://api.elections.kalshi.com/trade-api/v2"

# Both hosts were checked from an unauthenticated session and expose an
# identical endpoint surface, so either works; this is the one the public
# collector already uses.
ALT_HOST = "https://external-api.kalshi.com/trade-api/v2"

READ_PATHS = ("/portfolio/balance", "/portfolio/fills", "/portfolio/settlements")


async def _collect(base: str) -> Dict[str, Any]:
    from src.auth import KalshiClient

    client = KalshiClient(base_url=base)
    out: Dict[str, Any] = {"base_url": base, "http": {}, "balance": None,
                           "fills": [], "settlements": []}
    try:
        for path in READ_PATHS:
            try:
                r = await client.get(path)
                out["http"][path] = r.status_code
                if r.status_code != 200:
                    continue
                body = r.json()
                if path.endswith("balance"):
                    out["balance"] = body
                elif path.endswith("fills"):
                    out["fills"] = body.get("fills") or []
                elif path.endswith("settlements"):
                    out["settlements"] = body.get("settlements") or []
            except Exception as exc:                     # noqa: BLE001
                out["http"][path] = f"{type(exc).__name__}"
    finally:
        try:
            await client.close()
        except Exception:                                # noqa: BLE001
            pass
    return out


async def main_async() -> None:
    expected = os.getenv("KALSHI_EXPECTED_CASH_USD", "").strip()
    try:
        raw = await _collect(PROD)
        report = preflight(
            balance_payload=raw["balance"],
            fills=raw["fills"],
            settlements=raw["settlements"],
            expected_cash_usd=(D(expected) if expected else None),
        )
        report["http_status_by_path"] = raw["http"]
        report["fills_seen"] = len(raw["fills"])
        report["settlements_seen"] = len(raw["settlements"])
    except Exception as exc:                             # noqa: BLE001
        # Never let an exception carry key material into the log.
        report = {"kind": "KALSHI_ACCOUNT_PREFLIGHT",
                  "error": type(exc).__name__,
                  "note": ("exception type only; the message is suppressed "
                           "because a signing failure can echo key bytes"),
                  "read_only": True, "order_entry": False}
    print("KALSHI_ACCOUNT_PREFLIGHT " + json.dumps(report, sort_keys=True))


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
