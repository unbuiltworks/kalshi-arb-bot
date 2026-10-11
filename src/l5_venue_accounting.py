"""Two different questions about the same position, kept apart on purpose.

Probe 05 exposed a conflation. The local ledger computed one number,
`committed_usd`, and used it both as "what this cost" and as "how much of
the $5 envelope is spent". Those are not the same quantity and on Probe 05
they were not even the same sign: the eight `ORDER_INTENT_BUY_SHORT` orders
CREDITED $1.2432 at the venue, while the ledger counted them as $1.2432 of
cash out. `committed_usd` came to $2.8479 against an actual net movement of
$0.46150.

So:

  venue_cashflow   what the venue actually did to the balance -- signed
                   execution cash, plus commission, plus settlement. It is
                   an accounting fact and it must reconcile to the account
                   balance to the cent.

  risk_exposure    what we could still lose. It is a bound, not a fact, and
                   it is what the $5 gates are allowed to consult.

A credit NEVER buys headroom. A short outcome-YES position takes cash in
today and owes up to $1 a share if YES settles 1, so `risk_exposure` prices
both settlement outcomes and takes the worse one. That is not netting a
credit against a debit: the short leg's full settlement liability is carried
under the outcome where it hurts, and the long leg's full loss is carried
under the other. Nothing is allowed to cancel except positions that genuinely
offset under the SAME settlement of the SAME market.

Outstanding orders are counted as if they fill in full, at their limit
price, immediately. An order that might fill is exposure.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: a binary market settles at exactly one of these, per share of outcome YES
SETTLEMENTS = (D(0), D(1))

LONG_INTENTS = ("ORDER_INTENT_BUY_LONG",)
SHORT_INTENTS = ("ORDER_INTENT_BUY_SHORT",)


class UnknownIntent(ValueError):
    """An order whose direction we cannot read. Never assumed to be long."""


def _d(v: Any) -> D:
    if isinstance(v, Mapping):
        v = v.get("value")
    return D(str(v if v is not None else 0))


def direction(order: Mapping[str, Any]) -> str:
    """LONG or SHORT, from the venue's own intent, or refuse.

    Read from `intent` first because it is unambiguous; `side`+`outcomeSide`
    is the fallback for a row that predates it. An unreadable row raises:
    guessing LONG would understate the settlement liability of a short, which
    is the one direction where guessing wrong lets real risk through a gate.
    """
    intent = str(order.get("intent") or "").upper()
    if intent in LONG_INTENTS:
        return "LONG"
    if intent in SHORT_INTENTS:
        return "SHORT"
    side = str(order.get("side") or "").upper()
    outcome = str(order.get("outcomeSide") or "").upper()
    if side.endswith("BUY") and outcome.endswith("YES"):
        return "LONG"
    if side.endswith("SELL") and outcome.endswith("NO"):
        return "SHORT"
    if side.endswith("SELL") and outcome.endswith("YES"):
        return "SHORT"
    raise UnknownIntent("cannot read direction from %r"
                        % {k: order.get(k) for k in
                           ("intent", "side", "outcomeSide")})


def execution_cash(order: Mapping[str, Any]) -> D:
    """Signed cash the venue moved for this FILL, commission excluded.

    Negative for a long: shares bought cost money. Positive for a short:
    selling outcome-YES short takes cash in. The price used is the venue's
    own `avgPx` when present -- what we were actually charged -- and the
    limit `price` only when there is no average yet.
    """
    qty = _d(order.get("cumQuantity") if order.get("cumQuantity") is not None
             else order.get("quantity"))
    px = order.get("avgPx")
    if px is None or _d(px) == 0:
        px = order.get("price")
    px = _d(px)
    return (-(px * qty) if direction(order) == "LONG" else (px * qty))


def commission(order: Mapping[str, Any]) -> D:
    return _d(order.get("commissionNotionalTotalCollected")
              if order.get("commissionNotionalTotalCollected") is not None
              else order.get("commission"))


def settlement_cash(order: Mapping[str, Any], settlement_px: D) -> D:
    """What this order's shares pay at settlement, once the market resolves.

    A long share is worth the settlement price. A short outcome-YES share
    owes it: the cash came in at trade time and the liability lands here.
    """
    qty = _d(order.get("cumQuantity") if order.get("cumQuantity") is not None
             else order.get("quantity"))
    return (qty * settlement_px if direction(order) == "LONG"
            else -(qty * settlement_px))


def venue_cashflow(orders: Sequence[Mapping[str, Any]],
                   settlement_px: Optional[D] = None) -> Dict[str, Any]:
    """The balance movement these orders explain, as separate lines.

    Execution, commission and settlement stay apart: netting them into one
    number is how a loss disguises itself as a fee, and how a fee disguises
    itself as a price.
    """
    ex = sum((execution_cash(o) for o in orders), D(0))
    fee = sum((commission(o) for o in orders), D(0))
    st = (D(0) if settlement_px is None else
          sum((settlement_cash(o, settlement_px) for o in orders), D(0)))
    return {"execution": ex, "commission": -fee, "settlement": st,
            "net": ex - fee + st,
            "orders": len(orders),
            "labels": ("signed venue cash. Execution is negative for longs "
                       "and positive for shorts because that is what the "
                       "venue did, not a modelling choice.")}


# ------------------------------------------------------------------- risk

def as_if_filled(order: Mapping[str, Any]) -> Dict[str, Any]:
    """The same order, priced as though it fills in full at its limit.

    An outstanding order carries `cumQuantity 0` and no `avgPx`, so reading
    it the way a fill is read values it at nothing -- which would let a
    resting order consume none of the envelope while being every bit as able
    to lose money. Risk reads this; cash never does.
    """
    o = dict(order)
    o["cumQuantity"] = o.get("quantity")
    o["avgPx"] = None
    o["commissionNotionalTotalCollected"] = None
    return o


def _legs(orders: Iterable[Mapping[str, Any]]) -> Dict[str, D]:
    long_q = short_q = D(0)
    for o in orders:
        q = _d(o.get("cumQuantity") if o.get("cumQuantity") is not None
               else o.get("quantity"))
        if direction(o) == "LONG":
            long_q += q
        else:
            short_q += q
    return {"long_qty": long_q, "short_qty": short_q}


def risk_exposure(filled: Sequence[Mapping[str, Any]],
                  outstanding: Sequence[Mapping[str, Any]] = ()
                  ) -> Dict[str, Any]:
    """Worst-case cash loss from here, over both settlements of this market.

    Outstanding orders are treated as filled in full at their limit price,
    immediately, because an order that might fill is exposure. Their cash is
    included at both the execution step and the settlement step, so a resting
    short cannot look like free money.

    The number reported is a LOSS: positive means we can still be down that
    much. A position that cannot lose reports 0, never a negative "credit"
    that some other market could spend.
    """
    everything = list(filled) + [as_if_filled(o)
                                 for o in outstanding]
    per_outcome: Dict[str, str] = {}
    worst = D(0)
    for s in SETTLEMENTS:
        cash = D(0)
        for o in everything:
            cash += execution_cash(o) - commission(o) + settlement_cash(o, s)
        per_outcome["settles_%s" % s] = str(cash)
        loss = -cash
        if loss > worst:
            worst = loss
    legs = _legs(everything)
    return {"worst_case_loss_usd": worst,
            "by_settlement": per_outcome,
            "long_qty": str(legs["long_qty"]),
            "short_qty": str(legs["short_qty"]),
            "matched_qty": str(min(legs["long_qty"], legs["short_qty"])),
            "residual_qty": str(legs["long_qty"] - legs["short_qty"]),
            "outstanding_counted": len(outstanding),
            "labels": ("a bound, not a fact. Outstanding orders are counted "
                       "as filled in full. A credit never buys headroom: the "
                       "short leg's settlement liability is carried in full "
                       "under the outcome where it hurts.")}


def envelope_breached(filled: Sequence[Mapping[str, Any]],
                      outstanding: Sequence[Mapping[str, Any]],
                      cap_usd: D) -> bool:
    return risk_exposure(filled, outstanding)["worst_case_loss_usd"] > cap_usd


def walk_exposure(orders: Sequence[Mapping[str, Any]],
                  cap_usd: D) -> Dict[str, Any]:
    """Replay the orders in venue time order and watch the bound move.

    A probe that ends inside the envelope may still have breached it in the
    middle, so the peak is taken over every prefix rather than at the end.
    """
    ordered = sorted(orders, key=lambda o: str(o.get("insertTime") or ""))
    peak = D(0)
    peak_at: Optional[str] = None
    steps: List[Dict[str, Any]] = []
    for i in range(1, len(ordered) + 1):
        r = risk_exposure(ordered[:i])
        w = r["worst_case_loss_usd"]
        steps.append({"after": i, "order": ordered[i - 1].get("id"),
                      "worst_case_loss_usd": str(w)})
        if w > peak:
            peak, peak_at = w, str(ordered[i - 1].get("id"))
    return {"orders": len(ordered), "peak_worst_case_loss_usd": peak,
            "peak_after_order": peak_at, "cap_usd": str(cap_usd),
            "breached": peak > cap_usd, "steps": steps}
