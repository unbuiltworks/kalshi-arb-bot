"""Platform-wide scan: discovery -> relations -> quoted screen -> depth economics.

WHY THE QUOTED SCREEN IS SAFE. Deeper levels are always worse than the touch, so
a basket whose BEST prices do not already cross cannot become profitable at
larger size. The touch test is therefore a NECESSARY condition, and using it to
decide which books to pull discards nothing real while keeping the scan to a few
hundred requests instead of thousands.

WHAT IS CLAIMED AND WHAT IS NOT. A candidate reaching DEPTH_EXECUTABLE means the
resting ladder, at the instant it was read, priced a guaranteed payoff below its
cost after taker fees on every leg. It does NOT mean the fills would have
happened: see `legging` in the output, and `executable.py` on why displayed size
is an upper bound.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from src.gemini_arb_discovery import books as BK
from src.gemini_arb_discovery import structure as ST
from src.gemini_arb_discovery import universe as UN
from src.gemini_arb_discovery.executable import (BUY_NO, BUY_YES, best_size)

#: granularity of the settled quantity, per family prefix of the event ticker.
#: Required by `verify_partition` and never guessed: see structure.py.
STEP_BY_CATEGORY = {"Weather": D("1")}


def _q(c: Dict[str, Any], field: str, default: str) -> D:
    v = c.get(field)
    try:
        return D(str(v)) if v not in (None, "") else D(default)
    except Exception:  # noqa: BLE001
        return D(default)


def touch(c: Dict[str, Any]) -> Tuple[Optional[D], Optional[D]]:
    """(bestBid, bestAsk) from the catalogue's quote block. QUOTED stage only."""
    p = c.get("prices") or {}
    def _d(x):
        try:
            return D(str(x)) if x not in (None, "") else None
        except Exception:  # noqa: BLE001
            return None
    return (_d(p.get("bestBid")), _d(p.get("bestAsk")))


# --------------------------------------------------------- candidate builders

def ladder_candidates(event: Dict[str, Any], rels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Monotonicity violations, screened at the touch.

    For an `above` ladder with s1 < s2, YES(s1) >= YES(s2) must hold. The trade
    is: buy YES(s1) and buy NO(s2). Its payoff is 1 when the underlying lands
    outside [s1, s2) and 2 inside it, so 1 is guaranteed. Cost is
    ask(s1) + (1 - bid(s2)), hence an edge exactly when bid(s2) > ask(s1).
    """
    by_ticker = {c.get("ticker"): c for c in UN.active_contracts(event)}
    out = []
    for rel in rels:
        if rel.get("relation") not in (ST.LADDER_ABOVE, ST.LADDER_BELOW):
            continue
        mem = rel["members"]
        for i in range(len(mem)):
            for j in range(i + 1, len(mem)):
                lo_c = by_ticker.get(mem[i]["ticker"])
                hi_c = by_ticker.get(mem[j]["ticker"])
                if not lo_c or not hi_c:
                    continue
                # cheap leg = the one that should be WORTH LESS but quotes richer
                if rel["relation"] == ST.LADDER_ABOVE:
                    long_c, short_c = lo_c, hi_c   # long lower strike, short higher
                else:
                    long_c, short_c = hi_c, lo_c   # mirrored for `below`
                _, long_ask = touch(long_c)
                short_bid, _ = touch(short_c)
                if long_ask is None or short_bid is None:
                    continue
                if short_bid <= long_ask:
                    continue  # no quoted gap -> cannot be executable at any size
                out.append({
                    "kind": rel["relation"], "event": event.get("ticker"),
                    "event_title": event.get("title"),
                    "category": event.get("category"),
                    "why_guaranteed": rel["why"],
                    "quoted_edge_per_unit": str(short_bid - long_ask),
                    "legs": [
                        {"symbol": long_c["instrumentSymbol"], "action": BUY_YES,
                         "ticker": long_c.get("ticker"), "label": long_c.get("label")},
                        {"symbol": short_c["instrumentSymbol"], "action": BUY_NO,
                         "ticker": short_c.get("ticker"), "label": short_c.get("label")},
                    ],
                    "increment": str(max(_q(long_c, "quantityIncrement", "1"),
                                         _q(short_c, "quantityIncrement", "1"))),
                    "minimum": str(max(_q(long_c, "quantityMinimum", "1"),
                                       _q(short_c, "quantityMinimum", "1"))),
                })
    return out


def partition_candidates(event: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Exhaustive-partition underpricing, screened at the touch.

    Returns (candidates, verification) so a set that FAILED verification is still
    reported as unverified rather than vanishing.
    """
    cs = UN.active_contracts(event)
    step = STEP_BY_CATEGORY.get(event.get("category"))
    if step is None or len(cs) < 2:
        return ([], {"exhaustive": False, "why": "no declared settlement step for "
                                                 "this family"})
    ver = ST.verify_partition(cs, step=step)
    if not ver.get("exhaustive"):
        return ([], ver)
    asks = []
    for c in cs:
        _, a = touch(c)
        if a is None:
            return ([], {"exhaustive": True, "why": ver["why"],
                         "screen": "a member has no ask; basket unbuyable"})
        asks.append(a)
    total = sum(asks, D("0"))
    if total >= D("1"):
        return ([], {"exhaustive": True, "why": ver["why"],
                     "screen": "sum of asks %s >= 1" % total})
    return ([{
        "kind": ST.PARTITION, "event": event.get("ticker"),
        "event_title": event.get("title"), "category": event.get("category"),
        "why_guaranteed": ver["why"],
        "quoted_edge_per_unit": str(D("1") - total),
        "legs": [{"symbol": c["instrumentSymbol"], "action": BUY_YES,
                  "ticker": c.get("ticker"), "label": c.get("label")} for c in cs],
        "increment": str(max((_q(c, "quantityIncrement", "1") for c in cs),
                             default=D("1"))),
        "minimum": str(max((_q(c, "quantityMinimum", "1") for c in cs),
                           default=D("1"))),
    }], ver)


def duplicate_candidates(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Equivalent contracts in different events quoting a crossed pair."""
    by_symbol = {c["instrumentSymbol"]: (e, c) for e in events
                 for c in UN.active_contracts(e)}
    out = []
    for rel in ST.duplicates(events):
        mem = rel["members"]
        for i in range(len(mem)):
            for j in range(len(mem)):
                if i == j:
                    continue
                a = by_symbol.get(mem[i]["symbol"])
                b = by_symbol.get(mem[j]["symbol"])
                if not a or not b:
                    continue
                _, ask_a = touch(a[1])
                bid_b, _ = touch(b[1])
                if ask_a is None or bid_b is None or bid_b <= ask_a:
                    continue
                out.append({
                    "kind": ST.DUPLICATE, "event": "%s|%s" % (mem[i]["event"],
                                                              mem[j]["event"]),
                    "event_title": "equivalent contracts, different events",
                    "category": a[0].get("category"),
                    "why_guaranteed": rel["why"],
                    "quoted_edge_per_unit": str(bid_b - ask_a),
                    "legs": [
                        {"symbol": mem[i]["symbol"], "action": BUY_YES,
                         "ticker": mem[i]["ticker"], "label": mem[i]["label"]},
                        {"symbol": mem[j]["symbol"], "action": BUY_NO,
                         "ticker": mem[j]["ticker"], "label": mem[j]["label"]},
                    ],
                    "increment": str(max(_q(a[1], "quantityIncrement", "1"),
                                         _q(b[1], "quantityIncrement", "1"))),
                    "minimum": str(max(_q(a[1], "quantityMinimum", "1"),
                                       _q(b[1], "quantityMinimum", "1"))),
                })
    return out


# ------------------------------------------------------------------- the scan

def depth_confirm(cand: Dict[str, Any], *, max_qty: D) -> Dict[str, Any]:
    """Pull every leg's full book and re-price the basket at real depth."""
    legs = []
    for leg in cand["legs"]:
        bk = BK.fetch_book(leg["symbol"])
        if not bk.get("readable"):
            return {**cand, "stage": QUOTED_ONLY,
                    "depth_verdict": "book unreadable for %s (%s)" % (
                        leg["symbol"], bk.get("err"))}
        legs.append({"symbol": leg["symbol"], "action": leg["action"], "book": bk})
    inc = D(cand["increment"])
    res = best_size(legs, increment=inc,
                    maximum=max(max_qty, inc), min_payoff_per_unit=D("1"))
    if not res["best"]:
        return {**cand, "stage": QUOTED_ONLY,
                "depth_verdict": "no size clears zero after taker fees at real depth"}
    return {**cand, "stage": "DEPTH_EXECUTABLE", "economics": res["best"],
            "max_profitable": res["max_profitable"],
            "legging": _legging(cand),
            "book_read_at": time.time()}


QUOTED_ONLY = "QUOTED"


def _legging(cand: Dict[str, Any]) -> Dict[str, Any]:
    n = len(cand["legs"])
    return {
        "legs": n,
        "simultaneously_submittable": (
            "UNVERIFIED: Gemini exposes no multi-leg or batched prediction-market "
            "order endpoint in the paths this repository has established, so the "
            "legs must be submitted as %d independent orders." % n),
        "risk": (
            "Each leg is a separate taker order. Between the first and last "
            "acknowledgement the remaining legs' books can move or empty, leaving "
            "outright exposure on whatever filled. With %d legs this risk grows "
            "with every additional leg." % n),
        "mitigation_not_assumed": (
            "Immediate-or-cancel is available per the venue's timeInForce "
            "enumeration, which bounds a stale leg but does NOT make the basket "
            "atomic: an IOC leg that cancels still leaves the filled legs on."),
    }


def run(out_dir: str, *, max_qty: D = D("500"), limit_candidates: int = 400,
        max_pages: int = 60) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    disc = UN.fetch_all_events(max_pages=max_pages)
    events = UN.active_events(disc["events"])
    evidence = UN.write_evidence(out_dir, disc)

    cands: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    rel_counts: Dict[str, int] = {}
    for e in events:
        cs = UN.active_contracts(e)
        rels = ST.ladders(e, cs)
        for r in rels:
            rel_counts[r["relation"]] = rel_counts.get(r["relation"], 0) + 1
        cands.extend(ladder_candidates(e, rels))
        pc, ver = partition_candidates(e)
        cands.extend(pc)
        if pc:
            rel_counts[ST.PARTITION] = rel_counts.get(ST.PARTITION, 0) + 1
        elif ver.get("exhaustive"):
            rel_counts["PARTITION_VERIFIED_NO_EDGE"] = rel_counts.get(
                "PARTITION_VERIFIED_NO_EDGE", 0) + 1
        if e.get("type") == "categorical" and e.get("category") != "Weather" \
                and len(cs) >= 2 and not any(ST.bucket_of(c) for c in cs):
            unverified.append({
                "event": e.get("ticker"), "title": e.get("title"),
                "category": e.get("category"), "n_contracts": len(cs),
                "why_unverified": (
                    "non-numeric categorical outcome set. Exhaustiveness (and so "
                    "any sum-of-asks claim) cannot be established from structured "
                    "fields; it needs the settlement text read for draw, void and "
                    "postponement provisions. NOT treated as an arbitrage."),
                "quoted_sum_of_asks": str(sum(
                    (touch(c)[1] or D("0") for c in cs), D("0"))),
            })
    dup = duplicate_candidates(events)
    cands.extend(dup)
    rel_counts[ST.DUPLICATE] = len(dup)

    cands.sort(key=lambda c: D(c["quoted_edge_per_unit"]), reverse=True)
    screened = cands[:limit_candidates]
    confirmed, rejected = [], []
    for c in screened:
        r = depth_confirm(c, max_qty=max_qty)
        (confirmed if r.get("stage") == "DEPTH_EXECUTABLE" else rejected).append(r)
    confirmed.sort(key=lambda r: D(r["economics"]["net_locked_profit"]), reverse=True)

    result = {
        "scanned_at": time.time(),
        "evidence": evidence,
        "discovery": {k: disc[k] for k in
                      ("pages", "errors", "total_claimed", "elapsed_s")},
        "active_events": len(events),
        "active_contracts": sum(len(UN.active_contracts(e)) for e in events),
        "relations_found": rel_counts,
        "quoted_candidates": len(cands),
        "depth_tested": len(screened),
        "depth_executable": confirmed,
        "quoted_but_not_executable": rejected,
        "unverified_outcome_sets": unverified,
        "structural_notes": {
            "same_contract_yes_no_complement": (
                "IMPOSSIBLE BY CONSTRUCTION on this venue: one book per contract, "
                "NO quoted as an exact complement of it. Verified over 4,099 "
                "active contracts with zero exceptions. Not scanned; asserted."),
        },
    }
    with open(os.path.join(out_dir, "scan_result.json"), "w") as fh:
        json.dump(result, fh, indent=2)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="analysis/gemini_arb_discovery/latest")
    ap.add_argument("--max-qty", default="500")
    ap.add_argument("--limit-candidates", type=int, default=400)
    ap.add_argument("--max-pages", type=int, default=60)
    a = ap.parse_args()
    r = run(a.out, max_qty=D(a.max_qty), limit_candidates=a.limit_candidates,
            max_pages=a.max_pages)
    print(json.dumps({k: v for k, v in r.items()
                      if k not in ("depth_executable", "quoted_but_not_executable",
                                   "unverified_outcome_sets")}, indent=2))
    print("DEPTH_EXECUTABLE: %d" % len(r["depth_executable"]))
    for c in r["depth_executable"][:20]:
        print("  $%s net | %s | %s | qty %s | roi %s" % (
            c["economics"]["net_locked_profit"], c["kind"], c["event"],
            c["economics"]["qty"], c["economics"]["roi_on_capital"]))


if __name__ == "__main__":
    main()
