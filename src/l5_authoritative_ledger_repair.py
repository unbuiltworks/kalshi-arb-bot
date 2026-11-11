"""Reconcile the stuck markets against ATTESTED venue order records.

Every number here traces to a `/v1/order/{id}` read captured by the GET-only
`pmus-readonly-recovery` image on 2026-10-12. Nothing is reconstructed,
inferred from a settlement price, or invented. An order that cannot be read
in full disqualifies its whole market rather than being guessed at.

WHY THIS EXISTS, AND WHAT IT CORRECTS

The executor's book held quantities with NO cost basis, so settlement refused
(`QUANTITY_WITHOUT_COST`) and the market could never retire -- 4,107 refusals
and climbing. The root cause is now established: **all six orders on these
markets are `MANUAL_ORDER_INDICATOR_MANUAL`.** The executor never placed
them, so it picked the quantities up from venue reconciliation while holding
no fill record and therefore no cost -- exactly the state
`labels_pending` warned about.

THE ERROR THIS MODULE PREVENTS. On settlement price alone, 1245z looks like
a phantom: UP settled 0.0000, our ledger showed cost 0, cash_deployed 0 and
fills_booked 0, and the venue now holds nothing. Dropping it would have been
defensible on every one of those signals -- and WRONG. The venue's own order
record shows `D23N485QAZ89`, 2 YES filled at 0.8400, `cumQuantity 2`,
`ORDER_STATE_FILLED`. That is a genuine **-$1.68 realized loss**. A
settlement price can tell you what a share paid; only the order record tells
you what it cost.

CONVENTION, as calibrated earlier and re-confirmed here: the venue quotes ONE
number per market, the LONG (YES) price. `BUY_LONG` at avgPx pays avgPx a
share. `BUY_SHORT` at avgPx SELLS YES at avgPx, taking cash in and owing the
YES settlement. Commission is positive for a fee paid and negative for a
credit received.

A ZERO COMMISSION IS NOT A REBATE, and both BTC15m fills here show exactly
`0.0000` while the NFL fills show +/-0.0100 and +0.0300. So BTC15m maker
fills earned no credit, which is evidence -- not merely conservatism -- for
carrying the rebate at zero in BTC15m economics.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

YES = "YES"
NO = "NO"

#: Verbatim from `/v1/order/{id}`. `commission` is positive for a fee paid.
#: Every row carries its venue order id so any figure can be traced back.
ATTESTED_ORDERS: List[Dict[str, Any]] = [
    # aec-nfl-bal-atl-2026-10-11 -- settled 1.0000
    {"id": "D1SA9RRSTZ73", "market": "aec-nfl-bal-atl-2026-10-11",
     "outcome": YES, "qty": D("2"), "avg_px": D("0.4400"),
     "commission": D("-0.0100"), "state": "ORDER_STATE_FILLED",
     "manual": True},
    {"id": "D1S42Z0APZ73", "market": "aec-nfl-bal-atl-2026-10-11",
     "outcome": YES, "qty": D("2"), "avg_px": D("0.4450"),
     "commission": D("0.0300"), "state": "ORDER_STATE_FILLED",
     "manual": True},
    {"id": "D1SA22CA2Z7Z", "market": "aec-nfl-bal-atl-2026-10-11",
     "outcome": NO, "qty": D("2"), "avg_px": D("0.4650"),
     "commission": D("0.0300"), "state": "ORDER_STATE_FILLED",
     "manual": True},
    {"id": "D1S4RM12JZ7T", "market": "aec-nfl-bal-atl-2026-10-11",
     "outcome": NO, "qty": D("2"), "avg_px": D("0.4500"),
     "commission": D("-0.0100"), "state": "ORDER_STATE_FILLED",
     "manual": True},
    # cpc-btc-updown-15m-2026-10-12-0030z -- settled 0.0000
    {"id": "D1SCCX4F8Z84", "market": "cpc-btc-updown-15m-2026-10-12-0030z",
     "outcome": NO, "qty": D("2"), "avg_px": D("0.8500"),
     "commission": D("0.0000"), "state": "ORDER_STATE_FILLED",
     "manual": True},
    # cpc-btc-updown-15m-2026-10-12-1245z -- settled 0.0000
    {"id": "D23N485QAZ89", "market": "cpc-btc-updown-15m-2026-10-12-1245z",
     "outcome": YES, "qty": D("2"), "avg_px": D("0.8400"),
     "commission": D("0.0000"), "state": "ORDER_STATE_FILLED",
     "manual": True},
]

#: Verbatim from `/v1/markets/{slug}/bbo`: state and settlementPx.
ATTESTED_SETTLEMENT: Dict[str, Dict[str, Any]] = {
    "aec-nfl-bal-atl-2026-10-11": {
        "state": "MARKET_STATE_EXPIRED", "settlement_px": D("1.0000")},
    "cpc-btc-updown-15m-2026-10-12-0030z": {
        "state": "MARKET_STATE_EXPIRED", "settlement_px": D("0.0000")},
    "cpc-btc-updown-15m-2026-10-12-1245z": {
        "state": "MARKET_STATE_EXPIRED", "settlement_px": D("0.0000")},
}

TERMINAL_STATES = ("MARKET_STATE_EXPIRED", "MARKET_STATE_SETTLED",
                   "MARKET_STATE_RESOLVED")

#: what the executor's book recognised before this repair, from L5_LIVE_HB
LEDGER_BEFORE: Dict[str, Any] = {
    "realized_attributed": D("1.6800"),
    "committed_usd": {"aec-nfl-bal-atl-2026-10-11": D("0.8800")},
    "retired_markets": ("cpc-btc-updown-15m-2026-10-12-0030z",),
}


def _signed_yes(order: Mapping[str, Any]) -> D:
    """A NO buy is a SELL of YES, so it is negative YES quantity."""
    q = D(str(order["qty"]))
    return q if order["outcome"] == YES else -q


def execution_cash(order: Mapping[str, Any]) -> D:
    """Cash OUT, positive. A short takes cash in, so it is negative."""
    q = D(str(order["qty"]))
    px = D(str(order["avg_px"]))
    return q * px if order["outcome"] == YES else -(q * px)


def market_report(market: str,
                  orders: Optional[Sequence[Mapping[str, Any]]] = None,
                  settlement: Optional[Mapping[str, Any]] = None
                  ) -> Dict[str, Any]:
    """Authoritative economics for one market, or a refusal."""
    rows = [o for o in (orders if orders is not None else ATTESTED_ORDERS)
            if o["market"] == market]
    st = (settlement if settlement is not None
          else ATTESTED_SETTLEMENT.get(market))
    if not rows:
        return {"market": market, "ok": False, "refuse": "NO_ATTESTED_ORDERS"}
    for o in rows:
        if o.get("state") != "ORDER_STATE_FILLED":
            return {"market": market, "ok": False,
                    "refuse": "ORDER_NOT_TERMINAL:%s" % o.get("id")}
        if o.get("qty") is None or o.get("avg_px") is None:
            return {"market": market, "ok": False,
                    "refuse": "ORDER_UNREADABLE:%s" % o.get("id")}
    if not st or st.get("state") not in TERMINAL_STATES:
        return {"market": market, "ok": False, "refuse": "NOT_TERMINAL"}
    spx = st.get("settlement_px")
    if spx is None:
        return {"market": market, "ok": False, "refuse": "NO_SETTLEMENT_PX"}
    spx = D(str(spx))

    net_yes = sum((_signed_yes(o) for o in rows), D(0))
    cash_out = sum((execution_cash(o) for o in rows), D(0))
    commission = sum((D(str(o["commission"])) for o in rows), D(0))
    # a long holds YES and is paid the settlement; a short owes it
    settlement_cash = net_yes * spx
    realized = settlement_cash - cash_out - commission
    return {
        "market": market, "ok": True,
        "order_ids": [o["id"] for o in rows],
        "all_manual": all(bool(o.get("manual")) for o in rows),
        "settlement_px": str(spx),
        "net_yes_position": str(net_yes),
        "gross_yes_bought": str(sum((D(str(o["qty"])) for o in rows
                                     if o["outcome"] == YES), D(0))),
        "gross_yes_sold": str(sum((D(str(o["qty"])) for o in rows
                                   if o["outcome"] == NO), D(0))),
        "cash_out_usd": str(cash_out),
        "commission_usd": str(commission),
        "settlement_cash_usd": str(settlement_cash),
        "realized_usd": str(realized),
        "cost_basis_by_leg": {
            o["id"]: {"outcome": o["outcome"], "qty": str(o["qty"]),
                      "px": str(o["avg_px"]),
                      "cash_out": str(execution_cash(o)),
                      "commission": str(D(str(o["commission"])))}
            for o in rows},
    }


def reconciliation() -> Dict[str, Any]:
    """Every attested market, and the correction against the ledger."""
    markets = sorted({o["market"] for o in ATTESTED_ORDERS})
    reports = [market_report(m) for m in markets]
    ok = [r for r in reports if r.get("ok")]
    refused = [r for r in reports if not r.get("ok")]
    total = sum((D(r["realized_usd"]) for r in ok), D(0))
    before = D(str(LEDGER_BEFORE["realized_attributed"]))
    return {
        "markets": reports,
        "refused": [r["market"] for r in refused],
        "authoritative_realized_total_usd": str(total),
        "ledger_realized_before_usd": str(before),
        "ledger_overstatement_usd": str(before - total),
        "committed_usd_to_release": {
            k: str(v) for k, v in LEDGER_BEFORE["committed_usd"].items()},
        "every_order_manual": all(r.get("all_manual") for r in ok),
        "labels": (
            "SETTLED realized, from attested /v1/order reads and the venue's "
            "own settlement price. Commissions are a separate confirmed line "
            "and are included in realized here because the venue charged "
            "them on these fills. A zero commission is NOT a rebate."),
    }


def corrections() -> List[Dict[str, Any]]:
    """The narrow, per-market repairs this evidence establishes.

    Each one books a cost basis the VENUE established for a quantity the
    venue agrees was filled, then recognises the settlement that already
    happened. No quantity is dropped and no loss is erased.
    """
    out: List[Dict[str, Any]] = []
    for r in reconciliation()["markets"]:
        if not r.get("ok"):
            out.append({"market": r["market"], "action": "REFUSE",
                        "reason": r["refuse"]})
            continue
        out.append({
            "market": r["market"],
            "action": "BOOK_COST_BASIS_THEN_SETTLE",
            "evidence": r["order_ids"],
            "book_cost_basis_usd": r["cash_out_usd"],
            "commission_usd": r["commission_usd"],
            "net_yes_position": r["net_yes_position"],
            "settlement_px": r["settlement_px"],
            "recognise_realized_usd": r["realized_usd"],
            "preserves": ("a genuine loss is recognised, never dropped; a "
                          "matched pair nets to zero position and realises "
                          "only its trade margin"),
        })
    return out
