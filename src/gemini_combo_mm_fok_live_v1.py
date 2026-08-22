"""Bounded q=1 same-Gemini sports combo FOK executor, dynamic catalog v2.

Why FOK only:
Gemini combo instruments can have quantityMinimum=1 and quantityIncrement=0.01.
A resting q=1 combo order can therefore partially fill below one contract.  Because
prediction fees round up to a cent, the 0.01-fill branch cannot preserve our
>=3c/contract guaranteed floor.  This executor never rests a combo order.

Safety model:
- Discover only Gemini's currently registered Active combo products and canonical
  required outcomes.  Settled PASS legs are removed; FAIL/ambiguous closed combos
  are excluded.
- Evaluate both exact conjunction payout covers for C=AND(A_i):
    C(YES) + SUM(1-A_i) >= 1
    C(NO)  + SUM(A_i)   >= 1
- q=1 only.  Final combo + component books are fetched concurrently and must have
  q1 depth, <=50ms receive skew, and >=3c conservative after-fee lock.
- First live send is BUY combo q=1 LIMIT+FOK.  Zero fill costs no hedge capital.
- Only after an exactly q=1 combo fill are all required component hedges sent as
  q=1 LIMIT+FOK at the just-certified limit prices.
- Known hedge failures trigger bounded marketable LIMIT+FOK recovery and immediate
  reconciliation.  Ambiguous order status hard-halts rather than guessing.
- A completed cover is FILLED_NOT_SETTLED, never REALIZED here.

The module is DISARMED unless campaign, matching arm token, enable flag and boot
expiry all pass.  Production service restart policy must remain NEVER when armed,
and the arm variables should be cleared after the authorized process boots.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping, Sequence

import httpx

import src.gemini_extreme_combo_live_canary as c
from src.gemini_combo_mm_fok_preflight import (
    _catalog_specs,
    _canonical_active_legs,
    _opposite,
)
from src.gemini_kalshi_probe_transport import GeminiTradingSession
from src.gemini_prediction_auth import private_rest_request

D = Decimal
ONE = D("1")
BASE = "https://api.gemini.com"
QTY = D("1")
TARGET = D("0.03")
TAKER = D("0.07")
MAX_FINAL_SKEW_MS = D(os.getenv("GEMINI_COMBO_FOK_MAX_FINAL_SKEW_MS", "50"))
POLL_SECONDS = max(0.25, min(5.0, float(os.getenv("GEMINI_COMBO_FOK_POLL_SECONDS", "0.75"))))
WATCH_SECONDS = max(60, min(21600, int(os.getenv("GEMINI_COMBO_FOK_WATCH_SECONDS", "21600"))))
MAX_ATTEMPTS = max(1, min(20, int(os.getenv("GEMINI_COMBO_FOK_MAX_ATTEMPTS", "5"))))
MAX_EXECUTED = max(1, min(3, int(os.getenv("GEMINI_COMBO_FOK_MAX_EXECUTED_TRADES", "1"))))
MAX_ACTIVE_LEGS = max(1, min(6, int(os.getenv("GEMINI_COMBO_FOK_MAX_ACTIVE_LEGS", "6"))))
MIN_AVAILABLE_USD = D(os.getenv("GEMINI_COMBO_FOK_MIN_AVAILABLE_USD", "1.05"))
FINAL_BALANCE_BUFFER = D(os.getenv("GEMINI_COMBO_FOK_FINAL_BALANCE_BUFFER", "0.02"))
CATALOG_REFRESH_SECONDS = max(60, min(900, int(os.getenv("GEMINI_COMBO_FOK_CATALOG_REFRESH_SECONDS", "300"))))


def emit(kind: str, payload: Mapping[str, Any]) -> None:
    print(kind + " " + json.dumps(dict(payload), separators=(",", ":"), default=str), flush=True)


def _parse_deadline(raw: str) -> datetime | None:
    if not raw.strip():
        return None
    try:
        out = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if out.tzinfo is None:
            out = out.replace(tzinfo=timezone.utc)
        return out.astimezone(timezone.utc)
    except Exception:
        return None


def _direction_hedges(spec: Mapping[str, Any], direction: str) -> tuple[str, list[dict[str, str]]]:
    required = [(str(s), str(o).upper()) for s, o in spec.get("active_required_legs") or []]
    if direction == "COMBO_YES_PLUS_COMPLEMENTS":
        return "YES", [{"symbol": s, "outcome": _opposite(o)} for s, o in required]
    if direction == "COMBO_NO_PLUS_SELECTED":
        return "NO", [{"symbol": s, "outcome": o} for s, o in required]
    raise ValueError(f"unsupported direction {direction}")


def _outcome_bid(book: Mapping[str, Any], outcome: str) -> tuple[D, D] | None:
    outcome = outcome.upper()
    if outcome == "YES":
        bids = [z for z in (c._level(x) for x in (book.get("bids") or [])) if z]
        return max(bids, key=lambda z: z[0]) if bids else None
    if outcome == "NO":
        asks = [z for z in (c._level(x) for x in (book.get("asks") or [])) if z]
        if not asks:
            return None
        ap, aq = min(asks, key=lambda z: z[0])
        return ONE - ap, aq
    return None


async def _json_get(http: httpx.AsyncClient, url: str, *, params: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    try:
        r = await http.get(url, params=params)
        try:
            body = r.json()
        except Exception:
            body = {}
        return r.status_code, body if isinstance(body, Mapping) else {}
    except Exception:
        return 0, {}


async def _book(http: httpx.AsyncClient, symbol: str) -> tuple[int, dict[str, Any], int]:
    status, body = await _json_get(http, f"{BASE}/v1/book/{symbol}", params={"limit_bids": 50, "limit_asks": 50})
    return status, body, time.perf_counter_ns()


async def _available_usd() -> tuple[int, D]:
    r = await private_rest_request("POST", "/v1/balances", timeout=5.0)
    usd = D("0")
    try:
        body = r.json()
    except Exception:
        body = []
    if isinstance(body, list):
        for row in body:
            if isinstance(row, Mapping) and str(row.get("currency") or "").upper() == "USD":
                usd = c._d(row.get("available")) or D("0")
                break
    return r.status_code, usd


async def _relevant_positions(symbols: set[str]) -> tuple[int, list[dict[str, Any]]]:
    r = await private_rest_request("POST", "/v1/prediction-markets/positions", params={}, timeout=6.0)
    try:
        body = r.json()
    except Exception:
        body = {}
    rows = body.get("positions", []) if isinstance(body, Mapping) else []
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        sym = str(row.get("symbol") or row.get("instrumentSymbol") or "")
        qty = c._d(row.get("quantity") or row.get("position") or row.get("netQuantity") or row.get("net_quantity"))
        if sym in symbols and (qty is None or qty != 0):
            out.append(dict(row))
    return r.status_code, out


async def _active_orders_for_symbols(symbols: set[str]) -> tuple[bool, list[dict[str, Any]]]:
    out: list[dict[str, Any]] = []
    ok = True
    for sym in sorted(symbols):
        r = await private_rest_request(
            "POST", "/v1/prediction-markets/orders/active",
            params={"symbol": sym, "limit": 100, "offset": 0}, timeout=5.0,
        )
        if r.status_code != 200:
            ok = False
            continue
        try:
            body = r.json()
        except Exception:
            body = {}
        for row in body.get("orders", []) if isinstance(body, Mapping) else []:
            if isinstance(row, Mapping):
                out.append(dict(row))
    return ok, out


def _grid_legal_for_q1(contract: Mapping[str, Any]) -> bool:
    qmin = c._d(contract.get("quantityMinimum"))
    qinc = c._d(contract.get("quantityIncrement"))
    pinc = c._d(contract.get("priceIncrement"))
    pmin = c._d(contract.get("priceMinimum"))
    if qmin is None or qinc is None or pinc is None or pmin is None or qinc <= 0 or pinc <= 0:
        return False
    if qmin > QTY:
        return False
    units = QTY / qinc
    return (
        units == units.to_integral_value()
        and str(contract.get("marketState") or "").lower() == "open"
        and str(contract.get("status") or "").lower() == "active"
    )


async def _event_contract(http: httpx.AsyncClient, event_ticker: str, symbol: str) -> dict[str, Any] | None:
    status, body = await _json_get(http, f"{BASE}/v1/prediction-markets/events/{event_ticker}")
    if status != 200:
        return None
    for row in body.get("contracts", []) if isinstance(body, Mapping) else []:
        if isinstance(row, Mapping) and str(row.get("instrumentSymbol") or "") == symbol:
            return dict(row)
    return None


async def _load_candidates(http: httpx.AsyncClient) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta, raw = await _catalog_specs(http)
    candidates: list[dict[str, Any]] = []
    for spec in raw:
        active = list(spec.get("active_required_legs") or [])
        if spec.get("blocker") or not active or len(active) > MAX_ACTIVE_LEGS:
            continue
        candidates.append(spec)
    return meta, candidates


async def _certify_dynamic_candidate(http: httpx.AsyncClient, spec: Mapping[str, Any]) -> dict[str, Any]:
    combo = str(spec["combo"])
    status, body = await _json_get(http, f"{BASE}/v1/prediction-markets/combos/{combo}")
    out: dict[str, Any] = {"combo": combo, "http": status, "pass": False}
    if status != 200:
        out["reason"] = "COMBO_METADATA_UNAVAILABLE"
        return out
    contract = body.get("contract") if isinstance(body.get("contract"), Mapping) else {}
    if str(contract.get("contractStatus") or "").lower() != "active":
        out["reason"] = "COMBO_NOT_ACTIVE"
        return out

    active, blocker, audit = _canonical_active_legs(body)
    out["canonical_legs"] = audit
    if blocker:
        out["reason"] = blocker
        return out
    expected = [(str(s), str(o).upper()) for s, o in spec.get("active_required_legs") or []]
    actual = [(str(s), str(o).upper()) for s, o in active]
    if actual != expected:
        out["reason"] = "ACTIVE_CANONICAL_LEGS_CHANGED"
        out["expected_active"] = expected
        out["actual_active"] = actual
        return out
    if not actual or len(actual) > MAX_ACTIVE_LEGS:
        out["reason"] = "ACTIVE_LEG_COUNT_OUT_OF_BOUNDS"
        return out

    event_ticker = str(contract.get("eventTicker") or contract.get("parentEventTicker") or "")
    combo_grid = await _event_contract(http, event_ticker, combo) if event_ticker else None
    if combo_grid is None or not _grid_legal_for_q1(combo_grid):
        out["reason"] = "COMBO_Q1_GRID_NOT_LIVE"
        return out

    contract_by_symbol: dict[str, Mapping[str, Any]] = {}
    for leg in body.get("legs", []) if isinstance(body.get("legs"), list) else []:
        lc = leg.get("contract") if isinstance(leg, Mapping) and isinstance(leg.get("contract"), Mapping) else None
        if lc is not None:
            contract_by_symbol[str(lc.get("instrumentSymbol") or "")] = lc

    grids = []
    for symbol, _outcome in actual:
        lc = contract_by_symbol.get(symbol)
        if lc is None:
            out["reason"] = "ACTIVE_COMPONENT_NOT_IN_CANONICAL_COMBO"
            return out
        ticker = str(lc.get("eventTicker") or lc.get("parentEventTicker") or "")
        grid = await _event_contract(http, ticker, symbol) if ticker else None
        ok = bool(grid and _grid_legal_for_q1(grid))
        grids.append({"symbol": symbol, "grid_ok": ok})
        if not ok:
            out["reason"] = "ACTIVE_COMPONENT_Q1_GRID_NOT_LIVE"
            out["component_grids"] = grids
            return out

    out.update({
        "pass": True,
        "reason": "SAME_GEMINI_CANONICAL_CONJUNCTION_PLUS_EXACT_COMPONENTS",
        "active_required_legs": actual,
        "component_grids": grids,
    })
    return out


async def _boot_preflight(campaign: str, candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"pass": False, "campaign": campaign, "candidate_count": len(candidates)}
    terms = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=5.0)
    try:
        tb = terms.json()
    except Exception:
        tb = {}
    out["terms_http"] = terms.status_code
    out["terms_accepted"] = bool(tb.get("hasAcceptedLatest")) if isinstance(tb, Mapping) else False
    balance_http, usd = await _available_usd()
    out["balance_http"] = balance_http
    out["usd_available"] = str(usd)

    symbols = {str(x["combo"]) for x in candidates}
    for spec in candidates:
        symbols.update(str(x[0]) for x in spec.get("active_required_legs") or [])
    positions_http, positions = await _relevant_positions(symbols) if symbols else (200, [])
    out["positions_http"] = positions_http
    out["relevant_position_count"] = len(positions)
    active_ok, active_orders = await _active_orders_for_symbols(symbols) if symbols else (True, [])
    out["active_order_scan_ok"] = active_ok
    out["relevant_active_order_count"] = len(active_orders)
    out["pass"] = bool(
        candidates
        and out["terms_accepted"]
        and balance_http == 200 and usd >= MIN_AVAILABLE_USD
        and positions_http == 200 and not positions
        and active_ok and not active_orders
    )
    return out


async def _fresh_economics(http: httpx.AsyncClient, spec: Mapping[str, Any], direction: str) -> dict[str, Any]:
    combo_outcome, hedge_specs = _direction_hedges(spec, direction)
    symbols = [str(spec["combo"]), *(x["symbol"] for x in hedge_specs)]
    rows = await asyncio.gather(*(_book(http, sym) for sym in symbols))
    received = [x[2] for x in rows]
    skew_ms = D(max(received) - min(received)) / D("1000000") if received else D("999999")
    combo_http, combo_book, _ = rows[0]
    combo_top = c._outcome_ask(combo_book, combo_outcome)
    hedge_rows: list[dict[str, Any]] = []
    hedge_all_in = D("0")
    hedge_ok = True
    for hedge, (status, book, _) in zip(hedge_specs, rows[1:]):
        top = c._outcome_ask(book, hedge["outcome"])
        row: dict[str, Any] = {"symbol": hedge["symbol"], "outcome": hedge["outcome"], "http": status}
        if status != 200 or top is None:
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

    out: dict[str, Any] = {
        "candidate": str(spec.get("name") or ""), "combo": str(spec["combo"]),
        "direction": direction, "combo_outcome": combo_outcome, "combo_http": combo_http,
        "quote_skew_ms": str(skew_ms), "hedges": hedge_rows, "hedge_all_in": str(hedge_all_in),
        "pass": False,
    }
    if combo_http != 200 or combo_top is None or not hedge_ok or skew_ms > MAX_FINAL_SKEW_MS:
        out["reason"] = "NO_FRESH_EXECUTABLE_COVER_BOOK"
        return out
    cp, cd = combo_top
    combo_fee = c._fee(TAKER, cp, QTY)
    all_in = cp + combo_fee + hedge_all_in
    locked = ONE - all_in
    out.update({
        "combo_ask": str(cp), "combo_depth": str(cd), "combo_fee": str(combo_fee),
        "all_in": str(all_in), "locked": str(locked),
        "pass": bool(cd >= QTY and locked >= TARGET),
        "reason": "EXECUTABLE_Q1_FOK" if cd >= QTY and locked >= TARGET else "BELOW_DEPTH_OR_3C_FLOOR",
    })
    return out


async def _send_pushover(title: str, message: str, priority: int = 0) -> bool:
    user = os.getenv("PUSHOVER_USER_KEY", "").strip()
    token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
    if not user or not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as h:
            r = await h.post(
                "https://api.pushover.net/1/messages.json",
                data={"user": user, "token": token, "title": title[:250], "message": message[:1024], "priority": str(priority)},
            )
        return r.status_code == 200
    except Exception:
        return False


async def _sell_outcome_fok(
    session: GeminiTradingSession,
    http: httpx.AsyncClient,
    *, symbol: str, outcome: str, quantity: D, client_id: str,
) -> dict[str, Any]:
    if quantity <= 0:
        return {"status": "FLATTEN_NOT_NEEDED", "symbol": symbol, "outcome": outcome, "quantity": str(quantity)}
    status, book, _ = await _book(http, symbol)
    bid = _outcome_bid(book, outcome) if status == 200 else None
    if bid is None or bid[1] < quantity:
        return {"status": "FLATTEN_NO_DEPTH", "symbol": symbol, "outcome": outcome, "quantity": str(quantity)}
    p, depth = bid
    params = {
        "symbol": symbol, "side": "SELL", "type": "LIMIT", "timeInForce": "FOK",
        "price": str(p), "quantity": str(quantity), "eventOutcome": outcome,
        "clientOrderId": client_id[:36],
    }
    result = await session.place_fok(params, terminal_timeout=3.0)
    return {
        "status": "FLATTEN_ATTEMPT", "symbol": symbol, "outcome": outcome,
        "quantity": str(quantity), "bid": str(p), "depth": str(depth), "result": result.as_dict(),
    }


async def _execute_candidate(
    session: GeminiTradingSession,
    http: httpx.AsyncClient,
    spec: Mapping[str, Any],
    econ: Mapping[str, Any],
    *, campaign: str, attempt: int,
) -> dict[str, Any]:
    cid_base = f"{campaign}-{attempt}"
    combo_outcome = str(econ["combo_outcome"])
    combo_params = {
        "symbol": spec["combo"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
        "price": str(econ["combo_ask"]), "quantity": "1", "eventOutcome": combo_outcome,
        "clientOrderId": f"{cid_base}-c"[:36],
    }
    combo = await session.place_fok(combo_params, terminal_timeout=3.0)
    out: dict[str, Any] = {
        "candidate": str(spec.get("name") or ""), "direction": econ["direction"],
        "economics": dict(econ), "combo": combo.as_dict(), "classification": "ATTEMPTED",
    }
    cq = c._d(combo.filled_quantity) or D("0")
    if combo.status == "UNKNOWN":
        out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_COMBO"
        return out
    if cq == 0:
        out["classification"] = "COMBO_FOK_NO_FILL_ZERO_HEDGE_COST"
        return out
    if combo.status != "FILLED" or cq != QTY:
        out["recovery"] = [await _sell_outcome_fok(
            session, http, symbol=str(spec["combo"]), outcome=combo_outcome,
            quantity=cq, client_id=f"{cid_base}-fc",
        )]
        out["classification"] = "LIVE_HARD_HALT_UNEXPECTED_PARTIAL_FOK_RECOVERY_ATTEMPTED"
        return out

    hedge_params = []
    for idx, hrow in enumerate(econ.get("hedges") or [], start=1):
        hedge_params.append({
            "symbol": hrow["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
            "price": str(hrow["ask"]), "quantity": "1", "eventOutcome": hrow["outcome"],
            "clientOrderId": f"{cid_base}-h{idx}"[:36],
        })
    hedge_results = await asyncio.gather(*(session.place_fok(x, terminal_timeout=3.0) for x in hedge_params))
    out["hedges"] = [x.as_dict() for x in hedge_results]

    unknown = any(x.status == "UNKNOWN" for x in hedge_results)
    if unknown:
        out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_HEDGE_MANUAL_RECONCILE"
        await _send_pushover(
            "Sports arb hard halt",
            f"{spec.get('name') or spec['combo']} combo filled but a hedge is ambiguous. Manual reconciliation required.", 1,
        )
        return out

    filled_qty = [(c._d(x.filled_quantity) or D("0")) for x in hedge_results]
    fully_filled = [x.status == "FILLED" and q == QTY for x, q in zip(hedge_results, filled_qty)]
    if all(fully_filled):
        out["classification"] = "FILLED_NOT_SETTLED"
        await _send_pushover(
            "Sports arb filled — awaiting settlement",
            f"{spec.get('name') or spec['combo']}\nGemini q1 combo + exact component cover filled.\nModeled lock before execution: ${econ['locked']}\nClassification: FILLED_NOT_SETTLED",
            0,
        )
        return out

    recovery = []
    for idx, (q, hrow) in enumerate(zip(filled_qty, econ.get("hedges") or []), start=1):
        if q > 0:
            recovery.append(await _sell_outcome_fok(
                session, http, symbol=str(hrow["symbol"]), outcome=str(hrow["outcome"]),
                quantity=q, client_id=f"{cid_base}-fh{idx}",
            ))
    recovery.append(await _sell_outcome_fok(
        session, http, symbol=str(spec["combo"]), outcome=combo_outcome,
        quantity=QTY, client_id=f"{cid_base}-fc",
    ))
    out["recovery"] = recovery

    symbols = {str(spec["combo"]), *(str(x["symbol"]) for x in econ.get("hedges") or [])}
    ph, positions = await _relevant_positions(symbols)
    active_ok, active = await _active_orders_for_symbols(symbols)
    out["post_recovery_positions_http"] = ph
    out["post_recovery_position_count"] = len(positions)
    out["post_recovery_active_scan_ok"] = active_ok
    out["post_recovery_active_order_count"] = len(active)
    out["classification"] = (
        "LIVE_HARD_HALT_HEDGE_FAILURE_RECOVERED_FLAT"
        if ph == 200 and not positions and active_ok and not active
        else "LIVE_HARD_HALT_HEDGE_FAILURE_RECOVERY_UNRESOLVED"
    )
    await _send_pushover(
        "Sports arb hard halt",
        f"{spec.get('name') or spec['combo']} combo filled but a hedge failed. Recovery attempted; classification {out['classification']}.",
        1,
    )
    return out


async def run() -> dict[str, Any]:
    campaign = os.getenv("GEMINI_COMBO_FOK_CAMPAIGN", "").strip()
    arm = os.getenv("GEMINI_COMBO_FOK_ARM_CAMPAIGN", "").strip()
    enabled = os.getenv("ENABLE_GEMINI_COMBO_FOK_LIVE", "").strip().upper() == "YES"
    deadline = _parse_deadline(os.getenv("GEMINI_COMBO_FOK_ARM_NOT_AFTER_UTC", ""))
    deadline_ok = bool(deadline and datetime.now(timezone.utc) <= deadline)
    armed = bool(enabled and campaign and arm == campaign and deadline_ok)

    async with httpx.AsyncClient(timeout=12.0) as public_http:
        catalog, candidates = await _load_candidates(public_http)
    preflight = await _boot_preflight(campaign, candidates)
    out: dict[str, Any] = {
        "status": "GEMINI_COMBO_MM_FOK_LIVE_V2", "campaign": campaign, "armed": armed,
        "deadline_ok": deadline_ok, "catalog": catalog, "preflight": preflight,
        "target_locked": str(TARGET), "max_final_skew_ms": str(MAX_FINAL_SKEW_MS),
        "max_attempts": MAX_ATTEMPTS, "max_executed_trades": MAX_EXECUTED,
        "candidate_count": len(candidates), "order_entry": False, "realized": False,
        "resting_combo_orders": False,
    }
    if not armed:
        out["classification"] = "DISARMED_PREFLIGHT_PASS" if preflight.get("pass") else "DISARMED_PREFLIGHT_BLOCKED"
        emit("GEMINI_COMBO_MM_FOK_LIVE_V2", out)
        return out
    if not preflight.get("pass"):
        out["classification"] = "LIVE_GATE_BLOCKED"
        emit("GEMINI_COMBO_MM_FOK_LIVE_V2", out)
        return out

    session = GeminiTradingSession()
    out["order_entry"] = True
    attempts = executed = 0
    started = time.monotonic()
    last_heartbeat = 0.0
    last_catalog_refresh = time.monotonic()
    try:
        await session.connect()
        async with httpx.AsyncClient(timeout=6.0) as http:
            while time.monotonic() - started < WATCH_SECONDS and attempts < MAX_ATTEMPTS and executed < MAX_EXECUTED:
                if time.monotonic() - last_catalog_refresh >= CATALOG_REFRESH_SECONDS:
                    catalog, candidates = await _load_candidates(http)
                    last_catalog_refresh = time.monotonic()

                potential: list[tuple[D, Mapping[str, Any], str]] = []
                combo_rows = await asyncio.gather(*(_book(http, str(spec["combo"])) for spec in candidates))
                for spec, (status, book, _) in zip(candidates, combo_rows):
                    if status != 200:
                        continue
                    for direction, outcome in (("COMBO_YES_PLUS_COMPLEMENTS", "YES"), ("COMBO_NO_PLUS_SELECTED", "NO")):
                        top = c._outcome_ask(book, outcome)
                        if top is not None and top[1] >= QTY:
                            potential.append((top[0], spec, direction))
                potential.sort(key=lambda x: x[0])

                for _combo_price, spec, direction in potential:
                    econ = await _fresh_economics(http, spec, direction)
                    if not econ.get("pass"):
                        continue
                    cert = await _certify_dynamic_candidate(http, spec)
                    if not cert.get("pass"):
                        emit("GEMINI_COMBO_MM_FOK_CERT_CHANGED", {"candidate": spec.get("name"), "direction": direction, "certification": cert, "order_entry": False})
                        continue

                    symbols = {str(spec["combo"]), *(str(x["symbol"]) for x in econ.get("hedges") or [])}
                    ph, pos = await _relevant_positions(symbols)
                    active_ok, active = await _active_orders_for_symbols(symbols)
                    bh, usd = await _available_usd()
                    required_cash = D(str(econ["all_in"])) + FINAL_BALANCE_BUFFER
                    if ph != 200 or pos or not active_ok or active or bh != 200 or usd < required_cash:
                        out["classification"] = "LIVE_HARD_HALT_ACCOUNT_OR_CAPITAL_STATE_CHANGED"
                        out["final_positions_count"] = len(pos)
                        out["final_active_orders_count"] = len(active)
                        out["final_balance_http"] = bh
                        out["final_available_usd"] = str(usd)
                        out["required_cash_with_buffer"] = str(required_cash)
                        emit("GEMINI_COMBO_MM_FOK_LIVE_V2", out)
                        return out

                    # Re-run the <=50ms economics check after metadata/account calls so the
                    # order prices are the final observations immediately before send.
                    econ = await _fresh_economics(http, spec, direction)
                    if not econ.get("pass"):
                        continue

                    attempts += 1
                    emit("GEMINI_COMBO_MM_FOK_EXECUTABLE", {
                        "campaign": campaign, "attempt": attempts, "candidate": spec.get("name"),
                        "direction": direction, "economics": econ, "classification": "EXECUTABLE",
                        "realized": False,
                    })
                    result = await _execute_candidate(session, http, spec, econ, campaign=campaign, attempt=attempts)
                    emit("GEMINI_COMBO_MM_FOK_ATTEMPT", {"campaign": campaign, "attempt": attempts, **result, "realized": False})
                    if result.get("classification") == "FILLED_NOT_SETTLED":
                        executed += 1
                        break
                    if str(result.get("classification") or "").startswith("LIVE_HARD_HALT"):
                        out["classification"] = result["classification"]
                        out["last_attempt"] = result
                        emit("GEMINI_COMBO_MM_FOK_LIVE_V2", out)
                        return out

                now = time.monotonic()
                if now - last_heartbeat >= 60:
                    emit("GEMINI_COMBO_MM_FOK_HEARTBEAT", {
                        "campaign": campaign, "seconds_alive": round(now - started, 1),
                        "candidate_count": len(candidates), "potential_combo_sides": len(potential),
                        "attempts": attempts, "executed": executed, "order_entry": True, "realized": False,
                    })
                    last_heartbeat = now
                if executed >= MAX_EXECUTED or attempts >= MAX_ATTEMPTS:
                    break
                await asyncio.sleep(POLL_SECONDS)
    finally:
        await session.close()

    out["attempts"] = attempts
    out["executed"] = executed
    out["classification"] = (
        "FILLED_NOT_SETTLED" if executed
        else ("WATCH_ATTEMPT_CAP_REACHED_NO_EXECUTION" if attempts >= MAX_ATTEMPTS else "WATCH_WINDOW_ENDED_NO_EXECUTION")
    )
    emit("GEMINI_COMBO_MM_FOK_LIVE_V2", out)
    return out


if __name__ == "__main__":
    asyncio.run(run())
