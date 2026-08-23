"""Read-only fee metadata probe for the current sports Dutch-book components/MVEs.

Isolated from all order-entry paths. Never logs credentials or private account data.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"
MVE_TICKER = "KXMVECROSSCATEGORY-SHARD1-S2026037922EEFBB-AA8CCC11F8D"
SERIES = ("KXNFLSPREAD", "KXMLBSPREAD", "KXMLBHIT")
COLLECTION = "KXMVECROSSCATEGORY-R"
FEE_KEYS = ("fee_type", "fee_multiplier", "maker_fee", "maker_fee_rate", "taker_fee", "taker_fee_rate", "fees")


def _safe_shape(body: Any) -> dict[str, Any]:
    row = body if isinstance(body, dict) else {}
    return {
        "keys": sorted(row.keys()),
        "fee_fields": {k: row.get(k) for k in FEE_KEYS if k in row},
    }


async def run_once() -> dict[str, Any]:
    out: dict[str, Any] = {"status": "SPORTS_MVE_FEE_METADATA_V1", "series": {}, "read_only": True, "order_entry": False}
    async with httpx.AsyncClient(timeout=20.0) as http:
        mr = await http.get(f"{BASE}/markets/{MVE_TICKER}")
        mb = mr.json() if mr.content else {}
        market = mb.get("market") if isinstance(mb, dict) and isinstance(mb.get("market"), dict) else mb
        out["mve_market"] = {"http": mr.status_code, **_safe_shape(market)}

        for series in SERIES:
            r = await http.get(f"{BASE}/series/{series}")
            body = r.json() if r.content else {}
            row = body.get("series") if isinstance(body, dict) and isinstance(body.get("series"), dict) else body
            out["series"][series] = {"http": r.status_code, **_safe_shape(row)}

        cr = await http.get(f"{BASE}/multivariate_event_collections/{COLLECTION}")
        cb = cr.json() if cr.content else {}
        collection = cb.get("multivariate_contract") if isinstance(cb, dict) and isinstance(cb.get("multivariate_contract"), dict) else cb
        out["collection"] = {"http": cr.status_code, **_safe_shape(collection)}

    print("SPORTS_MVE_FEE_METADATA " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
