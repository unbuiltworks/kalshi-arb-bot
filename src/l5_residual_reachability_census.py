"""Corpus-wide complement reachability, from `L5_SHADOW_RESIDUAL_BOOK_SAMPLE`.

READ-ONLY. Parses Railway deploy-log dumps. Opens no socket, holds no
credential, builds no order body. It is the stated extension of
`L5_COMPLETION_IS_A_QUEUE_PROBLEM.md`, whose own "honest limits" section
asks for exactly this:

    "One window. The obvious extension is the same measurement across many
    windows, which needs only the log stream already running -- no new
    service, capital, or authorization."

WHAT IT MEASURES. For each sample the engine already priced the complement
it would need to finish a pair against the real book. This reads back the
engine's OWN verdicts rather than re-deriving them, so the side semantics
(wire is YES/UP; own cost of DOWN at wire p is 1 - p) stay where they were
computed and cannot drift here:

    taker_walk.insufficient_displayed_depth    could we cross at all
    opposite_top_of_book.taker_cross_wire_px   was there anything to cross
    maker_quote.queue_ahead_multiple_of_need   how far back joining puts us

THE ONE METHODOLOGICAL CORRECTION. Six shadow controllers sample the same
book, so a raw sample count multiple-counts each book instant: the
single-window result quoted "501 samples" over 539 s at a 5 s sampler,
which is ~108 distinct instants observed ~5 times each, not 501
independent observations. Percentages barely move; the effective n moves by
almost 5x, and n is what a confidence claim rests on. So every rate is
reported BOTH ways, keyed on (window, book row t) for the book-level facts.

A DISPLAYED QUOTE IS NOT AN EXECUTABLE OPPORTUNITY, and `queue_ahead_bound`
is an UPPER bound -- the venue publishes no per-order queue position.
Nothing here is a fill, a realized number, or a profitability claim.
"""
from __future__ import annotations

import json
import statistics
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: BOTH SAMPLERS. The shadow arms emit L5_SHADOW_RESIDUAL_BOOK_SAMPLE and
#: carry modelled inventory; the money path emits L5_RESIDUAL_BOOK_SAMPLE and
#: carries the ACTUAL filled quantity. The reachability block is computed
#: identically for both -- it is a fact about the venue book and the quantity
#: needed, not about whose inventory it is -- so refusing the real-inventory
#: rows would discard the better evidence of the two. `source` on every row
#: keeps them distinguishable, and they are counted separately as well.
LABELS = ("L5_SHADOW_RESIDUAL_BOOK_SAMPLE", "L5_RESIDUAL_BOOK_SAMPLE")
LABEL = LABELS[0]


def iter_samples(paths: Iterable[str]) -> Iterable[dict]:
    """Every parsed sample across the dumps, deduplicated by identity.

    The same log line can appear in two overlapping time-range fetches, so
    identity is (window|arm, book row t, sample t) -- dropping repeats here
    rather than letting an overlapping pull inflate the corpus.
    """
    seen = set()
    for p in paths:
        try:
            doc = json.load(open(p))
        except (OSError, ValueError) as exc:
            print("skip %s: %s" % (p, exc), file=sys.stderr)
            continue
        for row in (doc.get("deploy") or []):
            msg = str(row.get("message") or "")
            if not any(l in msg for l in LABELS):
                continue
            try:
                d = json.loads(msg.split(" ", 1)[1])
            except (IndexError, ValueError):
                continue
            key = (d.get("slug"), (d.get("book") or {}).get("row", {}).get("t"),
                   d.get("t"))
            if key in seen:
                continue
            seen.add(key)
            yield d


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def census(samples: Iterable[dict]) -> Dict[str, Any]:
    arms: List[dict] = []
    # book-level facts keyed on one instant, so six arms count once
    instants: Dict[Tuple[Any, Any], dict] = {}
    for d in samples:
        slug = str(d.get("slug") or "")
        window = slug.split("|", 1)[0]
        arm = slug.split("|", 1)[1] if "|" in slug else ""
        row = (d.get("book") or {}).get("row") or {}
        reach = d.get("reachability") or {}
        otb = reach.get("opposite_top_of_book") or {}
        mk = reach.get("maker_quote") or {}
        tw = reach.get("taker_walk") or {}
        rec = {
            "window": window, "arm": arm,
            "source": str(d.get("label") or ""),
            "real_inventory": str(d.get("label") or "") == LABELS[1],
            "row_t": row.get("t"),
            "held_side": d.get("held_side"),
            "n_bids": row.get("n_bids"), "n_asks": row.get("n_asks"),
            # the engine's own verdicts
            "taker_impossible": bool(tw.get("insufficient_displayed_depth")),
            "nothing_to_cross": otb.get("taker_cross_wire_px") is None,
            "queue_mult": _f(mk.get("queue_ahead_multiple_of_need")),
            "qty_needed": _f(mk.get("qty_needed")),
            "queue_ahead_bound": _f(mk.get("queue_ahead_bound")),
        }
        arms.append(rec)
        instants.setdefault((window, row.get("t")), rec)

    def rate(rows: List[dict], key: str) -> Optional[float]:
        if not rows:
            return None
        return round(100.0 * sum(1 for r in rows if r[key]) / len(rows), 1)

    inst = list(instants.values())
    q = sorted(r["queue_mult"] for r in arms if r["queue_mult"] is not None)
    qi = sorted(r["queue_mult"] for r in inst if r["queue_mult"] is not None)

    def pct(xs: List[float], p: float) -> Optional[float]:
        if not xs:
            return None
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1)

    out: Dict[str, Any] = {
        "label": "L5_RESIDUAL_REACHABILITY_CENSUS",
        "windows": sorted({r["window"] for r in arms}),
        "n_windows": len({r["window"] for r in arms}),
        "n_arm_samples": len(arms),
        "n_book_instants": len(inst),
        "arms_seen": sorted({r["arm"] for r in arms if r["arm"]}),
        # the two populations, never collapsed: a modelled residual and a
        # venue-confirmed one are different grades of evidence
        "n_real_inventory_samples": sum(1 for r in arms if r["real_inventory"]),
        "n_shadow_samples": sum(1 for r in arms if not r["real_inventory"]),
        "nights": sorted({r["window"][-16:-6] for r in arms
                          if len(r["window"]) > 16}),
        "nothing_to_cross_pct": {
            "per_arm_sample": rate(arms, "nothing_to_cross"),
            "per_book_instant": rate(inst, "nothing_to_cross"),
        },
        "taker_impossible_pct": {
            "per_arm_sample": rate(arms, "taker_impossible"),
            "per_book_instant": rate(inst, "taker_impossible"),
        },
        "queue_ahead_multiple_of_need": {
            "n": len(q),
            "p10": pct(q, 0.10), "p50": pct(q, 0.50), "p90": pct(q, 0.90),
            "min": round(q[0], 1) if q else None,
            "max": round(q[-1], 1) if q else None,
            "ge_1x_pct": rate([{"x": v >= 1} for v in q], "x"),
            "ge_100x_pct": rate([{"x": v >= 100} for v in q], "x"),
            "ge_1000x_pct": rate([{"x": v >= 1000} for v in q], "x"),
            "per_book_instant_p50": pct(qi, 0.50),
            "per_book_instant_n": len(qi),
        },
        "per_window": {},
        "labels": ("queue_ahead_bound is an UPPER bound; the venue "
                   "publishes no per-order queue position. A displayed "
                   "quote is not an executable opportunity and nothing "
                   "here is a fill, a realized number or a profitability "
                   "claim. Rates are given per book instant as well as per "
                   "arm sample because six controllers share one book."),
    }
    for w in out["windows"]:
        aw = [r for r in arms if r["window"] == w]
        iw = [r for r in inst if r["window"] == w]
        qw = sorted(r["queue_mult"] for r in iw if r["queue_mult"] is not None)
        out["per_window"][w] = {
            "arm_samples": len(aw), "book_instants": len(iw),
            "nothing_to_cross_pct": rate(iw, "nothing_to_cross"),
            "taker_impossible_pct": rate(iw, "taker_impossible"),
            "queue_mult_p50": pct(qw, 0.50),
            "queue_mult_min": round(qw[0], 1) if qw else None,
            "held_sides": sorted({str(r["held_side"]) for r in iw}),
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    paths = list(argv if argv is not None else sys.argv[1:])
    if not paths:
        print(__doc__)
        print("usage: python3 -m src.l5_residual_reachability_census "
              "<railway-log-dump.json> ...", file=sys.stderr)
        return 2
    print(json.dumps(census(iter_samples(paths)), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
