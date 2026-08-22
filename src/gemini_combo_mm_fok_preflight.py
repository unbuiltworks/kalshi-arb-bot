"""Read-only FOK executable scan for accepted Gemini sports combos.

This intentionally rejects resting-maker promotion because combo instruments have
quantityMinimum=1 but quantityIncrement=0.01, so a q=1 resting order can receive
sub-contract partial fills. The safe lane here requires a full q=1 combo FOK fill
plus exact q=1 complement FOK hedges, all re-priced with conservative cent-rounded
Gemini taker fees and a >=3c locked floor.
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


def _max_combo_price(hedge_all_in: D) -> D | None:
    # Combo itself is taker in this lane. Search exact cent grid.
    for cents in range(99, 0, -1):
        p = D(cents) / D("100")
        total = p + c._fee(TAKER, p, QTY) + hedge_all_in
        if D("1") - total >= TARGET:
            return p
    return None


async def run_once() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=8.0) as h:
        for spec in CANDIDATES:
            symbols = [spec["combo"], *(x[0] for x in spec["hedges"])]
            results = await asyncio.gather(*(_book(h, s) for s in symbols))
            combo_http, combo_book = results[0]
            combo_top = _ask(combo_book, "YES")
            hedge_rows = []
            hedge_all_in = D("0")
            hedge_ok = True
            for (symbol, outcome), (status, b) in zip(spec["hedges"], results[1:]):
                top = _ask(b, outcome)
                row: dict[str, Any] = {"symbol": symbol, "outcome": outcome, "http": status}
                if top is None:
                    row["available"] = False
                    hedge_ok = False
                else:
                    p, depth = top
                    fee = c._fee(TAKER, p, QTY)
                    row.update({"available": True, "ask": str(p), "depth": str(depth), "fee": str(fee), "all_in": str(p + fee)})
                    hedge_all_in += p + fee
                    if depth < QTY:
                        hedge_ok = False
                hedge_rows.append(row)

            max_combo = _max_combo_price(hedge_all_in) if hedge_ok else None
            row = {
                "name": spec["name"],
                "combo": spec["combo"],
                "combo_http": combo_http,
                "combo_yes_ask": str(combo_top[0]) if combo_top else None,
                "combo_yes_depth": str(combo_top[1]) if combo_top else None,
                "hedges": hedge_rows,
                "hedge_all_in": str(hedge_all_in),
                "max_combo_fok_price_for_3c": str(max_combo) if max_combo is not None else None,
                "executable": False,
                "locked": None,
            }
            if combo_top is not None and max_combo is not None:
                cp, cd = combo_top
                combo_fee = c._fee(TAKER, cp, QTY)
                all_in = cp + combo_fee + hedge_all_in
                locked = D("1") - all_in
                row.update({"combo_taker_fee": str(combo_fee), "all_in": str(all_in), "locked": str(locked)})
                row["executable"] = bool(cd >= QTY and hedge_ok and cp <= max_combo and locked >= TARGET)
            rows.append(row)

    executable = [x for x in rows if x["executable"]]
    out = {
        "status": "GEMINI_COMBO_MM_FOK_PREFLIGHT",
        "target_locked": str(TARGET),
        "candidate_count": len(rows),
        "executable_count": len(executable),
        "rows": rows,
        "resting_maker_live_blocked": True,
        "resting_block_reason": "COMBO_QUANTITY_INCREMENT_0.01_PLUS_CENT_ROUNDED_FEES_MAKES_SUB_Q1_PARTIAL_FILL_UNSAFE",
        "safe_live_mode": "Q1_COMBO_FOK_THEN_EXACT_Q1_COMPLEMENT_FOK",
        "order_entry": False,
        "realized": False,
    }
    print("GEMINI_COMBO_MM_FOK_PREFLIGHT " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
