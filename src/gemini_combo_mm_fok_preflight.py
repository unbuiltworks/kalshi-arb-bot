"""Read-only q1 FOK economic scan for active Gemini sports combos.

Resting-maker promotion is intentionally blocked for combo instruments whose
quantityIncrement is 0.01. A q=1 resting combo order can receive sub-contract
partials and Gemini's cent-rounded fee makes the 0.01 fill case incompatible with
our >=3c/contract guaranteed floor.

For a conjunction combo C=AND(A_i), the minimal conservative payout covers are:

  C(YES) + sum(1-A_i) >= 1
  C(NO)  + A_i        >= 1   for every individual required leg i

The NO identity needs only one canonical required leg, reducing fee/capital and
sequential-hedge risk versus buying every selected leg. A required leg already
resolved PASS is removed from the active conjunction. Any resolved FAIL (or
ambiguous closed leg) blocks the combo.

All prices are q=1 taker/FOK economics with conservative cent-rounded Gemini fees.
No authenticated endpoint is imported or called; this module never places orders.
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
CATALOG_LIMIT = 500


def _opposite(outcome: str) -> str:
    return "NO" if outcome.upper() == "YES" else "YES"


def _cover_directions(active_required: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Return the exact minimal guaranteed covers for an unresolved conjunction."""
    required = [(str(symbol), str(outcome).upper()) for symbol, outcome in active_required]
    covers: list[dict[str, Any]] = [
        {
            "name": "COMBO_YES_PLUS_ALL_COMPLEMENTS",
            "combo_outcome": "YES",
            "component_specs": [(symbol, _opposite(outcome)) for symbol, outcome in required],
        }
    ]
    for idx, (symbol, outcome) in enumerate(required):
        covers.append(
            {
                "name": f"COMBO_NO_PLUS_REQUIRED_LEG_{idx}",
                "combo_outcome": "NO",
                "component_specs": [(symbol, outcome)],
            }
        )
    return covers


async def _json_get(h: httpx.AsyncClient, url: str, *, params: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    try:
        r = await h.get(url, params=params)
        try:
            body = r.json()
        except Exception:
            body = {}
        return r.status_code, body if isinstance(body, Mapping) else {}
    except Exception:
        return 0, {}


async def _book(h: httpx.AsyncClient, symbol: str) -> tuple[int, dict[str, Any]]:
    return await _json_get(h, f"{BASE}/v1/book/{symbol}", params={"limit_bids": 50, "limit_asks": 50})


def _ask(book: Mapping[str, Any], outcome: str) -> tuple[D, D] | None:
    return c._outcome_ask(book, outcome)


def _max_combo_price(component_all_in: D) -> D | None:
    for cents in range(99, 0, -1):
        p = D(cents) / D("100")
        total = p + c._fee(TAKER, p, QTY) + component_all_in
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


def _canonical_active_legs(body: Mapping[str, Any]) -> tuple[list[tuple[str, str]], str | None, list[dict[str, Any]]]:
    """Return active (symbol, required outcome), blocker, and compact audit rows."""
    active: list[tuple[str, str]] = []
    audit: list[dict[str, Any]] = []
    for idx, leg in enumerate(body.get("legs") or []):
        if not isinstance(leg, Mapping):
            return [], "MALFORMED_LEG", audit
        contract = leg.get("contract") if isinstance(leg.get("contract"), Mapping) else {}
        symbol = str(contract.get("instrumentSymbol") or "")
        required = str(leg.get("requiredOutcome") or "").upper()
        leg_outcome = str(leg.get("legOutcome") or "").upper()
        status = str(contract.get("contractStatus") or "").upper()
        row = {
            "index": idx,
            "symbol": symbol or None,
            "required": required or None,
            "contract_status": status or None,
            "leg_outcome": leg_outcome or None,
        }
        audit.append(row)
        if leg_outcome == "PASS":
            continue
        if leg_outcome == "FAIL":
            return [], "REQUIRED_LEG_ALREADY_FAILED", audit
        if status == "SETTLED":
            return [], "SETTLED_LEG_WITHOUT_PASS_FAIL", audit
        if status and status != "ACTIVE":
            return [], f"NON_ACTIVE_LEG_{status}", audit
        if not symbol or required not in {"YES", "NO"}:
            return [], "MISSING_CANONICAL_ACTIVE_LEG", audit
        active.append((symbol, required))
    if not active:
        return [], "NO_UNRESOLVED_ACTIVE_LEGS", audit
    return active, None, audit


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
        status, book = results_by_symbol.get(symbol, (0, {}))
        leg, cost, ok = _price_leg(symbol=symbol, outcome=outcome, status=status, book=book)
        component_rows.append(leg)
        component_all_in += cost
        component_ok = component_ok and ok

    combo_status, combo_book = results_by_symbol.get(combo, (0, {}))
    combo_top = _ask(combo_book, combo_outcome)
    max_combo = _max_combo_price(component_all_in) if component_ok else None
    out: dict[str, Any] = {
        "direction": name,
        "settlement_floor": "1",
        "combo_outcome": combo_outcome,
        "combo_http": combo_status,
        "combo_ask": str(combo_top[0]) if combo_top else None,
        "combo_depth": str(combo_top[1]) if combo_top else None,
        "components": component_rows,
        "component_all_in": str(component_all_in),
        "max_combo_fok_price_for_3c": str(max_combo) if max_combo is not None else None,
        "economic_executable": False,
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
            "economic_executable": bool(cd >= QTY and component_ok and cp <= max_combo and locked >= TARGET),
        }
    )
    return out


async def _catalog_specs(h: httpx.AsyncClient) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    status, catalog = await _json_get(
        h,
        f"{BASE}/v1/prediction-markets/combos",
        params={"status": "Active", "limit": CATALOG_LIMIT, "offset": 0},
    )
    rows = catalog.get("combos") or [] if isinstance(catalog, Mapping) else []
    symbols: list[str] = []
    catalog_name: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        contract = row.get("contract") if isinstance(row.get("contract"), Mapping) else {}
        symbol = str(contract.get("instrumentSymbol") or "")
        if symbol:
            symbols.append(symbol)
            catalog_name[symbol] = str(contract.get("contractName") or "")

    sem = asyncio.Semaphore(15)
    detail: dict[str, tuple[int, dict[str, Any]]] = {}

    async def fetch_detail(symbol: str) -> None:
        async with sem:
            detail[symbol] = await _json_get(h, f"{BASE}/v1/prediction-markets/combos/{symbol}")

    await asyncio.gather(*(fetch_detail(s) for s in symbols))
    specs: list[dict[str, Any]] = []
    for symbol in symbols:
        d_status, body = detail.get(symbol, (0, {}))
        active, blocker, audit = _canonical_active_legs(body)
        specs.append(
            {
                "combo": symbol,
                "name": catalog_name.get(symbol) or str((body.get("contract") or {}).get("contractName") or ""),
                "detail_http": d_status,
                "active_required_legs": active,
                "canonical_legs": audit,
                "blocker": blocker,
            }
        )
    pagination = catalog.get("pagination") if isinstance(catalog, Mapping) else None
    meta = {
        "http": status,
        "catalog_rows": len(rows),
        "registered_symbols": len(symbols),
        "pagination": {k: pagination.get(k) for k in ("limit", "offset", "total") if isinstance(pagination, Mapping) and k in pagination},
    }
    return meta, specs


async def run_once() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    executable_directions: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=12.0) as h:
        catalog_meta, specs = await _catalog_specs(h)
        for spec in specs:
            combo = spec["combo"]
            active_required = list(spec["active_required_legs"])
            if spec["blocker"]:
                rows.append({**spec, "directions": [], "economic_executable": False})
                continue

            symbols = [combo, *(x[0] for x in active_required)]
            fetched = await asyncio.gather(*(_book(h, s) for s in symbols))
            results_by_symbol = {symbol: result for symbol, result in zip(symbols, fetched)}

            directions: list[dict[str, Any]] = []
            for cover in _cover_directions(active_required):
                direction = _evaluate_direction(
                    name=str(cover["name"]),
                    combo=combo,
                    combo_outcome=str(cover["combo_outcome"]),
                    component_specs=list(cover["component_specs"]),
                    results_by_symbol=results_by_symbol,
                )
                directions.append(direction)
                if direction["economic_executable"]:
                    executable_directions.append(
                        {
                            "candidate": spec["name"],
                            "combo": combo,
                            "active_leg_count": len(active_required),
                            **direction,
                        }
                    )
            rows.append(
                {
                    **spec,
                    "directions": directions,
                    "economic_executable": any(d["economic_executable"] for d in directions),
                }
            )

    executable_directions.sort(key=lambda x: D(str(x.get("locked") or "-1")), reverse=True)
    combo_with_any_book = sum(
        any(d.get("combo_ask") is not None for d in row.get("directions") or []) for row in rows
    )
    out = {
        "status": "GEMINI_COMBO_MM_FOK_PREFLIGHT_V4",
        "target_locked": str(TARGET),
        "catalog": catalog_meta,
        "candidate_count": len(rows),
        "direction_count": sum(len(x.get("directions") or []) for x in rows),
        "combo_with_any_taker_side": combo_with_any_book,
        "economic_executable_candidate_count": sum(bool(x["economic_executable"]) for x in rows),
        "economic_executable_direction_count": len(executable_directions),
        "best_economic_executable": executable_directions[0] if executable_directions else None,
        "rows": rows,
        "resting_maker_live_blocked": True,
        "resting_block_reason": "OBSERVED_COMBO_QUANTITY_INCREMENT_0.01_PLUS_CENT_ROUNDED_FEES_MAKES_SUB_Q1_PARTIAL_FILL_UNSAFE",
        "safe_candidate_mode": "Q1_COMBO_FOK_THEN_MINIMAL_Q1_COMPONENT_COVER_FOK",
        "execution_atomicity": "NOT_ATOMIC_ACROSS_ORDERS;_LIVE_PROMOTION_REQUIRES_FRESH_DEPTH_AND_BOUNDED_RECOVERY",
        "settlement_identities": ["C_YES+SUM(ALL_COMPLEMENTS)>=1", "C_NO+ANY_SINGLE_REQUIRED_LEG>=1"],
        "order_entry": False,
        "realized": False,
    }
    print("GEMINI_COMBO_MM_FOK_PREFLIGHT_V4 " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(run_once())
