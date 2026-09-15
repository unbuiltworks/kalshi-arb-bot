"""Read the executable shadow's durable store and report what it has learned.

QUANTILES, NOT MEANS. Execution is a tail business: a mean all-in cost hides
the window where a venue quoted 9c wide, and a mean lag hides the 5m cohort
arriving two minutes late. Every distribution here is reported at p05/p50/p95
and max.

DEDUPED BY CONTENT HASH. `intent_id` is a hash of the economically meaningful
fields, so a row written twice (two runners briefly sharing a state directory,
a restart replaying a window) counts once. Counting it twice would inflate
every rate below with no new observation behind it.

NOTHING HERE IS A FILL. `route`, `would_submit` and `executable_qty` are
readings of public books. The only rows that carry evidence are maker outcomes
with `evidenced_fill` true, and those are MODELLED fills backed by later public
prints -- still not venue-confirmed, still never realized P&L.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence


def load(out_dir: str, name: str) -> List[dict]:
    p = os.path.join(out_dir, name)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def q(vals: Sequence[float]) -> Optional[Dict[str, Any]]:
    v = sorted(float(x) for x in vals if x is not None)
    if not v:
        return None

    def pick(p):
        return round(v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))], 4)
    return {"n": len(v), "p05": pick(0.05), "p50": pick(0.50),
            "p95": pick(0.95), "max": round(v[-1], 4)}


def report(out_dir: str) -> Dict[str, Any]:
    # READ EVERY FILE FIRST, then restrict to COMPLETE intents. These are
    # append-only logs a live runner is still writing: reading intents at one
    # instant and evaluations a moment later compares denominators that do not
    # correspond, which is exactly how one pass reported 17 Polymarket
    # evaluations against 19 Gemini ones and made two venues look asymmetric
    # when they were not. The route row is the LAST thing written for an
    # intent, so its presence is the completeness marker.
    raw_intents = load(out_dir, "intents.jsonl")
    raw_routes = load(out_dir, "routes.jsonl")
    evals = load(out_dir, "evaluations.jsonl")
    makers = load(out_dir, "maker_outcomes.jsonl")
    evo = load(out_dir, "book_evolution.jsonl")

    routes: Dict[str, dict] = {}
    for r in raw_routes:
        routes.setdefault(r.get("intent_id"), r)       # first writer wins
    complete = set(routes)
    intents = {r.get("intent_id"): r for r in raw_intents
               if r.get("intent_id") in complete}
    evals = [e for e in evals if e.get("intent_id") in complete]
    makers = [m for m in makers if m.get("intent_id") in complete]
    evo = [x for x in evo if x.get("intent_id") in complete]

    by_venue: Dict[str, dict] = {}
    for e in evals:
        if e.get("schema", "").endswith("/evaluation"):
            by_venue.setdefault(e["venue"], {})[
                (e["intent_id"], e["venue"])] = e

    out: Dict[str, Any] = {
        "intents": len(intents),
        "routes": len(routes),
        "by_cohort_source": dict(Counter(
            f"{r.get('cohort')}/{r.get('intent_source')}"
            for r in routes.values())),
        "route_distribution": dict(Counter(r.get("route") for r in routes.values())),
        "route_by_cohort": {},
        "route_by_source": {},
    }
    for key, field in (("route_by_cohort", "cohort"),
                       ("route_by_source", "intent_source")):
        d: Dict[str, Counter] = defaultdict(Counter)
        for r in routes.values():
            d[str(r.get(field))][r.get("route")] += 1
        out[key] = {k: dict(v) for k, v in d.items()}

    # why a venue was declined -- the reason, not just the count
    declines: Dict[str, Counter] = defaultdict(Counter)
    for r in routes.values():
        for d0 in r.get("declined") or []:
            declines[d0.get("venue")][_reason_class(d0.get("reason"))] += 1
    out["declined_reasons"] = {k: dict(v) for k, v in declines.items()}

    # per venue: can it supply, and at what all-in cost
    venue_stats = {}
    for venue, rows in by_venue.items():
        rs = list(rows.values())
        subm = [e for e in rs if e.get("would_submit")]
        venue_stats[venue] = {
            "evaluated": len(rs),
            "would_submit": len(subm),
            "submit_rate": (round(len(subm) / len(rs), 3) if rs else None),
            "no_market": sum(1 for e in rs if e.get("stage") == "no_market"),
            "executable_qty": q([_f(e.get("executable_qty")) for e in rs]),
            "all_in_cost": q([_f(e.get("effective_cost")) for e in subm]),
            "book_age_s": q([e.get("book_age_s") for e in rs]),
            "ladder_complete_rate": _ladder_rates(rs),
        }
    out["venues"] = venue_stats

    out["cross_venue_gap"] = q([_f((r.get("venue_gap") or {}).get("gap"))
                                for r in routes.values()])
    out["cheaper_venue"] = dict(Counter(
        (r.get("venue_gap") or {}).get("cheaper") for r in routes.values()
        if (r.get("venue_gap") or {}).get("cheaper")))

    lags: Dict[str, List[float]] = defaultdict(list)
    for i in intents.values():
        lag = (i.get("extra") or {}).get("l5_observation_lag_s")
        if lag is not None:
            lags[str(i.get("cohort"))].append(float(lag))
    out["l5_observation_lag_s"] = {k: q(v) for k, v in lags.items()}
    out["tte_at_book_s"] = q([r.get("tte_at_book_s") for r in routes.values()])

    # maker: placed, decided, and decided HOW
    placed = [e for e in evals
              if e.get("schema", "").endswith("/maker_placement")]
    dec = {(m.get("intent_id"), m.get("venue")): m for m in makers}
    out["maker"] = {
        "placements": len(placed),
        "submittable": sum(1 for p in placed if p.get("submittable")),
        "decided": len(dec),
        "evidenced_fill": sum(1 for m in dec.values()
                              if m.get("evidenced_fill") is True),
        "evidenced_no_fill": sum(1 for m in dec.values()
                                 if m.get("evidenced_fill") is False),
        "undecided_no_prints": sum(1 for m in dec.values()
                                   if m.get("evidenced_fill") is None),
        "time_to_fill_s": q([m.get("time_to_fill_s") for m in dec.values()]),
        "queue_ahead": q([_f(m.get("queue_ahead")) for m in dec.values()]),
        "by_venue": dict(Counter(f"{m.get('venue')}:"
                                 f"{m.get('evidenced_fill')}"
                                 for m in dec.values())),
        "labels": ("evidenced_fill is a MODELLED maker fill backed by later "
                   "public prints, under a queue-conservative test. It is not "
                   "a venue-confirmed fill and is never realized P&L."),
    }

    # did PATIENCE pay? an intent that could not be filled at once, re-checked
    out["patience"] = _patience(evo)
    out["labels"] = ("book readings, not fills. Deduped by intent_id content "
                     "hash and restricted to intents with a route row, so "
                     "every denominator is the same set. Quantiles, not means. "
                     "A slightly NEGATIVE book_age_s is venue-vs-local clock "
                     "skew (tens of ms), not a book from the future.")
    return out


def _patience(evo: Sequence[dict]) -> Dict[str, Any]:
    """Of the intents re-checked later, how often did a venue come to us?

    This is the measurement behind L5's patience: an intent that is not
    executable now is not the same as one that is never executable, and a
    single snapshot cannot tell the two apart.
    """
    per: Dict[str, Dict[str, Any]] = {}
    for r in evo:
        k = r.get("intent_id")
        s = per.setdefault(k, {"PMUS": False, "GEMINI": False, "checks": 0})
        s["checks"] += 1
        for v in ("PMUS", "GEMINI"):
            if (r.get(v) or {}).get("crossed_ceiling"):
                s[v] = True
    if not per:
        return {"intents_rechecked": 0}
    return {
        "intents_rechecked": len(per),
        "checks": sum(s["checks"] for s in per.values()),
        "crossed_ceiling_later_PMUS": sum(1 for s in per.values() if s["PMUS"]),
        "crossed_ceiling_later_GEMINI": sum(1 for s in per.values()
                                            if s["GEMINI"]),
        "note": ("'crossed later' means the venue's best price on the wanted "
                 "side reached the intent's ceiling at some later re-read. It "
                 "is not a fill and does not account for queue."),
    }


def _ladder_rates(rows: Sequence[dict]) -> Dict[str, Any]:
    """At each candidate size, how often could this venue supply it whole?"""
    tot: Counter = Counter()
    ok: Counter = Counter()
    for e in rows:
        lad = (e.get("extra") or {}).get("qty_ladder") or {}
        for qty, row in lad.items():
            tot[qty] += 1
            if row.get("complete") and row.get("submittable"):
                ok[qty] += 1
    return {k: {"n": tot[k], "complete": ok[k],
                "rate": round(ok[k] / tot[k], 3) if tot[k] else None}
            for k in sorted(tot, key=lambda x: D(x))}


def _reason_class(reason: Optional[str]) -> str:
    r = (reason or "").lower()
    for probe, label in (
            ("minimum order size", "below venue minimum order size"),
            ("no level at or under", "price above the intent ceiling"),
            ("can supply only", "insufficient size at an acceptable price"),
            ("quotes no", "venue quotes nothing on this side"),
            ("exceeds the intent ceiling", "all-in cost above the ceiling"),
            ("fee unknown", "venue fee undeclared"),
            ("unknown", "venue unreadable (UNKNOWN, not absent)"),
            ("capacity", "insufficient or unknown capacity")):
        if probe in r:
            return label
    return (reason or "unspecified")[:60]


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-exec-shadow")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.out_dir), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
