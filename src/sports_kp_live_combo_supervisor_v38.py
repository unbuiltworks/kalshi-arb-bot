"""Production combo supervisor v38: non-sensitive Polymarket US SDK capability probe.

Logs only public Python attribute/method names from the installed SDK client and
selected namespaces so the combo/RFQ/account integration can reuse the exact
production SDK surface. No credential values, account identifiers, balances,
positions, orders, or private response data are logged or requested.
"""
from __future__ import annotations

import json
import os

from src import sports_kp_live_combo_supervisor_v37 as v37

logger = v37.logger


def _public_names(obj):
    return sorted(n for n in dir(obj) if not n.startswith("_"))[:120]


def _sdk_probe() -> None:
    try:
        from polymarket_us import PolymarketUS
        client = PolymarketUS(
            key_id=os.environ["POLYMARKET_KEY_ID"],
            secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        )
        top = _public_names(client)
        namespaces = {}
        for name in ("combos", "rfqs", "account", "accounts", "portfolio", "markets", "events", "sports"):
            try:
                obj = getattr(client, name)
            except Exception:
                continue
            namespaces[name] = _public_names(obj)
        logger.warning("SPORTS_POLY_SDK_CAPABILITY %s", json.dumps({
            "sdk_import_ok": True,
            "top_level_public_names": top,
            "namespaces": namespaces,
            "credentials_logged": False,
            "private_data_requested": False,
            "order_entry": False,
        }, separators=(",", ":")))
    except Exception as exc:
        logger.warning("SPORTS_POLY_SDK_CAPABILITY_ERROR %s", json.dumps({
            "error": type(exc).__name__,
            "detail": str(exc)[:200],
            "credentials_logged": False,
            "order_entry": False,
        }, separators=(",", ":")))


async def run() -> None:
    _sdk_probe()
    await v37.run()


def main() -> None:
    import asyncio
    import logging
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
