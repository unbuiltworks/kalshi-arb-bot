"""AUTHORITATIVE OUTSTANDING-ORDER STATE. The venue is the only source.

WHY THIS MODULE EXISTS, STATED WITHOUT SOFTENING.

E1's frozen spec declares `max_outstanding_orders: 1` and
`one_outstanding_order_at_a_time: True`, and `runner.eligible()` has always
carried the rule that enforces it:

    if snapshot.get("our_outstanding_orders"):
        r.append("E3_OUR_ORDER_IS_OUTSTANDING")

But `pmus_provider` published that field as the literal `0`, and the
residual as the literal `None`. So the rule read a constant, could never
fire, and on 2026-10-08 the 1230z window accumulated TWENTY resting orders
-- one per placement slot -- each of which the declared invariant said was
impossible. The gate existed; nothing fed it.

The repair is not a cleverer rule. It is to make the field mean what its
name says: ASK THE VENUE.

THE RULES THIS MODULE OBEYS.

1. OUTSTANDING IS DERIVED, NEVER ASSUMED. An order counts as outstanding
   unless the venue reports it in a state this module recognises as
   terminal. A partially filled order is OUTSTANDING -- it has leaves at
   the venue and can still fill.

2. UNKNOWN IS OUTSTANDING. A state string this module has never seen is
   treated as live, not as finished. Fail-closed is the only safe default
   for a field whose job is to block submissions.

3. A SUBMISSION WE CANNOT CONFIRM STILL COUNTS. `pending_ids` carries
   locally recorded submission intents -- the ambiguous-acknowledgement
   case, where the POST may or may not have created an order. Those count
   toward outstanding until a venue read either shows them terminal or
   shows them absent from a read that succeeded. This is what makes the
   20-order runaway impossible even when acknowledgements are lost.

4. UNREADABLE, STALE OR INCONSISTENT STATE IS A REFUSAL, NOT A ZERO. Every
   failure path returns `ok: False` with a reason, and the provider
   publishes a sentinel that REFUSES rather than a count that permits.

5. RISK IS AGGREGATE. `open_notional_usd` sums the own-side cost of every
   outstanding order's REMAINING quantity, `filled_notional_usd` sums what
   is already ours, and `aggregate_exposure_usd` is their sum. A per-order
   size check cannot see twenty orders; this can.

THE PRICE CONVENTION IS THE VENUE'S, NOT OURS. PMUS quotes one book per
market and `price.value` is ALWAYS the long (YES) side. An order on
`OUTCOME_SIDE_NO` at an own-side cost of X therefore carries a wire value
of `1 - X`. A6 proved it: own-side 0.6700 went out as 0.3300. Exposure
arithmetic here converts back, because summing wire values for NO orders
would understate what we could lose by exactly the amount that matters.

This module performs GET requests only and builds no order body.
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

#: how old a venue order-state read may be before it stops being evidence.
#: Shorter than the book's 2.0s would be pointless (order state does not
#: move on every tick); much longer and a cancellation race could hide.
MAX_ORDER_STATE_AGE_S = 5.0

#: the ONLY states that end an order's life at the venue. Anything else --
#: including a string this module has never seen -- is outstanding.
TERMINAL_STATES = frozenset({
    "ORDER_STATE_FILLED",
    "ORDER_STATE_CANCELED",
    "ORDER_STATE_CANCELLED",
    "ORDER_STATE_EXPIRED",
    "ORDER_STATE_REJECTED",
    "ORDER_STATE_DONE_FOR_DAY",
    "ORDER_STATE_REPLACED",
})

#: a terminal state that nevertheless left us holding shares.
FILLED_STATES = frozenset({"ORDER_STATE_FILLED"})

#: states we recognise as LIVE. The point of listing them is that a state
#: in NEITHER set is UNRECOGNISED -- and an unrecognised state is treated as
#: live and reported as unrecognised, rather than quietly accepted because
#: it happened to begin with "ORDER_STATE_".
LIVE_STATES = frozenset({
    "ORDER_STATE_NEW",
    "ORDER_STATE_PENDING_NEW",
    "ORDER_STATE_ACCEPTED",
    "ORDER_STATE_OPEN",
    "ORDER_STATE_RESTING",
    "ORDER_STATE_PARTIALLY_FILLED",
    "ORDER_STATE_PENDING_CANCEL",
    "ORDER_STATE_PENDING_REPLACE",
})

#: the worst-case own-side cost of a submission we recorded but cannot yet
#: price from venue data. It is the frozen ceiling, never an average, and
#: never zero.
WORST_CASE_PENDING_COST_USD = D("0.60")
WORST_CASE_PENDING_QTY = D("0.13")

#: the sentinel the provider publishes when state could not be established.
#: It is TRUTHY, so `if snapshot["our_outstanding_orders"]` refuses -- which
#: is the entire point of choosing a sentinel rather than a zero.
UNKNOWN_OUTSTANDING = "ORDER_STATE_UNKNOWN_FAIL_CLOSED"

#: the prefix `runner.submit_first_leg` gives a submission it recorded but
#: could not identify. See RULE 3 below.
LOCAL_INTENT = "LOCAL_INTENT:"

OPEN_PATH = "/v1/orders/open"
ORDERS_PATH = "/v1/orders?limit=100"


class OrderStateUnavailable(Exception):
    """Venue order state could not be established. Never a zero."""


def _fin(x: Any) -> Optional[D]:
    """Decimal or None. Never raises, never returns NaN or an infinity."""
    if x is None:
        return None
    try:
        d = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _price_value(p: Any) -> Any:
    """PMUS prices arrive as {"value": "...", "currency": "USD"} on reads
    and as a bare string on some rows. Both are accepted; nothing else is
    guessed at."""
    if isinstance(p, Mapping):
        return p.get("value")
    return p


def own_side_price(wire_price: Any, outcome_side: Any, *,
                   outcome_up: Any) -> Optional[D]:
    """The cost to US of one share, from the venue's long-side-only wire.

    `outcome_up` names which outcome side IS the long side for this market,
    so this never hard-codes OUTCOME_SIDE_YES. A wire value for the long
    side is already our cost; for the other side our cost is `1 - wire`.
    """
    w = _fin(wire_price)
    if w is None:
        return None
    if outcome_side is None or outcome_up is None:
        return None
    if str(outcome_side) == str(outcome_up):
        return w
    return D("1") - w


def rows(body: Any, *keys: str) -> Optional[List[Dict[str, Any]]]:
    """The row list, or None if the shape is not one we recognise.

    None is NOT an empty list. An unrecognised body means we do not know
    what the venue said, and the caller must fail closed rather than read
    "no orders" into a response it could not parse.
    """
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, Mapping):
        for k in keys:
            v = body.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return None


def classify(order: Mapping[str, Any], *, outcome_up: Any) -> Dict[str, Any]:
    """One venue order row -> what it means for the invariant."""
    state = order.get("state")
    st = str(state) if state is not None else None
    qty = _fin(order.get("quantity"))
    cum = _fin(order.get("cumQuantity"))
    leaves = _fin(order.get("leavesQuantity"))
    if leaves is None and qty is not None and cum is not None:
        leaves = qty - cum
    wire = _price_value(order.get("price"))
    own = own_side_price(wire, order.get("outcomeSide"),
                         outcome_up=outcome_up)

    known_state = st in TERMINAL_STATES or st in LIVE_STATES
    terminal = st in TERMINAL_STATES
    # RULE 1 AND RULE 2, IN THREE LINES AND IN THIS ORDER.
    #   * a state we do not recognise, or no state at all, is OUTSTANDING;
    #   * a state we recognise as terminal is finished;
    #   * everything else is live -- INCLUDING a partial fill, which still
    #     has leaves resting at the venue and can still fill.
    if st is None or not known_state:
        outstanding = True
    else:
        outstanding = not terminal

    return {
        "id": order.get("id"),
        "slug": order.get("marketSlug"),
        "outcome_side": order.get("outcomeSide"),
        "state": st,
        "state_recognised": bool(known_state),
        "quantity": None if qty is None else str(qty),
        "cum_quantity": None if cum is None else str(cum),
        "leaves_quantity": None if leaves is None else str(leaves),
        "wire_price": None if wire is None else str(wire),
        "own_side_price": None if own is None else str(own),
        "terminal": terminal,
        "outstanding": bool(outstanding),
        "partially_filled": bool(cum is not None and cum > 0
                                 and leaves is not None and leaves > 0),
        "filled": st in FILLED_STATES or bool(
            cum is not None and cum > 0 and leaves is not None
            and leaves <= 0),
    }


def read_order_state(reader: Callable[[str], Any], *,
                     slugs: Iterable[str],
                     outcome_up: Any,
                     now: float,
                     pending_ids: Iterable[str] = (),
                     max_age_s: float = MAX_ORDER_STATE_AGE_S,
                     now_fn: Optional[Callable[[], float]] = None
                     ) -> Dict[str, Any]:
    """Establish outstanding orders and filled inventory FROM THE VENUE.

    `reader` is a one-argument GET callable: path -> decoded body. It is the
    seam the tests drive and in production it is a signed GET-only
    transport. Nothing in this function builds, previews or sends an order.

    Returns a view with `ok`. When `ok` is False the view still carries
    `outstanding_sentinel`, which is TRUTHY, so the eligibility rule that
    consumes it refuses. There is no code path that returns a permissive
    zero on a failed read.
    """
    want = {str(s) for s in slugs if s}
    pend = [str(p) for p in pending_ids if p]
    view: Dict[str, Any] = {
        "ok": False,
        "as_of": float(now),
        "age_s": 0.0,
        "source": "VENUE_AUTHORITATIVE",
        "paths": [OPEN_PATH, ORDERS_PATH],
        "slugs": sorted(want),
        "pending_ids": list(pend),
        "outstanding_sentinel": UNKNOWN_OUTSTANDING,
        "FAILS_CLOSED": True,
    }

    bodies: Dict[str, Any] = {}
    for path in (OPEN_PATH, ORDERS_PATH):
        try:
            bodies[path] = reader(path)
        except Exception as exc:                               # noqa: BLE001
            view["reason"] = "ORDER_STATE_UNREADABLE:%s:%s" % (
                path, type(exc).__name__)
            return view

    open_rows = rows(bodies[OPEN_PATH], "orders", "data")
    hist_rows = rows(bodies[ORDERS_PATH], "orders", "data")
    if open_rows is None or hist_rows is None:
        view["reason"] = "ORDER_STATE_UNPARSABLE"
        return view

    # SCOPE. Only this cohort's markets. An unrelated executor's order in
    # another market is not E1's outstanding order, and counting it would
    # deadlock E1 on someone else's inventory. Rows with no slug are kept,
    # because an unattributable live order is exactly the thing we must not
    # wave past.
    def _in_scope(r: Mapping[str, Any]) -> bool:
        s = r.get("marketSlug")
        return (not want) or s is None or str(s) in want

    by_id: Dict[str, Dict[str, Any]] = {}
    for r in hist_rows:
        if _in_scope(r):
            c = classify(r, outcome_up=outcome_up)
            if c["id"] is not None:
                by_id[str(c["id"])] = c

    open_ids: List[str] = []
    for r in open_rows:
        if not _in_scope(r):
            continue
        c = classify(r, outcome_up=outcome_up)
        oid = str(c["id"]) if c["id"] is not None else None
        # THE OPEN ENDPOINT WINS ON LIVENESS. If an id is listed as open it
        # is open, whatever a paged history says.
        c["outstanding"] = True
        c["listed_open"] = True
        if oid is None:
            # an open order with no id cannot be reconciled. Fail closed.
            view["reason"] = "ORDER_STATE_INCONSISTENT:OPEN_ROW_WITHOUT_ID"
            return view
        open_ids.append(oid)
        prior = by_id.get(oid)
        if prior is not None and prior["terminal"]:
            # THE CANCELLATION RACE, MADE EXPLICIT. One endpoint says this
            # order is finished and the other says it is resting. We do not
            # pick the convenient answer.
            view["reason"] = "ORDER_STATE_INCONSISTENT:%s_OPEN_AND_%s" % (
                oid, prior["state"])
            view["inconsistent_id"] = oid
            return view
        by_id[oid] = c

    # RULE 3, AND THE ONE DISTINCTION IT TURNS ON.
    #
    # A pending entry the venue did not mention is either:
    #
    #   * a LOCAL_INTENT placeholder -- we POSTed and got no identifiable
    #     order back, so we cannot prove an order does not exist. It
    #     COUNTS, indefinitely, until something authoritative resolves it.
    #     This is the ambiguous-acknowledgement case and it is meant to
    #     block; the tracker also stops the cohort on it.
    #
    #   * a CONFIRMED venue order id that neither endpoint lists. Here we
    #     DO have authority: `/v1/orders/open` is the venue's statement of
    #     what is resting, and a successful read that omits an id means
    #     that id is not resting. It is treated as resolved, which is also
    #     what stops a filled order paging out of the history window from
    #     blocking E1 forever.
    unseen_pending = [p for p in pend
                      if p not in by_id and str(p).startswith(LOCAL_INTENT)]
    resolved_pending = [p for p in pend
                        if p not in by_id
                        and not str(p).startswith(LOCAL_INTENT)]
    for p in unseen_pending:
        by_id[p] = {
            "id": p, "slug": None, "outcome_side": None,
            "state": "LOCAL_PENDING_UNCONFIRMED",
            "state_recognised": False,
            "quantity": None, "cum_quantity": None,
            "leaves_quantity": None, "wire_price": None,
            "own_side_price": None, "terminal": False,
            "outstanding": True, "partially_filled": False,
            "filled": False, "locally_pending": True,
        }

    outstanding = [c for c in by_id.values() if c["outstanding"]]

    # RULE 5. Aggregate, in dollars, on the OWN side.
    open_notional = D("0")
    filled_notional = D("0")
    priced = True
    net: Dict[str, D] = {}
    for c in by_id.values():
        own = _fin(c["own_side_price"])
        leaves = _fin(c["leaves_quantity"])
        cum = _fin(c["cum_quantity"])
        if c.get("locally_pending"):
            # A SUBMISSION WE CANNOT PRICE FROM VENUE DATA IS NOT FREE. It
            # is charged at the frozen first-leg ceiling -- the most it
            # could possibly have cost -- so an unconfirmed POST raises
            # exposure rather than vanishing from it.
            open_notional += WORST_CASE_PENDING_QTY \
                * WORST_CASE_PENDING_COST_USD
            continue
        if c["outstanding"] and (own is None or leaves is None):
            # an outstanding order we cannot price is not a $0 exposure.
            priced = False
            continue
        if c["outstanding"] and leaves is not None and own is not None:
            open_notional += max(leaves, D("0")) * own
        if cum is not None and cum > 0:
            if own is not None:
                filled_notional += cum * own
            else:
                priced = False
            side = str(c["outcome_side"])
            net[side] = net.get(side, D("0")) + cum

    if not priced:
        view["reason"] = "ORDER_STATE_INCONSISTENT:UNPRICEABLE_EXPOSURE"
        view["orders"] = sorted(by_id.values(), key=lambda c: str(c["id"]))
        return view

    # THE RESIDUAL. Matched inventory is the min across the two sides; what
    # is left over on one side is unmatched directional exposure.
    matched = min(net.values()) if len(net) >= 2 else D("0")
    residual = None
    for side, q in sorted(net.items()):
        extra = q - matched
        if extra > 0:
            residual = {"outcome_side": side, "qty": str(extra),
                        "matched_qty": str(matched),
                        "is_unmatched_directional_exposure": True}
            break

    # STALENESS IS MEASURED ACROSS THE READ ITSELF, not against the
    # timestamp we wrote a moment earlier. `as_of` is when the first GET
    # went out; the age is how long the whole read took to come back. A
    # hung or slow venue read is stale evidence, and stale evidence refuses.
    age = max(0.0, (float(now_fn()) if now_fn is not None else float(now))
              - float(view["as_of"]))
    if age > float(max_age_s):
        view["age_s"] = age
        view["reason"] = "ORDER_STATE_STALE:%.3fs>%.3fs" % (age, max_age_s)
        return view

    view.update({
        "ok": True,
        "age_s": age,
        "reason": None,
        "orders": sorted(by_id.values(), key=lambda c: str(c["id"])),
        "n_known": len(by_id),
        "outstanding_ids": sorted(str(c["id"]) for c in outstanding),
        "n_outstanding": len(outstanding),
        "open_ids": sorted(open_ids),
        "unconfirmed_pending_ids": sorted(unseen_pending),
        "pending_ids_resolved_by_this_read": sorted(resolved_pending),
        "partially_filled_ids": sorted(str(c["id"]) for c in by_id.values()
                                       if c["partially_filled"]),
        "filled_ids": sorted(str(c["id"]) for c in by_id.values()
                             if c["filled"]),
        "net_filled_by_outcome_side": {k: str(v) for k, v in net.items()},
        "matched_qty": str(matched),
        "residual": residual,
        "open_notional_usd": str(open_notional),
        "filled_notional_usd": str(filled_notional),
        "aggregate_exposure_usd": str(open_notional + filled_notional),
        "outstanding_sentinel": None,
        "PARTIAL_FILL_IS_OUTSTANDING": True,
        "UNKNOWN_STATE_IS_OUTSTANDING": True,
        "AGGREGATE_NOT_PER_ORDER": True,
    })
    return view


def outstanding_for_snapshot(view: Mapping[str, Any]) -> Any:
    """What `pmus_provider` publishes as `our_outstanding_orders`.

    On a good read it is the COUNT -- 0 permits, anything else refuses. On a
    failed read it is the truthy sentinel, so the SAME eligibility rule
    refuses without needing to know why. The field can never again be a
    constant that cannot refuse.
    """
    if not view or not view.get("ok"):
        return UNKNOWN_OUTSTANDING
    return int(view.get("n_outstanding") or 0)


def residual_for_snapshot(view: Mapping[str, Any]) -> Any:
    """What `pmus_provider` publishes as `our_open_residual`. Same shape of
    guarantee: a failed read is truthy, not None."""
    if not view or not view.get("ok"):
        return {"unknown": True, "why": (view or {}).get("reason"),
                "FAILS_CLOSED": True}
    return view.get("residual")


def reconcile_at_startup(view: Mapping[str, Any]) -> Dict[str, Any]:
    """RULE: NO SUBMISSION BEFORE RECONCILIATION.

    Called once before the decision loop opens a window. It does not cancel,
    amend or submit anything -- it decides whether this process is allowed
    to open a first leg at all, given what the venue says already exists.

    A process that restarts into pre-existing resting orders is the restart
    case of the same defect: durable progress correctly refused to grant a
    thirteenth window, but nothing asked the venue what was still live.
    """
    if not view or not view.get("ok"):
        return {"may_submit": False,
                "because": "ORDER_STATE_NOT_ESTABLISHED_AT_STARTUP:%s"
                           % (view or {}).get("reason"),
                "reconciled": False, "FAILS_CLOSED": True}
    n = int(view.get("n_outstanding") or 0)
    resid = view.get("residual")
    reasons: List[str] = []
    if n:
        reasons.append("E3_OUR_ORDER_IS_OUTSTANDING:%d:%s"
                       % (n, ",".join(view.get("outstanding_ids") or [])))
    if resid:
        reasons.append("E3_OUR_RESIDUAL_IS_OPEN:%s@%s"
                       % (resid.get("qty"), resid.get("outcome_side")))
    return {
        "may_submit": not reasons,
        "because": reasons or None,
        "reconciled": True,
        "n_outstanding": n,
        "residual": resid,
        "aggregate_exposure_usd": view.get("aggregate_exposure_usd"),
        # RULE 5, SAID AT STARTUP TOO: a filled first leg is INVENTORY and
        # belongs to complement management, not to another first leg.
        "filled_first_leg_goes_to_complement_management": bool(resid),
        "NOT_A_CANCEL": True,
    }


def can_read_venue() -> bool:
    """Whether a signed GET is even possible in this process.

    Asked BEFORE building a reader, so a process with no credentials fails
    closed without opening a socket. Any import or probe failure answers
    "no", because the only safe answer to "can I establish order state?"
    when we do not know is no.
    """
    try:
        from src import pmus_auth as A
        return bool(A.has_credentials())
    except Exception:                                          # noqa: BLE001
        return False


def venue_reader(transport: Any = None) -> Callable[[str], Any]:
    """A GET-only reader over the signed PMUS transport.

    Defaults to `src.pmus_auth.ReadOnlyTransport`, which raises
    PermissionError on any method but GET -- so the object that establishes
    the safety invariant is structurally incapable of placing an order.
    """
    if transport is None:
        from src import pmus_auth as A
        transport = A.ReadOnlyTransport()

    def _read(path: str) -> Any:
        return transport.request("GET", path)

    return _read
