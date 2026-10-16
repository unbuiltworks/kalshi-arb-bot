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


def _d(value: Any) -> Optional[D]:
    """A finite Decimal, or None. None means UNKNOWN, never zero."""
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


def walk_activities(transport: Any, *, max_pages: int = MAX_PAGES
                    ) -> Dict[str, Any]:
    """Every reachable activity row, newest first. READ-ONLY GETs only.

    The walk's ENDING is returned beside the rows, because a truncated feed
    and a complete one are different facts: a basis computed from a
    truncated walk could be missing fills, so the caller must be able to
    tell. `ended` is "eof", "no_cursor", "max_pages" or "error:<Type>".
    """
    rows: List[Dict[str, Any]] = []
    cursor, pages, ended = None, 0, "eof"
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
    return {"rows": rows, "pages": pages, "ended": ended,
            "complete": ended in ("eof", "no_cursor")}


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
SIDE_KEYS = ("side", "outcome", "outcomeSide", "orderSide", "positionSide",
             "direction", "takerSide", "buySell")

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
    if not v:
        return None
    # the venue's own vocabularies, including the POSITION_RESOLUTION_SIDE_*
    # and OUTCOME_SIDE_* forms observed on the feed. Suffix match, so a new
    # prefix does not silently fall through to the wrong side.
    if v.endswith(("UP", "YES", "LONG", "BUY_LONG")):
        return "UP"
    if v.endswith(("DOWN", "NO", "SHORT", "BUY_SHORT")):
        return "DOWN"
    return None


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
        fee = _d(b.get("commission") if b.get("commission") is not None
                 else b.get("commissionNotionalTotalCollected"))
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
        q = _d(b.get("quantity") if b.get("quantity") is not None
               else b.get("cumQuantity"))
        px = _d(b.get("price") if b.get("price") is not None
                else b.get("avgPx"))
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


def classify_fills(rows: List[Mapping[str, Any]], *, market: str, side: str,
                   our_coids: set) -> Dict[str, Any]:
    """Actual fills for one leg, split by owner. Nothing is assumed."""
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
        owner = (L5_OWNED if coid and coid in our_coids
                 else OWNER_MANUAL if coid else UNKNOWN_OWNER)
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
            "client_order_id": coid, "owner": owner,
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


def establish_basis(fills: List[Mapping[str, Any]], *, expect_qty: D
                    ) -> Dict[str, Any]:
    """The leg's ACTUAL cost and fees, or an explicit refusal.

    Every fill must carry a quantity, a price AND a fee, and the quantities
    must sum to the quantity the ledger holds. A partial match is not a
    basis: it would understate cost, which is the one direction that
    flatters realized.
    """
    if not fills:
        return {"established": False, "reason": "NO_FILLS_FOUND"}
    total_qty, cost, fee_total = D(0), D(0), D(0)
    for f in fills:
        q, p, fee = _d(f.get("qty")), _d(f.get("price")), _d(f.get("fee"))
        if q is None:
            return {"established": False, "reason": "FILL_WITHOUT_QUANTITY"}
        if p is None:
            return {"established": False, "reason": "FILL_WITHOUT_PRICE"}
        if fee is None:
            return {"established": False, "reason": "FILL_WITHOUT_FEE",
                    "labels": "an absent fee is not a free $0"}
        total_qty += q
        cost += q * p
        fee_total += fee
    if total_qty != expect_qty:
        return {"established": False, "reason": "QUANTITY_MISMATCH",
                "venue_qty": str(total_qty), "ledger_qty": str(expect_qty)}
    return {"established": True, "qty": str(total_qty),
            "actual_cost_usd": str(cost), "fees_usd": str(fee_total),
            "avg_price": str(cost / total_qty) if total_qty else None}


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


def audit(store: Any, transport: Any, *, max_pages: int = MAX_PAGES
          ) -> Dict[str, Any]:
    """The whole read-only pass. Writes nothing, decides nothing."""
    keys = sorted(set(str(k) for k in
                      ((getattr(store, "state", {}) or {})
                       .get("leg_cost_basis_unknown") or [])))
    walk = walk_activities(transport, max_pages=max_pages)
    our = our_client_order_ids(store)
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
                             our_coids=our)
        basis = (establish_basis(cls["fills"], expect_qty=held)
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
        # WHY nothing matched, when nothing matched. Computed only on the
        # failing case so a healthy audit does not carry the diagnostic.
        "feed_shape": (feed_shape(walk["rows"],
                                  markets=[k.split("|")[0] for k in keys],
                                  sides=["UP", "DOWN"])
                       if establishable == 0 and keys else None),
        "our_client_order_ids": len(our),
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
