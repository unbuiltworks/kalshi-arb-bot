"""Read-only minimal guaranteed-cover scan for active Gemini conjunction combos.

For a combo C that settles YES iff every required leg outcome A_i occurs:

  C(YES) + SUM_i(1 - A_i) >= 1

and, importantly, for *each individual required leg*:

  C(NO) + A_i >= 1

The second identity needs only one component hedge, not every selected leg.  This
scanner evaluates all such two-leg NO covers at q=1 with conservative cent-rounded
Gemini taker fees and exact top-of-book depth.  It never authenticates or places an
order. The production test service may run this module directly as a one-shot
read-only diagnostic.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import Any, Mapping

import httpx

import src.gemini_extreme_combo_live_canary as c
from src.gemini_combo_mm_fok_preflight import _catalog_specs, _opposite

D = Decimal
BASE = "https://api.gemini.com"
QTY = D("1")
TARGET = D("0.03")
TAKER = D("0.07")


async def _book(http: httpx.AsyncClient, symbol: str) -> tuple[int, dict[str, Any]]:
    try:
        r = await http.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 50, "limit_asks": 50})
        try:
            body = r.json()
        except Exception:
            body = {}
        return r.status_code, body if isinstance(body, Mapping) else {}
    except Exception:
        return 0, {}


def _priced(book: Mapping[str, Any], outcome: str) -> tuple[D, D, D] | None:
    top = c._outcome_ask(book, outcome)
    if top is None:
        return None
    price, depth = top
    fee = c._fee(TAKER, price, QTY)
    return price, depth, fee


def _cover_row(
    *,
    combo: str,
    combo_outcome: str,
    component_specs: list[tuple[str, str]],
    books: Mapping[str, tuple[int, Mapping[str, Any]]],
    identity: str,
) -> dict[str, Any]:
    combo_status, combo_book = books.get(combo, (0, {}))
    combo_top = _priced(combo_book, combo_outcome) if combo_status == 200 else None
    out: dict[str, Any] = {
        "identity": identity,
        "combo": combo,
        "combo_outcome": combo_outcome,
        "component_count": len(component_specs),
        "pass": False,
        "locked": None,
    }
    if combo_top is None or combo_top[1] < QTY:
        out["reason"] = "NO_Q1_COMBO_ASK"
        return out
    cp, cd, cf = combo_top
    total = cp + cf
    component_rows = []
    for symbol, outcome in component_specs:
        status, book = books.get(symbol, (0, {}))
        top = _priced(book, outcome) if status == 200 else None
        row: dict[str, Any] = {"symbol": symbol, "outcome": outcome, "http": status}
        if top is None or top[1] < QTY:
            row["available"] = False
            component_rows.append(row)
            out.update({
                "combo_ask": str(cp), "combo_depth": str(cd), "combo_fee": str(cf),
                "components": component_rows, "reason": "NO_Q1_COMPONENT_ASK",
            })
            return out
        p, depth, fee = top
        row.update({"available": True, "ask": str(p), "depth": str(depth), "fee": str(fee), "all_in": str(p + fee)})
        component_rows.append(row)
        total += p + fee
    locked = D("1") - total
    out.update({
        "combo_ask": str(cp), "combo_depth": str(cd), "combo_fee": str(cf),
        "components": component_rows,
        "all_in": str(total),
        "locked": str(locked),
        "pass": locked >= TARGET,
        "reason": "EXECUTABLE_Q1_FOK_ECONOMICS" if locked >= TARGET else "BELOW_3C_FLOOR",
    })
    return out


async def run_once() -> dict[str, Any]:
    candidate_rows: list[dict[str, Any]] = []
    executable: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=12.0) as http:
        catalog, specs = await _catalog_specs(http)
        valid = [x for x in specs if not x.get("blocker") and x.get("active_required_legs")]
        for spec in valid:
            combo = str(spec["combo"])
            required = [(str(s), str(o).upper()) for s, o in spec["active_required_legs"]]
            symbols = [combo, *(s for s, _ in required)]
            fetched = await asyncio.gather(*(_book(http, s) for s in symbols))
            books = {s: result for s, result in zip(symbols, fetched)}

            covers: list[dict[str, Any]] = []
            yes_cover = _cover_row(
                combo=combo,
                combo_outcome="YES",
                component_specs=[(s, _opposite(o)) for s, o in required],
                books=books,
                identity="COMBO_YES_PLUS_ALL_COMPLEMENTS",
            )
            covers.append(yes_cover)
            for idx, (symbol, outcome) in enumerate(required):
                covers.append(_cover_row(
                    combo=combo,
                    combo_outcome="NO",
                    component_specs=[(symbol, outcome)],
                    books=books,
                    identity=f"COMBO_NO_PLUS_REQUIRED_LEG_{idx}",
                ))

            for cover in covers:
                if cover["pass"]:
                    executable.append({"candidate": str(spec.get("name") or ""), **cover})
            candidate_rows.append({
                "candidate": str(spec.get("name") or ""),
                "combo": combo,
                "active_required_legs": required,
                "covers": covers,
                "executable": any(x["pass"] for x in covers),
            })

    executable.sort(key=lambda x: D(str(x.get("locked") or "-1")), reverse=True)
    out = {
        "status": "GEMINI_COMBO_MINIMAL_COVER_PREFLIGHT_V1",
        "catalog": catalog,
        "candidate_count": len(candidate_rows),
        "cover_count": sum(len(x["covers"]) for x in candidate_rows),
        "executable_cover_count": len(executable),
        "best_executable": executable[0] if executable else None,
        "target_locked": str(TARGET),
        "settlement_identities": [
            "C_YES+SUM(ALL_COMPLEMENTS)>=1",
            "C_NO+ANY_SINGLE_REQUIRED_LEG>=1",
        ],
        "rows": candidate_rows,
        "order_entry": False,
        "realized": False,
    }
    print("GEMINI_COMBO_MINIMAL_COVER_PREFLIGHT_V1 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
