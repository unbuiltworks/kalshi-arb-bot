"""P1: READ-ONLY authenticated-fill read for ONE historical CI15M-4 order.

WHAT THIS IS FOR. The CI15M-4 forensics pass established the order's final
status, filled quantity, execution price and settlement from the
order-history surface, but that script does not call /v1/mytrades, so three
things stayed UNKNOWN: the authenticated trade rows, the ACTUAL VENUE FEE,
and any attributable cashflow. This reads those surfaces with the EXISTING
proven primitive (`fill_reconciliation.authoritative_fills`, which joins
order history to mytrades on order_id and takes the fee from `fee_amount`)
and reports whatever is actually there.

IT SUBMITS NOTHING, CANCELS NOTHING AND MUTATES NO COHORT. It takes no
authorization and holds no order transport. It does not call
`ci15m_probe.authenticated_fills`, because that function HALTs a cohort on
an unreadable surface -- correct for a live executor, wrong for a read of a
historical order in a cohort that is already halted and must stay exactly
as it is.

AN ABSENT FEE IS UNKNOWN, NEVER ZERO, and a 400 from mytrades is a fact
about the endpoint rather than evidence of no trades: the forensics script
measured mytrades answering 400 for long-delisted prediction symbols across
nine Aug/Sep symbols, so a refusal here is reported as a refusal.

NOTHING HERE IS REALIZED P&L. The payout reconstruction is arithmetic on
authenticated fills and the venue's own resolution; CASH_CONFIRMED requires
attributable cash movement, and its absence is reported as absence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

LABEL = "CI15M4_AUTHENTICATED_FILL_READ_V1"

ORDER_ID = "145828836725658335"
SYMBOL = "GEMI-BTC15M2610032145-UP"


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


async def _mytrades(symbol: str, limit: int = 500) -> Dict[str, Any]:
    """`/v1/mytrades` for one symbol. `rows` is None iff UNREADABLE."""
    from src.gemini_prediction_auth import private_rest_request
    try:
        r = await private_rest_request(
            "POST", "/v1/mytrades",
            params={"symbol": symbol, "limit_trades": limit}, timeout=30.0)
    except Exception as exc:                                   # noqa: BLE001
        return {"rows": None, "http": None, "why": "raised %s" % exc}
    http = getattr(r, "status_code", None)
    if http != 200:
        body = None
        try:
            body = r.json()
        except Exception:                                      # noqa: BLE001
            body = str(getattr(r, "text", ""))[:300]
        return {"rows": None, "http": http,
                "why": "mytrades returned %s" % http, "detail": body}
    try:
        body = r.json()
    except ValueError:
        return {"rows": None, "http": http, "why": "body was not JSON"}
    rows = body.get("trades") if isinstance(body, Mapping) else body
    if not isinstance(rows, list):
        return {"rows": None, "http": http,
                "why": "payload carried no trade list"}
    return {"rows": rows, "http": http, "why": None}


async def _balances() -> Dict[str, Any]:
    """Cash anchor NOW. It can never attribute a historical settlement by
    itself -- a balance is a level, not a cashflow -- so it is reported as
    an anchor and explicitly not as proof."""
    from src.gemini_prediction_auth import private_rest_request
    try:
        r = await private_rest_request("POST", "/v1/balances", params={},
                                       timeout=30.0)
    except Exception as exc:                                   # noqa: BLE001
        return {"readable": False, "why": "raised %s" % exc}
    if getattr(r, "status_code", None) != 200:
        return {"readable": False, "http": r.status_code,
                "why": "balances returned %s" % r.status_code}
    try:
        rows = r.json()
    except ValueError:
        return {"readable": False, "why": "body was not JSON"}
    keep: List[Dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping) and str(
                    row.get("currency", "")).upper() == "USD":
                keep.append({k: row.get(k) for k in
                             ("currency", "amount", "available")})
    return {"readable": True, "usd_rows": keep,
            "labels": "A CASH ANCHOR, not a cashflow. A level read today "
                      "cannot attribute a settlement that happened "
                      "yesterday; it is recorded so a later reading can "
                      "difference it, never as CASH_CONFIRMED."}


async def capture(order_id: str, symbol: str) -> Dict[str, Any]:
    from src.gemini_execution import fill_reconciliation as FR
    from src.gemini_execution import metadata as MD
    from src.gemini_prediction_auth import has_credentials

    out: Dict[str, Any] = {"label": LABEL, "order_id": order_id,
                           "symbol": symbol}
    if not has_credentials():
        out["readable"] = False
        out["why"] = "NO_CREDENTIALS"
        return out

    # --- 1. the order row, from the surface that carries the OUTCOME
    try:
        hist = await MD.order_history(symbol=symbol,
                                      statuses=("filled", "cancelled"),
                                      limit=100, include_rows=True)
    except Exception as exc:                                   # noqa: BLE001
        hist = {"readable": False, "verdict": "RAISED", "why": str(exc)}
    orders_seen: List[Mapping[str, Any]] = []
    for e in (hist.get("per_status") or []) if isinstance(hist, Mapping) else []:
        if isinstance(e, Mapping) and isinstance(e.get("orders"), list):
            orders_seen.extend(r for r in e["orders"]
                               if isinstance(r, Mapping))
    ours = [r for r in orders_seen if str(r.get("orderId")) == str(order_id)]
    out["order_history"] = {
        "readable": bool(isinstance(hist, Mapping) and hist.get("readable")),
        "verdict": (hist or {}).get("verdict"),
        "rows_for_this_order": len(ours),
        "raw_rows": ours,                      # PRESERVED VERBATIM
    }

    # --- 2. the authenticated trade record
    tr = await _mytrades(symbol)
    out["mytrades"] = {"http": tr.get("http"), "readable": tr["rows"] is not None,
                       "why": tr.get("why"), "detail": tr.get("detail"),
                       "n_rows": (None if tr["rows"] is None
                                  else len(tr["rows"])),
                       "raw_rows": tr["rows"]}   # PRESERVED VERBATIM

    # --- 3. the PROVEN join, only when both surfaces are readable
    if ours and tr["rows"] is not None:
        auth = FR.authoritative_fills({"orders_seen": orders_seen,
                                       "trades": tr["rows"]})
        out["authoritative_fills"] = auth
        mine = [r for r in (auth.get("rows") or [])
                if str(r.get("order_id")) == str(order_id)]
        out["fills_for_this_order"] = mine
        fees = [r.get("fee_amount") for r in mine]
        missing = [f for f in fees if f is None]
        total = sum((_d(f) or D(0)) for f in fees if f is not None)
        out["fee"] = {
            "n_fills": len(mine),
            "fee_amounts": fees,
            "total_usd": (None if missing else str(total)),
            "authoritative_for_every_fill": (bool(mine) and not missing),
            "source": "/v1/mytrades fee_amount",
            "labels": ("an absent fee_amount is UNKNOWN, never zero, and "
                       "the fee model is NEVER substituted when "
                       "authenticated data exists"),
        }
    else:
        out["authoritative_fills"] = None
        out["fee"] = {"total_usd": None, "authoritative_for_every_fill": False,
                      "why": ("the join needs BOTH a matching order row and "
                              "a readable mytrades response; one or both "
                              "was absent"),
                      "labels": "UNKNOWN, never zero"}

    # --- 4. settlement, from the contract metadata on the order row
    cm = (ours[0].get("contractMetadata") if ours
          and isinstance(ours[0].get("contractMetadata"), Mapping) else {})
    strike = _d((cm.get("strike") or {}).get("value")
                if isinstance(cm.get("strike"), Mapping) else None)
    settled = _d(cm.get("settlementValue"))
    side = cm.get("resolutionSide")
    out["settlement"] = {
        "contract_status": cm.get("contractStatus"),
        "expiry": cm.get("expiryDate"), "resolved_at": cm.get("resolvedAt"),
        "strike": (None if strike is None else str(strike)),
        "settlement_value": (None if settled is None else str(settled)),
        "resolution_side": side,
        "delta_settlement_minus_strike": (
            None if (strike is None or settled is None)
            else str(settled - strike)),
    }

    # --- 5. payout reconstruction, from AUTHENTICATED fills only
    q = px = None
    if out.get("fills_for_this_order"):
        q = sum((_d(r.get("qty")) or D(0))
                for r in out["fills_for_this_order"])
        spend = sum((_d(r.get("price")) or D(0)) * (_d(r.get("qty")) or D(0))
                    for r in out["fills_for_this_order"])
        px = (spend / q) if q else None
    elif ours:
        q = _d(ours[0].get("filledQuantity"))
        px = _d(ours[0].get("avgExecutionPrice") or ours[0].get("price"))
    held = str((ours[0].get("outcome") if ours else "") or "").lower()
    won = (side is not None and held == str(side).lower())
    principal = (None if (q is None or px is None) else q * px)
    payout = (None if q is None else (q if won else D(0)))
    fee_total = _d((out.get("fee") or {}).get("total_usd"))
    out["payout_reconstruction"] = {
        "held_outcome": held or None, "resolution_side": side,
        "won": (None if side is None else won),
        "filled_qty": (None if q is None else str(q)),
        "avg_price": (None if px is None else str(px)),
        "principal_usd": (None if principal is None else str(principal)),
        "payout_usd": (None if payout is None else str(payout)),
        "gross_before_fee_usd": (None if (principal is None or payout is None)
                                 else str(payout - principal)),
        "net_after_authenticated_fee_usd": (
            None if (principal is None or payout is None or fee_total is None)
            else str(payout - principal - fee_total)),
        "labels": ("ARITHMETIC on authenticated fills and the venue's own "
                   "resolution. NOT realized P&L: REALIZED requires "
                   "attributable cash movement, which a reconstruction "
                   "cannot supply."),
    }

    # --- 6. attributable cashflow: positions + a cash anchor
    try:
        pos = await MD.positions()
    except Exception as exc:                                   # noqa: BLE001
        pos = {"ok": False, "why": "raised %s" % exc}
    out["positions_now"] = {
        "ok": bool(isinstance(pos, Mapping) and pos.get("ok")),
        "n_rows": (len(pos.get("rows") or [])
                   if isinstance(pos, Mapping)
                   and pos.get("rows") is not None else None),
        "labels": "the CURRENT representation, never evidence about a "
                  "historical moment that has since settled",
    }
    out["cash_anchor"] = await _balances()
    out["cashflow_attribution"] = {
        "attributable": False,
        "why": ("no surface read here ties a cash movement to this order. "
                "A balance is a level, not a cashflow, and no per-"
                "settlement credit record was located on this account."),
    }

    # --- 7. the accounting ladder, stated rather than implied
    filled_ok = bool(out.get("fills_for_this_order")) or bool(
        ours and _d(ours[0].get("filledQuantity"))
        and _d(ours[0].get("filledQuantity")) > 0)
    out["accounting"] = {
        "FILLED": filled_ok,
        "SETTLED": out["settlement"]["contract_status"] == "Settled",
        "CASH_CONFIRMED": False,
        "REALIZED": False,
        "first_break": "CASH_CONFIRMED",
        "labels": ("FILLED and SETTLED are authenticated. CASH_CONFIRMED is "
                   "absent, so NOTHING here is realized P&L."),
    }
    return out


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order-id", default=ORDER_ID)
    ap.add_argument("--symbol", default=SYMBOL)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = asyncio.run(capture(a.order_id, a.symbol))
    text = json.dumps(rep, indent=2, sort_keys=True, default=str)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
