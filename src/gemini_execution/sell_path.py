"""SELL / CASH-OUT: the smallest isolated write path, and it is DISARMED.

WHY THIS MODULE EXISTS. The momentum+pullback candidate needs an EXIT, and an
exit is a venue mutation this repository has never performed: every accepted
order on these credentials has been a BUY. Rather than discover the semantics
by sending one -- which is forbidden -- the capability was established from the
venue's own OpenAPI contract, and this module is the executor that contract
admits. It is not armed, and arming it is a separate authorization.

WHAT THE AUTHORITATIVE SPEC SAYS (developer.gemini.com/specs/openapi/
prediction-markets.yaml, fetched 2026-10-04, 208757 bytes):

  * `OrderRequest.side` is `enum: [buy, sell]`, and `POST /v1/prediction-
    markets/order` ships a worked `sellNo` example -- "Place a limit order to
    sell 50 NO contracts at 0.35". So a SELL is a first-class limit order on
    the same endpoint, with the same eight fields. There is no separate
    close/exit endpoint and no reduce-only flag.
  * An early sell is named by the venue: `POST /v1/prediction-markets/
    positions/settled?withCashOuts=true` returns `cashOuts[]`, described as
    "a qualifying cash-out (EARLY SELL BEFORE CONTRACT RESOLUTION) with
    cost-basis context", carrying `proceeds`, `costBasis` and `netProfit`
    ("Realized P&L from this cash-out fill (proceeds - costBasis). Equals the
    ledger realized_pl delta ..."), and `side` "Always `sell` for cash-outs".
    That is the venue's own realized-P&L primitive for an exit, which is what
    CASH_CONFIRMED has to be read from.
  * `Position.quantityOnHold` is "Quantity currently on hold from open
    orders", and `Position.realizedPl` is "Realized profit/loss from sells".
    A resting SELL therefore ENCUMBERS the position rather than creating a
    parallel obligation.
  * `Position.marketValue` is "**Absent** from the response when the held
    outcome has no live sell quote (no liquidity to sell into) -- surface a
    no-liquidity state rather than a price the user cannot transact at". The
    venue states plainly that an exit can be unreachable. A pullback rule that
    assumes a bid is always there is assuming something the venue denies.

WHAT THE SPEC DOES NOT SETTLE, and is therefore refused here rather than
assumed:

  * Whether `side: "sell"` on an outcome the account does NOT hold opens a
    SHORT. `SettledPosition.position` is exampled as "-200", so negative
    positions exist in the venue's own model -- but nothing in the spec says
    whether a naked prediction-market sell is permitted, margined, or simply
    rejected. This module never finds out: `free_quantity` must cover the
    whole request, so only owned, unencumbered contracts can be sold. That is
    both the risk bound and the reason the question stays open.
  * The fee schedule. It is not in the spec. Our OWN authenticated fills are
    the only evidence we have, and they are mixed: fee_amount "0" on maker
    fills (aggressor false) and "0.01".."0.06" elsewhere. A sell's fee is
    therefore MEASURED from the fill, never modelled -- same rule as BUY.

THE DISCIPLINE IS THE BUY PATH'S, NOT A NEW ONE. Every write still goes
through `ci15m_probe._write`, so SHADOW remains a single switch and a shadow
run cannot reach the venue. Terminal state still comes from
`ci15m_probe.poll_terminal`, cancellation from `cancel_and_reconcile`, and
inventory from `authenticated_fills`. Nothing here re-implements any of it.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional, Sequence

from src.gemini_execution import arming, ci15m_probe as P, payload
from src.gemini_execution import spec

#: DISARMED. A SELL is constructible only under an authorization that names
#: itself here AND is the build's live authorization. None means no
#: authorization grants it, which is the current state: the capability is
#: established, the path exists, and nothing may send one.
SELL_AUTH_ID: Optional[str] = None


class SellRefused(Exception):
    """Raised before anything is signed. Never downgraded to a warning."""


def armed() -> Dict[str, Any]:
    """Two independent facts, not one flag -- the taker gate's shape."""
    live = arming.EXPECTED_AUTH_ID
    ok = (SELL_AUTH_ID is not None and SELL_AUTH_ID == live)
    return {"armed": bool(ok), "sell_auth_id": SELL_AUTH_ID,
            "build_auth_id": live,
            "why": ("armed" if ok else
                    "SELL_AUTH_ID is None: no authorization grants a sell"
                    if SELL_AUTH_ID is None else
                    "this build's authorization is %r, not the sell "
                    "authorization %r" % (live, SELL_AUTH_ID))}


def build_sell(*, symbol: str, outcome: str, price: Any, quantity: Any,
               maker_or_cancel: bool = True) -> Dict[str, Any]:
    """The documented eight-field body with `side: "sell"`, validated.

    Construction is NOT authorization: this is pure and sends nothing, so the
    exact bytes can be shown and tested while the path stays disarmed.
    """
    body = payload.build_order(symbol=symbol, price=str(price),
                               quantity=str(quantity),
                               side=spec.SIDE_SELL, outcome=outcome,
                               maker_or_cancel=maker_or_cancel)
    payload.validate_structure(body)
    payload.assert_types(body, allow_taker=not maker_or_cancel)
    if body["side"] != spec.SIDE_SELL:                      # pragma: no cover
        raise SellRefused("build_sell produced side=%r" % body["side"])
    return body


def free_quantity(rows: Optional[Sequence[Mapping[str, Any]]], symbol: str,
                  outcome: str) -> Dict[str, Any]:
    """Owned-and-unencumbered quantity, or UNREADABLE. Never a silent zero.

    `totalQuantity - quantityOnHold`, both from the documented Position
    schema. UNREADABLE IS NOT EMPTY: `rows is None` is an unreadable position
    surface and fails closed, while a successful read that lists no row for
    this symbol/outcome is the FACT that we hold none.
    """
    if rows is None:
        return {"readable": False, "free": None,
                "why": "the positions surface was unreadable; an unreadable "
                       "read is not a zero position"}
    want = str(outcome).lower()
    tot = held = D("0")
    seen = False
    for r in rows:
        if str(r.get("symbol")) != symbol:
            continue
        if str(r.get("outcome", want)).lower() != want:
            continue
        seen = True
        tot += D(str(r.get("totalQuantity") or 0))
        held += D(str(r.get("quantityOnHold") or 0))
    free = tot - held
    return {"readable": True, "free": str(free if free > 0 else D("0")),
            "total_quantity": str(tot), "quantity_on_hold": str(held),
            "row_present": seen,
            "labels": "a position read is a COST BASIS and a size, never a "
                      "realized profit"}


def assert_position_backed(rows: Optional[Sequence[Mapping[str, Any]]],
                           symbol: str, outcome: str, quantity: Any
                           ) -> Dict[str, Any]:
    """REFUSE any sell the account cannot cover from owned contracts.

    This is what keeps the open short question open: we never send the request
    that would answer it.
    """
    q = D(str(quantity))
    if q <= 0:
        raise SellRefused("quantity must be positive, got %s" % q)
    got = free_quantity(rows, symbol, outcome)
    if not got["readable"]:
        raise SellRefused(got["why"])
    free = D(got["free"])
    if free < q:
        raise SellRefused(
            "a sell of %s is not position-backed: free=%s (total=%s onHold=%s)"
            ". A naked sell might open a SHORT, which no authorization here "
            "grants." % (q, free, got["total_quantity"],
                         got["quantity_on_hold"]))
    return dict(got, requested=str(q), position_backed=True)


async def submit_sell(cohort_id: str, symbol: str, ticker: str, *,
                      outcome: str, price: Any, quantity: Any,
                      maker_or_cancel: bool = True,
                      position_rows: Optional[Sequence[Mapping[str, Any]]]
                      = None) -> Dict[str, Any]:
    """ONE position-backed sell, through the SAME write boundary as a buy.

    Refusals, in order, and every one of them fails closed:
      1. not armed                   -- the default state
      2. cohort HALTED
      3. frozen policy fingerprint moved
      4. not position-backed / positions unreadable
      5. an order already outstanding on this symbol
    Only then is anything built, and the build is validated against the
    documented schema before `_write` sees it.
    """
    arm = armed()
    if not arm["armed"]:
        raise SellRefused(arm["why"])
    if P.BUD.halted(cohort_id):
        raise SellRefused("HALTED: %s" % P.BUD.halted(cohort_id))
    if (P.AUTHORIZED_POLICY_FINGERPRINT is None
            or P.AUTHORIZED_POLICY_FINGERPRINT != P.fingerprint()):
        raise SellRefused("the frozen policy on disk is not the one this "
                          "authorization was granted against")
    backing = assert_position_backed(position_rows, symbol, outcome, quantity)
    out = await P.one_outstanding_order(symbol)
    if not out["ok"]:
        raise SellRefused(out["why"])
    body = build_sell(symbol=symbol, outcome=outcome, price=price,
                      quantity=quantity, maker_or_cancel=maker_or_cancel)
    res = await P._write(symbol=symbol, side=spec.SIDE_SELL, outcome=outcome,
                         price=str(price), quantity=str(quantity),
                         allow_taker=not maker_or_cancel)
    return {"status": "SUBMITTED", "sent": True, "body": body, "venue": res,
            "backing": backing, "write_mode": P.write_mode(),
            "labels": "SUBMITTED is not FILLED, and a FILLED sell is not "
                      "REALIZED: proceeds are CASH_CONFIRMED only from the "
                      "venue's own cashOuts[] record."}
