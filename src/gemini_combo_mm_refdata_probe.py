"""Read-only quantity/price grid audit for accepted combo instruments."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

BASE = "https://api.gemini.com"
SYMBOLS = [
    "GEMI-CMB-260823-605EB3AACEDE12F2-CMB",
    "GEMI-CMB-260823-0C93A84ACACD7C1D-CMB",
    "GEMI-CMB-260823-130D47B64F4671DC-CMB",
    "GEMI-CMB-260823-790AAEFA6CECBA1E-CMB",
]
INTERESTING = {
    "quantityMinimum",
    "quantityIncrement",
    "priceMinimum",
    "priceIncrement",
    "marketState",
    "status",
    "contractStatus",
    "expiryDate",
    "instrumentSymbol",
    "symbol",
}


def _interesting_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in INTERESTING:
                out[path] = child
            out.update(_interesting_paths(child, path))
    elif isinstance(value, list):
        for idx, child in enumerate(value[:10]):
            path = f"{prefix}[{idx}]"
            out.update(_interesting_paths(child, path))
    return out


async def run_once() -> dict[str, Any]:
    rows = []
    async with httpx.AsyncClient(timeout=12.0) as h:
        for sym in SYMBOLS:
            r = await h.get(f"{BASE}/v1/prediction-markets/combos/{sym}")
            try:
                body = r.json()
            except Exception:
                body = {}
            rows.append(
                {
                    "symbol": sym,
                    "http": r.status_code,
                    "top_level_keys": sorted(body.keys()) if isinstance(body, dict) else [],
                    "interesting_paths": _interesting_paths(body),
                    "legCount": len(body.get("legs", [])) if isinstance(body, dict) and isinstance(body.get("legs"), list) else None,
                }
            )
    out = {
        "status": "GEMINI_COMBO_MM_REFDATA_PROBE_V2",
        "rows": rows,
        "read_only": True,
        "order_entry": False,
    }
    print("GEMINI_COMBO_MM_REFDATA_PROBE_V2 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
