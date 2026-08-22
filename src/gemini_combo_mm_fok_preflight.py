"""Read-only FOK executable scan for accepted Gemini sports combos.

Resting-maker promotion is intentionally blocked because combo instruments can have
quantityMinimum=1 but quantityIncrement=0.01, so a q=1 resting order can receive
sub-contract partial fills. With Gemini fees rounded up to a cent, arbitrary sub-q1
partials cannot preserve the >=3c/contract guaranteed floor.

The safe lane here is all-or-none q=1 execution. For every accepted conjunction
combo C=A*B, scan both conservative payout identities:

  C(YES) + (1-A) + (1-B) >= 1
  C(NO)  + A     + B     >= 1

All three legs are priced as q=1 taker FOK orders with conservative cent-rounded
Gemini fees. This module is read-only and never places an order.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, Mapping

import httpx

import src.gemini_extreme_combo_live_canary as c

D = Decimal
BASE = "https://api.gemini.com"
QTY = D("1")
TARGET = D("0.03")
TAKER = D("0.07")

CANDIDATES = [
    {
        "name": "tottenham_draw_no__nym_chw_over25_yes",
        "combo": "GEMI-CMB-260823-605EB3AACEDE12F2-CMB",
        "hedges": [
            ("GEMI-EPL-2608221630-TOT-BRE-M-D", "YES"),
            ("GEMI-MLB-2608222310-NYM-CHW-T-O2", "NO"),
        ],
    },
    {
        "name": "tor_mia_over05_yes__chc_sea_over15_yes",
        "combo": "GEMI-CMB-260823-0C93A84ACACD7C1D-CMB",
        "hedges": [
            ("GEMI-MLS-2608222330-TOR-MIA-REGT-O0", "NO"),
            ("GEMI-MLB-2608222315-CHC-SEA-T-O1", "NO"),
        ],
    },
    {
        "name": "clb_by25_no__sea_cin_over05_yes",
        "combo": "GEMI-CMB-260823-130D47B64F4671DC-CMB",
        "hedges": [
            ("GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2", "YES"),
            ("GEMI-MLS-2608222330-SEA-CIN-REGT-O0", "NO"),
        ],
    },
    {
        "name": "dal_by25_no__chi_nyrb_over05_yes",
        "combo": "GEMI-CMB-260823-790AAEFA6CECBA1E-CMB",
        "hedges": [
            ("GEMI-MLS-2608230130-DAL-VAN-REGS-DAL2", "YES"),
            ("GEMI-MLS-2608222330-CHI-NYRB-REGT-O0", "NO"),
        ],
    },
]


def _level(row: Any) -> tuple[D, D] | None:
    if not isinstance(row, Mapping):
        return None
    try:
        p = D(str(row.get("price")))
        q = D(str(row.get("amount") if row.get("amount") is not None else row.get("quantity")))
    except Exception:
        return None
    if not (D("0") < p < D("1")) or q <= 0:
        return None
    return p, q


async def _book(h: httpx.AsyncClient, symbol: str) -> tuple[int, dict[str, Any]]:
    r = await h.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 50, "limit_asks": 50})
    try:
        body = r.json()
    except Exception:
        body = {}
    return r.status_code, body if isinstance(body, Mapping) else {}


def _ask(book: Mapping[str, Any], outcome: str) -> tuple[D, D] | None:
    return c._outcome_ask(book, outcome)


def _opposite(outcome: str) -> str:
    return "NO" if outcome.upper() == "YES" else "YES"


def _max_combo_price(hedge_all_in: D) -> D | None:
    # Combo itself is taker in this lane. Search exact cent grid.
    for cents in range(99, 0, -1):
        p = D(cents) / D("100")
        total = p + c._fee(TAKER, p, QTY) + hedge_all_in
        if D("1") - total >= TARGET:
            return p
    return None


def _price_leg(
    *,
    symbol: str,
    outcome: str,
    status: int,
    book: Mapping[str, Any],
) -> tuple[dict[str, Any], D, bool]:
    top = _ask(book, outcome)
    row: dict[str, Any] = {"symbol": symbol, "outcome": outcome, "http": status}
    if top is None:
        row["available"] = False
        return row, D("0"), False
    p, depth = top
    fee = c._fee(TAKER, p, QTY)
    all_in = p + fee
    row.update(
        {
            "available": True,
            "ask": str(p),
            "depth": str(depth),
            "fee": str(fee),
            "all_in": str(all_in),
        }
    )
    return row, all_in, depth >= QTY


def _evaluate_direction(
    *,
    name: str,
    combo: str,
    combo_outcome: str,
    component_specs: list[tuple[str, str]],
    results_by_symbol: Mapping[str, tuple[int, Mapping[str, Any]]],
) -> dict[str, Any]:
    component_rows: list[dict[str, Any]] = []
    component_all_in = D("0")
    component_ok = True
    for symbol, outcome in component_specs:
        status, book = results_by_symbol[symbol]
        leg, cost, ok = _price_leg(symbol=symbol, outcome=outcome, status=status, book=book)
        component_rows.append(leg)
        component_all_in += cost
        component_ok = component_ok and ok

    combo_status, combo_book = results_by_symbol[combo]
    combo_top = _ask(combo_book, combo_outcome)
    max_combo = _max_combo_price(component_all_in) if component_ok else None
    out: dict[str, Any] = {
        "direction": name,
        "settlement_floor": "1",
        "combo": combo,
        "combo_outcome": combo_outcome,
        "combo_http": combo_status,
        "combo_ask": str(combo_top[0]) if combo_top else None,
        "combo_depth": str(combo_top[1]) if combo_top else None,
        "components": component_rows,
        "component_all_in": str(component_all_in),
        "max_combo_fok_price_for_3c": str(max_combo) if max_combo is not None else None,
        "executable": False,
        "locked": None,
    }
    if combo_top is None or max_combo is None:
        return out

    cp, cd = combo_top
    combo_fee = c._fee(TAKER, cp, QTY)
    all_in = cp + combo_fee + component_all_in
    locked = D("1") - all_in
    out.update(
        {
            "combo_taker_fee": str(combo_fee),
            "all_in": str(all_in),
            "locked": str(locked),
            "executable": bool(cd >= QTY and component_ok and cp <= max_combo and locked >= TARGET),
        }
    )
    return out


async def run_once() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    executable_directions: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=8.0) as h:
        for spec in CANDIDATES:
            symbols = [spec["combo"], *(x[0] for x in spec["hedges"])]
            fetched = await asyncio.gather(*(_book(h, s) for s in symbols))
            results_by_symbol = {symbol: result for symbol, result in zip(symbols, fetched)}

            yes_plus_complements = _evaluate_direction(
                name="COMBO_YES_PLUS_COMPLEMENTS",
                combo=spec["combo"],
                combo_outcome="YES",
                component_specs=list(spec["hedges"]),
                results_by_symbol=results_by_symbol,
            )
            selected = [(symbol, _opposite(outcome)) for symbol, outcome in spec["hedges"]]
            no_plus_selected = _evaluate_direction(
                name="COMBO_NO_PLUS_SELECTED",
                combo=spec["combo"],
                combo_outcome="NO",
                component_specs=selected,
                results_by_symbol=results_by_symbol,
            )
            directions = [yes_plus_complements, no_plus_selected]
            for direction in directions:
                if direction["executable"]:
                    executable_directions.append({"candidate": spec["name"], **direction})
            rows.append(
                {
                    "name": spec["name"],
                    "combo": spec["combo"],
                    "directions": directions,
                    "executable": any(d["executable"] for d in directions),
                }
            )

    executable_directions.sort(key=lambda x: D(str(x.get("locked") or "-1")), reverse=True)
    out = {
        "status": "GEMINI_COMBO_MM_FOK_PREFLIGHT_V2",
        "target_locked": str(TARGET),
        "candidate_count": len(rows),
        "direction_count": len(rows) * 2,
        "executable_candidate_count": sum(bool(x["executable"]) for x in rows),
        "executable_direction_count": len(executable_directions),
        "best_executable": executable_directions[0] if executable_directions else None,
        "rows": rows,
        "resting_maker_live_blocked": True,
        "resting_block_reason": "COMBO_QUANTITY_INCREMENT_0.01_PLUS_CENT_ROUNDED_FEES_MAKES_SUB_Q1_PARTIAL_FILL_UNSAFE",
        "safe_live_mode": "Q1_THREE_LEG_FOK_ONLY",
        "settlement_identities": ["C_YES+(1-A)+(1-B)>=1", "C_NO+A+B>=1"],
        "order_entry": False,
        "realized": False,
    }
    print("GEMINI_COMBO_MM_FOK_PREFLIGHT_V2 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
