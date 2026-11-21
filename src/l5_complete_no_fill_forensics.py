"""Every accepted-but-unfilled COMPLETE, and what would have completed it.

OWNER item 2: "Analyze every accepted-but-unfilled COMPLETE: authenticated
touch, L5 price, distance from touch, rest time, subsequent trades/book
movement, and whether a later L5-consistent action could have completed it."

This is the population that matters. Rejections are 17.9% of completes and
they get all the attention because they are loud; SILENT no-fills are 68.5%,
and they are where the residual comes from.

THE FIELD THAT ANSWERS IT. Each order carries rest_book, an aggregate over
the whole resting window:

    min_opp_ask  the best the opposite ask ever got while we rested
    max_touch    the highest L5's own touch price got while we rested
    polls        how many observations that aggregate is built from

So three separate questions can be asked of every unfilled complete:
  * how far was the book from our price at its CLOSEST  (min_opp_ask - px)
  * did L5's touch move above where we were sitting     (max_touch > px)
  * if it did, did the book ever reach that new touch   (min_opp_ask <= max_touch)

The third is the owner's question stated in observable terms: a later
L5-CONSISTENT action means repricing to where L5 was, not paying up to get
filled. The distinction is the whole difference between cloning L5 and
inventing a fill-chaser.

AND THE MECHANISM FOR IT ALREADY EXISTS. The live session already cancels
and re-quotes when L5's touch moves away from a resting order -- but the
condition reads

    if relation_ in ("open", "extend") and touch != px_:
        return f"touch_moved:{touch}"

so COMPLETE is excluded from it. The exclusion is visible in the tape: 36
OPEN orders and 35 EXTEND orders exited via repriced:touch_moved, against
0 of 868 COMPLETEs. So the finding below is not "we never built a working
loop" -- it is that a working loop was built, proven live, and switched off
for the one relation where residual is at stake. The crossing objection is
also already handled: `if px_ >= opp_ask: return "would_cross"` fires ahead
of the touch check and applies to every relation.

WHAT THAT DOES NOT ESTABLISH. EXTEND dies at the deadline 28.5% of the time
and COMPLETE 64.9%, but that gap is confounded by relation and cannot be
read as the effect of repricing. The mechanism gap is a fact; its effect
size is not established here.

WHAT THESE NUMBERS ARE NOT. Stated here rather than in a footnote because
every figure below inherits them:

  1. rest_book is an AGGREGATE, not a time series. min_opp_ask and max_touch
     are extrema over the same window but we cannot prove they coincided.
     A count of orders where both conditions held is therefore
     an UPPER BOUND on what repricing would have captured, never an
     estimate of it.
  2. The book reaching our price is not the same as filling it. We rest as
     maker; a fill needs a taker to come to us, and queue position decides
     who gets it. 2% of unfilled completes had the ask reach our price and
     still did not fill -- that is queue, not distance.
  3. Repricing TO the touch can cross, and post-only refuses a cross. Some
     of the upper bound is unreachable for that reason alone.

READ-ONLY. Completion pricing and the rest window are live-money parameters
and move only on an explicit owner decision.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from decimal import Decimal as D
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src import l5_completion_economics as ce

TICK = D("0.01")


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


def _ticks(a: Optional[D], b: Optional[D]) -> Optional[int]:
    return None if (a is None or b is None) else int((a - b) / TICK)


def completes(paths: Iterable[Any]) -> List[dict]:
    return [o for o in ce.all_orders(paths) if o.get("relation") == "complete"]


def classify(order: Mapping[str, Any]) -> dict:
    """One unfilled complete, reduced to the quantities the owner asked for."""
    rb = order.get("rest_book") or {}
    rec = order.get("recorder_book") or {}
    px = _d(order.get("px"))
    l5 = _d(order.get("l5_touch_px"))
    mo = _d(rb.get("min_opp_ask"))
    mt = _d(rb.get("max_touch"))
    reprice_existed = bool(px is not None and mt is not None and mt > px)
    return {
        "order_id": order.get("order_id"),
        "slug": order.get("slug"),
        "stage": order.get("stage"),
        "side": order.get("side"),
        "our_px": str(px) if px is not None else None,
        "l5_touch_px": str(l5) if l5 is not None else None,
        # authenticated touch, when the recorder served both sides
        "authenticated_opp_ask": rec.get("opp_ask_authenticated"),
        "authenticated_age_s": rec.get("authenticated_age_s"),
        "rest_s": order.get("rest_s"),
        "polls": rb.get("polls"),
        # distance from touch, at the book's CLOSEST during the whole rest
        "closest_book_gap_ticks": _ticks(mo, px),
        "book_gap_vs_l5_touch_ticks": _ticks(mo, l5),
        "placement_offset_vs_l5_ticks": _ticks(px, l5),
        # did L5 move, and would the book have met him
        "l5_touch_drift_ticks": _ticks(mt, px),
        "l5_reprice_existed": reprice_existed,
        "book_reached_that_touch": bool(
            reprice_existed and mo is not None and mt is not None and mo <= mt),
        "l5_touch_reached_by_opposite_ask":
            order.get("l5_touch_reached_by_opposite_ask"),
    }


def _dist(vals: List[int]) -> dict:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return {"n": 0}
    return {"n": len(v), "min": v[0], "p25": v[len(v) // 4],
            "median": median(v), "p75": v[3 * len(v) // 4], "max": v[-1],
            "share_at_or_inside_our_price": round(
                sum(1 for x in v if x <= 0) / len(v), 4),
            "share_one_tick_away": round(
                sum(1 for x in v if x == 1) / len(v), 4)}


def profile(orders: List[Mapping[str, Any]], outcome: str) -> dict:
    rows = [classify(o) for o in orders
            if ce.outcome_of(o) == outcome and o.get("rest_book")]
    gaps = [r["closest_book_gap_ticks"] for r in rows]
    rest = [float(r["rest_s"]) for r in rows if r.get("rest_s") is not None]
    existed = [r for r in rows if r["l5_reprice_existed"]]
    reached = [r for r in existed if r["book_reached_that_touch"]]
    place = Counter(r["placement_offset_vs_l5_ticks"] for r in rows)
    return {
        "orders": len(rows),
        "closest_book_gap_ticks": _dist(gaps),
        "book_gap_vs_l5_touch_ticks": _dist(
            [r["book_gap_vs_l5_touch_ticks"] for r in rows]),
        "median_rest_s": round(median(rest), 2) if rest else None,
        "placed_at_l5_touch": place.get(0, 0),
        "placed_at_l5_touch_share": (round(place.get(0, 0) / len(rows), 4)
                                     if rows else None),
        "l5_reprice_existed": len(existed),
        "l5_reprice_existed_share": (round(len(existed) / len(rows), 4)
                                     if rows else None),
        "and_book_reached_that_touch": len(reached),
        "reprice_opportunity_upper_bound_share": (
            round(len(reached) / len(rows), 4) if rows else None),
    }


def report(paths: Iterable[Any]) -> dict:
    comp = completes(paths)
    outcomes = Counter(ce.outcome_of(o) for o in comp)
    unfilled = profile(comp, "no_fill_cancelled")
    filled = profile(comp, "filled")
    rows = [classify(o) for o in comp
            if ce.outcome_of(o) == "no_fill_cancelled" and o.get("rest_book")]
    by_slug = Counter(r["slug"] for r in rows if r["book_reached_that_touch"])
    return {
        "complete_orders": len(comp),
        "outcomes": dict(outcomes),
        "with_rest_book": sum(1 for o in comp if o.get("rest_book")),
        "unfilled": unfilled,
        "filled_for_contrast": filled,
        "the_separation": (
            "Filled completes rest a median of "
            f"{filled['closest_book_gap_ticks'].get('median')} tick(s) from "
            "the best opposite ask; unfilled ones rest a median of "
            f"{unfilled['closest_book_gap_ticks'].get('median')} ticks away, "
            f"p75 {unfilled['closest_book_gap_ticks'].get('p75')} and as far "
            f"as {unfilled['closest_book_gap_ticks'].get('max')}. Distance "
            "from touch, not rest time, is what separates them: median rest "
            f"{filled['median_rest_s']}s filled against "
            f"{unfilled['median_rest_s']}s unfilled is the deadline, not a "
            "cause."),
        "the_finding": (
            f"In {unfilled['l5_reprice_existed']} of {unfilled['orders']} "
            f"({unfilled['l5_reprice_existed_share']:.1%}) unfilled completes "
            "L5's OWN touch moved above where we were sitting, and in "
            f"{unfilled['and_book_reached_that_touch']} of those the opposite "
            "ask reached that new touch -- "
            f"{unfilled['reprice_opportunity_upper_bound_share']:.1%} of all "
            "unfilled completes had an L5-CONSISTENT reprice available that "
            "the book would have met. We never made it, because the "
            "touch_moved re-quote is gated to OPEN and EXTEND: 0 of 868 "
            "COMPLETEs ever exited that way, against 36 OPEN and 35 EXTEND. "
            "Placement is not the defect -- we price AT L5's "
            f"touch in {unfilled['placed_at_l5_touch_share']:.1%} of them."),
        "markets_with_the_most_missed_reprices": dict(by_slug.most_common(8)),
        "upper_bound_not_an_estimate": (
            "rest_book is an AGGREGATE. min_opp_ask and max_touch are extrema "
            "over the same window and we cannot prove they coincided, so the "
            "count where both held is an UPPER BOUND on what repricing would "
            "have captured. Reaching a price is also not filling it -- we "
            "rest as maker and queue position decides -- and repricing to the "
            "touch can cross, which post-only refuses."),
        "classification": (
            "BEHAVIORAL-FIDELITY FIX, not a new strategy: L5's touch moved "
            "and our clone did not follow it. Following L5's own price is "
            "cloning him; paying up to get filled would not be."),
        "the_mechanism_already_exists": (
            "the live session re-quotes on touch_moved for OPEN and EXTEND "
            "and excludes COMPLETE by one condition. 36 OPEN and 35 EXTEND "
            "orders exited that way; 0 of 868 COMPLETEs did. The crossing "
            "guard (px >= opp_ask -> would_cross) already runs ahead of it "
            "for every relation. EXTEND's lower deadline share (28.5% vs "
            "64.9%) is confounded by relation and is NOT an effect size."),
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    u, f = rep["unfilled"], rep["filled_for_contrast"]
    L = ["# Accepted-but-unfilled COMPLETEs", "",
         f"{rep['complete_orders']} complete orders — "
         + ", ".join(f"{k} {v}" for k, v in sorted(rep["outcomes"].items()))
         + f" ({rep['with_rest_book']} carry a rest book)", "",
         "## Distance from touch", "",
         "| | unfilled | filled |", "|---|---|---|",
         f"| orders | {u['orders']} | {f['orders']} |"]
    for k in ("min", "p25", "median", "p75", "max"):
        L.append(f"| closest book gap, {k} (ticks) | "
                 f"{u['closest_book_gap_ticks'].get(k)} | "
                 f"{f['closest_book_gap_ticks'].get(k)} |")
    L += [f"| share one tick away | "
          f"{u['closest_book_gap_ticks'].get('share_one_tick_away')} | "
          f"{f['closest_book_gap_ticks'].get('share_one_tick_away')} |",
          f"| share the ask reached our price | "
          f"{u['closest_book_gap_ticks'].get('share_at_or_inside_our_price')} | "
          f"{f['closest_book_gap_ticks'].get('share_at_or_inside_our_price')} |",
          f"| median rest (s) | {u['median_rest_s']} | {f['median_rest_s']} |",
          f"| placed at L5's touch | {u['placed_at_l5_touch_share']} | "
          f"{f['placed_at_l5_touch_share']} |", "",
          rep["the_separation"], "",
          "## Was a later L5-consistent action available?", "",
          "| | unfilled | filled |", "|---|---|---|",
          f"| L5's touch moved above our price | {u['l5_reprice_existed']} "
          f"({u['l5_reprice_existed_share']:.1%}) | {f['l5_reprice_existed']} |",
          f"| ...and the book reached that touch | "
          f"{u['and_book_reached_that_touch']} "
          f"({u['reprice_opportunity_upper_bound_share']:.1%}) | "
          f"{f['and_book_reached_that_touch']} |", "",
          f"**{rep['the_finding']}**", "",
          "Markets where the most reprices were missed:", ""]
    for k, v in rep["markets_with_the_most_missed_reprices"].items():
        L.append(f"- `{k}` — {v}")
    L += ["", "## This is an upper bound, not an estimate", "",
          rep["upper_bound_not_an_estimate"], "",
          "## Classification", "", rep["classification"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    paths = ([os.path.join(a.logs, f) for f in sorted(os.listdir(a.logs))]
             if os.path.isdir(a.logs) else [a.logs])
    rep = report(paths)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "COMPLETE_NO_FILL_FORENSICS.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "complete_no_fill_forensics.json"),
                  "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
