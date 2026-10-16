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
FILL_KINDS = frozenset(("filled", "fill", "trade"))

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
    for k in ("marketSlug", "market", "slug"):
        v = row.get(k)
        if v:
            return str(v)
    return ""


def _row_side(row: Mapping[str, Any]) -> Optional[str]:
    v = str(row.get("side") or row.get("outcome") or "").strip().upper()
    if v in ("UP", "YES", "BUY_LONG"):
        return "UP"
    if v in ("DOWN", "NO", "BUY_SHORT"):
        return "DOWN"
    return None


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
        coid = str(r.get("clientOrderId") or r.get("clientOrderID") or "") or None
        owner = (L5_OWNED if coid and coid in our_coids
                 else OWNER_MANUAL if coid else UNKNOWN_OWNER)
        qty = _d(r.get("quantity") if r.get("quantity") is not None
                 else r.get("cumQuantity"))
        px = _d(r.get("price") if r.get("price") is not None
                else r.get("avgPx"))
        fee = _d(r.get("commission") if r.get("commission") is not None
                 else r.get("commissionNotionalTotalCollected"))
        fills.append({
            "trade_id": str(r.get("tradeId") or r.get("id") or "") or None,
            "order_id": str(r.get("orderId") or "") or None,
            "client_order_id": coid, "owner": owner,
            "qty": None if qty is None else str(qty),
            "price": None if px is None else str(px),
            "fee": None if fee is None else str(fee),
            "t": str(r.get("time") or r.get("createTime") or "") or None,
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
