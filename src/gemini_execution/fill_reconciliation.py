"""GEMINI_FILL_RECONCILIATION_V1 -- the complete, authoritative fill record.

WHY THIS EXISTS. COHORT-2 finished with 56 fills that happened and 54 that
were reconstructable. `_drain_events` takes at most `_MAX_EVENTS_PER_CYCLE`
WebSocket frames per cycle, so the `order_events` list in a window artifact is
a CAPPED SAMPLE, not a ledger. Summing it understated gross spend by $0.34 and
-- worse -- silently narrowed the set of fills whose maker flag anyone could
check. A live experiment must never end that way.

THE FIX IS NOT MORE WEBSOCKET. The venue already publishes an authoritative,
complete record and the production reader was already capturing it; nothing
was reading it for economics. Three independent sources exist per window and
this module reconciles all three:

    A  controller inventory    n_fills, yes/no qty, total_spend_usd
    B  WS `order_events`       LOW LATENCY, capped, never authoritative
    C  `after.orders_seen` +   COMPLETE: the venue's own filled-order rows
       `after.trades`          joined to its own trade record by order_id

C is the economic record. B keeps its job -- it is what lets the controller
react inside a cycle -- but it settles nothing after the fact.

WHAT C CARRIES, and it is everything the proofs need:

    orders_seen  orderId, outcome (yes/no), filledQuantity, price,
                 timeInForce (observed `maker-or-cancel`), clientOrderId
    trades       tid (unique per fill), order_id, price, amount, aggressor,
                 fee_amount, fee_currency, timestampms

    `aggressor` is the venue's OWN maker/taker field: false = maker.
    It is authoritative in a way the WS `m` flag on a capped stream is not.

UNKNOWN STAYS UNKNOWN. A fill whose `aggressor` is absent is UNKNOWN and is
never counted as maker, exactly as an absent `fee_amount` is never counted as
zero. Absence of evidence is recorded as absence.

READ-ONLY AND OFFLINE: a pure function over artifacts already on disk.
"""
from __future__ import annotations

from decimal import Decimal as D
from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_FILL_RECONCILIATION_V1"

COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
UNREADABLE = "UNREADABLE"

MAKER, TAKER, UNKNOWN = "maker", "taker", "UNKNOWN"


def _fr(v: Any) -> Optional[F]:
    try:
        return F(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def _f(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, F):
        q = D(v.numerator) / D(v.denominator)
        v = q if q * D(v.denominator) == D(v.numerator) else q.quantize(D("0.000001"))
    return format(D(v).normalize(), "f")


def _maker_of(trade: Optional[Mapping[str, Any]]) -> str:
    """The venue's own verdict, or UNKNOWN. Never inferred from post-only.

    Requesting maker-or-cancel is what WE asked for; `aggressor` is what the
    venue says happened. A rejected assumption here is the difference between
    a verified property and a hopeful one.
    """
    if trade is None or "aggressor" not in trade:
        return UNKNOWN
    a = trade.get("aggressor")
    if a is False:
        return MAKER
    if a is True:
        return TAKER
    return UNKNOWN


def authoritative_fills(after: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The complete fill record, joined order_id -> trade. Never guesses.

    An unreadable source returns readable=False. That is a different fact from
    "there were no fills" and the two never collapse here.
    """
    if not isinstance(after, Mapping):
        return {"readable": False, "why": "no authoritative after-state"}
    orders, trades = after.get("orders_seen"), after.get("trades")
    if orders is None or trades is None:
        return {"readable": False,
                "why": ("the authoritative order/trade read did not succeed; "
                        "this is not the same claim as 'no fills'")}

    by_order: Dict[str, Dict[str, Any]] = {}
    for o in orders:
        oid = str(o.get("orderId") or o.get("order_id") or o.get("id") or "")
        if oid:
            by_order[oid] = o

    rows: List[Dict[str, Any]] = []
    seen_tids: List[Any] = []
    for t in trades:
        oid = str(t.get("order_id") or "")
        o = by_order.get(oid) or {}
        leg = str(o.get("outcome") or "").lower() or None
        rows.append({
            "tid": t.get("tid"),
            "order_id": oid or None,
            "client_order_id": o.get("clientOrderId"),
            "leg": leg,
            "qty": _f(_fr(t.get("amount"))),
            "price": _f(_fr(t.get("price"))),
            "maker": _maker_of(t),
            "aggressor": t.get("aggressor"),
            "fee_amount": (None if t.get("fee_amount") is None
                           else _f(_fr(t.get("fee_amount")))),
            "fee_currency": t.get("fee_currency"),
            "time_in_force": o.get("timeInForce"),
            "timestampms": t.get("timestampms"),
            "leg_resolved": leg in ("yes", "no"),
        })
        seen_tids.append(t.get("tid"))
    return {"readable": True, "n": len(rows), "rows": rows,
            "tids": seen_tids,
            "orders_joined": sum(1 for r in rows if r["order_id"] in by_order),
            "source": ("after.orders_seen (venue filled-order rows) joined to "
                       "after.trades (venue trade record) on order_id")}


def reconcile_window(rep: Mapping[str, Any]) -> Dict[str, Any]:
    """Every proof the gate asks for, on one window."""
    win = rep.get("window") or {}
    inv = win.get("inventory") or {}
    auth = authoritative_fills(rep.get("after"))

    ws_n = sum(1 for e in (win.get("order_events") or [])
               if str(e.get("X") or "").upper()
               in ("FILLED", "PARTIALLY_FILLED"))
    claimed = inv.get("n_fills")

    out: Dict[str, Any] = {
        "label": LABEL, "symbol": win.get("symbol"),
        "controller_n_fills": claimed,
        "ws_n_fills": ws_n,
        "ws_note": ("the WS order_events list is a CAPPED sample -- at most "
                    "_MAX_EVENTS_PER_CYCLE frames per cycle -- and is a "
                    "latency signal, never the economic record"),
    }
    if not auth.get("readable"):
        out.update({"verdict": UNREADABLE, "why": auth.get("why"),
                    "proofs": {}, "fills": None})
        return out

    rows = auth["rows"]
    tids = [t for t in auth["tids"] if t is not None]
    n = len(rows)

    # --- per-leg quantity and spend, from the authoritative rows alone
    qty = {"yes": F(0), "no": F(0)}
    spend = {"yes": F(0), "no": F(0)}
    bad_rows = []
    for r in rows:
        q, p = _fr(r["qty"]), _fr(r["price"])
        if r["leg"] not in ("yes", "no") or q is None or p is None:
            bad_rows.append(r.get("tid"))
            continue
        qty[r["leg"]] += q
        spend[r["leg"]] += q * p
    total_spend = spend["yes"] + spend["no"]

    # --- maker / taker / unknown, from `aggressor` only
    mk = [r for r in rows if r["maker"] == MAKER]
    tk = [r for r in rows if r["maker"] == TAKER]
    un = [r for r in rows if r["maker"] == UNKNOWN]

    # --- fees, from `fee_amount` only; absent is UNKNOWN, never zero
    fee_missing = [r["tid"] for r in rows if r["fee_amount"] is None]
    fees = sum((_fr(r["fee_amount"]) or F(0)) for r in rows
               if r["fee_amount"] is not None)

    ctrl_spend = _fr(inv.get("total_spend_usd"))
    ctrl_yes, ctrl_no = _fr(inv.get("yes_qty")), _fr(inv.get("no_qty"))

    proofs = {
        "count_complete": (claimed is not None and int(claimed) == n),
        "no_double_count": len(set(tids)) == len(tids) == n,
        "every_fill_priced_and_legged": not bad_rows,
        "spend_reconciles": (ctrl_spend is not None
                             and ctrl_spend == total_spend),
        "qty_reconciles": (ctrl_yes is not None and ctrl_no is not None
                           and qty["yes"] == ctrl_yes and qty["no"] == ctrl_no),
        "maker_resolved_for_every_fill": not un,
        "fees_authoritative_for_every_fill": not fee_missing,
    }
    out.update({
        "verdict": COMPLETE if all(proofs.values()) else INCOMPLETE,
        "n_authoritative_fills": n,
        "ws_missing": (None if claimed is None else int(claimed) - ws_n),
        "proofs": proofs,
        "failed_proofs": [k for k, v in proofs.items() if not v],
        "quantity": {"yes": _f(qty["yes"]), "no": _f(qty["no"]),
                     "controller_yes": inv.get("yes_qty"),
                     "controller_no": inv.get("no_qty")},
        "spend": {"authoritative_usd": _f(total_spend),
                  "yes_usd": _f(spend["yes"]), "no_usd": _f(spend["no"]),
                  "controller_usd": inv.get("total_spend_usd"),
                  "agrees": proofs["spend_reconciles"]},
        "maker": {"maker": len(mk), "taker": len(tk), "unknown": len(un),
                  "all_maker": bool(rows) and not tk and not un,
                  "source": "the venue's own `aggressor` field; false = maker",
                  "taker_tids": [r["tid"] for r in tk],
                  "unknown_tids": [r["tid"] for r in un],
                  "note": ("UNKNOWN stays UNKNOWN. post-only is what we "
                           "REQUESTED; aggressor is what the venue says "
                           "happened.")},
        "fees": {"total_usd": _f(fees), "n_missing": len(fee_missing),
                 "missing_tids": fee_missing,
                 "currencies": sorted({r["fee_currency"] for r in rows
                                       if r["fee_currency"]}),
                 "source": "after.trades fee_amount",
                 "note": ("an absent fee_amount is UNKNOWN, never zero, and "
                          "is counted here rather than summed as 0")},
        "time_in_force": sorted({r["time_in_force"] for r in rows
                                 if r["time_in_force"]}),
        "fills": rows,
        "source": auth["source"],
    })
    return out


def reconcile_all(reps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The cohort-level gate. COMPLETE only if every window is."""
    per = [reconcile_window(r) for r in reps]
    ok = [p for p in per if p["verdict"] == COMPLETE]
    n_auth = sum(p.get("n_authoritative_fills") or 0 for p in per)
    n_ws = sum(p.get("ws_n_fills") or 0 for p in per)
    n_ctrl = sum(int(p.get("controller_n_fills") or 0) for p in per)
    mk = sum((p.get("maker") or {}).get("maker", 0) for p in per)
    tk = sum((p.get("maker") or {}).get("taker", 0) for p in per)
    un = sum((p.get("maker") or {}).get("unknown", 0) for p in per)
    fees = sum((_fr((p.get("fees") or {}).get("total_usd")) or F(0))
               for p in per)
    spend = sum((_fr((p.get("spend") or {}).get("authoritative_usd")) or F(0))
                for p in per)
    return {
        "label": LABEL, "n_windows": len(per),
        "n_complete": len(ok),
        "verdict": COMPLETE if len(ok) == len(per) and per else INCOMPLETE,
        "fills": {"authoritative": n_auth, "controller": n_ctrl,
                  "ws_recorded": n_ws,
                  "ws_missing": n_ctrl - n_ws},
        "maker": {"maker": mk, "taker": tk, "unknown": un,
                  "all_maker": bool(n_auth) and not tk and not un},
        "fees_usd": _f(fees),
        "gross_spend_usd": _f(spend),
        "windows": per,
        "gate": ("a live experiment may not be reported as economics unless "
                 "this verdict is COMPLETE for every window: the count "
                 "matches, tids are unique, every fill is priced and legged, "
                 "spend and quantity reconcile, and maker and fee are the "
                 "venue's own values or explicitly UNKNOWN."),
    }
