"""MODEL B OVERLAY STUDY: is it the venue, or is it our price?

The question the owner put: does Gemini lack counterparties, or are we
offering liquidity at prices counterparties do not want to interact with?

Three candidate quotes are evaluated against the SAME recorded book, for the
same leg, at the same instant:

    NATIVE          what the frozen L5 policy actually chose
    JOIN_BEST       the best passive price already showing
    IMPROVE_1_TICK  one tick better than the best passive price, and ONLY
                    when that is still strictly inside the spread so the
                    order remains maker-only

MODEL A IS NOT TOUCHED. Nothing here is wired into the controller, no order
is sent, and the overlays are evaluated on recorded evidence after the fact.
The frozen policy's own choice is carried as one of the three candidates so
the comparison is like-for-like.

WHAT MAKES THIS DIFFERENT FROM EVERY EARLIER SHADOW NUMBER. The public tape
carries PRINT SIZES and serves expired contracts, so a passive buy at price P
can be tested against what actually traded: it needs a SELL print at a price
at or below P while it was resting, AND that print must be bigger than the
size resting ahead of it. Both are measurable. That is a counterfactual fill
test against real executions rather than against a model of them.

IT IS STILL COUNTERFACTUAL. A THEORETICAL fill is not a fill, is never
submitted, filled, settled or realized, and adding our own size to a book
would have changed it. Every figure here carries that label.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

NATIVE = "NATIVE"
JOIN = "JOIN_BEST"
IMPROVE = "IMPROVE_1_TICK"
OVERLAYS = (NATIVE, JOIN, IMPROVE)

TICK = D("0.01")

THEORETICAL = ("THEORETICAL. No order existed, nothing was submitted, filled, "
               "settled or realized, and our own size would have changed the "
               "book it is measured against.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def candidates(book: Mapping[str, Any], leg: str, native_price: Any, *,
               tick: D = TICK) -> Dict[str, Any]:
    """The three prices, with a reason wherever one does not exist."""
    side = "yes" if str(leg).upper() == "YES" else "no"
    q = (book or {}).get(side) or {}
    bid, ask = _dec(q.get("bid")), _dec(q.get("ask"))
    nat = _dec(native_price)
    out: Dict[str, Any] = {"leg": str(leg).upper(), "best_bid": q.get("bid"),
                           "best_ask": q.get("ask")}

    out[NATIVE] = ({"price": str(nat), "exists": True} if nat is not None
                   else {"exists": False, "why": "the policy chose no price "
                         "on this leg this cycle"})
    out[JOIN] = ({"price": str(bid), "exists": True} if bid is not None
                 else {"exists": False, "why": "no passive best price showing"})

    if bid is None or ask is None:
        out[IMPROVE] = {"exists": False,
                        "why": "one side of the book was absent, so a "
                               "maker-only improvement cannot be proven"}
    else:
        imp = bid + tick
        # MAKER-ONLY OR NOT AT ALL. An improvement that reaches the ask is a
        # cross, and crossing is not authorized on this venue.
        if imp < ask:
            out[IMPROVE] = {"price": str(imp), "exists": True,
                            "maker_only": True}
        else:
            out[IMPROVE] = {"exists": False, "maker_only": False,
                            "why": ("bid+1 tick (%s) would reach the ask (%s), "
                                    "so it is not maker-only and is refused"
                                    % (imp, ask))}
    out["ticks_native_behind_best"] = (
        str((bid - nat) / tick) if bid is not None and nat is not None
        else None)
    return out


def size_ahead(depth: Any, price: Any) -> Optional[D]:
    """Resting size at a price, from the contract ladder (YES side only).

    This is what a new order would queue behind. A price BETTER than the best
    bid has nothing ahead of it by construction.
    """
    p = _dec(price)
    if p is None or not isinstance(depth, Mapping) or not depth.get("readable"):
        return None
    lv = (depth.get("bids") or {}).get("levels") or []
    tot = D("0")
    seen = False
    for row in lv:
        px, qty = _dec(row.get("price")), _dec(row.get("qty"))
        if px is None or qty is None:
            continue
        if px == p:
            tot += qty
            seen = True
    return tot if seen else D("0")


def counterfactual_fill(*, price: Any, leg: str, prints: Sequence[Mapping],
                        from_ms: Any, to_ms: Any,
                        ahead: Optional[D] = None,
                        our_qty: Any = "0.01") -> Dict[str, Any]:
    """Would a passive buy at `price` have been hit by real executions?

    A resting BUY is hit by an aggressor SELLING at or below our price. It
    fills only once the print exceeds the size queued ahead of it, so the
    queue is respected rather than wished away. When the queue is unreadable
    -- the NO leg has no published ladder -- that is stated and the answer is
    given BOTH ways rather than assumed.
    """
    p = _dec(price)
    qty = _dec(our_qty) or D("0.01")
    lo, hi = _dec(from_ms), _dec(to_ms)
    out: Dict[str, Any] = {"price": (str(p) if p is not None else None),
                           "leg": str(leg).upper(), "labels": THEORETICAL}
    if p is None or lo is None or hi is None:
        out.update({"computable": False, "why": "price or window unreadable"})
        return out
    # A BINARY CONTRACT TRADES STRICTLY BETWEEN 0 AND 1. A price outside that
    # is not a cheap quote, it is a bad read, and answering it would put a
    # number on something that cannot exist.
    if not (D("0") < p < D("1")):
        out.update({"computable": False,
                    "why": "price %s is outside (0, 1); a binary contract "
                           "cannot trade there" % p})
        return out

    # THE TAPE IS PRICED IN YES AND RECORDS THE AGGRESSOR'S SIDE. Verified
    # against CONTINUOUS-5: our passive NO buy at 0.46 appears on the tape as
    # an aggressor YES *buy* at 0.54, because selling NO to us IS buying YES.
    # So the two legs need opposite tests, and using the YES test for a NO
    # rung -- which this module did until it was checked against C5 -- counts
    # the wrong prints entirely.
    yes = str(leg).upper() == "YES"
    want_type = "sell" if yes else "buy"
    threshold = p if yes else (D("1") - p)

    hits = []
    for t in prints or []:
        ms = _dec(t.get("utc_ms"))
        tp, ta = _dec(t.get("price")), _dec(t.get("amount"))
        if ms is None or tp is None or ta is None:
            continue
        if not (lo <= ms <= hi):
            continue
        if str(t.get("type")) != want_type:
            continue
        # YES rung at p: an aggressor SELLS YES at or below p.
        # NO  rung at p: an aggressor BUYS  YES at or above 1 - p.
        reaches = (tp <= threshold) if yes else (tp >= threshold)
        if reaches:
            hits.append({"utc_ms": int(ms), "price": str(tp),
                         "amount": str(ta),
                         "our_leg_price": (str(tp) if yes
                                           else str(D("1") - tp))})

    traded = sum((_dec(h["amount"]) or D("0") for h in hits), D("0"))
    out.update({
        "computable": True,
        "tape_side_tested": want_type,
        "yes_price_threshold": str(threshold),
        "side_why": ("the tape is priced in YES and records the aggressor. A "
                     "YES rung is reached by an aggressor SELL at or below "
                     "its price; a NO rung at p is reached by an aggressor "
                     "BUY at or above 1 - p."),
        "reaching_prints": len(hits),
        "reaching_contracts": str(traded),
        "prints": hits[:8],
        "filled_ignoring_queue": bool(hits),
    })
    if ahead is None:
        out.update({
            "queue_readable": False,
            "filled_respecting_queue": None,
            "why": ("the venue publishes one ladder, the YES side; this leg's "
                    "queue is not readable, so a queue-respecting answer is "
                    "not given"),
        })
    else:
        out.update({
            "queue_readable": True,
            "size_ahead": str(ahead),
            "needed_to_reach_us": str(ahead + qty),
            "filled_respecting_queue": bool(traded > ahead),
        })
    return out


def overlay_cycle(*, utc_ms: Any, next_utc_ms: Any, leg: str,
                  native_price: Any, book: Mapping[str, Any], depth: Any,
                  prints: Sequence[Mapping], our_qty: Any = "0.01",
                  complement_book: Any = None) -> Dict[str, Any]:
    """One cycle, three overlays, measured against what actually traded."""
    cand = candidates(book, leg, native_price)
    side = "yes" if str(leg).upper() == "YES" else "no"
    q = (book or {}).get(side) or {}
    best = _dec(q.get("bid"))
    readable_queue = (side == "yes")

    rows: Dict[str, Any] = {}
    for name in OVERLAYS:
        c = cand.get(name) or {}
        if not c.get("exists"):
            rows[name] = {"exists": False, "why": c.get("why")}
            continue
        p = _dec(c["price"])
        ahead = (size_ahead(depth, p) if readable_queue else None)
        if readable_queue and best is not None and p is not None and p > best:
            ahead = D("0")           # better than the touch: nothing ahead
        rows[name] = {
            "exists": True, "price": c["price"],
            "would_be_best_liquidity": (None if best is None or p is None
                                        else p >= best),
            "improves_the_touch": (None if best is None or p is None
                                   else p > best),
            "size_ahead": (str(ahead) if ahead is not None else None),
            "fill": counterfactual_fill(price=p, leg=leg, prints=prints,
                                        from_ms=utc_ms, to_ms=next_utc_ms,
                                        ahead=ahead, our_qty=our_qty),
        }

    comp = complement_book if isinstance(complement_book, Mapping) else {}
    other = "no" if side == "yes" else "yes"
    oc = (comp or book or {}).get(other) or {}
    return {
        "utc_ms": utc_ms, "leg": str(leg).upper(),
        "best_bid": q.get("bid"), "best_ask": q.get("ask"),
        "ticks_native_behind_best": cand.get("ticks_native_behind_best"),
        "overlays": rows,
        "complement": {
            "leg": other.upper(), "passive_price": oc.get("bid"),
            "crossing_price": oc.get("ask"),
            "why": ("what completing the other side would have cost at this "
                    "instant. Recorded for every overlay, not just filled "
                    "ones, so the counterfactual is available either way."),
        },
        "labels": THEORETICAL,
    }


def overlay_census(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """How the three compare, with the sample size and the caveats attached."""
    out: Dict[str, Any] = {"cycles": len(rows)}
    for name in OVERLAYS:
        got = [r["overlays"][name] for r in rows
               if isinstance(r.get("overlays"), Mapping)
               and isinstance(r["overlays"].get(name), Mapping)]
        live = [g for g in got if g.get("exists")]
        best = [g for g in live if g.get("would_be_best_liquidity")]
        improves = [g for g in live if g.get("improves_the_touch")]
        reach = [g for g in live
                 if (g.get("fill") or {}).get("reaching_prints")]
        qfill = [g for g in live
                 if (g.get("fill") or {}).get("filled_respecting_queue")]
        qknown = [g for g in live
                  if (g.get("fill") or {}).get("queue_readable")]
        surv = [(g.get("survival") or {}).get("seconds_still_best")
                for g in live]
        surv = [x for x in surv if isinstance(x, (int, float))]
        adv = [_dec((g.get("adverse_after_theoretical_fill") or {}).get("last"))
               for g in live]
        adv = [x for x in adv if x is not None]
        out[name] = {
            "cycles_where_it_exists": len(live),
            "median_seconds_still_best": (
                sorted(surv)[len(surv) // 2] if surv else None),
            "max_seconds_still_best": (max(surv) if surv else None),
            "median_adverse_move_after_theoretical_fill": (
                str(sorted(adv)[len(adv) // 2]) if adv else None),
            "refused_or_absent": len(got) - len(live),
            "would_be_best_liquidity": len(best),
            "best_liquidity_share": (round(len(best) / len(live), 3)
                                     if live else None),
            "improves_the_touch": len(improves),
            "cycles_with_a_reaching_print": len(reach),
            "queue_readable_cycles": len(qknown),
            "theoretical_fills_respecting_queue": len(qfill),
            "theoretical_fill_share_where_queue_known": (
                round(len(qfill) / len(qknown), 3) if qknown else None),
        }
    out["labels"] = THEORETICAL
    out["caveats"] = [
        "OUR OWN SIZE WOULD HAVE CHANGED THE BOOK. Improving the touch puts "
        "us in front of everyone; the prints that then arrive are not the "
        "prints that arrived without us.",
        "QUEUE IS READABLE ON THE YES SIDE ONLY. The venue publishes one "
        "ladder, so NO-leg overlays get a reaching-print count and no "
        "queue-respecting answer.",
        "A REACHING PRINT IS NOT A FILL. It is an execution that crossed our "
        "hypothetical price; whether it would have reached us depends on the "
        "queue, and on our order having been there at all.",
        "IMPROVE_1_TICK IS REFUSED WHEREVER IT WOULD NOT BE MAKER-ONLY, so "
        "its sample is smaller than the others by construction and the two "
        "are not compared over the same cycles unless stated.",
    ]
    return out


def survival(price: Any, leg: str, later: Sequence[Mapping[str, Any]], *,
             interval_s: float = 8.0) -> Dict[str, Any]:
    """How long a quote at this price would still have BEEN the best bid.

    It stops surviving the moment someone bids above it: from then on the
    quote is no longer best liquidity, whatever else happens. This is an
    upper bound on useful resting time, not a fill model.
    """
    p = _dec(price)
    if p is None:
        return {"computable": False, "why": "price unreadable"}
    side = "yes" if str(leg).upper() == "YES" else "no"
    n = 0
    outbid_at = None
    for i, c in enumerate(later or []):
        q = ((c.get("book") or {}).get(side) or {})
        b = _dec(q.get("bid"))
        if b is None:
            break
        if b > p:
            outbid_at = i
            break
        n += 1
    return {"computable": True, "cycles_still_best": n,
            "seconds_still_best": round(n * interval_s, 1),
            "outbid": outbid_at is not None,
            "survived_to_end_of_record": outbid_at is None,
            "why": ("stops the moment a higher bid appears; an upper bound "
                    "on useful resting time, not a fill model")}


def adverse_after(price: Any, leg: str, later: Sequence[Mapping[str, Any]], *,
                  horizon: int = 8) -> Dict[str, Any]:
    """Where the presented mid went after a THEORETICAL fill at this price.

    Mark-to-market on a market measurement. Not realized, not P&L, and never
    summed with matched-pair economics.
    """
    p = _dec(price)
    if p is None:
        return {"computable": False, "why": "price unreadable"}
    side = "yes" if str(leg).upper() == "YES" else "no"
    moves = []
    for c in (later or [])[:horizon]:
        q = ((c.get("book") or {}).get(side) or {})
        b, a = _dec(q.get("bid")), _dec(q.get("ask"))
        if b is None or a is None:
            continue
        moves.append((b + a) / D("2") - p)
    if not moves:
        return {"computable": False,
                "why": "no later cycle presented a readable book"}
    return {"computable": True, "samples": len(moves),
            "worst_against": str(min(moves)), "best_for": str(max(moves)),
            "last": str(moves[-1]),
            "mean": str(sum(moves) / len(moves)),
            "labels": ("mark-to-market on the presented mid. NOT realized, "
                       "not a P&L line, never summed with matched-pair "
                       "economics.")}
