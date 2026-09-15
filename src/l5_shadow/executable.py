"""ONE ladder evaluator, used by BOTH venues, so a difference means economics.

If Polymarket were scored by one code path and Gemini by another, every gap
between them would be ambiguous -- venue economics, or an implementation
accident? Everything venue-specific is confined to `VenueTerms`, which is built
from what the venue itself declared, and the walk below is identical.

WHAT CHANGES VERSUS THE SINGLE-LEVEL EVALUATOR, and why it matters:

  price       the VWAP for the quantity actually wanted, not the touch. If we
              want 10 and the touch holds 1, we pay the walk, and pricing the
              touch flatters whichever venue happens to show a small best quote.
  capacity    the size resting at prices we would ACCEPT, not the touch depth.
              This is the reading that turned "Gemini depth 1, declined" into
              "500 resting one tick behind it".
  fee         summed PER LEVEL. A multi-level take is several fills, and
              charging one fee at the average price understates it. Gemini's
              is ceil-cent per fill, so per-level is also the venue's own shape.
  min size    a venue minimum is a hard gate, not a preference. Polymarket's
              crypto markets declare orderMinSize 5, so q=1 and q=2 are not
              submittable there AT ALL while Gemini quotes them. A comparison
              that ignores this reports an execution that could not be placed.

STILL NOT A FILL. Every number here comes from a public book, where queue
position is unobservable. `executable_qty` is an UPPER BOUND on what a taker
would get and nothing in this module may be counted as realized.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.l5_adapters.base import AdapterEvaluation, no_market, within_ceiling
from src.l5_shadow import depth as DEP

ONE = D("1")


@dataclass
class VenueTerms:
    """What a venue declared about itself. None means IT DID NOT SAY."""

    venue: str
    taker_fee: Optional[Callable[[D, D], Optional[D]]] = None
    maker_fee: Optional[Callable[[D, D], Optional[D]]] = None
    min_order_size: Optional[D] = None
    tick: Optional[D] = None
    maker_pays_fee: Optional[bool] = None
    source: str = ""
    extra: dict = field(default_factory=dict)


def _sum_level_fee(fee_fn, used: Sequence, qty_fn) -> Optional[D]:
    tot = D("0")
    for px, q in used:
        f = fee_fn(D(str(px)), D(str(q)))
        if f is None:
            return None
        tot += D(str(f))
    return tot


def walk_levels(levels: Sequence, qty: D) -> List:
    """[(price, taken)] consumed to fill qty -- the per-level shape of the fill."""
    got, out = D("0"), []
    for px, sz in levels:
        if got >= qty:
            break
        take = min(D(str(sz)), qty - got)
        got += take
        out.append((D(str(px)), take))
    return out


def evaluate_executable(intent, terms: VenueTerms, levels: Optional[Sequence], *,
                        venue_market_id: Optional[str] = None,
                        mapping_confidence: Optional[str] = None,
                        mapping_reason: Optional[str] = None,
                        book_source_ts: Optional[float] = None,
                        observed_at: Optional[float] = None,
                        ladder_construction: str = "",
                        assume_taker_fee: bool = True,
                        now: Optional[float] = None) -> AdapterEvaluation:
    """Score one frozen intent against one venue's FULL ladder.

    `levels` is best-first for BUYING the wanted outcome. `None` means the venue
    could not be read -- unknown, not absent -- and produces a `no_market`
    result carrying that distinction in its reason.
    """
    t = time.time() if now is None else now
    if levels is None:
        return no_market(terms.venue, intent,
                         mapping_reason or "venue book could not be read: "
                                           "UNKNOWN, not absent")
    ev = AdapterEvaluation(
        venue=terms.venue, intent_id=intent.id, market_key=intent.market_key,
        evaluated_at=t, venue_market_id=venue_market_id,
        mapping_confidence=mapping_confidence, mapping_reason=mapping_reason,
        book_age_s=(None if (book_source_ts is None or observed_at is None)
                    else round(observed_at - book_source_ts, 3)),
        stage="not_executable")
    ev.extra["ladder_construction"] = ladder_construction
    ev.extra["terms_source"] = terms.source
    ev.extra["book_source_ts"] = book_source_ts
    ev.extra["observed_at"] = observed_at
    ev.extra["total_levels"] = len(levels)

    if not levels:
        ev.would_submit = False
        ev.would_submit_reason = (
            f"venue quotes no {intent.desired_outcome} side at all "
            f"(one-sided or empty book)")
        return ev

    ceiling = D(intent.max_acceptable_price)
    qty = D(intent.incremental_qty)
    ev.available_price = str(levels[0][0])
    ev.available_depth = str(levels[0][1])

    fee_fn = terms.taker_fee if assume_taker_fee else terms.maker_fee
    if fee_fn is None:
        ev.expected_fee_basis = "unavailable: venue did not declare a fee"
        ev.would_submit = False
        ev.would_submit_reason = "fee unknown; refusing to price against it"
        return ev

    # The FULL ladder, at every candidate size, priced and fee'd. This is the
    # record even when the intent's own quantity fails: "q=10 failed" and "the
    # venue can do nothing" are different findings.
    ladder_rows: Dict[str, Any] = {}
    for q in DEP.QTY_LADDER:
        ladder_rows[str(q)] = _score_qty(levels, q, ceiling, fee_fn, terms)
    ev.extra["qty_ladder"] = ladder_rows
    ev.extra["qty_ladder_note"] = (
        "the SHAPE of what this venue can supply, at 1/2/5/10/20, rather than "
        "one pass/fail at the intent's own size")

    eligible = [(p, s) for p, s in levels if D(str(p)) <= ceiling]
    ev.extra["immediately_executable_qty"] = str(
        sum((D(str(s)) for _, s in eligible), D("0")))
    ev.extra["best_eligible_price"] = (str(eligible[0][0]) if eligible else None)
    ev.executable_qty = ev.extra["immediately_executable_qty"]

    row = ladder_rows.get(str(qty)) or _score_qty(levels, qty, ceiling, fee_fn,
                                                  terms)
    ev.submission_price = row["vwap"]
    ev.expected_fee = row["fee"]
    ev.expected_fee_basis = row["fee_basis"]
    ev.effective_cost = row["effective_cost"]
    ev.within_ceiling = row["within_ceiling"]
    ev.extra["filled_qty_modelled"] = row["filled"]
    ev.extra["complete_at_qty"] = row["complete"]
    ev.extra["levels_used"] = row["levels_used"]
    ev.extra["min_order_size"] = (None if terms.min_order_size is None
                                  else str(terms.min_order_size))

    if not row["submittable"]:
        ev.would_submit = False
        ev.would_submit_reason = row["reason"]
        return ev
    if not row["complete"]:
        ev.would_submit = False
        ev.would_submit_reason = (
            f"venue can supply only {row['filled']} of {qty} at or under "
            f"{ceiling}; a partial take is not this intent")
        ev.would_fill = False
        ev.would_fill_basis = row["reason"]
        return ev
    if row["within_ceiling"] is not True:
        ev.would_submit = False
        ev.would_submit_reason = (
            f"effective cost {row['effective_cost']} exceeds the intent "
            f"ceiling {ceiling}")
        return ev

    ev.would_submit = True
    ev.would_submit_reason = (
        f"{qty} executable at VWAP {row['vwap']} over {row['levels_used']} "
        f"level(s); all-in {row['effective_cost']} <= ceiling {ceiling}")
    _resulting_inventory(ev, intent, row)
    ev.would_fill = True
    ev.would_fill_basis = (
        f"{ev.extra['immediately_executable_qty']} resting at acceptable "
        f"prices. UPPER BOUND -- queue position is unobservable from a public "
        f"book, so this is not evidence of a fill.")
    return ev


def _resulting_inventory(ev: AdapterEvaluation, intent, row: Dict[str, Any]
                         ) -> None:
    """What we would HOLD if this modelled fill happened -- clearly modelled.

    Reconstructed exactly from the intent's own `current_matched` and signed
    `residual` (up = m + max(r,0), dn = m + max(-r,0)), so it cannot drift from
    the state the intent was frozen against.

    `pair_cost` stays None on purpose. A pair cost is this leg's price plus the
    average cost of the inventory it matches against, and a single-action
    evaluation does not know what the opposite side cost us -- we hold nothing
    yet. Filling it with this leg's price alone would report half a pair as a
    whole one, which is the number that decides whether a pair is fee-safe.
    """
    m = D(str(intent.current_matched))
    r = D(str(intent.residual))
    up, dn = m + max(r, D("0")), m + max(-r, D("0"))
    got = D(str(row["filled"]))
    if intent.desired_outcome == "Up":
        up += got
    else:
        dn += got
    ev.resulting_matched = str(min(up, dn))
    ev.resulting_residual = str(up - dn)
    ev.pair_cost = None
    ev.extra["resulting_inventory"] = {
        "up": str(up), "dn": str(dn),
        "matched_delta": str(min(up, dn) - m),
        "residual_before": str(r), "residual_after": str(up - dn),
        "reduces_residual": bool(abs(up - dn) < abs(r)),
        "completes_pair": bool(min(up, dn) > m),
        "basis": "MODELLED: assumes the whole evaluated quantity fills",
    }
    ev.extra["pair_cost_basis"] = (
        "not computable from one action: a pair cost needs the average cost of "
        "the inventory this leg matches against, and nothing is held yet")


def _score_qty(levels: Sequence, qty: D, ceiling: D, fee_fn,
               terms: VenueTerms) -> Dict[str, Any]:
    """One candidate size, priced honestly.

    Levels above the ceiling are dropped BEFORE the walk: a VWAP that averages
    an acceptable price with an unacceptable one can land under the ceiling
    while containing fills we would never have accepted.
    """
    eligible = [(D(str(p)), D(str(s))) for p, s in levels if D(str(p)) <= ceiling]
    used = walk_levels(eligible, qty)
    filled = sum((q for _, q in used), D("0"))
    notional = sum((p * q for p, q in used), D("0"))
    vwap = (notional / filled).quantize(D("0.000001")) if filled > 0 else None
    fee = _sum_level_fee(fee_fn, used, qty) if used else D("0")
    minsz = terms.min_order_size
    submittable = True
    reason = ""
    if minsz is not None and qty < D(str(minsz)):
        submittable, reason = False, (
            f"venue minimum order size is {minsz}; {qty} cannot be submitted "
            f"here at all")
    elif filled <= 0:
        submittable, reason = False, (
            f"no level at or under {ceiling}; best is "
            f"{levels[0][0] if levels else None}")
    elif fee is None:
        submittable, reason = False, "fee could not be computed for these levels"
    eff = None
    if vwap is not None and fee is not None and filled > 0:
        eff = str((vwap + (fee / filled)).quantize(D("0.000001")))
    return {
        "requested": str(qty), "filled": str(filled),
        "vwap": (str(vwap) if vwap is not None else None),
        "notional": str(notional),
        "levels_used": len(used),
        "complete": filled >= qty,
        "fee": (str(fee) if fee is not None else None),
        "fee_basis": ("taker, summed per level (conservative default: until "
                      "the venue says we were a maker, plan against crossing)"
                      if fee is not None else "unavailable"),
        "effective_cost": eff,
        "within_ceiling": (None if eff is None
                           else within_ceiling(eff, ceiling)),
        "submittable": submittable,
        "reason": reason or "ok",
    }
