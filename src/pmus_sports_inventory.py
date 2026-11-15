"""Account-wide inventory reconciliation: VENUE positions vs the sports book.

OWNER 2026-11-15 (Priority 0): the audit reported "$26.72 of one-sided
exposure" while the heartbeat reported $0. Root cause: the audit's
`6_unmatched_cost_open_usd` came from PNL.book_economics(), which sums the
lone-leg COST of EVERY market the book has ever traded -- settled markets
included -- while the heartbeat's figure (Book.total_unmatched) counts only
markets still open. $26.72 was lifetime lone-leg cost (42 legs, 37 of them
already settled at zero), labelled "open". Neither figure was the venue's.

This module answers the question from the venue itself. Pure: the engine
supplies the raw /v1/portfolio/positions rows, the open orders and the book.

VENUE FACTS USED (proven elsewhere in this repository, not assumed here):
  * positions are NET per market; buying NO while long YES is a sale, so the
    venue's net equals the book's YES - NO (l5_live_execution notes).
  * a row's sign is proven from qtyBought - qtySold (long-YES positive);
    a netPosition disagreeing with that is reported, never resolved by guess
    (l5_live_execution.position_row).
  * `availablePositions` is NOT a held position (pmus_settlement_auditor).

Ownership is by market: a market in the sports book is SPORTS; the BTC
lanes trade only cpc-btc-updown-5m-* / -15m-*; any other aec-* market is a
sports-venue market this bot does not track (manual or unknown); anything
else is UNKNOWN. Read-only: nothing here cancels, sells or adopts anything.
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

BTC5M_PREFIX = "cpc-btc-updown-5m-"
BTC15M_PREFIX = "cpc-btc-updown-15m-"


def _num(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _first(row: Mapping[str, Any], keys) -> Optional[D]:
    for k in keys:
        if k in row:
            v = _num(row.get(k))
            if v is not None:
                return v
    return None


def rows_of(resp: Any, key: str = "positions") -> List[Dict[str, Any]]:
    """Position rows whichever shape the venue served (object keyed by slug
    on this account, or a list)."""
    rows = resp.get(key) if isinstance(resp, Mapping) else None
    if isinstance(rows, Mapping):
        return [{"marketSlug": k, **v} for k, v in rows.items()
                if isinstance(v, Mapping)]
    return [dict(r) for r in (rows or []) if isinstance(r, Mapping)]


def parse_row(p: Mapping[str, Any]) -> Dict[str, Any]:
    """slug, signed net (long-YES positive) and how it was proven."""
    slug = str(p.get("marketSlug") or p.get("market") or "")
    bought = _first(p, ("qtyBoughtDecimal", "qtyBought"))
    sold = _first(p, ("qtySoldDecimal", "qtySold"))
    net = _first(p, ("netPositionDecimal", "netPosition"))
    out: Dict[str, Any] = {"slug": slug, "expired": bool(p.get("expired")),
                           "venue_cost": _num(p.get("cost")),
                           "cash_value": _num(p.get("cashValue"))}
    if bought is not None and sold is not None:
        signed = bought - sold
        out.update(net=signed, proof="bought_minus_sold",
                   sign_conflict=net is not None and net != signed)
    elif net is not None:
        out.update(net=net, proof="net_only_unproven_sign",
                   sign_conflict=False)
    else:
        q = _first(p, ("quantity", "qty"))
        out.update(net=q, proof="quantity_only" if q is not None
                   else "unreadable", sign_conflict=False)
    return out


def owner_of(slug: str, sports_slugs) -> str:
    if slug in sports_slugs:
        return "sports"
    if slug.startswith(BTC5M_PREFIX):
        return "btc5m"
    if slug.startswith(BTC15M_PREFIX):
        return "btc15m"
    if slug.startswith("aec-"):
        return "sports_venue_untracked"
    return "unknown"


def book_net(mk: Mapping[str, Any]) -> D:
    return D(str(mk["pos"]["YES"])) - D(str(mk["pos"]["NO"]))


def book_unmatched_cost(mk: Mapping[str, Any]) -> D:
    y, n = D(str(mk["pos"]["YES"])), D(str(mk["pos"]["NO"]))
    cy, cn = D(str(mk["cost"]["YES"])), D(str(mk["cost"]["NO"]))
    if y > n and y > 0:
        return cy * (y - n) / y
    if n > y and n > 0:
        return cn * (n - y) / n
    return D(0)


def reconcile(position_rows: Iterable[Mapping[str, Any]],
              open_orders: Iterable[Mapping[str, Any]],
              book_markets: Mapping[str, Mapping[str, Any]],
              own_order_ids: Iterable[str] = (),
              walk_complete: bool = True,
              tol: D = D("0.0001")) -> Dict[str, Any]:
    """Every venue position and open order attributed to an owner, and every
    sports-book market compared to the venue. Exposure figures:

      sports_open_unmatched_cost_usd   cost of lone legs the VENUE still holds
                                       in live (not done) sports markets
      sports_pending_resolution_cost_usd  lone legs the venue still holds in
                                       markets that closed but have not paid
      sports_lifetime_unmatched_cost_usd  the old, mislabelled figure
    """
    sports = set(book_markets)
    own = {str(x) for x in own_order_ids}
    venue: Dict[str, Dict[str, Any]] = {}
    for r in position_rows:
        pr = parse_row(r)
        if pr["slug"]:
            venue[pr["slug"]] = pr

    by_owner: Dict[str, Dict[str, Any]] = {}

    def bucket(o: str) -> Dict[str, Any]:
        return by_owner.setdefault(o, {"positions": 0, "open_orders": 0,
                                       "markets": []})

    for slug, pr in venue.items():
        if not pr["net"]:
            continue
        b = bucket(owner_of(slug, sports))
        b["positions"] += 1
        if len(b["markets"]) < 12:
            b["markets"].append({"slug": slug, "net": str(pr["net"]),
                                 "expired": pr["expired"]})

    orders_by_owner: Dict[str, int] = {}
    foreign_orders_in_sports_markets = []
    for o in open_orders:
        if not isinstance(o, Mapping):
            continue
        slug = str(o.get("marketSlug") or "")
        oid = str(o.get("id") or "")
        ow = "sports" if oid in own else owner_of(slug, ())
        if ow != "sports" and slug in sports:
            foreign_orders_in_sports_markets.append(
                {"slug": slug, "order_id": oid})
        orders_by_owner[ow] = orders_by_owner.get(ow, 0) + 1
        bucket(ow)["open_orders"] += 1

    rows, mismatches, awaiting = [], [], []
    open_cost = pending_cost = lifetime_cost = D(0)
    counts = {"match": 0, "pending_resolution": 0, "settled_flat": 0,
              "settled_awaiting_px": 0, "settled_venue_still_holds": 0,
              "mismatch": 0}
    for slug, mk in book_markets.items():
        bn = book_net(mk)
        uc = book_unmatched_cost(mk)
        lifetime_cost += uc
        pr = venue.get(slug)
        vn = pr["net"] if pr and pr["net"] is not None else D(0)
        done = bool(mk.get("done"))
        settled = mk.get("settle_px") is not None
        expired = bool(pr and pr["expired"])
        if done and (settled or expired):
            status = "settled_venue_still_holds" if vn and not expired \
                else "settled_flat"
        elif done and not vn:
            status = "settled_flat" if bn == 0 else "settled_awaiting_px"
        elif (vn - bn).copy_abs() <= tol:
            status = "pending_resolution" if done else "match"
        else:
            status = "mismatch"
        counts[status] += 1
        if status == "match":
            open_cost += uc
        elif status == "pending_resolution":
            pending_cost += uc
        if status == "settled_venue_still_holds" and len(awaiting) < 20:
            awaiting.append({"slug": slug, "venue_net": str(vn),
                             "settle_px": str(mk.get("settle_px"))})
        if status == "mismatch" or (pr and pr.get("sign_conflict")):
            mismatches.append({"slug": slug, "status": status,
                               "venue_net": str(vn), "book_net": str(bn),
                               "done": done, "proof": pr and pr["proof"],
                               "sign_conflict": bool(pr and
                                                     pr.get("sign_conflict"))})
        rows.append(slug)
    return {
        "walk_complete": walk_complete,
        "venue_positions_nonzero": sum(1 for p in venue.values() if p["net"]),
        "by_owner": by_owner,
        "open_orders_by_owner": orders_by_owner,
        "foreign_orders_in_sports_markets": foreign_orders_in_sports_markets,
        "sports_book_markets": len(rows),
        "sports_status": counts,
        "sports_open_unmatched_cost_usd": str(open_cost.quantize(D("0.0001"))),
        "sports_pending_resolution_cost_usd": str(
            pending_cost.quantize(D("0.0001"))),
        "sports_lifetime_unmatched_cost_usd": str(
            lifetime_cost.quantize(D("0.0001"))),
        "mismatches": mismatches[:20],
        # resolved (public settlement price final) but the venue row is
        # still there: a settlement-accounting lag, not live risk
        "settled_awaiting_venue_update": awaiting,
        "reconciled": walk_complete and not mismatches,
        "label": ("VENUE positions vs sports book; read-only. 'open' = lone "
                  "legs the venue still holds in live markets; 'lifetime' "
                  "includes settled markets and is NOT exposure."),
    }
