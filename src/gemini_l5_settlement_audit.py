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
        out["status"] = "UNRESOLVED"          # loud flag: fills, window over,
        return out                            # no settled rows, no open pos
    if window_over:
        out["status"] = "PENDING_SETTLEMENT"
        return out
    out["status"] = "MATCHED_UNSETTLED" if matched else "FILLED_UNSETTLED"
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
        settled = _rows(settled_body, "positions")
        wend = event_window_end_epoch(ev)
        window_over = (wend is not None and now > wend + 120)
        row = classify_event(ev_fills, settled, open_by_event.get(ev, []),
                             window_over)
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
