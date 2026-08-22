"""Read-only quantity/price-grid and live-liquidity audit for Gemini combos.

The hard-coded rows preserve the canonical-leg audit for our four accepted combos.
The catalog scan also checks currently active registered combo instruments for real
book liquidity, then resolves quantity/price grids only for liquid symbols. This is
used to determine whether any q=1 resting maker can be indivisible (increment=1) or
whether the safe execution path must remain FOK-only.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, Mapping

import httpx

BASE = "https://api.gemini.com"
SYMBOLS = [
    "GEMI-CMB-260823-605EB3AACEDE12F2-CMB",
    "GEMI-CMB-260823-0C93A84ACACD7C1D-CMB",
    "GEMI-CMB-260823-130D47B64F4671DC-CMB",
    "GEMI-CMB-260823-790AAEFA6CECBA1E-CMB",
]
GRID_KEYS = {
    "quantityMinimum", "quantityIncrement", "priceMinimum", "priceIncrement",
    "quoteAssetPrecision", "marketState", "status", "contractStatus", "expiryDate",
    "instrumentSymbol", "ticker", "eventTicker", "parentEventTicker", "contractTicker",
    "contractId", "id", "requiredOutcome", "outcome",
}
D = Decimal


def _safe_scalars(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, (str, int, float, bool)) or v is None}


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


def _d(value: Any) -> D | None:
    try:
        x = D(str(value))
    except Exception:
        return None
    return x if x.is_finite() else None


def _level(row: Any) -> tuple[D, D] | None:
    if not isinstance(row, Mapping):
        return None
    p = _d(row.get("price"))
    q = _d(row.get("amount") if row.get("amount") is not None else row.get("quantity"))
    if p is None or q is None or q <= 0 or not D("0") < p < D("1"):
        return None
    return p, q


def _book_summary(body: Any) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        return {"bid_levels": 0, "ask_levels": 0, "has_liquidity": False}
    bids = [x for x in (_level(r) for r in (body.get("bids") or [])) if x]
    asks = [x for x in (_level(r) for r in (body.get("asks") or [])) if x]
    best_bid = max(bids, key=lambda x: x[0]) if bids else None
    best_ask = min(asks, key=lambda x: x[0]) if asks else None
    return {
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "best_yes_bid": str(best_bid[0]) if best_bid else None,
        "best_yes_bid_depth": str(best_bid[1]) if best_bid else None,
        "best_yes_ask": str(best_ask[0]) if best_ask else None,
        "best_yes_ask_depth": str(best_ask[1]) if best_ask else None,
        "best_no_ask": str(D("1") - best_bid[0]) if best_bid else None,
        "best_no_ask_depth": str(best_bid[1]) if best_bid else None,
        "has_liquidity": bool(bids or asks),
        "two_sided": bool(bids and asks),
    }


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


async def _catalog_liquidity(h: httpx.AsyncClient) -> dict[str, Any]:
    r = await h.get(f"{BASE}/v1/prediction-markets/combos", params={"status": "Active", "limit": 500, "offset": 0})
    try:
        body = r.json()
    except Exception:
        body = {}
    combos = body.get("combos", []) if isinstance(body, Mapping) else []
    registered: list[dict[str, str]] = []
    for row in combos:
        if not isinstance(row, Mapping):
            continue
        contract = row.get("contract") if isinstance(row.get("contract"), Mapping) else {}
        symbol = str(contract.get("instrumentSymbol") or "")
        event_ticker = str(contract.get("eventTicker") or contract.get("parentEventTicker") or "")
        if symbol:
            registered.append({"symbol": symbol, "eventTicker": event_ticker, "name": str(contract.get("contractName") or "")})

    sem = asyncio.Semaphore(20)
    books: dict[str, tuple[int, dict[str, Any]]] = {}

    async def fetch_book(item: dict[str, str]) -> None:
        async with sem:
            try:
                br = await h.get(f"{BASE}/v1/book/{item['symbol']}", params={"limit_bids": 20, "limit_asks": 20})
                try:
                    bb = br.json()
                except Exception:
                    bb = {}
                books[item["symbol"]] = (br.status_code, bb if isinstance(bb, Mapping) else {})
            except Exception:
                books[item["symbol"]] = (0, {})

    await asyncio.gather(*(fetch_book(x) for x in registered))
    liquid = []
    for item in registered:
        status, book = books.get(item["symbol"], (0, {}))
        summary = _book_summary(book)
        if summary["has_liquidity"]:
            liquid.append({**item, "book_http": status, **summary})

    # Grid lookups are only needed for instruments with actual liquidity.
    sem_grid = asyncio.Semaphore(12)

    async def attach_grid(item: dict[str, Any]) -> None:
        async with sem_grid:
            item["event_grid"] = await _event_grid(h, item.get("eventTicker"), item["symbol"])

    await asyncio.gather(*(attach_grid(x) for x in liquid))
    whole_increment = []
    fractional_increment = []
    unknown_increment = []
    for item in liquid:
        grid = item.get("event_grid") or {}
        contract = grid.get("contract") if isinstance(grid, Mapping) else {}
        inc = str(contract.get("quantityIncrement") or "") if isinstance(contract, Mapping) else ""
        if inc == "1" or inc == "1.0" or inc == "1.00":
            whole_increment.append(item)
        elif inc:
            fractional_increment.append(item)
        else:
            unknown_increment.append(item)

    # Prefer two-sided and tighter books in the compact output.
    liquid.sort(key=lambda x: (not x["two_sided"], -(x["bid_levels"] + x["ask_levels"]), x["symbol"]))
    pagination = body.get("pagination") if isinstance(body, Mapping) else None
    return {
        "http": r.status_code,
        "catalog_rows": len(combos),
        "registered_symbols": len(registered),
        "pagination": {k: pagination.get(k) for k in ("limit", "offset", "total") if isinstance(pagination, Mapping) and k in pagination},
        "liquid_combo_count": len(liquid),
        "two_sided_combo_count": sum(bool(x["two_sided"]) for x in liquid),
        "whole_increment_liquid_count": len(whole_increment),
        "fractional_increment_liquid_count": len(fractional_increment),
        "unknown_increment_liquid_count": len(unknown_increment),
        "whole_increment_liquid": whole_increment[:25],
        "top_liquid": liquid[:25],
    }


async def run_once() -> dict[str, Any]:
    rows = []
    async with httpx.AsyncClient(timeout=15.0) as h:
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
                leg_rows.append({
                    "leg_scalars": _safe_scalars(leg),
                    "contract_scalars": _safe_scalars(lc),
                    "event_grid": await _event_grid(h, str(lticker) if lticker else None, str(lc.get("instrumentSymbol") or "")),
                })
            event_ticker = contract.get("eventTicker") or contract.get("parentEventTicker")
            rows.append({
                "symbol": sym,
                "http": r.status_code,
                "contract_scalars": _safe_scalars(contract),
                "interesting_paths": _interesting_paths(body),
                "combo_event_grid": await _event_grid(h, str(event_ticker) if event_ticker else None, sym),
                "legs": leg_rows,
            })
        catalog = await _catalog_liquidity(h)
    out = {
        "status": "GEMINI_COMBO_MM_REFDATA_PROBE_V5",
        "rows": rows,
        "catalog_liquidity": catalog,
        "read_only": True,
        "order_entry": False,
    }
    print("GEMINI_COMBO_MM_REFDATA_PROBE_V5 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
