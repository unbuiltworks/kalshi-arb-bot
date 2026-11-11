"""One-shot, reduce-only migration close of a legacy residual.

OWNER, 2026-09-11: "GO on the one-time migration close. Build this as a
separate, one-shot, reduce-only migration path, not a general bypass inside
R5F... This is a migration/risk-reduction close, not an R5F COMPLETE and not
evidence for >$1 rescue."

WHY IT CANNOT LIVE IN R5F. The probe's arming gate refuses to arm unless the
account is flat (`flat_held_state`), which is exactly right for acquisition
and exactly wrong for a close: closing a residual is only ever done WHILE
holding one. So this is its own module with its own gate, and the exemption
is bought back by making the operation incapable of doing anything except
shrink an existing position:

  * it may only BUY the side that REDUCES abs(residual)
  * quantity is capped at abs(residual), so it cannot cross through zero
  * one order, one shot, no re-arm, no repeat after completion or crash

A path that cannot open, cannot extend, and cannot overshoot is not a bypass
of the flatness gate; it is the thing the flatness gate is waiting for.

MARKETABLE, DELIBERATELY. OWNER: "Do not use maker-first if it means waiting
again." The R5F complement has sat unfilled against this book for hours
because an acceptable maker price was never reached. So this crosses the
spread -- with the maximum acceptable price computed from the loss bound and
sent as an explicit limit, never a market order. The limit is the bound.

ACCOUNTING. OWNER: "Do not describe ~$0.45 as the cash outlay." The cash
submitted and the loss locked are different numbers by an order of magnitude
-- roughly $9.92 out against roughly $0.45 of above-par loss on the residual
portion -- and this module reports them as separate fields that no caller can
conflate. Everything it records is labelled `migration_cleanup` and is never
R5F realized.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal as D
from typing import Any, Callable, Mapping, Optional

ONE = D("1")
KIND = "migration_cleanup"          # never R5F realized; its own label

# The owner's bounds, named here so a reader sees them without the venue.
MAX_LOCKED_LOSS_USD = D("0.50")     # incremental, on the residual portion
MAX_UNITS = D("12")
PRICE_CEILING = D("0.99")           # a sanity stop; the loss bound binds first
MAX_SNAPSHOT_AGE_S = 10.0           # "fresh executable quote/depth"
COMBINED_LOSS_STOP = D("-15.00")    # the existing stop, not a new one

# Outcomes. Only CLOSED means the residual is gone.
REFUSED = "REFUSED_PRECONDITION"
NOT_EXECUTABLE = "REFUSED_NOT_EXECUTABLE"
SUBMIT_FAILED = "SUBMIT_FAILED"
UNFILLED = "UNFILLED"
PARTIAL = "PARTIAL_STILL_HOLDING"
CLOSED = "CLOSED_FLAT"
UNVERIFIED = "FILLED_BUT_UNVERIFIED"


def _d(x) -> Optional[D]:
    if x is None:
        return None
    try:
        return D(str(x))
    except Exception:                                       # noqa: BLE001
        return None


def reduce_side(residual: D) -> Optional[str]:
    """The ONLY side this module may buy: the one that shrinks the position.

    residual = up - dn. Negative means dn-heavy, so buying UP reduces it.
    A zero residual has no reducing side, which is how "already flat" comes
    out as a refusal rather than an order."""
    if residual < 0:
        return "OUTCOME_SIDE_YES"
    if residual > 0:
        return "OUTCOME_SIDE_NO"
    return None


def max_price(units: D, avg_excess_cost: D,
              loss_cap: D = MAX_LOCKED_LOSS_USD) -> D:
    """The highest price whose locked loss still respects the bound.

    Completing `units` at price p pairs them against inventory already bought
    at `avg_excess_cost`, so each pair costs (avg_excess_cost + p) and redeems
    at 1.00. The above-par loss is therefore

        (avg_excess_cost + p - 1) * units <= loss_cap

    which is the bound the owner set, solved for p. Sent as a LIMIT, so the
    bound is enforced by the venue and not merely by our intention."""
    if units <= 0:
        return D("0")
    p = (ONE - avg_excess_cost) + (loss_cap / units)
    return min(p, PRICE_CEILING).quantize(D("0.0001"))


def locked_loss(units: D, avg_excess_cost: D, price: D) -> D:
    """Above-par loss on the residual portion. NOT the cash outlay."""
    return ((avg_excess_cost + price - ONE) * units).quantize(D("0.0001"))


def preconditions(snap: Mapping[str, Any], *, slug: str,
                  now: float, loss_cap: D = MAX_LOCKED_LOSS_USD) -> dict:
    """The owner's gate, every check, fail closed.

    A check that cannot be ESTABLISHED fails. "We could not ask" is never
    "the answer was yes" -- the rule this project has had to relearn twice
    tonight.
    """
    checks: list = []

    def _c(name, ok, got=None):
        checks.append({"check": name, "ok": bool(ok), "got": got})

    got_slug = snap.get("slug")
    _c("exact_market_only", got_slug == slug and bool(slug), got_slug)

    # quantity provenance: qtyDecimal only. A venue total built from any
    # other field is the 774.62 phantom.
    _c("qty_from_qtydecimal_only",
       snap.get("qty_source") == "qtyDecimal", snap.get("qty_source"))

    venue_q, ledger_q = _d(snap.get("venue_qty")), _d(snap.get("ledger_qty"))
    _c("venue_qty_equals_ledger_qty",
       venue_q is not None and ledger_q is not None and venue_q == ledger_q,
       f"{venue_q} vs {ledger_q}")

    residual = _d(snap.get("residual"))
    up, dn = _d(snap.get("up")), _d(snap.get("dn"))
    direction_ok = (residual is not None and up is not None and dn is not None
                    and residual == up - dn and residual != 0)
    _c("residual_direction_and_size_agree", direction_ok,
       f"residual={residual} up={up} dn={dn}")

    orders = snap.get("open_orders")
    _c("zero_existing_orders", orders == 0, orders)

    side = reduce_side(residual) if residual is not None else None
    units = None
    if residual is not None and side:
        units = min(abs(residual), MAX_UNITS)
    _c("buy_reduces_and_cannot_cross_zero",
       bool(side) and units is not None and 0 < units <= abs(residual), 
       f"side={side} units={units} residual={residual}")

    age = None
    ts = _d(snap.get("quote_ts"))
    ask, depth = _d(snap.get("ask")), _d(snap.get("ask_depth"))
    if ts is not None:
        age = round(now - float(ts), 3)
    _c("fresh_executable_quote_and_depth",
       age is not None and 0 <= age <= MAX_SNAPSHOT_AGE_S
       and ask is not None and ask > 0
       and depth is not None and units is not None and depth >= units,
       f"age_s={age} ask={ask} depth={depth}")

    avg_excess = _d(snap.get("avg_excess_cost"))
    cap_px = loss = None
    if units and avg_excess is not None:
        cap_px = max_price(units, avg_excess, loss_cap)
        if ask is not None:
            loss = locked_loss(units, avg_excess, ask)
    _c("incremental_locked_loss_within_cap",
       loss is not None and loss <= loss_cap, f"{loss} <= {loss_cap}")

    combined = _d(snap.get("combined_loss_usd"))
    worst = None if (combined is None or loss is None) else combined - loss
    _c("combined_loss_stays_above_stop",
       worst is not None and worst > COMBINED_LOSS_STOP,
       f"{combined} - {loss} = {worst} vs {COMBINED_LOSS_STOP}")

    failed = [c["check"] for c in checks if not c["ok"]]
    return {"kind": KIND, "stage": "preconditions", "slug": slug,
            "ok": not failed, "checks": checks, "failed": failed,
            "side": side, "units": None if units is None else str(units),
            "max_price": None if cap_px is None else str(cap_px),
            "ask": None if ask is None else str(ask),
            "projected_locked_loss_usd": None if loss is None else str(loss),
            "action_if_failed": "submit nothing; the residual keeps holding",
            "labels": ("a check that cannot be established FAILS; reduce-only "
                       "and capped at abs(residual) so it can never open, "
                       "extend or cross through zero")}


def economics(units: D, avg_excess_cost: D, fill_price: D,
              fees_usd=None) -> dict:
    """The four numbers the owner asked to see separately.

    OWNER: "Do not describe ~$0.45 as the cash outlay." The cash submitted and
    the loss locked differ by an order of magnitude; they are separate fields
    and nothing here sums them into one."""
    cash = (units * fill_price).quantize(D("0.0001"))
    loss = locked_loss(units, avg_excess_cost, fill_price)
    pair = (avg_excess_cost + fill_price).quantize(D("0.0001"))
    return {
        "cash_required_for_buy_usd": str(cash),
        "incremental_locked_loss_usd": str(loss),
        "fees_usd": "venue_unreported" if fees_usd is None else str(fees_usd),
        "pair_cost_on_residual_portion": str(pair),
        "redeems_at_settlement_usd": str(units * ONE),
        "units": str(units),
        "fill_price": str(fill_price),
        "labels": ("cash_required is what LEAVES the account; "
                   "incremental_locked_loss is the above-par loss on the "
                   "residual portion only. They are different numbers and "
                   "neither is the other. Fees are the venue's own figure, "
                   "never estimated. Nothing here is R5F realized."),
    }


def run(*, slug: str, snapshot: Callable[[], Mapping[str, Any]],
        submit: Callable[..., Mapping[str, Any]],
        fill_status: Callable[[str], Mapping[str, Any]],
        reconcile: Callable[[], Mapping[str, Any]],
        cancel_resting: Callable[[], Any],
        record: Callable[[Mapping[str, Any]], Any],
        now: Callable[[], float] = time.time,
        loss_cap: D = MAX_LOCKED_LOSS_USD,
        fill_wait_s: float = 30.0,
        sleep: Callable[[float], Any] = time.sleep) -> dict:
    """Snapshot -> gate -> ONE marketable limit buy -> verify -> reconcile.

    Returns a row whose `state` is the only thing a caller should branch on.
    CLOSED is the single state meaning the residual is gone and the venue
    agrees. Never raises; every venue call is injected."""
    steps: list = []

    def _out(state, **kw):
        row = {"kind": KIND, "stage": "migration_close", "slug": slug,
               "state": state, "flat": state == CLOSED, "steps": steps,
               "at": now(), **kw}
        try:
            record(row)
        except Exception as exc:                            # noqa: BLE001
            row["record_error"] = repr(exc)
        return row

    try:
        snap = snapshot()
    except Exception as exc:                                # noqa: BLE001
        steps.append({"step": "snapshot", "ok": False, "err": repr(exc)})
        return _out(REFUSED, failed=["fresh_snapshot_unavailable"])
    if not isinstance(snap, Mapping):
        steps.append({"step": "snapshot", "ok": False, "err": "not a mapping"})
        return _out(REFUSED, failed=["fresh_snapshot_unavailable"])

    gate = preconditions(snap, slug=slug, now=now(), loss_cap=loss_cap)
    steps.append({"step": "preconditions", "ok": gate["ok"],
                  "failed": gate["failed"]})
    if not gate["ok"]:
        return _out(REFUSED, preconditions=gate, failed=gate["failed"])

    units, side = D(gate["units"]), gate["side"]
    cap_px, ask = D(gate["max_price"]), D(gate["ask"])
    if ask > cap_px:
        # The market is not currently reachable inside the loss bound. This
        # is the 39c/59c/65c situation stated in dollars: not "they won't
        # fill us", but "the price we can accept is not where the market is".
        steps.append({"step": "marketable", "ok": False,
                      "ask": str(ask), "max_price": str(cap_px)})
        return _out(NOT_EXECUTABLE, ask=str(ask), max_price=str(cap_px),
                    why="the ask is outside the loss bound; nothing submitted")

    try:
        resp = submit(slug=slug, side=side, limit_price=cap_px, quantity=units)
        order_id = str((resp or {}).get("id") or "")
    except Exception as exc:                                # noqa: BLE001
        steps.append({"step": "submit", "ok": False, "err": repr(exc)})
        return _out(SUBMIT_FAILED, err=repr(exc))
    if not order_id:
        steps.append({"step": "submit", "ok": False, "err": "no order id"})
        return _out(SUBMIT_FAILED, err="no order id")
    steps.append({"step": "submit", "ok": True, "order_id": order_id,
                  "limit_price": str(cap_px), "quantity": str(units),
                  "marketable": True})

    # Bounded by ITERATIONS as well as by the clock. A wall-clock-only bound
    # spins forever against a clock that does not advance -- which is exactly
    # what a frozen or stubbed `now` does, and what a test caught here.
    deadline = now() + fill_wait_s
    max_polls = max(1, int(fill_wait_s) + 2)
    filled, px, polls = D("0"), None, 0
    while polls < max_polls:
        polls += 1
        try:
            st = fill_status(order_id) or {}
        except Exception as exc:                            # noqa: BLE001
            st = {"error": repr(exc)}
        filled = _d(st.get("filled_qty")) or D("0")
        px = _d(st.get("avg_price")) or px
        if filled >= units or st.get("terminal") or now() >= deadline:
            break
        sleep(min(1.0, max(0.0, deadline - now())))
    steps.append({"step": "fill", "filled_qty": str(filled), "polls": polls,
                  "avg_price": None if px is None else str(px)})

    try:
        cancel_resting()
        steps.append({"step": "cancel_resting", "ok": True})
    except Exception as exc:                                # noqa: BLE001
        steps.append({"step": "cancel_resting", "ok": False, "err": repr(exc)})

    econ = (economics(filled, D(str(snap.get("avg_excess_cost"))), px)
            if filled > 0 and px is not None else None)

    if filled <= 0:
        return _out(UNFILLED, order_id=order_id, economics=econ,
                    why="marketable order did not fill; residual unchanged")

    try:
        after = reconcile() or {}
    except Exception as exc:                                # noqa: BLE001
        steps.append({"step": "reconcile", "ok": False, "err": repr(exc)})
        return _out(UNVERIFIED, order_id=order_id, economics=econ,
                    why="filled but the venue could not be re-read")
    resid_after = _d(after.get("residual"))
    agrees = after.get("agrees")
    steps.append({"step": "reconcile", "residual": str(resid_after),
                  "agrees": agrees})
    if resid_after is None or agrees is not True:
        return _out(UNVERIFIED, order_id=order_id, economics=econ,
                    reconcile=after,
                    why="post-fill quantities do not reconcile; not flat")
    if resid_after != 0:
        return _out(PARTIAL, order_id=order_id, economics=econ,
                    reconcile=after, residual_after=str(resid_after))
    return _out(CLOSED, order_id=order_id, economics=econ, reconcile=after,
                residual_after="0")
