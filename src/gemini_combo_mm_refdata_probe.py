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
GRID_KEYS = {
    "quantityMinimum",
    "quantityIncrement",
    "priceMinimum",
    "priceIncrement",
    "quoteAssetPrecision",
    "marketState",
    "status",
    "contractStatus",
    "expiryDate",
    "instrumentSymbol",
    "ticker",
    "eventTicker",
    "parentEventTicker",
    "contractTicker",
    "contractId",
    "id",
}


def _safe_scalars(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, child in value.items():
        if isinstance(child, (str, int, float, bool)) or child is None:
            out[str(key)] = child
    return out


def _interesting_paths(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in GRID_KEYS:
                out[path] = child
            out.update(_interesting_paths(child, path))
    elif isinstance(value, list):
        for idx, child in enumerate(value[:10]):
            out.update(_interesting_paths(child, f"{prefix}[{idx}]"))
    return out


async def _event_grid(h: httpx.AsyncClient, event_ticker: str | None, symbol: str) -> dict[str, Any] | None:
    if not event_ticker:
        return None
    r = await h.get(f"{BASE}/v1/prediction-markets/events/{event_ticker}")
    try:
        body = r.json()
    except Exception:
        body = {}
    contract = None
    for c in body.get("contracts", []) if isinstance(body, dict) else []:
        if isinstance(c, dict) and str(c.get("instrumentSymbol") or "") == symbol:
            contract = c
            break
    return {
        "http": r.status_code,
        "eventTicker": event_ticker,
        "contract": {k: contract.get(k) for k in GRID_KEYS if isinstance(contract, dict) and k in contract},
    }


async def run_once() -> dict[str, Any]:
    rows = []
    async with httpx.AsyncClient(timeout=12.0) as h:
        for sym in SYMBOLS:
            r = await h.get(f"{BASE}/v1/prediction-markets/combos/{sym}")
            try:
                body = r.json()
            except Exception:
                body = {}
            contract = body.get("contract", {}) if isinstance(body, dict) else {}
            leg_rows = []
            for leg in body.get("legs", []) if isinstance(body, dict) else []:
                lc = leg.get("contract", {}) if isinstance(leg, dict) else {}
                lticker = lc.get("eventTicker") or lc.get("parentEventTicker")
                leg_rows.append(
                    {
                        "contract_scalars": _safe_scalars(lc),
                        "event_grid": await _event_grid(h, str(lticker) if lticker else None, str(lc.get("instrumentSymbol") or "")),
                    }
                )
            event_ticker = contract.get("eventTicker") or contract.get("parentEventTicker")
            rows.append(
                {
                    "symbol": sym,
                    "http": r.status_code,
                    "top_level_keys": sorted(body.keys()) if isinstance(body, dict) else [],
                    "contract_keys": sorted(contract.keys()) if isinstance(contract, dict) else [],
                    "contract_scalars": _safe_scalars(contract),
                    "interesting_paths": _interesting_paths(body),
                    "combo_event_grid": await _event_grid(h, str(event_ticker) if event_ticker else None, sym),
                    "legs": leg_rows,
                }
            )
    out = {
        "status": "GEMINI_COMBO_MM_REFDATA_PROBE_V3",
        "rows": rows,
        "read_only": True,
        "order_entry": False,
    }
    print("GEMINI_COMBO_MM_REFDATA_PROBE_V3 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
