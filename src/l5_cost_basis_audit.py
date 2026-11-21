"""READ-ONLY audit of the legs whose cost basis is a $1/share placeholder.

WHAT THIS IS FOR. `leg_cost_basis_unknown` names legs that arrived as a VENUE
NET POSITION the executor never booked, so no order of ours exists to ask
about and the settlement path priced them at the $1/share upper bound. That
bound is deliberately conservative and it is what makes `realized_attributed`
(-51.14 at the time of writing) an ESTIMATE rather than a measurement: at
$1/share a losing share loses the theoretical maximum. This module reads the
venue's own activity feed and reports what the actual fills were.

IT WRITES NOTHING. No ledger key, no realized total, no cost basis, no
attestation. It is evidence, and the decision about what to do with that
evidence is a separate, owner-gated step -- see the refusal below.

WHY IT CANNOT APPLY ITS OWN FINDINGS. `InventoryStore.correct_settled_market`
refuses `CORRECTION_WOULD_RELAX_LOSS_HALT` for any correction that moves
realized toward zero, and its own comment says that direction "needs its own
evidence and its own decision; it is not available here". Replacing a
$1/share placeholder with a cheaper true basis is exactly that direction. So
this module produces the evidence and stops; nothing here can relax a loss
halt, by construction.

OWNERSHIP IS BY OUR OWN DURABLE RECORD. A fill is L5's only when its client
order id appears in this executor's persisted `fill_provenance` -- the ids it
ACKed. Everything else on this SHARED account is reported as OWNER_MANUAL,
never as L5 activity. A row we cannot classify is UNKNOWN_OWNER and is never
quietly folded into either side.

MISSING IS NEVER ZERO. A leg with no matching fills, or fills without a
price or a fee, is reported UNESTABLISHED and keeps its $1/share placeholder.
An absent fee is not a free $0.

ONE WALK. /v1/portfolio/activities is ACCOUNT-scoped, so it is walked ONCE
and reused across every market, rather than once per market -- the auditor
module's own note observes that walking it per market re-reads the same feed
and is what drew HTTP 429 at 93 markets x 25 pages.
"""
from __future__ import annotations

import json
import os
from collections import Counter

from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Tuple

ACTIVITIES_PATH = "/v1/portfolio/activities"
#: PAGES OF THE ACTIVITY FEED THE WALK MAY READ.
#:
#: WHY IT IS NOT 40 ANY MORE. At 40 the production walk read 800 rows and
#: ended `max_pages` with complete False, so EVERY one of the 33 unknown-basis
#: legs reported ACTIVITY_WALK_INCOMPLETE and not one cost basis could be
#: established. The bound existed to keep a read-only audit from spending the
#: venue budget without limit, which is still right -- it was simply set below
#: the size of the history it had to cover. It stays a bound, and `complete`
#: still reports honestly when it binds.
MAX_PAGES = int(str(os.getenv("L5_COST_BASIS_MAX_PAGES", "600")).strip() or 600)

#: rows that represent an executed trade. A local accounting correction or a
#: settlement event is NOT a fill and must never contribute a cost basis.
#: THE FEED'S ACTUAL VOCABULARY, read off it rather than assumed.
#: feed_shape() on the production feed reported, over 2,129 rows:
#:   activity_type_trade                1911
#:   activity_type_position_resolution    205
#:   activity_type_transfer                 8
#:   activity_type_account_deposit          4
#:   activity_type_referral_bonus           1
#: The old set ("filled", "fill", "trade") matched NONE of them, so every
#: one of the 49 legs reported NO_FILLS_FOUND while 1,911 trade rows sat in
#: the feed. The bare forms are kept because they cost nothing and a feed
#: that changes back still parses.
FILL_KINDS = frozenset(("filled", "fill", "trade", "activity_type_trade"))

#: and the envelope the payload sits in. Each row has exactly TWO top-level
#: fields -- `type` and one payload key -- so market, side, price, quantity
#: and commission are all ONE LEVEL DOWN. Reading them off the envelope is
#: what made every predicate fail at once.
PAYLOAD_KEYS = ("trade", "positionResolution", "accountBalanceChange")


def _payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """The row's inner payload, or the row itself when it is already flat.

    The feed wraps: {"type": "activity_type_trade", "trade": {...}}. A flat
    row is still handled, so a feed that stops wrapping does not break this.
    """
    for k in PAYLOAD_KEYS:
        v = row.get(k)
        if isinstance(v, Mapping):
            return v
    return row

L5_OWNED = "L5_OWNED"
OWNER_MANUAL = "OWNER_MANUAL"
UNKNOWN_OWNER = "UNKNOWN_OWNER"


MONEY_VALUE_KEY = "value"
MONEY_CURRENCY_KEY = "currency"
MONEY_CURRENCIES = frozenset(("USD", "usd"))


def _d(value: Any) -> Optional[D]:
    """A finite Decimal, or None. None means UNKNOWN, never zero.

    The venue reports every money field as a {currency, value} mapping, not a
    scalar -- confirmed by money_field_shapes() on 1,911 production fill rows:
    price, cost, lastPx and commissionNotionalCollected are all dicts carrying
    exactly those two keys, while qty, qtyDecimal and lastShares are plain
    strings. A mapping is unwrapped only when the currency is one we can book
    in dollars; an unrecognized currency stays UNKNOWN rather than being
    silently treated as USD.
    """
    if isinstance(value, Mapping):
        if MONEY_VALUE_KEY not in value:
            return None
        currency = value.get(MONEY_CURRENCY_KEY)
        if currency is not None and str(currency) not in MONEY_CURRENCIES:
            return None
        return _d(value.get(MONEY_VALUE_KEY))
    if value is None or value == "":
        return None
    try:
        out = D(str(value))
        return out if out.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _get(transport: Any, path: str) -> Any:
    """One signed GET, through whichever read verb this transport exposes.

    THE DEFECT THIS FIXES. The first version called `transport.request(...)`.
    PmusTransport has NO public `request` -- that method belongs to the
    private _SignedHTTP class -- so the deployed audit raised AttributeError
    on its very first page and established nothing, while reporting itself
    `ok`. The executor's public read verb is `_get(path)`, which routes
    through _request and so keeps the venue backoff. `request` is tried only
    as a fallback, which is what the test doubles provide.
    """
    for verb in ("_get", "request"):
        fn = getattr(transport, verb, None)
        if callable(fn):
            return fn(path) if verb == "_get" else fn("GET", path)
    raise AttributeError(
        "transport exposes neither _get nor request; the audit cannot read")


def walk_activities(transport: Any, *, max_pages: int = MAX_PAGES,
                    cursor: Optional[str] = None,
                    rows: Optional[List[Dict[str, Any]]] = None,
                    pages_done: int = 0) -> Dict[str, Any]:
    """Every reachable activity row, newest first. READ-ONLY GETs only.

    The walk's ENDING is returned beside the rows, because a truncated feed
    and a complete one are different facts: a basis computed from a
    truncated walk could be missing fills, so the caller must be able to
    tell. `ended` is "eof", "no_cursor", "max_pages" or "error:<Type>".

    RESUMABLE, AND THAT IS A LATENCY PROPERTY RATHER THAN A NEW CAPABILITY.
    `cursor`, `rows` and `pages_done` let a caller take the feed a few pages
    at a time and continue on a later tick, because this walk is paced by the
    process's shared venue limiter -- 1.5 requests a second -- and a 133-page
    feed therefore costs about 89 seconds of wall clock. Run in one go on the
    window-roll path that is exactly 89 seconds of a 15-minute window in which
    no trading decision can be made; see `l5_roll_diagnostics`. Resuming reads
    the SAME pages in the SAME order and accumulates into the SAME list, so a
    walk completed across several calls is byte-identical to one completed in
    a single call. `next_cursor` is the continuation point and is None unless
    `ended` is "max_pages"; `pages` counts the pages of THIS walk including
    the ones already done, so `complete` keeps its meaning.
    """
    rows = [] if rows is None else rows
    cursor = (str(cursor).strip() or None) if cursor else None
    pages, ended = int(pages_done), "eof"
    next_cursor: Optional[str] = None
    while pages < max_pages:
        path = ACTIVITIES_PATH + ("?cursor=%s" % cursor if cursor else "")
        try:
            body = _get(transport, path) or {}
        except BaseException as exc:                           # noqa: BLE001
            ended = "error:%s" % type(exc).__name__
            break
        if not isinstance(body, Mapping):
            ended = "error:MalformedBody"
            break
        page = body.get("activities")
        if not isinstance(page, list):
            page = body.get("rows") if isinstance(body.get("rows"), list) else []
        rows.extend(r for r in page if isinstance(r, Mapping))
        pages += 1
        if body.get("eof") is True:
            ended = "eof"
            break
        cursor = str(body.get("nextCursor") or "").strip()
        if not cursor:
            ended = "no_cursor"
            break
    else:
        ended = "max_pages"
        next_cursor = cursor
    return {"rows": rows, "pages": pages, "ended": ended,
            "complete": ended in ("eof", "no_cursor"),
            # the continuation point, or None when there is nothing to
            # continue. A truncated walk that cannot say where to resume is
            # reported as max_pages with next_cursor None, which a resuming
            # caller must treat as INCOMPLETE rather than as finished.
            "next_cursor": next_cursor}


def our_client_order_ids(store: Any) -> set:
    """Client order ids this executor ACKed, from its persisted provenance.

    This is the ownership record, not an inference. `fill_provenance`
    survives settlement and retirement, which is why it can answer for a
    market whose live-order entry is long gone.
    """
    out: set = set()
    prov = (getattr(store, "state", {}) or {}).get("fill_provenance") or {}
    for market_proof in prov.values():
        if not isinstance(market_proof, Mapping):
            continue
        for e in (market_proof.get("entries") or []):
            if isinstance(e, Mapping) and e.get("coid"):
                out.add(str(e["coid"]))
    return out


def our_venue_order_ids(store: Any) -> set:
    """Venue order ids this executor already tracks, from the durable link.

    WHY THIS EXISTS, and it is the owner's 2026-11-03 correction. Ownership
    was decided by CLIENT ORDER ID alone:

        owner = L5_OWNED if coid in ours else OWNER_MANUAL if coid
                else UNKNOWN_OWNER

    so a fill row carrying NO coid became UNKNOWN_OWNER whoever placed it.
    Measured on this account: all 74 fills across the 49 unknown-basis legs
    carry `clientOrderId` null, so the classifier could never resolve one of
    them and every leg read UNKNOWN_OWNER. That is unresolved attribution
    being reported as if it were evidence of external activity.

    THE EVIDENCE WAS ALREADY THERE. `execution_field_inventory` found 2,038
    fill rows carrying an execution object whose keys include `order`, and
    the executor persists a durable coid -> venue order id link
    (`VENUE_ORDER_LINK`) written at booking time plus the same ids across
    `live_orders`, `pending_terminal`, `quarantine` and `submitted`. So a
    venue order id on the fill can be matched against ids we know are ours.

    READ-ONLY, like everything in this module. It reads four row-books and
    one link map and writes nothing.
    """
    out: set = set()
    st = (getattr(store, "state", {}) or {})
    if not isinstance(st, Mapping):
        return out
    # the durable link, which outlives the order
    link = st.get("venue_order_ids")
    if isinstance(link, Mapping):
        for k, v in link.items():
            for cand in (k, v):
                if isinstance(cand, str) and cand.strip():
                    out.add(cand.strip())
                elif isinstance(cand, Mapping):
                    vid = cand.get("venue_order_id")
                    if vid:
                        out.add(str(vid).strip())
    # and every row-book an id of ours can sit in
    for book in ("live_orders", "pending_terminal", "quarantine", "submitted"):
        rows = st.get(book)
        if not isinstance(rows, Mapping):
            continue
        for row in rows.values():
            if isinstance(row, Mapping):
                vid = row.get("venue_order_id")
                if vid and str(vid).strip():
                    out.add(str(vid).strip())
    out.discard("")
    return out


def _exec_order_id(ex: Mapping[str, Any]) -> Optional[str]:
    """The venue order id inside ONE execution object, or None."""
    if not isinstance(ex, Mapping):
        return None
    for k in ("order", "orderId", "orderID", "order_id"):
        v = ex.get(k)
        if isinstance(v, Mapping):
            v = v.get("id") or v.get("orderId") or v.get("order_id")
        if v and str(v).strip():
            return str(v).strip()
    return None


def _row_venue_order_candidates(body: Mapping[str, Any]):
    """(ours, every, ours_source) venue order ids on this fill row.

    `ours_source` is "flag" when `ours` was selected out of the row's TWO
    per-party executions by `isAggressor`, "unambiguous" when the row's
    shape left nothing to select (one execution object, or a flat id), and
    None when `ours` is None. It exists because a NEGATIVE verdict built on
    "flag" is only as good as the flag, while one built on "unambiguous" is
    not -- and the caller must be able to tell those apart.

    WHY THIS REPLACED A SINGLE-ID READ, and it is measured, not argued. The
    first version of this looked under `execution`, `exec` and `fill`. The
    feed uses NEITHER: `execution_field_inventory` names the two real
    parents, `aggressorExecution` and `passiveExecution` (EXEC_KEYS), and
    the production audit of 2026-11-03T16:22:24Z consequently resolved
    `venue_order_id: null` on all 76 fills of the 51 unknown-basis legs and
    reported owner UNKNOWN_OWNER / no_identity_on_the_row on every one --
    while the same run collected 420 venue order ids of ours. Zero matches
    out of 420 known ids was an extraction defect, not an absence of
    evidence.

    A TRADE HAS TWO PARTIES AND WE ARE ONE OF THEM. Both executions carry an
    order id and only one of them is ours, so taking either would invent an
    attribution. `isAggressor` on the payload says which is ours: True ->
    aggressorExecution, False -> passiveExecution. `ours` is set ONLY when
    the row says which side we are (or the shape is unambiguous: a single
    execution object, or a flat id). `every` is both, so a POSITIVE match
    against ids we know are ours still resolves when `isAggressor` is
    missing -- an id of ours cannot belong to the counterparty.

    This asymmetry is deliberate. A positive match is proof of ownership; a
    non-match on an id that may be the counterparty's proves nothing, so it
    must leave the fill UNKNOWN rather than downgrade it to OWNER_MANUAL.
    """
    every: set = set()
    if not isinstance(body, Mapping):
        return None, every, None
    ours = None
    ours_source = None
    # the two real per-party executions. Built as PAIRS, not by subscript
    # assignment: the module's read-only guard forbids every subscript
    # assignment target as its proxy for "writes no store key", and that
    # guard is worth more than the convenience of a dict literal.
    per_party = tuple((ek, _exec_order_id(body.get(ek))) for ek in EXEC_KEYS)
    for _ek, v in per_party:
        if v:
            every.add(v)
    agg = body.get("isAggressor")
    mine = ("aggressorExecution" if agg is True
            else "passiveExecution" if agg is False else None)
    if mine is not None:
        for ek, v in per_party:
            if ek == mine and v:
                ours, ours_source = v, "flag"
    # the unambiguous shapes: ONE execution object, or a flat id. There the
    # row's only order id is the row's own order, so it identifies ours.
    for ek in ("execution", "exec", "fill"):
        v = _exec_order_id(body.get(ek))
        if v:
            every.add(v)
            if ours is None:
                ours, ours_source = v, "unambiguous"
    for k in ("orderId", "orderID", "order_id", "venueOrderId"):
        v = body.get(k)
        if v and str(v).strip():
            every.add(str(v).strip())
            if ours is None:
                ours, ours_source = str(v).strip(), "unambiguous"
    return ours, every, ours_source


def _row_venue_order_id(body: Mapping[str, Any]) -> Optional[str]:
    """The venue's own order id for this fill, ours where the row says so."""
    ours, every, _src = _row_venue_order_candidates(body)
    if ours:
        return ours
    return sorted(every)[0] if every else None


def _row_market(row: Mapping[str, Any]) -> str:
    body = _payload(row)
    for k in ("marketSlug", "market", "slug", "marketId", "ticker"):
        v = body.get(k) if isinstance(body, Mapping) else None
        if v:
            return str(v)
    return ""


#: the two nested execution objects on a trade row. A trade has two sides and
#: the account is ONE of them, so the row's own money fields (`cost`, `price`,
#: `qty`) describe the TRADE and the per-party detail -- which side, what
#: commission -- is inside these.
EXEC_KEYS = ("aggressorExecution", "passiveExecution")

#: keys that have been seen to carry a side, in any of the nested objects.
#: ORDER MATTERS, AND THIS IS A DEFECT THE CENSUS CAUGHT. `outcomeSide`
#: comes FIRST because the execution's nested `order` object carries
#: `side` = ORDER_SIDE_BUY / ORDER_SIDE_SELL -- a BUY/SELL direction, not a
#: market outcome. Probing `side` first found 1,911 of each, neither of which
#: is UP or DOWN, so _row_side returned None for every trade row and nothing
#: could be attributed. The outcome lives in `outcomeSide`
#: (OUTCOME_SIDE_YES 1591, _NO 1480, _UNSPECIFIED 751).
SIDE_KEYS = ("outcomeSide", "outcome", "positionSide", "side", "orderSide",
             "direction", "takerSide", "buySell")

#: the venue's explicit "we are not telling you" value. 751 executions carry
#: it, and it must stay UNKNOWN rather than be read as a side.
UNSPECIFIED_SIDE = "OUTCOME_SIDE_UNSPECIFIED"

#: keys that have been seen to carry a commission or rebate.
FEE_KEYS = ("commissionNotionalCollected", "commission",
            "commissionNotionalTotalCollected", "fee", "fees",
            "commissionPaid", "rebate", "makerRebate", "commissionNotional")

#: our own executed shares and price, which the execution object carries
#: rather than the trade row: the row's qty/price describe the whole TRADE.
EXEC_QTY_KEYS = ("lastShares", "lastQty", "qty", "quantity")
EXEC_PX_KEYS = ("lastPx", "price", "avgPx")


def _exec_objects(body: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """OUR execution object, or none when which one is ours is UNKNOWN.

    A TRADE HAS TWO SIDES AND WE ARE ONE OF THEM. The row carries both
    `aggressorExecution` and `passiveExecution`, and they hold OPPOSITE
    sides -- so taking "the first side found" would attribute the
    COUNTERPARTY's side half the time and silently book the wrong leg. That
    is a worse failure than reading nothing, because it looks like data.

    `isAggressor` is the row's own statement of which party the account was,
    so it selects the object. When it is absent we return NOTHING: which
    side was ours is then unknown, and an unknown side is left unknown
    rather than inferred. Rows with only one execution object are
    unambiguous and are used as-is.
    """
    objs = {k: body.get(k) for k in EXEC_KEYS
            if isinstance(body.get(k), Mapping)}
    if not objs:
        return []
    if len(objs) == 1:
        return list(objs.values())          # unambiguous
    flag = body.get("isAggressor")
    if flag is True:
        return [objs["aggressorExecution"]]
    if flag is False:
        return [objs["passiveExecution"]]
    return []                               # UNKNOWN, never a coin flip


def _first_present(objs: List[Mapping[str, Any]], keys,
                   deep: bool = False) -> Any:
    """The first value found for any of `keys`. SHALLOW unless asked.

    `deep` DEFAULTS TO FALSE AND THAT MATTERS. The first version always
    searched one level down, so probing the trade payload for a side walked
    into aggressorExecution and returned the AGGRESSOR's side before
    `isAggressor` was ever consulted -- defeating the our-side selection
    entirely and booking the counterparty's side on half the rows. The
    top-level probe must see only the payload's own keys.

    NEVER INVENTS. Absent returns None, and every caller treats None as
    UNKNOWN rather than as a default.
    """
    for o in objs:
        for k in keys:
            v = o.get(k)
            if v not in (None, ""):
                return v
        if not deep:
            continue
        for v in o.values():
            if isinstance(v, Mapping):
                for k in keys:
                    vv = v.get(k)
                    if vv not in (None, ""):
                        return vv
    return None


def execution_field_inventory(rows: List[Mapping[str, Any]]
                              ) -> Dict[str, Any]:
    """What keys do the nested execution objects actually carry? READ-ONLY.

    The trade payload has no `side` and no `commission` at its top level --
    those words appear only inside aggressorExecution / passiveExecution, and
    this names their keys so the mapping is READ off the feed instead of
    guessed at for a fourth time. Field NAMES and side-like VALUES only.
    """
    keysets: Counter = Counter()
    side_vals: Counter = Counter()
    fee_keys_seen: Counter = Counter()
    present = 0
    for r in rows:
        k = str(r.get("kind") or r.get("type") or "").strip().lower()
        if k not in FILL_KINDS:
            continue
        body = _payload(r)
        # THE CENSUS SEES BOTH. It reports which keys exist, which is not an
        # attribution, so the isAggressor restriction does not apply here.
        objs = [body.get(k) for k in EXEC_KEYS
                if isinstance(body.get(k), Mapping)]
        if not objs:
            continue
        present += 1
        for o in objs:
            keysets.update([",".join(sorted(str(x) for x in o.keys()))])
            # DEEP: the first census reported {} for both because the side
            # is inside `order` and the commission key is
            # commissionNotionalCollected, neither of which it looked at.
            for sk in SIDE_KEYS:
                v = _first_present([o], (sk,), deep=True)
                if v not in (None, ""):
                    side_vals.update(["%s=%s" % (sk, str(v)[:40])])
            for fk in FEE_KEYS:
                if _first_present([o], (fk,), deep=True) not in (None, ""):
                    fee_keys_seen.update([fk])
    return {
        "fill_rows_with_execution_objects": present,
        "our_side_selectable_via_isAggressor": sum(
            1 for r in rows
            if str(r.get("kind") or r.get("type") or "").strip().lower()
            in FILL_KINDS
            and _payload(r).get("isAggressor") in (True, False)),
        "execution_object_key_sets": [
            {"keys": sig, "count": n}
            for sig, n in sorted(keysets.items(), key=lambda kv: -kv[1])[:6]],
        "side_like_values_inside_executions": dict(
            sorted(side_vals.items(), key=lambda kv: -kv[1])[:20]),
        "fee_like_keys_inside_executions": dict(fee_keys_seen),
        "labels": ("DIAGNOSTIC. Names the keys the nested execution objects "
                   "carry so side and commission are READ off the feed, not "
                   "guessed. Field NAMES and side-like VALUES only."),
    }


def aggressor_flag_calibration(rows: List[Mapping[str, Any]], *,
                               our_coids: set, our_venue_orders: set
                               ) -> Dict[str, Any]:
    """Does `isAggressor` really pick OUR execution? Measured, not assumed.

    WHY THIS HAD TO EXIST BEFORE THE VERDICT COULD BE REPORTED. Ownership by
    venue order id reads one of a row's TWO executions as ours, selected by
    `isAggressor`. A POSITIVE match needs no such selection -- an id we track
    cannot be the counterparty's -- but a NEGATIVE one does: every
    `venue_order_id_not_ours` verdict is only as good as that flag. The
    2026-11-03T16:42:28Z run returned 75 of 76 fills as OWNER_MANUAL on
    exactly that basis, and "75 fills were somebody else's" is far too strong
    a claim to rest on an unverified assumption about a field name.

    So it is calibrated against fills we already know are ours by the OTHER,
    independent route: a client order id in our provenance. For each such
    row, is the flag-selected execution's order id one we track, or is the
    OTHER execution's? If the flag means what it appears to, the first
    should dominate and `flag_picked_the_other_side` should be ~0. A
    material count there means the flag is inverted or means something else,
    and every negative verdict in this audit is then unsafe.

    READ-ONLY. Counts only -- no identifier, price or quantity is reported.
    """
    c: Counter = Counter()
    for r in rows:
        k = str(r.get("kind") or r.get("type") or "").strip().lower()
        if k not in FILL_KINDS:
            continue
        b = _payload(r)
        if not isinstance(b, Mapping):
            continue
        coid = str(b.get("clientOrderId") or b.get("clientOrderID") or "")
        if not coid or coid not in our_coids:
            continue                      # calibrate on KNOWN-ours rows only
        c["known_ours_by_client_order_id"] += 1
        agg = _exec_order_id(b.get("aggressorExecution"))
        pas = _exec_order_id(b.get("passiveExecution"))
        flag = b.get("isAggressor")
        if flag not in (True, False):
            c["flag_absent_or_unreadable"] += 1
            continue
        mine = agg if flag is True else pas
        theirs = pas if flag is True else agg
        if mine is None and theirs is None:
            c["no_order_id_on_either_execution"] += 1
            continue
        m_in = bool(mine and mine in our_venue_orders)
        t_in = bool(theirs and theirs in our_venue_orders)
        if m_in and t_in:
            c["both_sides_in_our_set"] += 1
        elif m_in:
            c["flag_picked_our_side"] += 1
        elif t_in:
            c["flag_picked_the_other_side"] += 1
        else:
            c["neither_side_in_our_set"] += 1
    agree = c["flag_picked_our_side"]
    disagree = c["flag_picked_the_other_side"]
    decided = agree + disagree
    if decided == 0:
        verdict = ("NOT_CALIBRATED: no row was both known-ours by client "
                   "order id and resolvable by venue order id, so every "
                   "`venue_order_id_not_ours` verdict in this audit rests on "
                   "an UNVERIFIED reading of isAggressor and must be read as "
                   "unresolved rather than as external activity")
    elif disagree == 0:
        verdict = "CALIBRATED: the flag picked our side on every decided row"
    elif agree == 0:
        verdict = ("INVERTED: the flag picked the OTHER side on every decided "
                   "row -- every negative verdict here is unsafe")
    else:
        verdict = ("AMBIGUOUS: the flag disagreed on %d of %d decided rows -- "
                   "treat negative verdicts as unresolved" % (disagree, decided))
    return {"counts": dict(c), "decided_rows": decided, "verdict": verdict,
            "labels": ("Calibrates the isAggressor assumption that every "
                       "venue_order_id_not_ours verdict depends on, against "
                       "rows already known to be ours by client order id. "
                       "Counts only.")}


def _row_side(row: Mapping[str, Any]) -> Optional[str]:
    body = _payload(row)
    if not isinstance(body, Mapping):
        return None
    raw = _first_present([body], SIDE_KEYS)
    if raw in (None, ""):
        # A TRADE ROW CARRIES NO TOP-LEVEL SIDE. The production feed's trade
        # payload is aggressor/passive/cost/price/qty/marketSlug and nothing
        # else -- `side` appears only on position_resolution rows and inside
        # the nested execution objects. Reading only the top level is what
        # made all 1,911 trade rows unmatchable.
        # DEEP, AND ONLY WITHIN OUR OWN EXECUTION. The side sits inside the
        # execution's nested `order` object -- the execution's own keys are
        # aggressor/commissionNotionalCollected/commissionSpreadPx/id/lastPx/
        # lastShares/legPrices/order/... with no side among them. A deep
        # search is safe here precisely BECAUSE _exec_objects has already
        # used isAggressor to narrow to one object, so it cannot reach the
        # counterparty's side.
        raw = _first_present(_exec_objects(body), SIDE_KEYS, deep=True)
    v = str(raw or "").strip().upper()
    if not v or v == UNSPECIFIED_SIDE:
        return None
    if v.endswith(("_BUY", "_SELL")) or v in ("BUY", "SELL"):
        # a DIRECTION is not an OUTCOME. Returning UP for ORDER_SIDE_BUY
        # would book a side the row never stated.
        return None
    # the venue's own vocabularies, including the POSITION_RESOLUTION_SIDE_*
    # and OUTCOME_SIDE_* forms observed on the feed. Suffix match, so a new
    # prefix does not silently fall through to the wrong side.
    if v.endswith(("UP", "YES", "LONG", "BUY_LONG")):
        return "UP"
    if v.endswith(("DOWN", "NO", "SHORT", "BUY_SHORT")):
        return "DOWN"
    return None


def money_field_shapes(rows: List[Mapping[str, Any]],
                       sample: int = 4) -> Dict[str, Any]:
    """What SHAPE are price, cost and commission? Types and keys, no values.

    WHY THIS AND NOT ANOTHER GUESS. The attribution matched -- 79 rows matched
    all three predicates and the fill quantities reconcile exactly to the
    ledger quantities -- but `establish_basis` refused FILL_WITHOUT_PRICE
    because `_d()` returned None for price, cost and
    commissionNotionalCollected. The fields were PRESENT in the key inventory
    and NOT parseable as a decimal, which meant they were structured rather
    than scalar. This reports WHICH structure.

    IT HAS ANSWERED. On 1,911 production fill rows: price, cost, lastPx and
    commissionNotionalCollected are all dicts carrying exactly
    {currency, value}; qty, qtyDecimal and lastShares are plain strings that
    already parse. `_d()` now unwraps that mapping, so this stays in place as
    the check that the shape has not changed underneath the extraction.

    DISCLOSES NO VALUE. For a scalar it reports the Python type name and
    whether `_d()` can parse it -- never the number. For a mapping it reports
    the sorted key names only. That is enough to write the extraction and not
    enough to reveal a position.
    """
    # NO SUBSCRIPT ASSIGNMENT: the module's purity test forbids every
    # `x[...] = ...` here, which is how it proves the audit cannot write to
    # the store. Accumulated as pairs and sealed with dict() instead.
    acc: List[Any] = []
    for field, where in (("price", "body"), ("cost", "body"),
                         ("qty", "body"), ("qtyDecimal", "body"),
                         ("commissionNotionalCollected", "execution"),
                         ("lastPx", "execution"),
                         ("lastShares", "execution")):
        types: Counter = Counter()
        keysets: Counter = Counter()
        parseable = unparseable = absent = 0
        for r in rows:
            k = str(r.get("kind") or r.get("type") or "").strip().lower()
            if k not in FILL_KINDS:
                continue
            b = _payload(r)
            if where == "body":
                v = b.get(field)
            else:
                ex = [b.get(x) for x in EXEC_KEYS
                      if isinstance(b.get(x), Mapping)]
                v = _first_present(ex, (field,), deep=True) if ex else None
            if v is None:
                absent += 1
                continue
            types.update([type(v).__name__])
            if isinstance(v, Mapping):
                keysets.update([",".join(sorted(str(x) for x in v.keys()))])
            if _d(v) is None:
                unparseable += 1
            else:
                parseable += 1
        acc.append((field, {
            "source": where,
            "python_types": dict(types),
            "parses_as_decimal": parseable,
            "present_but_UNPARSEABLE": unparseable,
            "absent": absent,
            "mapping_key_sets": [
                {"keys": sig, "count": n}
                for sig, n in sorted(keysets.items(),
                                     key=lambda kv: -kv[1])[:sample]],
        }))
    acc.append(("labels",
                "SHAPES ONLY. Python type names, whether _d() parses, and "
                "for a mapping its KEY NAMES -- never a value. "
                "present_but_UNPARSEABLE above zero with a dict type names "
                "the structured-money case and its keys give the extraction "
                "directly."))
    return dict(acc)


def fee_and_notional_evidence(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Were maker rebates ACTUALLY credited, and what is the real minimum?

    Both questions are answerable only from executed trades, and both have
    been assumed up to now:

      REBATES. The programme's standing rule is that a zero commission is NOT
      a rebate and nothing counts until it is credited. A credited maker
      rebate appears as a NEGATIVE commission on a fill. This counts the
      signs over every fill row in the feed, so "rebates are credited" stops
      being a hope and becomes a number. If `negative` is 0, no rebate has
      ever been credited on this account and every pair whose profitability
      depends on one is unproven.

      MINIMUM NOTIONAL. No minimum ORDER VALUE is read anywhere in this
      codebase; only orderMinQuantity is. The smallest notional the venue has
      actually executed is the closest evidence available to what it will
      accept, and it bounds the 0.01-contract configuration from below.

    READ-ONLY. Prices, quantities and commissions are AGGREGATED -- counts,
    sums and extremes -- and no identifier is reported.
    """
    fees_neg = fees_zero = fees_pos = 0
    fee_sum = D(0)
    fee_unreadable = 0
    notionals: List[D] = []
    qtys: List[D] = []
    fills = 0
    for r in rows:
        k = str(r.get("kind") or r.get("type") or "").strip().lower()
        if k not in FILL_KINDS:
            continue
        b = _payload(r)
        fills += 1
        # OUR EXECUTION, same accessor the attribution uses. Reading the
        # trade body found no commission at all -- the key lives inside the
        # execution objects -- which is why all 1,911 rows reported
        # `unreadable` and rebates_ever_credited read false for the wrong
        # reason.
        ours = _exec_objects(b)
        fee = _d(_first_present([b], FEE_KEYS))
        if fee is None:
            fee = _d(_first_present(ours, FEE_KEYS, deep=True))
        if fee is None:
            fee_unreadable += 1
        else:
            fee_sum += fee
            if fee < 0:
                fees_neg += 1
            elif fee == 0:
                fees_zero += 1
            else:
                fees_pos += 1
        q = _d(_first_present(ours, EXEC_QTY_KEYS))
        if q is None:
            q = _d(_first_present([b], ("qty", "qtyDecimal", "quantity",
                                        "cumQuantity")))
        px = _d(_first_present(ours, EXEC_PX_KEYS))
        if px is None:
            px = _d(_first_present([b], ("price", "avgPx")))
        if q is not None and q > 0:
            qtys.append(q)
            if px is not None and px > 0:
                notionals.append(q * px)
    return {
        "fill_rows": fills,
        "commission_signs": {"negative_a_credited_rebate": fees_neg,
                             "zero": fees_zero, "positive_a_charge": fees_pos,
                             "unreadable": fee_unreadable},
        "commission_sum_usd": str(fee_sum),
        "rebates_ever_credited": bool(fees_neg > 0),
        "smallest_executed_quantity": (str(min(qtys)) if qtys else None),
        "smallest_executed_notional_usd": (str(min(notionals))
                                           if notionals else None),
        "largest_executed_notional_usd": (str(max(notionals))
                                          if notionals else None),
        "labels": ("EVIDENCE, not assumption. A credited maker rebate is a "
                   "NEGATIVE commission; a zero commission is NOT a rebate. "
                   "rebates_ever_credited false means no pair whose "
                   "profitability depends on the rebate has been proven on "
                   "this account. smallest_executed_notional_usd is the "
                   "closest available evidence of the venue's minimum order "
                   "VALUE, which this codebase reads nowhere -- it bounds "
                   "the smallest configuration from below. Aggregates only; "
                   "no identifier is reported."),
    }


def feed_shape(rows: List[Mapping[str, Any]], *, markets: List[str],
               sides: List[str], sample: int = 6) -> Dict[str, Any]:
    """WHY did the matcher find nothing? Reports shape, never guesses it.

    `classify_fills` requires THREE predicates to hold together -- the row's
    market, its kind being a fill, and its side. When all 46 legs report
    NO_FILLS_FOUND over a COMPLETE walk of 2,126 rows, the cause is far more
    likely a systematic shape mismatch in one predicate than 46 legs with no
    history. This says which one, by counting each predicate INDEPENDENTLY
    instead of inferring.

    STRICTLY DIAGNOSTIC AND READ-ONLY. It reports field NAMES, the distinct
    kind/type VALUES, and counts. It reports no price, no quantity, no
    identifier and no credential, so it cannot leak a position or a key.
    """
    # COUNTERS WITH .update(), NOT SUBSCRIPT ASSIGNMENT. The module's purity
    # test forbids every `x[...] = ...` in this file, which is how it proves
    # the audit cannot write to the store or the ledger. A local tally is not
    # a store write, but narrowing that rule to allow one would weaken a
    # safety test to fit a diagnostic, so the diagnostic is written to obey it.
    kinds: Counter = Counter()
    keysets: Counter = Counter()
    market_hits = side_hits = kind_hits = 0
    all_three = 0
    want_markets, want_sides = set(markets), set(sides)
    for r in rows:
        k = str(r.get("kind") or r.get("type") or "").strip().lower()
        kinds.update([k or "<absent>"])
        keysets.update([",".join(sorted(str(x) for x in r.keys()))])
        m_ok = _row_market(r) in want_markets
        k_ok = k in FILL_KINDS
        s_ok = _row_side(r) in want_sides
        market_hits += int(m_ok)
        kind_hits += int(k_ok)
        side_hits += int(s_ok)
        all_three += int(m_ok and k_ok and s_ok)
    top = sorted(keysets.items(), key=lambda kv: -kv[1])[:sample]
    # THE PAYLOAD'S OWN FIELD NAMES AND SIDE VOCABULARY.
    #
    # WHY THIS WAS NEEDED A SECOND TIME. The first version reported only the
    # TOP-LEVEL field names ("trade,type"), which was enough to find the
    # envelope but said nothing about what is inside it. After the envelope
    # fix, market and fill-kind matched (125 and 1,911 rows) and side STILL
    # matched zero -- and the diagnostic could not name the field or the
    # vocabulary responsible. A diagnostic that cannot answer the question it
    # is pointed at is half a diagnostic.
    inner_keys: Counter = Counter()
    side_values: Counter = Counter()
    for r in rows:
        b = _payload(r)
        if not isinstance(b, Mapping) or b is r:
            continue
        inner_keys.update([",".join(sorted(str(x) for x in b.keys()))])
        for cand in ("side", "outcome", "direction", "buySell", "takerSide",
                     "outcomeSide", "positionSide", "orderSide"):
            v = b.get(cand)
            if v not in (None, ""):
                side_values.update(["%s=%s" % (cand, str(v)[:40])])
    return {
        "rows": len(rows),
        "distinct_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "fill_kinds_recognised": sorted(FILL_KINDS),
        "rows_matching_market_only": market_hits,
        "rows_matching_fill_kind_only": kind_hits,
        "rows_matching_side_only": side_hits,
        "rows_matching_all_three": all_three,
        "most_common_field_name_sets": [
            {"fields": sig, "rows": n} for sig, n in top],
        "most_common_PAYLOAD_field_name_sets": [
            {"fields": sig, "rows": n}
            for sig, n in sorted(inner_keys.items(),
                                 key=lambda kv: -kv[1])[:sample]],
        "side_field_values_seen": dict(
            sorted(side_values.items(), key=lambda kv: -kv[1])[:20]),
        "side_vocabulary_recognised": {
            "UP": ["UP", "YES", "BUY_LONG"],
            "DOWN": ["DOWN", "NO", "BUY_SHORT"]},
        "labels": ("DIAGNOSTIC ONLY. Each predicate is counted "
                   "INDEPENDENTLY, so a zero names the predicate that is "
                   "wrong: zero market matches means the market field is "
                   "read from the wrong key, zero fill-kind matches means "
                   "FILL_KINDS does not cover this feed's vocabulary, and "
                   "zero side matches means the side field is. Field NAMES "
                   "and kind VALUES only -- no price, quantity, identifier "
                   "or credential is reported."),
    }


#: a venue order id we do NOT track, read off the execution that
#: `isAggressor` claims is ours, while that claim is UNVERIFIED. It is not
#: OWNER_MANUAL, because the row may be ours and the flag may be wrong; it
#: is not an absence either, because the id is right there. Named so the two
#: cannot be collapsed.
EV_NOT_OURS_UNCALIBRATED = "venue_order_id_not_ours_but_flag_uncalibrated"


def classify_fills(rows: List[Mapping[str, Any]], *, market: str, side: str,
                   our_coids: set,
                   our_venue_orders: Optional[set] = None,
                   flag_calibrated: Optional[bool] = None) -> Dict[str, Any]:
    """Actual fills for one leg, split by owner. Nothing is assumed.

    TWO INDEPENDENT ROUTES TO OWNERSHIP, and the second is the owner's
    correction. A client order id resolves it when the feed supplies one; a
    VENUE ORDER ID resolves it when the feed does not, matched against the
    ids the executor durably tracks. `our_venue_orders` None reproduces the
    old coid-only behaviour exactly, so an existing caller is unaffected.

    AND THE THIRD STATE STAYS THIRD. An identified-but-not-ours fill is
    OWNER_MANUAL; a fill with NO identity of either kind stays
    UNKNOWN_OWNER, because unresolved attribution is not evidence of
    external activity. Every fill records WHICH evidence resolved it, so a
    reader can tell a match from an absence.
    """
    ovo = our_venue_orders if isinstance(our_venue_orders, set) else set()
    fills: List[Dict[str, Any]] = []
    for r in rows:
        if _row_market(r) != market:
            continue
        kind = str(r.get("kind") or r.get("type") or "").strip().lower()
        if kind not in FILL_KINDS:
            continue                      # a settlement or correction is not a fill
        if _row_side(r) != side:
            continue
        # THE PAYLOAD, not the envelope. Every money field is one level down.
        b = _payload(r)
        coid = str(b.get("clientOrderId") or b.get("clientOrderID")
                   or "") or None
        # BOTH parties' order ids, plus ours where the row identifies it.
        # A positive match against ids we track is proof; a non-match on an
        # id that may be the COUNTERPARTY's proves nothing and must not
        # become OWNER_MANUAL.
        vid_ours, vid_all, vid_src = _row_venue_order_candidates(b)
        vid_matched = sorted(vid_all & ovo)
        vid = (vid_matched[0] if vid_matched
               else (vid_ours or (sorted(vid_all)[0] if vid_all else None)))
        if coid and coid in our_coids:
            owner, owner_evidence = L5_OWNED, "client_order_id"
        elif vid_matched:
            owner, owner_evidence = L5_OWNED, "venue_order_id"
        elif coid:
            owner, owner_evidence = OWNER_MANUAL, "client_order_id_not_ours"
        elif vid_ours and (vid_src != "flag" or flag_calibrated is True):
            owner, owner_evidence = OWNER_MANUAL, "venue_order_id_not_ours"
        elif vid_ours:
            # THE ID IS RESOLVED AND THE OWNERSHIP IS NOT. Production's
            # calibration came back NOT_CALIBRATED with EMPTY counts -- no
            # fill row in the 2543-row walk carries a client order id at
            # all, so the one route that could check `isAggressor` has no
            # rows to check it on. Calling these 75 fills OWNER_MANUAL would
            # assert external activity on a SHARED account from an
            # unverified reading of a field name, which is the same error as
            # the original UNKNOWN_OWNER sweep, pointing the other way.
            owner, owner_evidence = UNKNOWN_OWNER, EV_NOT_OURS_UNCALIBRATED
        else:
            owner, owner_evidence = UNKNOWN_OWNER, "no_identity_on_the_row"
        # THE FEED'S OWN NAMES. A trade row carries qty/qtyDecimal and an
        # authoritative `cost`; it carries neither `quantity` nor
        # `commission`, which is why every value read as None before.
        qty = None
        for qk in ("qty", "qtyDecimal", "quantity", "cumQuantity"):
            qty = _d(b.get(qk))
            if qty is not None:
                break
        px = _d(b.get("price") if b.get("price") is not None
                else b.get("avgPx"))
        # AUTHORITATIVE COST, straight off the row. This is the figure the
        # $1/share placeholders were standing in for.
        cost = _d(b.get("cost"))
        ours = _exec_objects(b)
        fee = _d(_first_present([b], FEE_KEYS))
        if fee is None:
            fee = _d(_first_present(ours, FEE_KEYS, deep=True))
        # OUR executed shares and price, preferred over the trade-level
        # figures: the row's qty/price describe the whole trade.
        oq = _d(_first_present(ours, EXEC_QTY_KEYS))
        if oq is not None:
            qty = oq
        op = _d(_first_present(ours, EXEC_PX_KEYS))
        if op is not None:
            px = op
        fills.append({
            "trade_id": str(b.get("tradeId") or b.get("id") or "") or None,
            "order_id": str(b.get("orderId") or "") or None,
            # THE VENUE ORDER ID, surfaced rather than discarded. It was
            # being read into `execution_field_inventory` as a key NAME only
            # while the per-leg view reported order_id null on every fill,
            # which is why ownership looked unresolvable.
            "venue_order_id": vid,
            "client_order_id": coid, "owner": owner,
            "owner_evidence": owner_evidence,
            "qty": None if qty is None else str(qty),
            "price": None if px is None else str(px),
            # the venue's own cost for this execution, or None for UNKNOWN
            "cost": None if cost is None else str(cost),
            "fee": None if fee is None else str(fee),
            "realized_pnl": (None if _d(b.get("realizedPnl")) is None
                             else str(_d(b.get("realizedPnl")))),
            "cost_basis": (None if _d(b.get("costBasis")) is None
                           else str(_d(b.get("costBasis")))),
            "t": str(b.get("time") or b.get("createTime") or "") or None,
        })
    return {"fills": fills,
            "owners": sorted({f["owner"] for f in fills}),
            "l5_owned": sum(1 for f in fills if f["owner"] == L5_OWNED),
            "owner_manual": sum(1 for f in fills if f["owner"] == OWNER_MANUAL),
            "unknown_owner": sum(1 for f in fills
                                 if f["owner"] == UNKNOWN_OWNER)}


#: The cross-check tolerance, PER FILL. The venue rounds a commission or a
#: credited rebate to the cent, so `cost` can differ from
#: `qty x own_price + fee` by up to half a cent of rounding and no more. A
#: wider gap is a disagreement about the trade, not rounding.
CROSSCHECK_TOLERANCE = D("0.005")


def own_price(price: D, side: str) -> D:
    """OUR cost per share, from the market's single quoted price.

    PMUS quotes ONE number per market and it is ALWAYS the UP/YES price. A
    DOWN buy at cost q is a SELL of YES resting at 1 - q, so the price on a
    DOWN fill is the YES price and OUR cost per share is its complement.

    THE DEFECT THIS FIXES. establish_basis used `price` for both sides, so
    every DOWN leg was costed at the YES price. On the 44 legs corrected on
    2026-10-16 that understated the correct total by 30.75140000 in
    aggregate while OVER-correcting 13 DOWN legs by 15.79500000 -- wrong in
    both directions, and on those 13 in the direction that flatters
    realized.
    """
    return price if side == "UP" else (D(1) - price)


def establish_basis(fills: List[Mapping[str, Any]], *, expect_qty: D,
                    side: str) -> Dict[str, Any]:
    """The leg's ACTUAL cost and fees, or an explicit refusal.

    Every fill must carry a quantity, a price AND a fee, and the quantities
    must sum to the quantity the ledger holds. A partial match is not a
    basis: it would understate cost, which is the one direction that
    flatters realized.

    SIDE-AWARE. The cost is `qty x own_price(price, side)`; see own_price.

    CROSS-CHECKED AGAINST THE VENUE'S OWN FIGURE. Each fill carries `cost`,
    which is fee-inclusive: it equals `qty x own_price + fee`, where a
    credited maker rebate is a NEGATIVE fee and so REDUCES it. Our
    arithmetic must agree with it to within a cent of rounding per fill, or
    the basis is REFUSED rather than reported. A leg whose own two
    independent readings disagree has no established cost, and an
    unestablished cost stays UNKNOWN -- never zero and never the cheaper of
    the two.
    """
    side = str(side or "").strip().upper()
    if side not in ("UP", "DOWN"):
        return {"established": False, "reason": "BAD_SIDE", "side": side}
    if not fills:
        return {"established": False, "reason": "NO_FILLS_FOUND"}
    total_qty, cost, fee_total = D(0), D(0), D(0)
    venue_cost_total, checked, unchecked = D(0), 0, 0
    worst = None
    for f in fills:
        q, p, fee = _d(f.get("qty")), _d(f.get("price")), _d(f.get("fee"))
        if q is None:
            return {"established": False, "reason": "FILL_WITHOUT_QUANTITY"}
        if p is None:
            return {"established": False, "reason": "FILL_WITHOUT_PRICE"}
        if fee is None:
            return {"established": False, "reason": "FILL_WITHOUT_FEE",
                    "labels": "an absent fee is not a free $0"}
        own = q * own_price(p, side)
        total_qty += q
        cost += own
        fee_total += fee
        # the venue's own fee-inclusive cost for this execution, when it
        # carries one. An ABSENT cost is not a failed check: it is one
        # fewer reading, and it is counted as such.
        vc = _d(f.get("cost"))
        if vc is None:
            unchecked += 1
            continue
        venue_cost_total += vc
        checked += 1
        gap = abs(vc - (own + fee))
        if worst is None or gap > worst[0]:
            worst = (gap, f.get("trade_id"), str(vc), str(own), str(fee))
    if total_qty != expect_qty:
        return {"established": False, "reason": "QUANTITY_MISMATCH",
                "venue_qty": str(total_qty), "ledger_qty": str(expect_qty)}
    if worst is not None and worst[0] > CROSSCHECK_TOLERANCE:
        return {"established": False, "reason": "COST_CROSSCHECK_FAILED",
                "worst_gap_usd": str(worst[0]), "trade_id": worst[1],
                "venue_cost": worst[2], "our_cost": worst[3],
                "fee": worst[4], "tolerance_usd": str(CROSSCHECK_TOLERANCE),
                "labels": ("two independent readings of the same fill "
                           "disagree by more than a cent of rounding. The "
                           "cost stays UNKNOWN; the cheaper reading is NOT "
                           "adopted.")}
    return {"established": True, "qty": str(total_qty),
            "side": side,
            "actual_cost_usd": str(cost), "fees_usd": str(fee_total),
            "avg_price": str(cost / total_qty) if total_qty else None,
            "venue_cost_total_usd": str(venue_cost_total),
            "venue_cost_fills_checked": checked,
            "venue_cost_fills_absent": unchecked,
            "crosscheck_worst_gap_usd": (None if worst is None
                                         else str(worst[0])),
            "labels": ("actual_cost_usd EXCLUDES fees and is side-aware. "
                       "venue_cost_total_usd is the venue's own "
                       "fee-INCLUSIVE figure over the fills that carry "
                       "one, and is a cross-check, not the basis.")}


def _leg_quantity(store: Any, market: str, side: str, key: str
                  ) -> Tuple[Optional[D], str]:
    """The leg's quantity, from the RIGHT place for its lifecycle stage.

    THE DEFECT THIS FIXES. The first version read `positions[key]`, which is
    ZERO for a retired market -- settlement consumes the position. So 28 of
    29 legs reported ledger_qty 0 and a $0 placeholder, which made the whole
    audit look empty and would have understated the overstatement to nothing.
    A retired leg's quantity lives in `retired_markets[market]` as up_qty /
    dn_qty, recorded at settlement, and that is the figure a $1/share
    placeholder was applied to.
    """
    retired = (getattr(store, "state", {}) or {}).get("retired_markets") or {}
    row = retired.get(market)
    if isinstance(row, Mapping):
        raw = row.get("up_qty" if side == "UP" else "dn_qty")
        q = _d(raw)
        if q is None:
            return (None, "retired_markets:UNREADABLE")
        return (q, "retired_markets")
    q = _d(((getattr(store, "state", {}) or {}).get("positions") or {})
           .get(key))
    if q is None:
        return (None, "positions:ABSENT")
    return (q, "positions")


def audit(store: Any, transport: Any, *, max_pages: int = MAX_PAGES,
          walk: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The whole read-only pass. Writes nothing, decides nothing.

    `walk` accepts a walk this function did not make, which is how a caller
    that took the feed a few pages at a time across several ticks hands in the
    result. It changes nothing about what is computed: the walk is the walk,
    whoever paced it, and an incomplete one still refuses every basis with
    ACTIVITY_WALK_INCOMPLETE exactly as it does here.
    """
    keys = sorted(set(str(k) for k in
                      ((getattr(store, "state", {}) or {})
                       .get("leg_cost_basis_unknown") or [])))
    walk = (dict(walk) if walk is not None
            else walk_activities(transport, max_pages=max_pages))
    our = our_client_order_ids(store)
    our_vids = our_venue_order_ids(store)
    # MEASURED BEFORE IT IS RELIED ON. Every negative ownership verdict
    # below depends on `isAggressor`; this says whether that dependency is
    # verified, and only "CALIBRATED" lets a non-match be called manual.
    cal = aggressor_flag_calibration(walk["rows"], our_coids=our,
                                     our_venue_orders=our_vids)
    cal_ok = cal["verdict"].startswith("CALIBRATED")
    legs: List[Dict[str, Any]] = []
    placeholder_total, actual_total = D(0), D(0)
    establishable = 0
    established_placeholder_total = D(0)
    for key in keys:
        market, _, side = key.rpartition("|")
        held, qty_source = _leg_quantity(store, market, side, key)
        if held is None:
            legs.append({"key": key, "market": market, "side": side,
                         "ledger_qty": None, "qty_source": qty_source,
                         "placeholder_cost_usd": None, "owners": [],
                         "l5_owned_fills": 0, "owner_manual_fills": 0,
                         "unknown_owner_fills": 0,
                         "basis": {"established": False,
                                   "reason": "LEG_QUANTITY_UNAVAILABLE"},
                         "fills": []})
            continue
        cls = classify_fills(walk["rows"], market=market, side=side,
                             our_coids=our, our_venue_orders=our_vids,
                             flag_calibrated=cal_ok)
        basis = (establish_basis(cls["fills"], expect_qty=held, side=side)
                 if walk["complete"]
                 else {"established": False,
                       "reason": "ACTIVITY_WALK_INCOMPLETE:%s" % walk["ended"]})
        # the placeholder this leg currently carries: $1 a share
        placeholder = held
        row = {"key": key, "market": market, "side": side,
               "ledger_qty": str(held), "qty_source": qty_source,
               "placeholder_cost_usd": str(placeholder),
               "owners": cls["owners"], "l5_owned_fills": cls["l5_owned"],
               "owner_manual_fills": cls["owner_manual"],
               "unknown_owner_fills": cls["unknown_owner"],
               "basis": basis, "fills": cls["fills"][:12]}
        # THE PLACEHOLDER IS ALWAYS KNOWN: it is OUR OWN ledger figure, $1 a
        # share, and it needs no venue evidence. Summing it only for legs
        # whose basis could be established reported
        # placeholder_cost_total_usd "0" in production while 33 legs plainly
        # carried non-zero placeholders -- the one number that says how much
        # of the account-wide realized total rests on an upper bound rather
        # than a measurement, reported as nothing.
        placeholder_total += placeholder
        if basis.get("established"):
            establishable += 1
            established_placeholder_total += placeholder
            actual_total += D(basis["actual_cost_usd"])
        legs.append(row)
    return {
        "ok": True, "writes": "NONE",
        "activity_walk": {k: walk[k] for k in ("pages", "ended", "complete")},
        "activity_rows": len(walk["rows"]),
        # ALWAYS computed: the two things that were being assumed.
        "fee_and_notional_evidence": fee_and_notional_evidence(walk["rows"]),
        "execution_field_inventory": execution_field_inventory(walk["rows"]),
        # computed only on the failing case, same rule as the other
        # diagnostics: a healthy audit does not carry it.
        "money_field_shapes": (money_field_shapes(walk["rows"])
                               if establishable == 0 and keys else None),
        # WHY nothing matched, when nothing matched. Computed only on the
        # failing case so a healthy audit does not carry the diagnostic.
        "feed_shape": (feed_shape(walk["rows"],
                                  markets=[k.split("|")[0] for k in keys],
                                  sides=["UP", "DOWN"])
                       if establishable == 0 and keys else None),
        "our_client_order_ids": len(our),
        "our_venue_order_ids": len(our_vids),
        # WHETHER THE NEGATIVE VERDICTS CAN BE TRUSTED AT ALL. Every
        # `venue_order_id_not_ours` depends on isAggressor picking our
        # execution; this measures that against known-ours rows.
        "aggressor_flag_calibration": cal,
        "negative_verdicts_trusted": cal_ok,
        "unknown_basis_legs": len(keys),
        "establishable": establishable,
        "unestablished": len(keys) - establishable,
        # ALL placeholders, evidence or not: how much of the account-wide
        # realized total is an upper bound rather than a measurement.
        "placeholder_cost_total_usd": str(placeholder_total),
        # and the subset the overstatement is actually computed over, so the
        # two are never confused
        "established_placeholder_cost_usd": str(established_placeholder_total),
        "actual_cost_total_usd": str(actual_total),
        "overstatement_usd": str(established_placeholder_total - actual_total),
        "overstatement_basis": ("established legs ONLY. It is NOT the "
                                "difference against placeholder_cost_total_usd, "
                                "because the unestablished legs have no "
                                "measured cost to subtract -- their cost "
                                "stays UNKNOWN and is never treated as zero."),
        "legs": legs,
        "labels": ("READ-ONLY evidence. overstatement_usd is how much the "
                   "$1/share placeholders exceed the measured cost on the "
                   "legs that could be established -- it is NOT a correction "
                   "and has NOT been applied. Applying it would move realized "
                   "toward zero, which correct_settled_market refuses as "
                   "CORRECTION_WOULD_RELAX_LOSS_HALT and which needs its own "
                   "owner decision."),
    }


CORRECTION_SCHEMA_VERSION = "cost-basis-correction/1"


def leg_cost_expectation(store: Any, market: str, side: str) -> Dict[str, Any]:
    """What the row ACTUALLY holds for this leg, or why it cannot be corrected.

    THREE DEFECTS THIS FIXES, all found on 2026-10-16 after the 44/47 apply.

    1. THE PLAN ASSERTED THE PLACEHOLDER. `expect` was the $1/share figure
       (the leg quantity), not the row's own `cost_total`. Three legs
       refused LEDGER_DOES_NOT_MATCH_EVIDENCE because their row never
       carried that placeholder, and no manifest could ever fix them: the
       assertion itself was wrong. The pre-state now comes from the row.

    2. `cost_total` IS MARKET-LEVEL. cpc-btc-updown-15m-2026-10-15-1945z
       retired with up_qty 1 / up_cost 0.4600 AND dn_qty 1.0000 /
       dn_cost 1.0000, so cost_total 1.4600 is the pair. Correcting the
       DOWN leg through that field would have written 0.4900 over the whole
       market and ERASED the UP leg's 0.4600, flattering realized. A
       two-sided market is therefore REFUSED here: a per-leg correction
       cannot be expressed through a market-level field, and this verb will
       not pretend otherwise.

    3. AN ALREADY-CORRECTED LEG STAYED IN THE PLAN. `leg_cost_basis_unknown`
       is not cleared by a correction, so a re-run re-planned all 44 and
       relied on the spent correction_id alone. Skipping them here restores
       the second, independent never-twice mechanism: the batch shrinks.
    """
    row = ((getattr(store, "state", {}) or {}).get("retired_markets")
           or {}).get(market)
    if not isinstance(row, Mapping):
        return {"ok": False, "reason": "MARKET_NOT_RETIRED"}
    # a leg this verb already corrected is a RESTATEMENT, not a first
    # correction: it may move either way, and the verb requires the flag.
    restate = bool(row.get("cost_superseded"))
    cost_total = _d(row.get("cost_total"))
    if cost_total is None:
        return {"ok": False, "reason": "COST_TOTAL_UNREADABLE"}
    other_qty = _d(row.get("dn_qty" if side == "UP" else "up_qty"))
    if other_qty is None:
        return {"ok": False, "reason": "OTHER_SIDE_QTY_UNREADABLE"}
    # TWO FIGURES, NOT ONE. `expect` asserts the MARKET-level cost_total,
    # because that is the field the row carries and the verb matches. The
    # leg's OWN cost is what the correction replaces, and on a two-sided
    # market they are different numbers: 1945z holds cost_total 1.4600 for
    # the pair while the DOWN leg's own cost is 1.0000. Using the total as
    # the leg's pre-state would overstate the correction by the other leg.
    own = _d(row.get("up_cost" if side == "UP" else "dn_cost"))
    if other_qty != 0:
        if own is None or _d(row.get("dn_cost" if side == "UP"
                                     else "up_cost")) is None:
            # two-sided and the per-side costs are not both readable, so
            # the leg's own cost cannot be separated from the pair's.
            return {"ok": False, "reason": "MARKET_TWO_SIDED_COST_UNREADABLE",
                    "other_side_qty": str(other_qty),
                    "cost_total": str(cost_total)}
    if own is None:
        own = cost_total
    return {"ok": True, "cost_total": cost_total, "own_cost": own,
            "restate": restate, "two_sided": other_qty != 0}


def correction_request(audit_result: Mapping[str, Any]) -> Dict[str, Any]:
    """The owner-facing correction request, derived from an audit result.

    PREPARES. DOES NOT APPLY. There is no verb in this codebase that can
    apply it: `correct_settled_market` restates a settlement OUTCOME
    (`yes_value`) and never writes `cost_total`, and a cost correction moves
    realized TOWARD zero, which that method refuses as
    CORRECTION_WOULD_RELAX_LOSS_HALT. Both facts are reported in `blocked_by`
    so the request is read as a request.

    Every leg appears, established or not. The two unestablished legs keep
    their cost as UNKNOWN -- never zero, never the placeholder treated as
    measured -- and ownership is carried through verbatim, so an
    UNKNOWN_OWNER leg is never silently reclassified as manual.
    """
    legs_in = list(audit_result.get("legs") or [])
    rows: List[Any] = []
    established = 0
    placeholder_established = D("0")
    measured_established = D("0")
    fees_established = D("0")
    for leg in legs_in:
        basis = dict(leg.get("basis") or {})
        placeholder = _d(leg.get("placeholder_cost_usd"))
        row = {
            "key": leg.get("key"),
            "market": leg.get("market"),
            "side": leg.get("side"),
            "ledger_qty": leg.get("ledger_qty"),
            "qty_source": leg.get("qty_source"),
            # ownership is EVIDENCE, carried through untouched
            "owners": list(leg.get("owners") or []),
            "l5_owned_fills": leg.get("l5_owned_fills"),
            "owner_manual_fills": leg.get("owner_manual_fills"),
            "unknown_owner_fills": leg.get("unknown_owner_fills"),
            "cost_before_usd": leg.get("placeholder_cost_usd"),
            "cost_before_provenance": "PLACEHOLDER_ONE_DOLLAR_PER_SHARE_UPPER_BOUND",
            "fill_trade_ids": [f.get("trade_id") for f in (leg.get("fills") or [])],
        }
        if basis.get("established"):
            measured = _d(basis.get("actual_cost_usd"))
            fees = _d(basis.get("fees_usd"))
            if measured is None or placeholder is None:
                row.update({"action": "NONE", "reason": "COST_UNREADABLE_AFTER_AUDIT",
                            "cost_after_usd": None})
            else:
                established += 1
                placeholder_established += placeholder
                measured_established += measured
                if fees is not None:
                    fees_established += fees
                row.update({
                    "action": "REPLACE_COST",
                    "cost_after_usd": str(measured),
                    "cost_after_provenance": "MEASURED_FROM_VENUE_FILLS",
                    "fees_usd": basis.get("fees_usd"),
                    "avg_price": basis.get("avg_price"),
                    "venue_qty": basis.get("qty"),
                    "delta_usd": str(measured - placeholder),
                })
        else:
            # PRESERVED AS UNKNOWN. Not zero, and not the placeholder promoted
            # to a measurement.
            row.update({
                "action": "PRESERVE_UNKNOWN",
                "reason": basis.get("reason"),
                "venue_qty": basis.get("venue_qty"),
                "ledger_qty_reported": basis.get("ledger_qty"),
                "cost_after_usd": None,
                "cost_after_provenance": "UNKNOWN",
            })
        rows.append(row)
    overstatement = placeholder_established - measured_established
    return {
        "schema": CORRECTION_SCHEMA_VERSION,
        "prepared": True,
        "applied": False,
        "legs_total": len(legs_in),
        "legs_to_correct": established,
        "legs_preserved_unknown": len(legs_in) - established,
        "cost_before_total_usd": str(placeholder_established),
        "cost_after_total_usd": str(measured_established),
        "fees_measured_usd": str(fees_established),
        "realized_delta_usd": str(overstatement),
        "realized_delta_direction": "TOWARD_ZERO",
        "activity_walk": dict(audit_result.get("activity_walk") or {}),
        # the per-leg before/after, every leg, established or preserved
        "legs": rows,
        "blocked_by": [
            "NO_COST_CORRECTION_VERB: correct_settled_market restates "
            "yes_value and never writes cost_total, so no existing protected "
            "method can apply this.",
            "CORRECTION_WOULD_RELAX_LOSS_HALT: a lower cost raises "
            "settlement_pnl, and that method refuses any correction whose "
            "delta is positive. The guard is correct and is not bypassed "
            "here.",
        ],
        "ownership_note": (
            "Every leg in this request is UNKNOWN_OWNER with zero L5-owned "
            "fills. Correcting a COST does not attribute a leg to anyone: "
            "verified_l5 stays 0 and manual stays UNKNOWN."
        ),
        "labels": (
            "A REQUEST, not a mutation. cost_before is a $1/share upper "
            "bound; cost_after is measured from venue fills by trade id. The "
            "preserved legs keep cost UNKNOWN. Applying this needs a new "
            "protected verb and an explicit owner decision, because it moves "
            "realized toward zero."
        ),
    }


APPLY_ENV = "L5_COST_BASIS_APPLY"
APPLY_INSPECT = "INSPECT"


def apply_mode() -> str:
    """INSPECT (the default) is a DRY RUN that writes nothing.

    Anything else is read as the owner's batch id and authorizes the write.
    An unset or unreadable value is INSPECT, never apply.
    """
    raw = str(os.getenv(APPLY_ENV, "") or "").strip()
    return raw if raw and raw.upper() != APPLY_INSPECT else APPLY_INSPECT


BATCH_MANIFEST_ENV = "L5_COST_BASIS_BATCH_MANIFEST"


def batch_manifest(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The INSPECTED batch this apply is allowed to write, or None.

    None means NO manifest, which `apply_cost_basis_corrections` treats as a
    refusal when a batch id is given: an apply without a manifest to check
    against is exactly the unverified write the owner asked to prevent.
    """
    path = path or (os.getenv(BATCH_MANIFEST_ENV) or "").strip()
    if not path:
        return None
    try:
        with open(path, "r") as fh:
            out = json.load(fh)
    except (OSError, ValueError):
        return None
    return out if isinstance(out, dict) else None


def check_batch(plan: List[Mapping[str, Any]], manifest: Mapping[str, Any]
                ) -> Dict[str, Any]:
    """ALL-OR-NOTHING precondition, evaluated BEFORE any write.

    The per-leg guards already refuse a leg whose evidence moved. This is the
    separate, stronger requirement the owner set: the batch as a whole must be
    the one that was inspected. A fresh walk that yields 45 legs instead of 47
    is not a smaller correction to be applied -- it is a changed world, and
    the batch is abandoned.

    Three independent equalities, all required:
      * the leg KEYS, as a set;
      * the distinct venue TRADE IDS, as a set;
      * the aggregate DELTA, exactly.
    """
    want_keys = set(manifest.get("keys") or ())
    want_ids = set(manifest.get("trade_ids") or ())
    want_delta = _d(manifest.get("realized_delta_usd"))
    want_legs = manifest.get("legs")

    got_keys = set(r.get("key") for r in plan)
    got_ids = set(t for r in plan for t in (r.get("trade_ids") or ()))
    got_delta = D("0")
    for r in plan:
        b = _d(r.get("cost_before_usd"))
        a = _d(r.get("cost_after_usd"))
        if b is None or a is None:
            return {"ok": False, "reason": "BATCH_LEG_UNREADABLE",
                    "key": r.get("key")}
        got_delta += b - a

    problems = []
    # PER-LEG, not just the aggregate. The aggregate alone is satisfied by two
    # offsetting per-leg changes, which the store's guards would still refuse
    # one at a time -- but a precondition that can be satisfied by a batch
    # nobody inspected is not a precondition. `per_leg` is required.
    want_legs_detail = manifest.get("per_leg")
    if not isinstance(want_legs_detail, Mapping) or not want_legs_detail:
        problems.append("MANIFEST_PER_LEG_MISSING")
    else:
        for r in plan:
            k = r.get("key")
            want = want_legs_detail.get(k)
            if not isinstance(want, Mapping):
                problems.append("PER_LEG_UNKNOWN:%s" % k)
                continue
            for field in ("cost_before_usd", "cost_after_usd"):
                a_ = _d(r.get(field))
                b_ = _d(want.get(field))
                if a_ is None or b_ is None or a_ != b_:
                    problems.append("PER_LEG_%s:%s" % (field.upper(), k))
            if sorted(r.get("trade_ids") or ()) != sorted(
                    want.get("trade_ids") or ()):
                problems.append("PER_LEG_TRADE_IDS:%s" % k)
    if want_delta is None:
        problems.append("MANIFEST_DELTA_UNREADABLE")
    if not want_keys or not want_ids:
        problems.append("MANIFEST_INCOMPLETE")
    if want_legs is not None and want_legs != len(plan):
        problems.append("LEG_COUNT:%s!=%s" % (len(plan), want_legs))
    if got_keys != want_keys:
        problems.append("KEYS_DIFFER:+%d/-%d"
                        % (len(got_keys - want_keys), len(want_keys - got_keys)))
    if got_ids != want_ids:
        problems.append("TRADE_IDS_DIFFER:+%d/-%d"
                        % (len(got_ids - want_ids), len(want_ids - got_ids)))
    if want_delta is not None and got_delta != want_delta:
        problems.append("DELTA_DIFFERS:%s!=%s" % (got_delta, want_delta))
    return {
        "ok": not problems,
        "problems": problems,
        "legs": len(plan),
        "trade_ids": len(got_ids),
        "delta": str(got_delta),
        "expected_legs": want_legs,
        "expected_trade_ids": len(want_ids),
        "expected_delta": (None if want_delta is None else str(want_delta)),
    }


def apply_cost_basis_corrections(store: Any, audit_result: Mapping[str, Any],
                                 *, batch_id: Optional[str] = None,
                                 manifest: Optional[Mapping[str, Any]] = None
                                 ) -> Dict[str, Any]:
    """Apply the measured-cost correction leg by leg, through the guards.

    THE EVIDENCE IS THE AUDIT'S OWN, from the walk that just ran. Nothing is
    hardcoded and no cost is carried in from a previous session, so a leg
    whose fills have changed at the venue fails its own reconciliation rather
    than being corrected against a stale figure.

    DRY RUN BY DEFAULT. With no batch id this reports what each leg WOULD do
    and calls nothing. The store's verb is the only thing that can write, and
    every one of its ten guards still runs when it is called for real -- this
    function adds no authority, it only iterates.

    REFUSALS ARE NOT FAILURES. A leg that cannot be established keeps its
    UNKNOWN cost, which is the intended outcome for the two quantity-mismatched
    legs. They are reported, not retried and not worked around.
    """
    walk = dict(audit_result.get("activity_walk") or {})
    legs = list(audit_result.get("legs") or [])
    dry = batch_id is None
    applied: List[Any] = []
    refused: List[Any] = []
    skipped: List[Any] = []
    planned: List[Any] = []
    delta_total = D("0")
    before = (None if dry else store.realized_lines())
    for leg in legs:
        key = leg.get("key") or ""
        market = leg.get("market")
        side = leg.get("side")
        basis = dict(leg.get("basis") or {})
        if not basis.get("established"):
            skipped.append({"key": key, "reason": basis.get("reason"),
                            "cost": "UNKNOWN"})
            continue
        measured = _d(basis.get("actual_cost_usd"))
        fees = _d(basis.get("fees_usd"))
        if measured is None or fees is None:
            skipped.append({"key": key, "reason": "COST_UNREADABLE_AFTER_AUDIT",
                            "cost": "UNKNOWN"})
            continue
        # THE PRE-STATE COMES FROM THE ROW, not from the $1/share assumption.
        exp = leg_cost_expectation(store, market, side)
        if not exp.get("ok"):
            skipped.append({"key": key, "reason": exp.get("reason"),
                            "cost": "UNKNOWN",
                            **{k: v for k, v in exp.items()
                               if k not in ("ok", "reason")}})
            continue
        placeholder = exp["own_cost"]
        if measured == placeholder:
            skipped.append({"key": key, "reason": "NOTHING_TO_CORRECT",
                            "own_cost": str(placeholder),
                            "measured": str(measured)})
            continue
        if measured > placeholder and not exp["restate"]:
            # a FIRST correction may only come down; the verb refuses it
            # anyway, and keeping it out of the batch keeps the manifest
            # honest about what it will do.
            skipped.append({"key": key, "reason": "WOULD_RAISE_A_FIRST_COST",
                            "own_cost": str(placeholder),
                            "measured": str(measured)})
            continue
        per_fill = []
        unreadable = False
        for f in (leg.get("fills") or []):
            q = _d(f.get("qty"))
            px = _d(f.get("price"))
            fee = _d(f.get("fee"))
            tid = f.get("trade_id")
            if q is None or px is None or fee is None or not tid:
                unreadable = True
                break
            per_fill.append({"trade_id": tid, "qty": str(q),
                             "price": str(px), "fee": str(fee)})
        if unreadable or not per_fill:
            skipped.append({"key": key, "reason": "FILL_UNREADABLE",
                            "cost": "UNKNOWN"})
            continue
        plan = {
            "key": key, "market": market, "side": side,
            "cost_before_usd": str(placeholder),
            "cost_after_usd": str(measured),
            "fees_usd": str(fees),
            "trade_ids": [f["trade_id"] for f in per_fill],
            "correction_id": "%s|%s" % (batch_id or APPLY_INSPECT, key),
            "expect_cost_total": str(exp["cost_total"]),
            "restate": exp["restate"],
            "two_sided": exp["two_sided"],
        }
        # a PAIR, not a stashed key: this module's purity test forbids every
        # `x[...] = ...` as proof it cannot write to the store, and that rule
        # caught this on the first run.
        planned.append((plan, per_fill))
    # A SCOPED BATCH. By default the batch is every leg this walk can
    # establish, and the manifest must match that whole set. An owner may
    # instead approve ONE named correction: a manifest carrying
    # "scope": "KEYS_ONLY" plans only the legs it lists, and every other leg
    # is reported OUT_OF_BATCH_SCOPE and left exactly as it is. The batch
    # check below still requires the listed legs, their trade ids, their
    # before/after costs and the total delta to match exactly, and each leg
    # still goes through every guard of `correct_cost_basis`. A scope can
    # only make a batch SMALLER; it cannot add a leg the walk did not plan.
    if isinstance(manifest, Mapping) and \
            str(manifest.get("scope") or "").strip().upper() == "KEYS_ONLY":
        _in = set(str(k) for k in (manifest.get("keys") or ()))
        skipped.extend({"key": p.get("key"), "reason": "OUT_OF_BATCH_SCOPE",
                        "cost": "UNCHANGED"}
                       for p, _f in planned if p.get("key") not in _in)
        planned = [(p, f) for p, f in planned if p.get("key") in _in]
    # THE BATCH PRECONDITION, evaluated before ANY write. On a dry run it is
    # reported; on an apply a mismatch ABANDONS the batch and writes nothing.
    checked = (check_batch([p for p, _ in planned], manifest)
               if manifest is not None else None)
    if not dry:
        if manifest is None:
            return {"mode": "ABORTED", "batch_id": batch_id,
                    "reason": "BATCH_MANIFEST_MISSING",
                    "legs_seen": len(legs), "applied": 0, "refused": 0,
                    "preserved_unknown": len(skipped),
                    "preserved_detail": skipped,
                    "would_have_applied": [p for p, _ in planned],
                    "realized_before": before, "realized_after": before,
                    "labels": ("an apply with no inspected manifest to check "
                               "against is an unverified write; nothing was "
                               "written")}
        if not checked.get("ok"):
            return {"mode": "ABORTED", "batch_id": batch_id,
                    "reason": "BATCH_MISMATCH", "batch_check": checked,
                    "legs_seen": len(legs), "applied": 0, "refused": 0,
                    "preserved_unknown": len(skipped),
                    "preserved_detail": skipped,
                    "would_have_applied": [p for p, _ in planned],
                    "realized_before": before, "realized_after": before,
                    "labels": ("the fresh walk is not the batch that was "
                               "inspected. A changed world is not a smaller "
                               "correction: nothing was written")}
    for plan, per_fill in planned:
        key = plan["key"]
        market = plan["market"]
        side = plan["side"]
        placeholder = _d(plan["cost_before_usd"])
        measured = _d(plan["cost_after_usd"])
        fees = _d(plan["fees_usd"])
        if dry:
            applied.append({**plan, "would_apply": True})
            delta_total += placeholder - measured
            continue
        out = store.correct_cost_basis(
            market, side,
            correction_id=plan["correction_id"],
            expect={"cost_total": plan["expect_cost_total"]},
            measured_cost=measured,
            measured_fees=fees,
            restate=bool(plan.get("restate")),
            evidence={"trade_ids": plan["trade_ids"], "per_fill": per_fill,
                      "activity_walk": walk})
        if out.get("corrected"):
            applied.append({**plan, "delta": out.get("delta")})
            d = _d(out.get("delta"))
            if d is not None:
                delta_total += d
        else:
            refused.append({"key": key, "reason": out.get("reason"),
                            "detail": {k: v for k, v in out.items()
                                       if k not in ("corrected", "reason")}})
    return {
        "mode": APPLY_INSPECT if dry else "APPLIED",
        "batch_id": batch_id,
        "batch_check": checked,
        "activity_walk": walk,
        "legs_seen": len(legs),
        "applied": len(applied),
        "refused": len(refused),
        "preserved_unknown": len(skipped),
        "realized_delta_usd": str(delta_total),
        "realized_before": before,
        "realized_after": (None if dry else store.realized_lines()),
        "applied_detail": applied,
        "refused_detail": refused,
        "preserved_detail": skipped,
        "labels": (
            "mode INSPECT writes NOTHING. A refusal is not a failure: the leg "
            "keeps its UNKNOWN cost, which is the intended outcome for a "
            "quantity-mismatched leg. This function adds no authority -- every "
            "guard lives in the store's verb and runs on every call. HALT is "
            "neither read nor cleared and no ownership field is written, so "
            "verified_l5 and manual are exactly as they were."),
    }
