"""Production combo supervisor v40: signed-auth probe on Polymarket exchange host.

Read-only. Uses the existing Retail API Key ID/Secret Key and documented Ed25519
request-signature construction against api.prod.polymarketexchange.com for RFQ
identity/account discovery. Logs only HTTP status and response shape; never logs
credentials, IDs, balances, or private field values. No writes or orders.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Any, Mapping

import httpx
from cryptography.hazmat.primitives.asymmetric import ed25519

from src import sports_kp_live_combo_supervisor_v39 as v39

logger = v39.logger
EXCHANGE = "https://api.prod.polymarketexchange.com"


def _shape(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {"type": "mapping", "keys": sorted(str(k) for k in value.keys())[:40]}
    if isinstance(value, list):
        return {"type": "list", "length": len(value),
                "item_keys": sorted(str(k) for k in value[0].keys())[:40] if value and isinstance(value[0], Mapping) else []}
    return {"type": type(value).__name__}


def _headers(method: str, path: str) -> dict[str, str]:
    key_id = os.environ["POLYMARKET_KEY_ID"]
    raw = base64.b64decode(os.environ["POLYMARKET_SECRET_KEY"])
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
    ts = str(int(time.time() * 1000))
    sig = base64.b64encode(private_key.sign(f"{ts}{method.upper()}{path}".encode())).decode()
    return {"X-PM-Access-Key": key_id, "X-PM-Timestamp": ts, "X-PM-Signature": sig, "Content-Type": "application/json"}


async def _exchange_auth_probe() -> None:
    out: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=15.0) as http:
        for label, path in (("rfq_user", "/v1/rfqs/user-id"), ("accounts", "/v1/accounts"), ("whoami", "/v1/whoami")):
            try:
                r = await http.get(EXCHANGE + path, headers=_headers("GET", path))
                body: Any = None
                try:
                    body = r.json()
                except Exception:
                    body = None
                out[label] = {"status": r.status_code, "ok": r.status_code == 200, "shape": _shape(body) if body is not None else None}
            except Exception as exc:
                out[label] = {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:120]}
    logger.warning("SPORTS_POLY_EXCHANGE_SIGNED_AUTH_PREFLIGHT %s", json.dumps({
        **out, "host": EXCHANGE, "private_values_logged": False, "writes": False, "order_entry": False,
    }, separators=(",", ":"), default=str))


async def run() -> None:
    await _exchange_auth_probe()
    await v39.run()


def main() -> None:
    import logging
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
