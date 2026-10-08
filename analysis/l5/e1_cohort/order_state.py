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

#: THE ONLY ORDER-STATE ENDPOINTS PMUS ACTUALLY SERVES.
#:
#: The first version of this module required `/v1/orders?limit=100`, which
#: returns **HTTP 501 Not Implemented** on the account host. So every read
#: failed closed -- safe, and unusable. A venue probe established the whole
#: surface: `/v1/orders`, `?status=all`, `/v1/orders/history`,
#: `/v1/orders/closed`, `/v1/mytrades`, `/v1/trades` and `/v1/fills` are all
#: 501 or 404. There is NO order-history and NO trade endpoint.
#:
#: What exists, and what each is authority for:
#:
#:   /v1/orders/open          what is RESTING right now
#:   /v1/order/{id}           one order's final state and cumQuantity
#:   /v1/portfolio/positions  current inventory
#:   /v1/portfolio/activities fills and settlements, by order id
#:
#: Fills therefore come from per-id `cumQuantity`, which is both simpler and
#: stronger than a trade feed: it is the venue's own figure for OUR order.
OPEN_PATH = "/v1/orders/open"
ORDER_PATH = "/v1/order/%s"
POSITIONS_PATH = "/v1/portfolio/positions"
ACTIVITIES_PATH = "/v1/portfolio/activities?limit=50"

#: roles an order can play. The distinction is load-bearing: a resting FIRST
#: LEG must block another first leg, and must NOT block the complement that
#: closes it.
FIRST_LEG = "first_leg"
COMPLEMENT = "complement"


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


#: how much venue-reported inventory may exceed what our own filled orders
#: account for before the view refuses.
#:
#: ONE THOUSANDTH OF A SHARE, not one share. My first value here was 0.999,
#: reasoning that the venue rounds its display position to whole shares --
#: which is true of the integer `netPosition` field and irrelevant, because
#: this code reads `netPositionDecimal` when it is present, and the real
#: 1215z row carried `2.6000`. At 0.999 an ENTIRE untracked 0.13-share fill
#: would have slipped through the cross-check, which is precisely the
#: inventory-we-do-not-know-about case the check exists for. E1's whole
#: order size is 0.13; a tolerance larger than an order is not a tolerance.
POSITION_TOLERANCE = D("0.001")


def _positions_for(body: Any, want: Iterable[str]
                   ) -> Optional[Dict[str, D]]:
    """Venue inventory for OUR markets, or None if the body is unreadable.

    None is not "no positions". `/v1/portfolio/positions` answers with both
    a `positions` mapping and an `availablePositions` list depending on
    state, and an empty account answers `{"positions": {}, ...}`, so an
    empty-but-recognised body is {} while an unrecognised one is None.
    """
    if not isinstance(body, Mapping):
        return None
    scope = {str(w) for w in want}
    out: Dict[str, D] = {}
    seen_shape = False
    for key in ("positions", "availablePositions", "data"):
        v = body.get(key)
        if isinstance(v, Mapping):
            seen_shape = True
            for slug, row in v.items():
                if scope and str(slug) not in scope:
                    continue
                q = _fin(row.get("netPositionDecimal")
                         if isinstance(row, Mapping) else row) \
                    or _fin(row.get("netPosition")
                            if isinstance(row, Mapping) else None)
                if q is not None and q != 0:
                    out[str(slug)] = q
        elif isinstance(v, list):
            seen_shape = True
            for row in v:
                if not isinstance(row, Mapping):
                    continue
                slug = str(row.get("marketSlug") or row.get("slug") or "")
                if scope and slug not in scope:
                    continue
                q = (_fin(row.get("netPositionDecimal"))
                     or _fin(row.get("netPosition"))
                     or _fin(row.get("quantity")))
                if q is not None and q != 0:
                    out[slug] = out.get(slug, D("0")) + q
    return out if seen_shape else None


def settlement_for(reader: Callable[[str], Any], *, slug: str,
                   expected_qty: Any, pages: int = 20) -> Dict[str, Any]:
    """The venue's settlement record for ONE market, attributed by an EXACT
    quantity match against what our own orders filled.

    THIS IS THE 1215z RULE, MADE REUSABLE. The account is shared: 1,100
    activity rows, 901 of them trades, mostly other strategies at sizes
    like 16.21 and 23.49 shares. A position resolution naming our market is
    therefore not automatically ours. It is treated as E1's only when its
    net position equals our filled quantity exactly -- 2.6000 against
    2.60 -- and otherwise returned UNCONFIRMED with its own figures intact
    so a human can see why.

    No cashflow is ever apportioned, inferred from a balance delta, or
    netted to zero.
    """
    want = _fin(expected_qty)
    out: Dict[str, Any] = {"slug": slug, "expected_qty": None if want is None
                           else str(want), "rows": [], "attributed": None,
                           "ATTRIBUTION_IS_BY_EXACT_QTY_MATCH": True,
                           "NEVER_APPORTIONED": True}
    cursor = ""
    for _ in range(int(pages)):
        path = ACTIVITIES_PATH + ("&cursor=%s" % cursor if cursor else "")
        try:
            body = reader(path)
        except Exception as exc:                               # noqa: BLE001
            out["error"] = "ACTIVITIES_UNREADABLE:%s" % type(exc).__name__
            return out
        if not isinstance(body, Mapping):
            out["error"] = "ACTIVITIES_UNPARSABLE"
            return out
        for a in (body.get("activities") or []):
            if not isinstance(a, Mapping):
                continue
            if str(a.get("type")) != "ACTIVITY_TYPE_POSITION_RESOLUTION":
                continue
            pr = a.get("positionResolution") or {}
            if str(pr.get("marketSlug")) != str(slug):
                continue
            before = pr.get("beforePosition") or {}
            netq = (_fin(before.get("netPositionDecimal"))
                    or _fin(pr.get("netPositionDecimal")))
            cost = _fin(_price_value(before.get("cost")))
            cash = _fin(_price_value(before.get("cashValue")))
            fees_raw = _price_value(before.get("fees"))
            fees = _fin(fees_raw)
            row = {
                "net_position_decimal": None if netq is None else str(netq),
                "cost_usd": None if cost is None else str(cost),
                "cash_value_usd": None if cash is None else str(cash),
                # null is NOT zero. Other strategies' rows on this account
                # populate `fees`, so an absent value is undisclosed, and
                # the net below says so instead of assuming.
                "settlement_fees_usd": None if fees is None else str(fees),
                "settlement_fees_disclosed": fees_raw is not None,
                "avg_px": str(_fin(_price_value(before.get("avgPx")))),
                "outcome": ((before.get("marketMetadata") or {})
                            .get("outcome")),
                "exact_qty_match": (want is not None and netq is not None
                                    and netq == want),
            }
            if cost is not None and cash is not None:
                row["gross_net_usd"] = str(cash - cost)
                row["net_usd_less_undisclosed_fees"] = str(
                    cash - cost - (fees or D("0")))
            out["rows"].append(row)
            if row["exact_qty_match"] and out["attributed"] is None:
                out["attributed"] = row
        cursor = body.get("nextCursor") or ""
        if body.get("eof") or not cursor:
            break
    if out["attributed"] is None:
        out["label"] = "UNCONFIRMED_NOT_ATTRIBUTABLE_TO_E1"
    else:
        out["label"] = "SETTLED_VENUE_CONFIRMED_EXACT_QTY_MATCH"
    return out


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
                     known_ids: Iterable[str] = (),
                     roles: Optional[Mapping[str, str]] = None,
                     pending_ids: Iterable[str] = (),
                     max_age_s: float = MAX_ORDER_STATE_AGE_S,
                     now_fn: Optional[Callable[[], float]] = None
                     ) -> Dict[str, Any]:
    """Establish outstanding orders and filled inventory FROM THE VENUE.

    ON THE FOUR ENDPOINTS, AND WHY THERE IS NO HISTORY READ. `/v1/orders`
    is HTTP 501 here and every history and trade path is 404, so there is
    no way to ask "what were all my orders". The answer is to stop asking:
    E1 KNOWS ITS OWN ORDER IDS -- they are in its append-only tape -- so
    `known_ids` names them and each one is read individually. That is
    stronger than a history feed, because it is exact-id attribution on an
    account shared with other strategies, where a slug-scoped sum pulled in
    a counterparty's $28.92 commission and other desks' 16-share trades.

    `roles` maps an order id to `FIRST_LEG` or `COMPLEMENT`. An id with no
    role is treated as a FIRST LEG, which is the conservative reading: it
    blocks another first leg rather than being waved through.

    `reader` is a one-argument GET callable: path -> decoded body, the seam
    the tests drive; in production it is a signed GET-only transport.
    Nothing here builds, previews or sends an order.

    Returns a view with `ok`. When `ok` is False the view still carries
    `outstanding_sentinel`, which is TRUTHY, so the eligibility rule that
    consumes it refuses. There is no code path from a failed read to a
    permissive zero.
    """
    want = {str(s) for s in slugs if s}
    pend = [str(p) for p in pending_ids if p]
    known = [str(i) for i in known_ids if i]
    role_of = {str(k): str(v) for k, v in (roles or {}).items()}
    view: Dict[str, Any] = {
        "ok": False,
        "as_of": float(now),
        "age_s": 0.0,
        "source": "VENUE_AUTHORITATIVE",
        "paths": [OPEN_PATH, ORDER_PATH % "{id}", POSITIONS_PATH],
        "endpoint_note": "/v1/orders is HTTP 501 on this venue; there is no "
                         "order-history or trade endpoint, so fills come "
                         "from per-id cumQuantity",
        "slugs": sorted(want),
        "known_ids": sorted(known),
        "pending_ids": list(pend),
        "outstanding_sentinel": UNKNOWN_OUTSTANDING,
        "FAILS_CLOSED": True,
    }

    try:
        open_body = reader(OPEN_PATH)
    except Exception as exc:                                   # noqa: BLE001
        view["reason"] = "ORDER_STATE_UNREADABLE:%s:%s" % (
            OPEN_PATH, type(exc).__name__)
        return view
    open_rows = rows(open_body, "orders", "data")
    if open_rows is None:
        view["reason"] = "ORDER_STATE_UNPARSABLE:%s" % OPEN_PATH
        return view

    # PER-ID READS. Every id this cohort has ever submitted, plus anything
    # resting, plus any confirmed pending id. An id we cannot read is NOT
    # assumed finished -- it fails the whole view closed, because an
    # unreadable order of ours is exactly the state we must not trade over.
    hist_rows: List[Dict[str, Any]] = []
    resting_ids = {str(r.get("id")) for r in open_rows if r.get("id")}
    absent: List[str] = []
    id_set = list(dict.fromkeys(
        known
        + sorted(resting_ids)
        + [p for p in pend if not p.startswith(LOCAL_INTENT)]))
    for oid in id_set:
        try:
            body = reader(ORDER_PATH % oid)
        except Exception as exc:                               # noqa: BLE001
            code = getattr(exc, "code", None)
            # A 404 IS AN ANSWER, NOT A FAILURE. The venue is saying "no
            # such order", which is definite -- unlike a timeout or a 5xx,
            # where we learn nothing. An id the venue does not have is not
            # outstanding.
            #
            # UNLESS the open-orders list just told us it IS resting. Then
            # the two endpoints contradict each other about an order of
            # ours, and we stop rather than pick the convenient one.
            if code == 404 and oid not in resting_ids:
                absent.append(oid)
                continue
            if code == 404:
                view["reason"] = ("ORDER_STATE_INCONSISTENT:%s_IS_RESTING_"
                                  "BUT_404_ON_ITS_OWN_RECORD" % oid)
                return view
            view["reason"] = "ORDER_STATE_UNREADABLE:%s:%s:%s" % (
                ORDER_PATH % oid, type(exc).__name__, code)
            return view
        row = body.get("order") if isinstance(body, Mapping) else None
        if row is None and isinstance(body, Mapping) and body.get("id"):
            row = body
        if not isinstance(row, Mapping):
            view["reason"] = "ORDER_STATE_UNPARSABLE:%s" % (
                ORDER_PATH % oid)
            return view
        r = dict(row)
        r.setdefault("id", oid)
        hist_rows.append(r)

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

    # ROLES. An id with no recorded role is a FIRST LEG, conservatively: it
    # then blocks another first leg rather than slipping through as "some
    # other order".
    for oid, c in by_id.items():
        c["role"] = role_of.get(str(oid), FIRST_LEG)

    outstanding = [c for c in by_id.values() if c["outstanding"]]
    outstanding_first = [c for c in outstanding if c["role"] == FIRST_LEG]
    outstanding_comp = [c for c in outstanding if c["role"] == COMPLEMENT]

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

    # THE POSITIONS CROSS-CHECK. `/v1/portfolio/positions` is the venue's
    # own statement of inventory, and it is read as a SECOND OPINION on the
    # inventory we derived from per-id cumQuantity. If the venue reports a
    # position in one of our markets that our own orders cannot account
    # for, something is wrong with our picture and we stop rather than
    # trade on it. An absent or unparsable positions body is itself a
    # refusal, for the same reason.
    try:
        pos_body = reader(POSITIONS_PATH)
    except Exception as exc:                                   # noqa: BLE001
        view["reason"] = "ORDER_STATE_UNREADABLE:%s:%s" % (
            POSITIONS_PATH, type(exc).__name__)
        return view
    venue_positions = _positions_for(pos_body, want)
    if venue_positions is None:
        view["reason"] = "ORDER_STATE_UNPARSABLE:%s" % POSITIONS_PATH
        return view
    derived_net_qty = sum(net.values()) if net else D("0")
    venue_net_qty = sum(venue_positions.values()) if venue_positions \
        else D("0")
    # A venue position we cannot source from our own filled orders is the
    # 1215z lesson in the other direction: never let inventory exist that
    # our accounting does not know about.
    if abs(venue_net_qty) > abs(derived_net_qty) + POSITION_TOLERANCE:
        view["reason"] = (
            "ORDER_STATE_INCONSISTENT:VENUE_POSITION_%s_EXCEEDS_OUR_"
            "DERIVED_%s" % (venue_net_qty, derived_net_qty))
        view["venue_positions"] = {k: str(v)
                                   for k, v in venue_positions.items()}
        return view

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
        # THE ROLE SPLIT, which is what makes a correctly managed pair
        # possible at all. A resting FIRST LEG blocks another first leg and
        # must NOT block the complement that closes it.
        "n_outstanding_first_legs": len(outstanding_first),
        "n_outstanding_complements": len(outstanding_comp),
        "outstanding_first_leg_ids": sorted(str(c["id"])
                                            for c in outstanding_first),
        "outstanding_complement_ids": sorted(str(c["id"])
                                             for c in outstanding_comp),
        "venue_positions": {k: str(v) for k, v in venue_positions.items()},
        "derived_net_qty": str(derived_net_qty),
        "venue_net_qty": str(venue_net_qty),
        "positions_corroborate_our_orders": True,
        "open_ids": sorted(open_ids),
        "unconfirmed_pending_ids": sorted(unseen_pending),
        "pending_ids_resolved_by_this_read": sorted(resolved_pending),
        "ids_absent_at_venue_404": sorted(absent),
        "A_404_IS_AN_ANSWER_A_TIMEOUT_IS_NOT": True,
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
    """What `pmus_provider` publishes as `our_outstanding_orders`, read by
    E1's E3 rule on the FIRST-LEG path.

    It counts OUTSTANDING FIRST LEGS, not every outstanding order. That is
    the point of the role split: a resting first leg must block another
    first leg, while the complement that closes it is submitted through
    `submit_complement`, which has its own authorization
    (`authorize_complement`) and its own latch (`consume_complement`) and
    never consults `eligible()`. If this counted complements too, a filled
    first leg would block its own hedge -- turning a safety rule into the
    cause of an unhedged position, which is the exact failure the 1215z
    window already produced once.

    On a good read it is the count -- 0 permits, anything else refuses. On a
    failed read it is the truthy sentinel, so the SAME rule refuses without
    needing to know why. The field can never again be a constant that
    cannot refuse.
    """
    if not view or not view.get("ok"):
        return UNKNOWN_OUTSTANDING
    n = view.get("n_outstanding_first_legs")
    if n is None:                      # older view shape: be conservative
        n = view.get("n_outstanding")
    return int(n or 0)


def complement_gate(view: Mapping[str, Any], *,
                    residual_qty: Any = None) -> Dict[str, Any]:
    """May a COMPLEMENT be submitted right now?

    DELIBERATELY NOT THE FIRST-LEG GATE. A resting or filled first leg is
    the REASON to submit a complement, so it cannot be the thing that
    blocks one. What does block one:

      * order state that could not be established (fail closed, as ever);
      * a complement of ours already outstanding -- one at a time, the same
        discipline as first legs, so tranches cannot stack;
      * nothing to complement.

    This returns a decision and its reasons; it submits nothing, and it
    cannot relax a price, a ceiling or a tranche rule.
    """
    if not view or not view.get("ok"):
        return {"may_submit_complement": False,
                "because": ["COMPLEMENT_ORDER_STATE_NOT_ESTABLISHED:%s"
                            % (view or {}).get("reason")],
                "FAILS_CLOSED": True,
                "blocked_by_first_leg_guard": False}
    reasons: List[str] = []
    n_comp = int(view.get("n_outstanding_complements") or 0)
    if n_comp:
        reasons.append("COMPLEMENT_ALREADY_OUTSTANDING:%d:%s"
                       % (n_comp, ",".join(
                           view.get("outstanding_complement_ids") or [])))
    resid = view.get("residual")
    q = _fin(residual_qty) if residual_qty is not None else (
        _fin((resid or {}).get("qty")) if resid else None)
    if q is None or q <= 0:
        reasons.append("NOTHING_TO_COMPLEMENT")
    return {
        "may_submit_complement": not reasons,
        "because": reasons or None,
        "qty_to_complement": None if q is None else str(q),
        "residual": resid,
        # stated explicitly so a future reader cannot re-conflate the two
        # gates: an outstanding FIRST LEG is visible here and is NOT a
        # blocker.
        "n_outstanding_first_legs": int(
            view.get("n_outstanding_first_legs") or 0),
        "blocked_by_first_leg_guard": False,
        "has_its_own_authorized_path": "authorization.authorize_complement",
        "has_its_own_latch": "latch.consume_complement",
        "does_not_consume_a_first_leg_attempt_slot": True,
    }


def window_exposure(view: Mapping[str, Any], *,
                    prospective_qty: Any = None,
                    prospective_price: Any = None) -> Dict[str, Any]:
    """Aggregate dollars at risk in this window: resting first legs, resting
    complements, filled inventory, and the attempt about to be sent.

    BOTH ROLES COUNT. A complement is a real order at a real price and its
    cost is real money, so excluding it would reproduce the original defect
    one level up -- a bound that cannot see half the orders. Twenty 0.13
    orders pass every per-order check ever written; only a sum objects.
    """
    if not view or not view.get("ok"):
        return {"ok": False,
                "because": "EXPOSURE_UNKNOWN:%s" % (view or {}).get("reason"),
                "FAILS_CLOSED": True}
    openn = _fin(view.get("open_notional_usd")) or D("0")
    filled = _fin(view.get("filled_notional_usd")) or D("0")
    q = _fin(prospective_qty) or D("0")
    px = _fin(prospective_price) or D("0")
    prospective = q * px
    return {
        "ok": True,
        "open_notional_usd": str(openn),
        "filled_notional_usd": str(filled),
        "prospective_usd": str(prospective),
        "total_usd": str(openn + filled + prospective),
        "counts_first_legs_and_complements": True,
        "counts_filled_inventory": True,
        "counts_the_attempt_about_to_be_sent": True,
    }


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
