"""Read-only q=1 Gemini MLB combo scalar Dutch-book probe.

Prices four already-created ATL/MIL x WSH/MIA combo contracts plus their exact
single-game components from Gemini's public order books. Computes both scalar-
safe directions with conservative q=1 taker fees. No orders or RFQs.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING
import json
from typing import Any, Mapping

import httpx

D = Decimal
ONE = D("1")
BASE = "https://api.gemini.com"

SINGLES = {
    "ATL": "GEMI-MLB-2608221810-ATL-MIL-M-ATL",
    "MIL": "GEMI-MLB-2608221810-ATL-MIL-M-MIL",
    "WSH": "GEMI-MLB-2608222010-WSH-MIA-M-WSH",
    "MIA": "GEMI-MLB-2608222010-WSH-MIA-M-MIA",
}
COMBOS = {
    ("ATL", "WSH"): "GEMI-CMB-260823-79E3B2DFF8FD088F-CMB",
    ("ATL", "MIA"): "GEMI-CMB-260823-E7A3F3D7D3F85505-CMB",
    ("MIL", "WSH"): "GEMI-CMB-260823-7B688FFB75657D84-CMB",
    ("MIL", "MIA"): "GEMI-CMB-260823-C10BB79B90AC3A31-CMB",
}
OPPOSITE = {"ATL":"MIL", "MIL":"ATL", "WSH":"MIA", "MIA":"WSH"}


def _d(v: Any) -> Decimal | None:
    try:
        x = D(str(v))
    except Exception:
        return None
    return x if x.is_finite() else None


def _fee(p: Decimal) -> Decimal:
    raw = D("0.07") * p * (ONE-p)
    return (raw*D("100")).to_integral_value(rounding=ROUND_CEILING)/D("100")


def _level(level: Any) -> tuple[Decimal, Decimal] | None:
    if not isinstance(level, Mapping):
        return None
    p = _d(level.get("price"))
    q = _d(level.get("amount") if level.get("amount") is not None else level.get("quantity"))
    if p is None or q is None or q <= 0 or not (D("0") < p < ONE):
        return None
    return p, q


def _top(body: Any) -> dict[str, str] | None:
    if not isinstance(body, Mapping):
        return None
    asks = [_level(x) for x in (body.get("asks") or [])]
    bids = [_level(x) for x in (body.get("bids") or [])]
    asks = [x for x in asks if x]
    bids = [x for x in bids if x]
    if not asks or not bids:
        return None
    ask_p, ask_q = min(asks, key=lambda x:x[0])
    bid_p, bid_q = max(bids, key=lambda x:x[0])
    if bid_p >= ask_p:
        return None
    no_ask = ONE-bid_p
    return {
        "yes_ask": str(ask_p), "yes_depth": str(ask_q),
        "no_ask": str(no_ask), "no_depth": str(bid_q),
        "yes_fee": str(_fee(ask_p)), "no_fee": str(_fee(no_ask)),
        "yes_all_in": str(ask_p+_fee(ask_p)),
        "no_all_in": str(no_ask+_fee(no_ask)),
        "best_bid": str(bid_p),
    }


async def run_once() -> dict[str, Any]:
    symbols = list(SINGLES.values()) + list(COMBOS.values())
    books: dict[str, dict[str, str] | None] = {}
    statuses: dict[str, int] = {}
    raw_counts: dict[str, dict[str, int]] = {}
    async with httpx.AsyncClient(timeout=15.0) as h:
        async def one(s: str):
            r = await h.get(f"{BASE}/v1/book/{s}", params={"limit_bids":20,"limit_asks":20})
            statuses[s] = r.status_code
            try: body = r.json()
            except Exception: body = {}
            raw_counts[s] = {
                "bids": len(body.get("bids") or []) if isinstance(body, Mapping) else 0,
                "asks": len(body.get("asks") or []) if isinstance(body, Mapping) else 0,
            }
            books[s] = _top(body) if r.status_code == 200 else None
        await asyncio.gather(*(one(s) for s in symbols))

    rows=[]
    for (x,y), csym in COMBOS.items():
        cb = books.get(csym)
        sx, sy = books.get(SINGLES[x]), books.get(SINGLES[y])
        ox, oy = books.get(SINGLES[OPPOSITE[x]]), books.get(SINGLES[OPPOSITE[y]])
        if cb and ox and oy:
            costs=[D(cb["yes_all_in"]), D(ox["yes_all_in"]), D(oy["yes_all_in"])]
            depths=[D(cb["yes_depth"]), D(ox["yes_depth"]), D(oy["yes_depth"])]
            total=sum(costs,D("0")); locked=ONE-total
            rows.append({"corner":f"{x}+{y}","direction":"COMBO_YES_PLUS_OPPOSITES","all_in_cost":str(total),"locked_profit":str(locked),"depth_ok_q1":all(z>=ONE for z in depths),"legs":[{"symbol":csym,"outcome":"YES","cost":str(costs[0]),"depth":str(depths[0])},{"symbol":SINGLES[OPPOSITE[x]],"outcome":"YES","cost":str(costs[1]),"depth":str(depths[1])},{"symbol":SINGLES[OPPOSITE[y]],"outcome":"YES","cost":str(costs[2]),"depth":str(depths[2])}]})
        if cb and sx and sy:
            costs=[D(cb["no_all_in"]), D(sx["yes_all_in"]), D(sy["yes_all_in"])]
            depths=[D(cb["no_depth"]), D(sx["yes_depth"]), D(sy["yes_depth"])]
            total=sum(costs,D("0")); locked=ONE-total
            rows.append({"corner":f"{x}+{y}","direction":"COMBO_NO_PLUS_SELECTED","all_in_cost":str(total),"locked_profit":str(locked),"depth_ok_q1":all(z>=ONE for z in depths),"legs":[{"symbol":csym,"outcome":"NO","cost":str(costs[0]),"depth":str(depths[0])},{"symbol":SINGLES[x],"outcome":"YES","cost":str(costs[1]),"depth":str(depths[1])},{"symbol":SINGLES[y],"outcome":"YES","cost":str(costs[2]),"depth":str(depths[2])}]})
    rows.sort(key=lambda r:D(r["locked_profit"]), reverse=True)
    out={
        "status":"GEMINI_MLB_COMBO_SCALAR_Q1_PROBE_V2",
        "http_statuses":statuses,
        "raw_level_counts":raw_counts,
        "liquid_symbols":[s for s in symbols if books.get(s) is not None],
        "empty_or_one_sided_symbols":[s for s in symbols if books.get(s) is None],
        "liquid_singles":sum(books.get(s) is not None for s in SINGLES.values()),
        "liquid_combos":sum(books.get(s) is not None for s in COMBOS.values()),
        "books_available":sum(v is not None for v in books.values()),
        "symbols_requested":len(symbols),
        "candidates":rows,
        "positive_ge_3c_q1":sum(D(r["locked_profit"])>=D("0.03") and r["depth_ok_q1"] for r in rows),
        "best":rows[0] if rows else None,
        "pricing":"PUBLIC_REST_BOOK_Q1_TAKER_FEE_INCLUDED",
        "rule_certified":False,
        "execution_eligible":False,
        "read_only":True,
        "order_entry":False,
    }
    print("GEMINI_MLB_COMBO_SCALAR_Q1_PROBE_V2 "+json.dumps(out,separators=(",",":"),default=str),flush=True)
    return out


def main(): asyncio.run(run_once())
if __name__=="__main__": main()
