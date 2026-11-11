"""LARGE-PRINT FORENSICS: why did we miss the flow that was actually there?

The window-volume study established that BTC5m is thin and BURSTY, not dead:
$1,543.69 across 19 windows, and SIX prints of 100+ contracts carrying 77.7%
of all notional. This module asks, for every print of 10 contracts or more,
what the market looked like beforehand and whether our quote could have been
part of it.

MODEL A IS UNTOUCHED. Every decision replayed here comes from the FROZEN
policy -- `continuous_probe.plan`, which calls the unchanged engine surface.
Nothing in this module chooses a price, a size or an action. It builds the
state, hands it to the frozen policy, and records what the policy said.

IMMEDIATE ADVERSE MOVEMENT IS NOT THE ANSWER, and the owner is right that
treating it as final P&L would be wrong. A maker fill is the FIRST step of an
L5 sequence, so each hypothetical fill is replayed forward through the frozen
policy:

    maker fill -> price movement -> the policy's next action -> a complement
    or extension, filled only if a real print would have reached it ->
    matched-pair economics -> remaining residual -> settlement

Settlement is available because the venue publishes `resolutionSide` for
expired contracts, so the path closes on the venue's own outcome rather than
on a mark.

EVERY FILL HERE IS SHADOW/THEORETICAL. No order existed; our size would have
changed the book it is measured against; and a theoretical fill is never
submitted, filled, settled or realized.

RECONSTRUCTION IS LIMITED BY WHAT WE RECORDED. A lookback only exists where
one of our own cycles covers that instant. Where it does not, the field says
so rather than interpolating a book we never saw.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the owner's buckets
BUCKETS = (("<10", D("0"), D("10")), ("10-25", D("10"), D("25")),
           ("25-100", D("25"), D("100")), ("100+", D("100"), None))

#: reconstruct the market this far before each large print
LOOKBACKS_S = (5, 15, 30, 60)
#: and sample the market this far after it
FORWARDS_S = (5, 15, 30, 60)

#: the size we actually quote, plus larger OBSERVATIONAL sizes. The larger
#: ones are a measurement of how much of a print our quote could have
#: absorbed; they are NOT a proposal to trade them.
OBSERVATIONAL_SIZES = ("0.01", "0.10", "1.00", "10.00")

#: a lookback matches a recorded cycle only within this tolerance
MATCH_TOLERANCE_S = 6.0

THEORETICAL = ("SHADOW/THEORETICAL. No order existed, nothing was submitted, "
               "filled, settled or realized, and our own size would have "
               "changed the book this is measured against.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def bucket_of(amount: Any) -> Optional[str]:
    a = _dec(amount)
    if a is None:
        return None
    for name, lo, hi in BUCKETS:
        if a >= lo and (hi is None or a < hi):
            return name
    return None


def state_from_recorded_book(symbol: str, tte_s: Any, inv: Any,
                             book: Mapping[str, Any]) -> Dict[str, Any]:
    """The frozen builder's state, from a book we recorded earlier.

    IT MIRRORS `continuous_probe.state_from_venue` DELIBERATELY AND IS PINNED
    BY A TEST that asserts the two produce identical states for the same book.
    The mirror exists only to avoid one network read of the instrument's terms
    per replayed cycle; the tick comes from the recorded book, which carries
    it, so nothing is defaulted. No strategy lives here -- the surface and the
    actions still come from the frozen `plan`.
    """
    from src.gemini_execution import continuous_probe as CP
    b = book or {}
    if (not b.get("readable") or not b.get("identity_consistent")
            or CP.crossed(b)):
        return {"market_key": symbol, "state_id": "unreadable",
                "t_utc": b.get("t_utc"), "tte_s": tte_s, "cohort": "BTC5m",
                "inventory_known": False, "outcomes": {},
                "unreadable_why": (
                    "book not readable" if not b.get("readable")
                    else ("complement identity violated"
                          if not b.get("identity_consistent")
                          else "CROSSED book: a bid at or above its own ask")),
                "book": b}
    tick = b.get("tick") or "0.01"
    t = b.get("t_utc")
    return {
        "market_key": symbol,
        "state_id": "%s@%d" % (symbol, int(t)) if t is not None else "replay",
        "t_utc": t, "state_age_s": 0.0, "tte_s": tte_s, "cohort": "BTC5m",
        "inventory_known": True,
        "outcomes": {
            "Up": {"bid": b["yes"]["bid"], "ask": b["yes"]["ask"],
                   "tick": tick},
            "Down": {"bid": b["no"]["bid"], "ask": b["no"]["ask"],
                     "tick": tick}},
        "book": b,
    }


def nearest_cycle(cycles: Sequence[Mapping[str, Any]], target_ms: Any, *,
                  tolerance_s: float = MATCH_TOLERANCE_S
                  ) -> Optional[Mapping[str, Any]]:
    """The recorded cycle closest to an instant, or None. Never interpolates."""
    t = _dec(target_ms)
    if t is None:
        return None
    best, gap = None, None
    for c in cycles or []:
        ms = _dec(c.get("utc_ms"))
        if ms is None:
            continue
        g = abs(ms - t)
        if gap is None or g < gap:
            best, gap = c, g
    if best is None or gap is None or gap > D(str(tolerance_s)) * D("1000"):
        return None
    return best


def reaches(leg: str, price: Any, print_: Mapping[str, Any]) -> bool:
    """Would this print have reached a resting quote on this leg at this price?

    The tape is priced in YES and records the aggressor: a YES rung is reached
    by an aggressor SELL at or below its price; a NO rung at p is reached by
    an aggressor BUY at or above 1 - p.
    """
    p, tp = _dec(price), _dec(print_.get("price"))
    if p is None or tp is None:
        return False
    if str(leg).upper() == "YES":
        return str(print_.get("type")) == "sell" and tp <= p
    return str(print_.get("type")) == "buy" and tp >= (D("1") - p)


def absorbed(print_: Mapping[str, Any], ahead: Optional[D],
             our_qty: Any) -> Dict[str, Any]:
    """How much of a print our quote could have taken, given the queue.

    The print fills the queue ahead of us first; whatever is left reaches us,
    capped by our own size.
    """
    amt, q = _dec(print_.get("amount")), _dec(our_qty)
    if amt is None or q is None:
        return {"computable": False, "why": "print size or our size unreadable"}
    if ahead is None:
        return {"computable": False, "our_qty": str(q),
                "why": ("the queue is not readable on this leg -- the venue "
                        "publishes one ladder, the YES side")}
    spare = amt - ahead
    got = max(D("0"), min(q, spare))
    return {"computable": True, "our_qty": str(q), "size_ahead": str(ahead),
            "print_size": str(amt), "reached_us": str(max(D("0"), spare)),
            "hypothetically_filled": str(got), "fully_filled": got >= q,
            "labels": THEORETICAL}


def size_ahead_at(depth: Any, price: Any, best_bid: Any) -> Optional[D]:
    """Resting size at our price on the YES ladder; 0 if we improve on it."""
    p, b = _dec(price), _dec(best_bid)
    if p is None:
        return None
    if b is not None and p > b:
        return D("0")
    if not isinstance(depth, Mapping) or not depth.get("readable"):
        return None
    tot = D("0")
    for lv in ((depth.get("bids") or {}).get("levels") or []):
        px, qty = _dec(lv.get("price")), _dec(lv.get("qty"))
        if px is not None and qty is not None and px == p:
            tot += qty
    return tot


def reconstruct_before(print_: Mapping[str, Any], symbol: str,
                       cycles: Sequence[Mapping[str, Any]],
                       *, lookbacks_s: Sequence[int] = LOOKBACKS_S
                       ) -> List[Dict[str, Any]]:
    """The market 5/15/30/60 s before a print, where we have evidence."""
    from src.gemini_execution import continuous_probe as CP
    out: List[Dict[str, Any]] = []
    t = _dec(print_.get("utc_ms"))
    for back in lookbacks_s:
        row: Dict[str, Any] = {"lookback_s": back}
        c = (nearest_cycle(cycles, t - D(str(back)) * D("1000"))
             if t is not None else None)
        if c is None:
            row.update({"evidence": False,
                        "why": ("no cycle of ours covers this instant within "
                                "%.0fs; the book is not reconstructed"
                                % MATCH_TOLERANCE_S)})
            out.append(row)
            continue
        book = c.get("book") or {}
        yes, no = (book.get("yes") or {}), (book.get("no") or {})
        acts = c.get("actions") or []
        native = {str(a.get("leg")).upper(): a.get("price") for a in acts
                  if a.get("leg")}
        leg = "YES" if str(print_.get("type")) == "sell" else "NO"
        nat_px = native.get(leg)
        best = (yes if leg == "YES" else no).get("bid")
        ask = (yes if leg == "YES" else no).get("ask")
        imp = (_dec(best) + D("0.01")) if _dec(best) is not None else None
        ahead = size_ahead_at(c.get("depth"), nat_px, best)
        row.update({
            "evidence": True,
            "cycle_utc": c.get("utc"), "cycle_tte_s": c.get("tte_s"),
            "yes_bid": yes.get("bid"), "yes_ask": yes.get("ask"),
            "no_bid": no.get("bid"), "no_ask": no.get("ask"),
            "spread": yes.get("spread"),
            "book_readable": bool(book.get("readable"))
            and not CP.crossed(book),
            "touch_depth": ((c.get("depth") or {}).get("bids") or {})
            .get("best_qty"),
            "l5_desired_surface": acts,
            "relevant_leg": leg,
            "native_price": nat_px,
            "native_was_best": (None if nat_px is None or best is None
                                else _dec(nat_px) == _dec(best)),
            "depth_ahead_of_native": (str(ahead) if ahead is not None
                                      else None),
            "improve_1_price": (str(imp) if imp is not None else None),
            "improve_1_stays_maker": (None if imp is None or _dec(ask) is None
                                      else imp < _dec(ask)),
            "hypothetical_queue_position": (
                "behind %s" % ahead if ahead is not None and ahead > 0
                else ("front of queue" if ahead == D("0") else "unreadable")),
        })
        out.append(row)
    return out


def forward_movement(print_: Mapping[str, Any],
                     cycles: Sequence[Mapping[str, Any]], leg: str,
                     *, forwards_s: Sequence[int] = FORWARDS_S
                     ) -> List[Dict[str, Any]]:
    """Where the book went after the print, and what the complement cost."""
    out = []
    t = _dec(print_.get("utc_ms"))
    side = "yes" if str(leg).upper() == "YES" else "no"
    other = "no" if side == "yes" else "yes"
    for fwd in forwards_s:
        c = (nearest_cycle(cycles, t + D(str(fwd)) * D("1000"))
             if t is not None else None)
        if c is None:
            out.append({"forward_s": fwd, "evidence": False,
                        "why": "no cycle of ours covers this instant"})
            continue
        b = c.get("book") or {}
        q, oc = (b.get(side) or {}), (b.get(other) or {})
        bid, ask = _dec(q.get("bid")), _dec(q.get("ask"))
        out.append({
            "forward_s": fwd, "evidence": True,
            "bid": q.get("bid"), "ask": q.get("ask"),
            "mid": (str((bid + ask) / D("2")) if bid is not None
                    and ask is not None else None),
            "complement_passive": oc.get("bid"),
            "complement_crossing": oc.get("ask"),
            "labels": ("mark-to-market on a market measurement. NOT "
                       "realized, and never summed with matched-pair "
                       "economics."),
        })
    return out


# ===================================================== the inventory path
#
# THE PART THAT ANSWERS THE OWNER'S OBJECTION. An immediate adverse mark is
# not the outcome of a maker fill; it is the first step of an L5 sequence.
# So the fill is replayed forward through the FROZEN policy and closed on the
# venue's own settlement.

def inventory_path(*, symbol: str, leg: str, qty: Any, price: Any,
                   from_ms: Any, cycles: Sequence[Mapping[str, Any]],
                   prints: Sequence[Mapping[str, Any]],
                   resolution: Mapping[str, Any]) -> Dict[str, Any]:
    """maker fill -> movement -> L5 action -> complement -> pair -> settlement.

    Every action comes from `continuous_probe.plan`, unchanged. A complement
    or extension only fills if a REAL print would have reached it in the
    interval before the next cycle, tested with the same queue-respecting
    rule as the original fill.
    """
    from src.gemini_execution import continuous_probe as CP

    inv = CP.Inventory()
    q, p = _dec(qty), _dec(price)
    out: Dict[str, Any] = {
        "symbol": symbol, "seed_fill": {"leg": str(leg).upper(),
                                        "qty": str(q) if q else None,
                                        "price": str(p) if p else None},
        "labels": THEORETICAL,
    }
    if q is None or p is None or q <= 0:
        out.update({"computable": False, "why": "seed fill unreadable"})
        return out
    # THE SOURCE LABEL IS THE PROVENANCE. `Inventory.apply_fill`
    # requires one, and every fill in this replay is marked as what it
    # is so no downstream reader can mistake it for a venue fill.
    inv.apply_fill(str(leg).upper(), q, p,
                   source="SHADOW_FORENSIC_SEED_THEORETICAL")

    t0 = _dec(from_ms)
    later = [c for c in cycles
             if _dec(c.get("utc_ms")) is not None
             and _dec(c["utc_ms"]) > (t0 if t0 is not None else D("0"))]
    later.sort(key=lambda c: _dec(c["utc_ms"]))

    steps: List[Dict[str, Any]] = []
    for i, c in enumerate(later):
        book = c.get("book") or {}
        st = state_from_recorded_book(symbol, c.get("tte_s"), inv, book)
        got = CP.plan(st, inv, actions_taken=len(steps))
        step: Dict[str, Any] = {
            "utc": c.get("utc"), "tte_s": c.get("tte_s"),
            "window_phase": got.get("window_phase"),
            "stop": got.get("stop"),
            "why_no_surface": got.get("why_no_surface"),
            "actions": got.get("actions") or [],
            "holds": [{"leg": h.get("leg"), "held_by": h.get("held_by"),
                       "why": h.get("why")} for h in (got.get("holds") or [])],
            "inventory_before": inv.report(),
        }
        # would any of those actions have been filled before the next cycle?
        nxt = (_dec(later[i + 1]["utc_ms"]) if i + 1 < len(later)
               else _dec(c["utc_ms"]) + D("8000"))
        fills_here = []
        for a in (got.get("actions") or []):
            a_leg, a_px = str(a.get("leg")).upper(), a.get("price")
            ahead = size_ahead_at(c.get("depth"), a_px,
                                  (book.get("yes") or {}).get("bid")
                                  if a_leg == "YES" else None)
            for pr in prints:
                ms = _dec(pr.get("utc_ms"))
                if ms is None or not (_dec(c["utc_ms"]) < ms <= nxt):
                    continue
                if not reaches(a_leg, a_px, pr):
                    continue
                ab = absorbed(pr, ahead, a.get("qty") or "0.01")
                if ab.get("computable") and _dec(
                        ab.get("hypothetically_filled")) > D("0"):
                    got_qty = _dec(ab["hypothetically_filled"])
                    inv.apply_fill(
                        a_leg, got_qty, a_px,
                        source="SHADOW_FORENSIC_REPLAY_THEORETICAL")
                    fills_here.append({"leg": a_leg, "price": a_px,
                                       "qty": str(got_qty),
                                       "action": a.get("action"),
                                       "print_tid": pr.get("tid")})
                    break
        step["theoretical_fills"] = fills_here
        step["inventory_after"] = inv.report()
        steps.append(step)

    settle = CP.settlement_report(inv, dict(resolution or {}))
    out.update({
        "computable": True,
        "cycles_replayed": len(steps),
        "steps": steps,
        "actions_seen": sorted({a.get("action") for s in steps
                                for a in s["actions"] if a.get("action")}),
        "theoretical_fills_after_seed": sum(len(s["theoretical_fills"])
                                            for s in steps),
        "final_inventory": inv.report(),
        "settlement": settle,
        "path_labels": (
            "maker fill -> movement -> the FROZEN policy's next action -> a "
            "complement or extension filled only where a REAL print would "
            "have reached it -> matched-pair economics -> residual -> "
            "settlement. Matched and residual are never summed, and nothing "
            "here is realized."),
    })
    return out


def forensic(*, symbol: str, prints: Sequence[Mapping[str, Any]],
             cycles: Sequence[Mapping[str, Any]],
             resolution: Mapping[str, Any],
             min_size: Any = "10") -> List[Dict[str, Any]]:
    """One row per print of `min_size` contracts or more."""
    floor = _dec(min_size) or D("10")
    rows: List[Dict[str, Any]] = []
    for pr in prints or []:
        amt = _dec(pr.get("amount"))
        if amt is None or amt < floor:
            continue
        leg = "YES" if str(pr.get("type")) == "sell" else "NO"
        before = reconstruct_before(pr, symbol, cycles)
        at = next((b for b in before if b.get("evidence")), None)

        row: Dict[str, Any] = {
            "symbol": symbol, "tid": pr.get("tid"),
            "print": {"price": pr.get("price"), "amount": pr.get("amount"),
                      "aggressor": pr.get("type"), "utc_ms": pr.get("utc_ms"),
                      "tte_s": pr.get("tte_s")},
            "bucket": bucket_of(amt),
            "reachable_leg": leg,
            "before": before,
            "after": forward_movement(pr, cycles, leg),
            "labels": THEORETICAL,
        }
        if at is None:
            row.update({"evaluable": False,
                        "why": "no recorded cycle covers the run-up to this "
                               "print, so no quote can be tested against it"})
            rows.append(row)
            continue

        nat_px, imp_px = at.get("native_price"), at.get("improve_1_price")
        ahead_nat = _dec(at.get("depth_ahead_of_native"))
        tests = {}
        for name, px, ahead in (("NATIVE", nat_px, ahead_nat),
                                ("IMPROVE_1_TICK", imp_px, D("0"))):
            if px is None:
                tests[name] = {"exists": False,
                               "why": ("the policy quoted nothing on this leg"
                                       if name == "NATIVE" else
                                       "no best price to improve on")}
                continue
            if name == "IMPROVE_1_TICK" and at.get("improve_1_stays_maker") \
                    is False:
                tests[name] = {"exists": False,
                               "why": "bid+1 tick would not be maker-only"}
                continue
            hit = reaches(leg, px, pr)
            tests[name] = {
                "exists": True, "price": str(px), "would_reach_us": hit,
                "absorbed": ({s: absorbed(pr, ahead, s)
                              for s in OBSERVATIONAL_SIZES} if hit else None),
            }
        row.update({"evaluable": True, "quote_tests": tests})
        rows.append(row)
    return rows


def census(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Bucketed, with the coverage limit stated rather than hidden."""
    out: Dict[str, Any] = {"prints": len(rows)}
    by: Dict[str, Dict[str, Any]] = {}
    for name, _lo, _hi in BUCKETS:
        g = [r for r in rows if r.get("bucket") == name]
        ev = [r for r in g if r.get("evaluable")]
        def hit(k):
            return sum(1 for r in ev
                       if ((r.get("quote_tests") or {}).get(k) or {})
                       .get("would_reach_us"))
        by[name] = {
            "prints": len(g),
            "with_a_reconstructed_book": len(ev),
            "no_evidence": len(g) - len(ev),
            "native_would_reach_us": hit("NATIVE"),
            "improve_1_would_reach_us": hit("IMPROVE_1_TICK"),
        }
    out["buckets"] = by
    out["coverage"] = {
        "why": ("a print can only be evaluated where one of our own cycles "
                "covers the run-up to it. Our cycles exist from TTE ~280 to "
                "TTE ~104 in the soaks, so prints outside that are recorded "
                "and NOT evaluated rather than guessed at."),
    }
    out["labels"] = THEORETICAL
    return out


#: the boundaries the attribution uses. They are the FROZEN policy's own --
#: below NO_NEW_OPEN_EXTEND_TTE_S we cannot OPEN, above the band's top we have
#: not entered yet -- so this is a statement about our envelope, not a new one.
ATTRIBUTION_BAND_HI_S = 280.0

A_PRICE = "A_price_our_quote_would_not_be_reached"
B_QUEUE = "B_queue_consumed_ahead_of_us"
C_LATE = "C_timing_tte_under_60_where_we_cannot_OPEN"
C_EARLY = "C_timing_tte_over_280_before_our_band_opens"
REACHABLE = "reachable_we_would_have_been_there"
NO_EVIDENCE = "no_evidence_our_cycles_do_not_cover_it"


def attribute_one(row: Optional[Mapping[str, Any]], *, tte_s: Any,
                  band_hi_s: float = ATTRIBUTION_BAND_HI_S) -> str:
    """Why we did not interact with this print. One cause, decided in order.

    TIMING IS DECIDED FIRST and on TTE alone, because a print outside our
    quoting window could not have been reached at any price with any queue
    position -- calling it a price failure would be attributing to price
    something price could not have fixed.

    A print with no reconstructed book is NO_EVIDENCE and is never quietly
    folded into a cause; the share of notional sitting in that bucket is the
    honest measure of how much of this attribution rests on coverage.
    """
    from src.gemini_execution import continuous_probe as CP
    t = None
    try:
        t = float(tte_s)
    except (TypeError, ValueError):
        t = None
    if t is not None and t < CP.NO_NEW_OPEN_EXTEND_TTE_S:
        return C_LATE
    if t is not None and t > band_hi_s:
        return C_EARLY
    if not row or not row.get("evaluable"):
        return NO_EVIDENCE
    qt = row.get("quote_tests") or {}
    nat = qt.get("NATIVE") or {}
    imp = qt.get("IMPROVE_1_TICK") or {}
    if nat.get("would_reach_us"):
        got = ((nat.get("absorbed") or {}).get("0.01") or {}).get(
            "hypothetically_filled")
        try:
            return REACHABLE if got is not None and float(got) > 0 else B_QUEUE
        except (TypeError, ValueError):
            return B_QUEUE
    if imp.get("would_reach_us"):
        return A_PRICE
    return NO_EVIDENCE


def attribution(prints: Sequence[Mapping[str, Any]],
                rows: Sequence[Mapping[str, Any]], *,
                band_hi_s: float = ATTRIBUTION_BAND_HI_S) -> Dict[str, Any]:
    """A, B, C or D over EVERY print, not only the large ones.

    Computed and stored rather than derived by hand in prose, which is how the
    first pass ended up not being reproducible. Prints below the forensic's
    size floor carry no reconstructed book, so they can only ever be timed or
    unevidenced -- and the split says which.
    """
    by_tid = {r.get("tid"): r for r in rows or []}
    buckets: Dict[str, Dict[str, Any]] = {}
    total = D("0")
    for p in prints or []:
        amt, px = _dec(p.get("amount")), _dec(p.get("price"))
        if amt is None or px is None:
            continue
        v = amt * px
        total += v
        cause = attribute_one(by_tid.get(p.get("tid")), tte_s=p.get("tte_s"),
                              band_hi_s=band_hi_s)
        b = buckets.setdefault(cause, {"prints": 0, "notional_usd": D("0")})
        b["prints"] += 1
        b["notional_usd"] += v
    out = {k: {"prints": v["prints"], "notional_usd": str(v["notional_usd"]),
               "share": (str(round(v["notional_usd"] / total, 4))
                         if total > 0 else None)}
           for k, v in buckets.items()}
    c = sum((buckets.get(k) or {}).get("notional_usd", D("0"))
            for k in (C_LATE, C_EARLY))
    return {
        "total_notional_usd": str(total),
        "total_prints": sum(v["prints"] for v in buckets.values()),
        "band_hi_s": band_hi_s,
        "causes": out,
        "C_timing_total_share": (str(round(c / total, 4)) if total > 0
                                 else None),
        "why": ("timing is decided first and on TTE alone: a print outside "
                "our quoting window could not have been reached at any price "
                "with any queue position. NO_EVIDENCE is never folded into a "
                "cause -- its share is how much of this rests on coverage."),
        "labels": ("EXECUTED notional from the tape, attributed to why we did "
                   "not interact with it. No fill is claimed and nothing here "
                   "is realized."),
    }
