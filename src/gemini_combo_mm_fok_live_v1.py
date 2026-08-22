"""Bounded q=1 same-Gemini sports combo FOK watcher.

Live safety model:
- NO resting combo orders. Gemini combo quantity increments can be 0.01, so resting
  q=1 maker orders can partially fill and fail the >=3c guarantee after cent-rounded fees.
- Watch only already-canonicalized Active combo instruments.
- A candidate is actionable only after a final concurrent refresh of combo + both exact
  complement books with <=50ms receive skew and >=3c conservative after-fee lock.
- First leg is BUY combo YES q=1 LIMIT+FOK at the observed executable ask. FOK must
  return either 0 or exactly 1 contract; anything else is a hard halt.
- Only after the combo fully fills do we BUY both exact complements q=1 LIMIT+FOK at
  their just-observed asks. If either hedge fails, flatten any confirmed positions with
  supported marketable LIMIT+FOK orders and hard-halt for reconciliation.
- A completed three-leg fill is FILLED_NOT_SETTLED, never REALIZED here.

DISARMED unless campaign, arm token, enable flag, and boot deadline all pass.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping

import httpx

import src.gemini_extreme_combo_live_canary as c
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
MIN_AVAILABLE_USD = D(os.getenv("GEMINI_COMBO_FOK_MIN_AVAILABLE_USD", "1.05"))

CANDIDATES = [
    {
        "name": "tottenham_draw_no__nym_chw_over25_yes",
        "combo": "GEMI-CMB-260823-605EB3AACEDE12F2-CMB",
        "legs": [("431650", "No"), ("498224", "Yes")],
        "hedges": [
            {"symbol": "GEMI-EPL-2608221630-TOT-BRE-M-D", "outcome": "YES"},
            {"symbol": "GEMI-MLB-2608222310-NYM-CHW-T-O2", "outcome": "NO"},
        ],
    },
    {
        "name": "tor_mia_over05_yes__chc_sea_over15_yes",
        "combo": "GEMI-CMB-260823-0C93A84ACACD7C1D-CMB",
        "legs": [("494125", "Yes"), ("498299", "Yes")],
        "hedges": [
            {"symbol": "GEMI-MLS-2608222330-TOR-MIA-REGT-O0", "outcome": "NO"},
            {"symbol": "GEMI-MLB-2608222315-CHC-SEA-T-O1", "outcome": "NO"},
        ],
    },
    {
        "name": "clb_by25_no__sea_cin_over05_yes",
        "combo": "GEMI-CMB-260823-130D47B64F4671DC-CMB",
        "legs": [("494155", "No"), ("494116", "Yes")],
        "hedges": [
            {"symbol": "GEMI-MLS-2608230030-CLB-NSH-REGS-CLB2", "outcome": "YES"},
            {"symbol": "GEMI-MLS-2608222330-SEA-CIN-REGT-O0", "outcome": "NO"},
        ],
    },
    {
        "name": "dal_by25_no__chi_nyrb_over05_yes",
        "combo": "GEMI-CMB-260823-790AAEFA6CECBA1E-CMB",
        "legs": [("494222", "No"), ("494143", "Yes")],
        "hedges": [
            {"symbol": "GEMI-MLS-2608230130-DAL-VAN-REGS-DAL2", "outcome": "YES"},
            {"symbol": "GEMI-MLS-2608222330-CHI-NYRB-REGT-O0", "outcome": "NO"},
        ],
    },
]


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


def _outcome_bid(book: Mapping[str, Any], outcome: str) -> tuple[D, D] | None:
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


async def _book(http: httpx.AsyncClient, symbol: str) -> tuple[int, dict[str, Any], int]:
    r = await http.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 50, "limit_asks": 50})
    received = time.perf_counter_ns()
    try:
        body = r.json()
    except Exception:
        body = {}
    return r.status_code, body if isinstance(body, Mapping) else {}, received


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
    out = []
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
        r = await private_rest_request("POST", "/v1/prediction-markets/orders/active", params={"symbol": sym, "limit": 100, "offset": 0}, timeout=5.0)
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
    return units == units.to_integral_value() and str(contract.get("marketState") or "").lower() == "open" and str(contract.get("status") or "").lower() == "active"


async def _event_contract(http: httpx.AsyncClient, event_ticker: str, symbol: str) -> dict[str, Any] | None:
    r = await http.get(f"{BASE}/v1/prediction-markets/events/{event_ticker}")
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except Exception:
        return None
    for row in body.get("contracts", []) if isinstance(body, Mapping) else []:
        if isinstance(row, Mapping) and str(row.get("instrumentSymbol") or "") == symbol:
            return dict(row)
    return None


async def _certify_candidate(http: httpx.AsyncClient, spec: Mapping[str, Any]) -> dict[str, Any]:
    r = await http.get(f"{BASE}/v1/prediction-markets/combos/{spec['combo']}")
    try:
        body = r.json()
    except Exception:
        body = {}
    result: dict[str, Any] = {"combo": spec["combo"], "http": r.status_code, "pass": False}
    if r.status_code != 200 or not isinstance(body, Mapping):
        result["reason"] = "COMBO_METADATA_UNAVAILABLE"
        return result
    contract = body.get("contract", {}) if isinstance(body.get("contract"), Mapping) else {}
    if str(contract.get("contractStatus") or "").lower() != "active":
        result["reason"] = "COMBO_NOT_ACTIVE"
        return result
    actual: set[tuple[str, str]] = set()
    for leg in body.get("legs", []) if isinstance(body.get("legs"), list) else []:
        if not isinstance(leg, Mapping):
            continue
        cid = str(leg.get("contractId") or (leg.get("contract", {}) if isinstance(leg.get("contract"), Mapping) else {}).get("contractId") or "")
        required = str(leg.get("requiredOutcome") or leg.get("outcome") or "").strip().title()
        actual.add((cid, required))
    expected = {(str(cid), str(outcome).title()) for cid, outcome in spec["legs"]}
    result["actual_legs"] = sorted(actual)
    result["expected_legs"] = sorted(expected)
    if actual != expected:
        result["reason"] = "CANONICAL_LEG_DIRECTION_MISMATCH"
        return result
    event_ticker = str(contract.get("eventTicker") or "")
    combo_grid = await _event_contract(http, event_ticker, str(spec["combo"])) if event_ticker else None
    if combo_grid is None or not _grid_legal_for_q1(combo_grid):
        result["reason"] = "COMBO_Q1_GRID_NOT_LIVE"
        return result
    hedge_grid = []
    legs_by_symbol = {}
    for leg in body.get("legs", []) if isinstance(body.get("legs"), list) else []:
        if isinstance(leg, Mapping) and isinstance(leg.get("contract"), Mapping):
            lc = leg["contract"]
            legs_by_symbol[str(lc.get("instrumentSymbol") or "")] = lc
    for hedge in spec["hedges"]:
        sym = str(hedge["symbol"])
        lc = legs_by_symbol.get(sym)
        if lc is None:
            result["reason"] = "HEDGE_SYMBOL_NOT_CANONICAL_COMPONENT"
            return result
        event = str(lc.get("eventTicker") or "")
        grid = await _event_contract(http, event, sym) if event else None
        hedge_grid.append({"symbol": sym, "grid_ok": bool(grid and _grid_legal_for_q1(grid))})
        if grid is None or not _grid_legal_for_q1(grid):
            result["reason"] = "HEDGE_Q1_GRID_NOT_LIVE"
            result["hedge_grid"] = hedge_grid
            return result
    result.update({"pass": True, "reason": "SAME_GEMINI_CANONICAL_PRODUCT_PLUS_EXACT_COMPONENT_COMPLEMENTS", "hedge_grid": hedge_grid})
    return result


async def _boot_preflight(campaign: str) -> dict[str, Any]:
    symbols = {str(x["combo"]) for x in CANDIDATES}
    for spec in CANDIDATES:
        symbols.update(str(x["symbol"]) for x in spec["hedges"])
    out: dict[str, Any] = {"pass": False, "campaign": campaign}
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
    positions_http, positions = await _relevant_positions(symbols)
    out["positions_http"] = positions_http
    out["relevant_position_count"] = len(positions)
    active_ok, active = await _active_orders_for_symbols(symbols)
    out["active_order_scan_ok"] = active_ok
    out["relevant_active_order_count"] = len(active)
    async with httpx.AsyncClient(timeout=8.0) as http:
        certs = [await _certify_candidate(http, spec) for spec in CANDIDATES]
    out["certifications"] = certs
    out["certified_count"] = sum(1 for x in certs if x.get("pass"))
    out["pass"] = bool(
        out["terms_accepted"] and balance_http == 200 and usd >= MIN_AVAILABLE_USD
        and positions_http == 200 and not positions and active_ok and not active
        and out["certified_count"] == len(CANDIDATES)
    )
    return out


async def _fresh_economics(http: httpx.AsyncClient, spec: Mapping[str, Any]) -> dict[str, Any]:
    symbols = [str(spec["combo"]), *(str(x["symbol"]) for x in spec["hedges"])]
    rows = await asyncio.gather(*(_book(http, sym) for sym in symbols))
    received = [x[2] for x in rows]
    skew_ms = D(max(received) - min(received)) / D("1000000") if received else D("999999")
    combo_http, combo_book, _ = rows[0]
    combo_top = c._outcome_ask(combo_book, "YES")
    hedge_rows = []
    hedge_all_in = D("0")
    hedge_ok = True
    for hedge, (status, book, _) in zip(spec["hedges"], rows[1:]):
        top = c._outcome_ask(book, str(hedge["outcome"]))
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
        "candidate": spec["name"], "combo": spec["combo"], "combo_http": combo_http,
        "quote_skew_ms": str(skew_ms), "hedges": hedge_rows, "hedge_all_in": str(hedge_all_in),
        "pass": False,
    }
    if combo_http != 200 or combo_top is None or not hedge_ok or skew_ms > MAX_FINAL_SKEW_MS:
        out["reason"] = "NO_FRESH_EXECUTABLE_THREE_LEG_BOOK"
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
            r = await h.post("https://api.pushover.net/1/messages.json", data={"user": user, "token": token, "title": title[:250], "message": message[:1024], "priority": str(priority)})
        return r.status_code == 200
    except Exception:
        return False


async def _sell_outcome_fok(session: GeminiTradingSession, http: httpx.AsyncClient, *, symbol: str, outcome: str, client_id: str) -> dict[str, Any]:
    status, book, _ = await _book(http, symbol)
    bid = _outcome_bid(book, outcome) if status == 200 else None
    if bid is None or bid[1] < QTY:
        return {"status": "FLATTEN_NO_Q1_BID", "symbol": symbol, "outcome": outcome}
    p, depth = bid
    params = {"symbol": symbol, "side": "SELL", "type": "LIMIT", "timeInForce": "FOK", "price": str(p), "quantity": "1", "eventOutcome": outcome, "clientOrderId": client_id[:36]}
    result = await session.place_fok(params, terminal_timeout=3.0)
    return {"status": "FLATTEN_ATTEMPT", "symbol": symbol, "outcome": outcome, "bid": str(p), "depth": str(depth), "result": result.as_dict()}


async def _execute_candidate(session: GeminiTradingSession, http: httpx.AsyncClient, spec: Mapping[str, Any], econ: Mapping[str, Any], *, campaign: str, attempt: int) -> dict[str, Any]:
    cid_base = f"{campaign}-{attempt}"
    combo_params = {
        "symbol": spec["combo"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
        "price": str(econ["combo_ask"]), "quantity": "1", "eventOutcome": "YES",
        "clientOrderId": f"{cid_base}-c"[:36],
    }
    combo = await session.place_fok(combo_params, terminal_timeout=3.0)
    out: dict[str, Any] = {"candidate": spec["name"], "economics": dict(econ), "combo": combo.as_dict(), "classification": "ATTEMPTED"}
    cq = c._d(combo.filled_quantity) or D("0")
    if combo.status == "UNKNOWN":
        out["classification"] = "LIVE_HARD_HALT_AMBIGUOUS_COMBO"
        return out
    if cq == 0:
        out["classification"] = "COMBO_FOK_NO_FILL_ZERO_HEDGE_COST"
        return out
    if combo.status != "FILLED" or cq != QTY:
        out["classification"] = "LIVE_HARD_HALT_UNEXPECTED_PARTIAL_FOK"
        return out

    # The final refresh price is the maximum allowed hedge price. If either hedge moved
    # worse, FOK cancels instead of silently spending the guaranteed edge.
    hedge_params = []
    for idx, (hedge, hrow) in enumerate(zip(spec["hedges"], econ["hedges"]), start=1):
        hedge_params.append({
            "symbol": hedge["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
            "price": str(hrow["ask"]), "quantity": "1", "eventOutcome": hedge["outcome"],
            "clientOrderId": f"{cid_base}-h{idx}"[:36],
        })
    h1, h2 = await asyncio.gather(session.place_fok(hedge_params[0], terminal_timeout=3.0), session.place_fok(hedge_params[1], terminal_timeout=3.0))
    out["hedges"] = [h1.as_dict(), h2.as_dict()]
    ok = [x.status == "FILLED" and (c._d(x.filled_quantity) or D("0")) == QTY for x in (h1, h2)]
    if all(ok):
        out["classification"] = "FILLED_NOT_SETTLED"
        await _send_pushover("Sports arb filled — awaiting settlement", f"{spec['name']}\nGemini same-venue q1 combo + exact complements filled.\nModeled lock before execution: ${econ['locked']}\nClassification: FILLED_NOT_SETTLED", 0)
        return out

    # Recovery after any hedge failure. Flatten confirmed hedge fills first, then combo.
    recovery = []
    for idx, (filled, hedge) in enumerate(zip(ok, spec["hedges"]), start=1):
        if filled:
            recovery.append(await _sell_outcome_fok(session, http, symbol=str(hedge["symbol"]), outcome=str(hedge["outcome"]), client_id=f"{cid_base}-fh{idx}"))
    recovery.append(await _sell_outcome_fok(session, http, symbol=str(spec["combo"]), outcome="YES", client_id=f"{cid_base}-fc"))
    out["recovery"] = recovery
    out["classification"] = "LIVE_HARD_HALT_HEDGE_FAILURE_RECOVERY_ATTEMPTED"
    await _send_pushover("Sports arb hard halt", f"{spec['name']} combo filled but a hedge failed. Recovery attempted; manual reconciliation required.", 1)
    return out


async def run() -> dict[str, Any]:
    campaign = os.getenv("GEMINI_COMBO_FOK_CAMPAIGN", "").strip()
    arm = os.getenv("GEMINI_COMBO_FOK_ARM_CAMPAIGN", "").strip()
    enabled = os.getenv("ENABLE_GEMINI_COMBO_FOK_LIVE", "").strip().upper() == "YES"
    deadline = _parse_deadline(os.getenv("GEMINI_COMBO_FOK_ARM_NOT_AFTER_UTC", ""))
    deadline_ok = bool(deadline and datetime.now(timezone.utc) <= deadline)
    armed = bool(enabled and campaign and arm == campaign and deadline_ok)
    preflight = await _boot_preflight(campaign)
    out: dict[str, Any] = {
        "status": "GEMINI_COMBO_MM_FOK_LIVE_V1", "campaign": campaign, "armed": armed,
        "deadline_ok": deadline_ok, "preflight": preflight, "target_locked": str(TARGET),
        "max_final_skew_ms": str(MAX_FINAL_SKEW_MS), "max_attempts": MAX_ATTEMPTS,
        "max_executed_trades": MAX_EXECUTED, "order_entry": False, "realized": False,
    }
    if not armed:
        out["classification"] = "DISARMED_PREFLIGHT_PASS" if preflight.get("pass") else "DISARMED_PREFLIGHT_BLOCKED"
        emit("GEMINI_COMBO_MM_FOK_LIVE_V1", out)
        return out
    if not preflight.get("pass"):
        out["classification"] = "LIVE_GATE_BLOCKED"
        emit("GEMINI_COMBO_MM_FOK_LIVE_V1", out)
        return out

    session = GeminiTradingSession()
    out["order_entry"] = True
    attempts = executed = 0
    started = time.monotonic()
    last_heartbeat = 0.0
    try:
        await session.connect()
        async with httpx.AsyncClient(timeout=5.0) as http:
            while time.monotonic() - started < WATCH_SECONDS and attempts < MAX_ATTEMPTS and executed < MAX_EXECUTED:
                # Cheap discovery: poll only four combo books. Hedge books are fetched only
                # after a real q1 combo YES ask exists.
                combo_rows = await asyncio.gather(*(_book(http, str(spec["combo"])) for spec in CANDIDATES))
                potential = []
                for spec, (status, book, _) in zip(CANDIDATES, combo_rows):
                    top = c._outcome_ask(book, "YES") if status == 200 else None
                    if top is not None and top[1] >= QTY:
                        potential.append((spec, top[0]))
                potential.sort(key=lambda x: x[1])
                for spec, _ in potential:
                    econ = await _fresh_economics(http, spec)
                    if not econ.get("pass"):
                        continue
                    # Final account guard immediately before the first live send.
                    symbols = {str(spec["combo"]), *(str(x["symbol"]) for x in spec["hedges"])}
                    ph, pos = await _relevant_positions(symbols)
                    active_ok, active = await _active_orders_for_symbols(symbols)
                    if ph != 200 or pos or not active_ok or active:
                        out["classification"] = "LIVE_HARD_HALT_ACCOUNT_STATE_CHANGED"
                        out["final_positions_count"] = len(pos)
                        out["final_active_orders_count"] = len(active)
                        emit("GEMINI_COMBO_MM_FOK_LIVE_V1", out)
                        return out
                    attempts += 1
                    emit("GEMINI_COMBO_MM_FOK_EXECUTABLE", {"campaign": campaign, "attempt": attempts, "candidate": spec["name"], "economics": econ, "classification": "EXECUTABLE", "realized": False})
                    result = await _execute_candidate(session, http, spec, econ, campaign=campaign, attempt=attempts)
                    emit("GEMINI_COMBO_MM_FOK_ATTEMPT", {"campaign": campaign, "attempt": attempts, **result, "realized": False})
                    if result.get("classification") == "FILLED_NOT_SETTLED":
                        executed += 1
                        break
                    if str(result.get("classification") or "").startswith("LIVE_HARD_HALT"):
                        out["classification"] = result["classification"]
                        out["last_attempt"] = result
                        return out
                now = time.monotonic()
                if now - last_heartbeat >= 60:
                    emit("GEMINI_COMBO_MM_FOK_HEARTBEAT", {"campaign": campaign, "seconds_alive": round(now - started, 1), "potential_combo_asks": len(potential), "attempts": attempts, "executed": executed, "order_entry": True, "realized": False})
                    last_heartbeat = now
                if executed >= MAX_EXECUTED or attempts >= MAX_ATTEMPTS:
                    break
                await asyncio.sleep(POLL_SECONDS)
    finally:
        await session.close()

    out["attempts"] = attempts
    out["executed"] = executed
    out["classification"] = "FILLED_NOT_SETTLED" if executed else ("WATCH_ATTEMPT_CAP_REACHED_NO_EXECUTION" if attempts >= MAX_ATTEMPTS else "WATCH_WINDOW_ENDED_NO_EXECUTION")
    emit("GEMINI_COMBO_MM_FOK_LIVE_V1", out)
    return out


if __name__ == "__main__":
    asyncio.run(run())
