"""READ-ONLY Gemini prediction-market settlement/cashflow audit (issue #167).

Purpose: classify every recent filled campaign strictly as
FILLED -> MATCHED -> SETTLED -> REALIZED, from source evidence only.

OBSERVABILITY RULE (the reason this module exists): `available_usd`
moving and `positions=[]` are NEVER settlement proof. Balanced YES+NO
inventory can disappear from the open-positions view by venue netting
long before settlement rows exist, and balance moves for many reasons.
A campaign is SETTLED only when `/v1/prediction-markets/positions/settled`
returns rows covering its filled quantity, and REALIZED only when those
rows carry consistent payout/costBasis (and, where present, netProfit)
economics. This module prints those source rows; it infers nothing.

Authoritative read surfaces (all POST, private, read-only):
  /v1/prediction-markets/orders/history   status=filled|cancelled - exact
      fills: clientOrderId, filledQuantity, avgExecutionPrice, fee fields
  /v1/prediction-markets/positions        open (unsettled) inventory
  /v1/prediction-markets/positions/settled  per eventTicker - the ONLY
      settlement authority: costBasis, payout, netProfit per outcome
  /v1/transfers                           account cashflow cross-check

Classification per (eventTicker, campaign):
  FILLED    exact fills exist in orders/history
  MATCHED   filled YES qty == filled NO qty > 0 (pair economics locked,
            NOT settled, NOT realized)
  SETTLED   settled-position rows cover the filled quantities
  REALIZED  SETTLED and payout/cost economics consistent (binary payout
            in {0, qty}); realized cash = payout - costBasis summed
  UNRESOLVED window long over, fills exist, but no settled rows AND no
            open position - flagged loudly for manual follow-up
  PENDING   window over, no settled rows yet, open position still shown
            (or window not yet over)

Netting-vs-settlement: whether a matched YES+NO pair vanishing from open
positions is venue NETTING (early $1 credit) or ordinary settlement is
answered empirically by this audit: if settled rows appear only after the
window closes while open positions emptied at match time, it is netting;
the report prints both timestamps side by side rather than assuming.

Runs on the credentialed host; fails closed with NO_CREDENTIALS anywhere
else. No mutation of any kind: no orders, no cancels, nothing armed.

    python3 -m src.gemini_l5_settlement_audit --days 3
    python3 -m src.gemini_l5_settlement_audit --event BTC05M2609021210   # probe #4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src.gemini_prediction_auth import has_credentials, private_rest_request

ZERO = D("0")
YES_NO = ("yes", "no")
GEMI_RE = re.compile(r"^GEMI-([A-Z0-9]+)-(UP|DOWN|YES|NO)$|^GEMI-(.+)$")


def _f(row: Mapping[str, Any], *keys: str, default: str = "0") -> D:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return D(str(v))
    return D(default)


def _s(row: Mapping[str, Any], *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


async def _post(path: str, params: Dict[str, Any]) -> Any:
    r = await private_rest_request("POST", path, params=params, timeout=10.0)
    if r.status_code != 200:
        return {"_http": r.status_code, "_body": r.text[:300]}
    try:
        return r.json()
    except Exception:
        return {"_http": r.status_code, "_body": r.text[:300]}


def _rows(body: Any, *keys: str) -> List[dict]:
    if isinstance(body, list):
        return [dict(r) for r in body if isinstance(r, Mapping)]
    if isinstance(body, Mapping):
        for k in keys:
            raw = body.get(k)
            if isinstance(raw, list):
                return [dict(r) for r in raw if isinstance(r, Mapping)]
    return []


# ---------------- settled-row identity filtering (AWS bug fix) ----------------
# Observed live: /v1/prediction-markets/positions/settled returns MIXED
# events even when called with eventTicker. Every settled row must
# therefore be locally re-verified against the exact target event before
# it can count as settlement evidence. Rows whose identity cannot be
# confirmed are AMBIGUOUS and fail closed (never counted as settled).

def _row_event_identity(row: Mapping[str, Any]) -> str:
    meta = row.get("contractMetadata")
    if isinstance(meta, Mapping):
        ev = _s(meta, "eventTicker", "event_ticker")
        if ev:
            return ev
    ev = _s(row, "eventTicker", "event_ticker")
    if ev:
        return ev
    sym = _s(row, "symbol", "instrumentSymbol", "instrument_symbol")
    if sym.startswith("GEMI-"):
        core = sym[5:]
        for suffix in ("-UP", "-DOWN", "-YES", "-NO"):
            if core.endswith(suffix):
                return core[: -len(suffix)]
        return core
    return ""


def filter_settled_rows(rows: List[dict],
                        event_ticker: str) -> Dict[str, Any]:
    """Split settled rows into exact-target / unrelated / ambiguous.

    exact: identity == event_ticker via contractMetadata.eventTicker or an
           instrument/event identifier that resolves to the same event.
    unrelated: identity confirms a DIFFERENT event (dropped, counted).
    ambiguous: no confirmable identity — fail-closed (their presence means
           settlement cannot be asserted from this response).
    """
    exact: List[dict] = []
    unrelated = ambiguous = 0
    for row in rows:
        ident = _row_event_identity(row)
        if not ident:
            ambiguous += 1
        elif ident == event_ticker:
            exact.append(row)
        else:
            unrelated += 1
    return {"exact": exact, "unrelated_dropped": unrelated,
            "ambiguous": ambiguous}


# ---------------- pure classification (unit-testable) ----------------

def classify_event(fills: List[dict], settled: List[dict],
                   open_positions: List[dict],
                   window_over: bool) -> Dict[str, Any]:
    """Strict source-evidence classification for one eventTicker."""
    yes_q = sum((_f(r, "filledQuantity", "filled_quantity") for r in fills
                 if _s(r, "eventOutcome", "outcome").lower() == "yes"), ZERO)
    no_q = sum((_f(r, "filledQuantity", "filled_quantity") for r in fills
                if _s(r, "eventOutcome", "outcome").lower() == "no"), ZERO)
    filled_total = yes_q + no_q
    if filled_total == ZERO:
        return {"status": "NO_FILLS", "yes_qty": str(yes_q), "no_qty": str(no_q)}

    matched = yes_q > ZERO and yes_q == no_q
    settled_qty = sum((_f(r, "positionQuantity", "position_quantity",
                          "totalQuantity", "total_quantity")
                       for r in settled), ZERO)
    open_qty = sum((_f(r, "positionQuantity", "position_quantity",
                       "totalQuantity", "total_quantity")
                    for r in open_positions), ZERO)

    out: Dict[str, Any] = {
        "yes_qty": str(yes_q), "no_qty": str(no_q),
        "matched": matched,
        "settled_rows": len(settled), "settled_qty": str(settled_qty),
        "open_qty": str(open_qty), "window_over": window_over,
    }
    if settled and settled_qty >= filled_total:
        payout = sum((_f(r, "payout") for r in settled), ZERO)
        cost = sum((_f(r, "costBasis", "cost_basis") for r in settled), ZERO)
        net = sum((_f(r, "netProfit", "net_profit") for r in settled), ZERO)
        binary_ok = all(
            _f(r, "payout") in (ZERO, _f(r, "positionQuantity",
                                         "position_quantity",
                                         "totalQuantity", "total_quantity"))
            for r in settled)
        out.update({"status": "REALIZED" if binary_ok else "SETTLED",
                    "payout": str(payout), "cost_basis": str(cost),
                    "net_profit": str(net),
                    "realized_cash": str(payout - cost) if binary_ok else None,
                    "binary_payout_consistent": binary_ok})
        return out
    if settled:
        out["status"] = "SETTLED_PARTIAL"
        return out
    if window_over and open_qty == ZERO:
        if matched:
            # VENUE SEMANTICS (Gemini docs): /positions/settled returns
            # positions HELD UNTIL RESOLUTION. A fully matched YES+NO pair
            # the venue nets/closes before resolution legitimately never
            # appears there. A matched q1 pair carries NO residual
            # liability (both legs sum to a guaranteed $1 whichever side
            # wins), so this is not the loud UNRESOLVED alarm — but it is
            # NOT settled and NOT realized either. Confirmation requires
            # source-accounted netting evidence (WS fills incl fee `n`,
            # balance `c` updates, or transfer rows) via
            # apply_netting_evidence(); balance drift alone never counts.
            out["status"] = "MATCHED_PAIR_NO_SETTLED_ROWS"
            out["residual_liability"] = False
            out["netting_note"] = ("doc-consistent with pre-resolution "
                                   "venue netting; awaiting source-"
                                   "accounted netting evidence")
            return out
        out["status"] = "UNRESOLVED"          # unmatched: loud flag, halts
        out["residual_liability"] = True
        return out
    if window_over:
        out["status"] = "PENDING_SETTLEMENT"
        return out
    out["status"] = "MATCHED_UNSETTLED" if matched else "FILLED_UNSETTLED"
    return out


# ------------- matched-pair netting reconciliation (source-accounted) -------

def apply_netting_evidence(classified: Dict[str, Any], fills: List[dict],
                           evidence: Mapping[str, Any]) -> Dict[str, Any]:
    """Upgrade MATCHED_PAIR_NO_SETTLED_ROWS using EXACT netting evidence.

    evidence = {
      "ws_fills":        [{clientOrderId, n(fee), m(maker), price, qty}, ...]
      "balance_updates": [{"c": confirmed_balance, "ts": ...}, ...]  (>=2:
                          bracketing the pair, authenticated WS `c` field)
      "transfers":       optional venue transfer rows for the credit
    }
    MATCHED_NETTED_CONFIRMED requires ALL of:
      * exact pair fills (already in `classified`)
      * WS fill rows covering every clientOrderId of the pair, with fee `n`
      * confirmed-balance (`c`) delta across the pair equal to
        matched_qty * $1 - pair cost - sum(fees) (exact, cent tolerance)
    Anything less leaves MATCHED_NETTING_UNCONFIRMED (safe to continue —
    zero residual liability — but never realized from balance alone).
    Even when confirmed, the status is NOT the project's REALIZED label:
    finality of venue netting as economic settlement is recorded as
    netting_final=True evidence for the operator's accounting decision.
    """
    out = dict(classified)
    if out.get("status") != "MATCHED_PAIR_NO_SETTLED_ROWS":
        return out
    ws_fills = list(evidence.get("ws_fills") or [])
    balances = list(evidence.get("balance_updates") or [])
    pair_ids = {_s(r, "clientOrderId", "client_order_id") for r in fills}
    ws_ids = {_s(r, "clientOrderId", "client_order_id") for r in ws_fills}
    if not pair_ids or not pair_ids.issubset(ws_ids) or len(balances) < 2:
        out["status"] = "MATCHED_NETTING_UNCONFIRMED"
        out["netting_evidence"] = {"ws_fill_ids_seen": sorted(ws_ids & pair_ids),
                                   "balance_updates": len(balances)}
        return out
    fees = sum((_f(r, "n", "fee", "feeAmount") for r in ws_fills
                if _s(r, "clientOrderId", "client_order_id") in pair_ids), ZERO)
    cost = sum((_f(r, "price") * _f(r, "quantity", "qty", default="1")
                for r in ws_fills
                if _s(r, "clientOrderId", "client_order_id") in pair_ids), ZERO)
    matched_qty = min(D(str(out.get("yes_qty", "0"))),
                      D(str(out.get("no_qty", "0"))))
    expected_credit = matched_qty * D("1") - cost - fees
    observed_delta = (_f(balances[-1], "c", "confirmed", "balance")
                      - _f(balances[0], "c", "confirmed", "balance"))
    consistent = abs(observed_delta - expected_credit) <= D("0.01")
    out["netting_evidence"] = {
        "ws_fee_total": str(fees), "ws_pair_cost": str(cost),
        "expected_credit": str(expected_credit),
        "observed_confirmed_balance_delta": str(observed_delta),
        "consistent": consistent,
    }
    if consistent:
        out["status"] = "MATCHED_NETTED_CONFIRMED"
        out["netting_final"] = True
        out["netted_cash_source_accounted"] = str(observed_delta)
    else:
        # contradictory evidence is a HALT condition, not a safe-continue
        out["status"] = "NETTING_CASHFLOW_INCONSISTENT"
        out["netting_note"] = ("confirmed-balance delta contradicts pair "
                               "economics — halt and investigate")
    return out


def event_window_end_epoch(event_ticker: str) -> Optional[float]:
    """BTC05M2609021210 -> window start 26-09-02 12:10 UTC + 5m."""
    m = re.match(r"^[A-Z]+?(\d{2})M(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$",
                 event_ticker)
    if not m:
        return None
    dur, yy, mm, dd, hh, mi = (int(g) for g in m.groups())
    try:
        start = datetime(2000 + yy, mm, dd, hh, mi,
                         tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    return start + dur * 60


# ---------------- audit runner ----------------

async def audit(days: float, event_filter: str = "") -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    frm = now_ms - int(days * 86400 * 1000)
    fills_body = await _post("/v1/prediction-markets/orders/history",
                             {"status": "filled", "limit": 500,
                              "from": frm, "to": now_ms + 1000})
    fills = _rows(fills_body, "orders")
    open_body = await _post("/v1/prediction-markets/positions", {"limit": 200})
    open_rows = _rows(open_body, "positions")
    transfers_body = await _post("/v1/transfers", {"limit_transfers": 50})
    transfers = _rows(transfers_body, "transfers")

    by_event: Dict[str, List[dict]] = defaultdict(list)
    for r in fills:
        ev = _s(r, "eventTicker", "event_ticker")
        if not ev:
            sym = _s(r, "symbol")
            ev = sym.split("-")[1] if sym.startswith("GEMI-") and "-" in sym[5:] else sym
        if event_filter and event_filter not in ev:
            continue
        by_event[ev].append(r)

    open_by_event: Dict[str, List[dict]] = defaultdict(list)
    for r in open_rows:
        open_by_event[_s(r, "eventTicker", "event_ticker", "symbol")].append(r)

    events: Dict[str, Any] = {}
    unresolved: List[str] = []
    now = time.time()
    for ev, ev_fills in sorted(by_event.items()):
        settled_body = await _post("/v1/prediction-markets/positions/settled",
                                   {"eventTicker": ev, "limit": 100,
                                    "offset": 0})
        settled_raw = _rows(settled_body, "positions")
        # AWS-observed bug: the endpoint returns mixed events despite the
        # eventTicker param. Locally verify every row's identity; classify
        # with exact-target rows only, fail closed on ambiguity.
        filt = filter_settled_rows(settled_raw, ev)
        settled = filt["exact"]
        wend = event_window_end_epoch(ev)
        window_over = (wend is not None and now > wend + 120)
        if filt["ambiguous"] > 0:
            row = {"status": "AMBIGUOUS_SETTLEMENT_IDENTITY",
                   "ambiguous_rows": filt["ambiguous"],
                   "unrelated_dropped": filt["unrelated_dropped"],
                   "note": ("settled response contained rows with no "
                            "confirmable event identity; settlement cannot "
                            "be asserted — fail closed")}
        else:
            row = classify_event(ev_fills, settled, open_by_event.get(ev, []),
                                 window_over)
            row["settled_identity_filter"] = {
                "exact": len(settled),
                "unrelated_dropped": filt["unrelated_dropped"],
                "ambiguous": 0}
        row["fills"] = [{
            "clientOrderId": _s(r, "clientOrderId", "client_order_id"),
            "outcome": _s(r, "eventOutcome", "outcome"),
            "filled_qty": str(_f(r, "filledQuantity", "filled_quantity")),
            "exec_price": _s(r, "avgExecutionPrice", "avg_execution_price",
                             "price"),
            "fee_fields": {k: str(v) for k, v in r.items()
                           if "fee" in k.lower() and v not in (None, "")},
            "ts": _s(r, "timestamp", "updatedAt", "created_at"),
        } for r in ev_fills]
        row["settled_rows_raw"] = settled[:4]
        events[ev] = row
        if row["status"] == "UNRESOLVED":
            unresolved.append(ev)

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "observability_rule": ("available_usd and positions=[] are NEVER "
                               "settlement proof; only positions/settled "
                               "rows with consistent payout/cost realize"),
        "days_scanned": days,
        "events": events,
        "unresolved_flagged": unresolved,
        "recent_transfers_sample": transfers[:10],
        "open_positions_now": open_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--event", default="",
                    help="filter to one eventTicker substring, e.g. "
                         "BTC05M2609021210 for probe #4")
    args = ap.parse_args()
    if not has_credentials():
        print(json.dumps({"verdict": "NO_CREDENTIALS",
                          "note": "run on the credentialed host (read-only)"}))
        return 2
    out = asyncio.run(audit(args.days, args.event))
    print(json.dumps(out, indent=1, default=str))
    summary = {ev: r["status"] for ev, r in out["events"].items()}
    print("STATUS SUMMARY:", json.dumps(summary, indent=1))
    if out["unresolved_flagged"]:
        print("!! UNRESOLVED (fills, window over, no settled rows, no open "
              "position):", out["unresolved_flagged"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
