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

    # HOW MUCH WORSE THAN L5 WOULD WE HAVE HAD TO DO, to trade at all?
    # "NEITHER" is a true answer to "could we match L5's own price", but it is
    # not a decision. The price gap is, and it is the third item on the
    # measured blocker list.
    out["price_slack"] = _slack(by_venue, intents, routes)
    # AND THE SAME QUESTION ON THE MAKER PATH, which is where L5's economics
    # live. Measuring reachability on the ask alone answers "could we CROSS to
    # L5's price", not "could we reach it at all".
    out["price_slack_maker"] = _slack_maker(evals, intents)

    # did PATIENCE pay? an intent that could not be filled at once, re-checked
    out["patience"] = _patience(evo)
    # WERE WE WATCHING? An absence of fills and an absence of observation look
    # identical inside an append-only store, so the coverage accounting is
    # attached to every report rather than left for a reader to go and find.
    out["coverage"] = _coverage(out_dir)

    out["labels"] = ("book readings, not fills. Deduped by intent_id content "
                     "hash and restricted to intents with a route row, so "
                     "every denominator is the same set. Quantiles, not means. "
                     "A slightly NEGATIVE book_age_s is venue-vs-local clock "
                     "skew (tens of ms), not a book from the future.")
    return out


SLACK_STEPS = ("0.00", "0.01", "0.02", "0.05", "0.10")


def _slack_maker(evals: Sequence[dict], intents: Dict[str, dict]) -> Dict[str, Any]:
    """Could a MAKER quote have rested at or under L5's own achieved price?

    THE TAKER NUMBER ANSWERS A DIFFERENT QUESTION. Crossing pays the spread, so
    "the ask was above L5's price" says we could not reach him by crossing --
    not that we could not reach him. A maker rests at the bid, which on a wide
    venue sits well below the ask, and L5 is a maker.

    Measured live, the two paths disagree sharply and reverse the venue
    ranking: Gemini reaches L5's price 22.3% of the time by crossing and 85.5%
    by resting, because its spread is wide. Reporting the taker figure alone
    understated both venues and Gemini nearly fourfold.

    A join price under the ceiling is NOT a fill, and a wide spread is exactly
    where a resting quote is least likely to be hit -- so `submittable` and the
    evidenced-fill rate are reported beside it and neither is implied by this.
    """
    out: Dict[str, Any] = {}
    rows = [e for e in evals
            if e.get("schema", "").endswith("/maker_placement")]
    by: Dict[str, Dict[str, Any]] = {}
    for m in rows:
        i = intents.get(m.get("intent_id"))
        if not i:
            continue
        v = by.setdefault(m["venue"], {"n": 0, "within": 0, "submittable": 0,
                                       "slack": []})
        v["n"] += 1
        if m.get("submittable"):
            v["submittable"] += 1
        # when the touch is above the ceiling there is no join price, but the
        # best bid says how far above -- that is the slack, and skipping these
        # rows emptied the distribution of precisely the cases it measures
        ref = m.get("price") if m.get("price") is not None else m.get("best_bid")
        if ref is None:
            continue
        try:
            px = D(str(ref))
            cap = D(str(i["max_acceptable_price"]))
        except Exception:                                     # noqa: BLE001
            continue
        if px <= cap:
            v["within"] += 1
        v["slack"].append(float(px - cap))
    for venue, v in by.items():
        out[venue] = {
            "n": v["n"],
            "submittable": v["submittable"],
            "submittable_rate": (round(v["submittable"] / v["n"], 4)
                                 if v["n"] else None),
            "join_at_or_under_ceiling": v["within"],
            "rate_at_or_under": (round(v["within"] / v["n"], 4)
                                 if v["n"] else None),
            "slack_needed": q([x for x in v["slack"] if x > 0]),
            "basis": ("the maker JOIN price against L5's own achieved price. A "
                      "join under the ceiling is a placement, never a fill -- "
                      "and a wide spread is where a resting quote is least "
                      "likely to be hit, so read this with the evidenced-fill "
                      "rate, not instead of it."),
        }
    return out


def _slack(by_venue: Dict[str, dict], intents: Dict[str, dict],
           routes: Dict[str, dict]) -> Dict[str, Any]:
    """Cents above the intent ceiling at which each venue's touch sat.

    The ceiling is L5's own achieved price on the wanted side, so this is
    literally "how far above what L5 paid was the market when we could first
    look". Positive slack means we could not match him; the DISTRIBUTION of
    that slack says whether the gap is a cent or a dime, and a cent is a
    business decision while a dime is not.

    Measured on the quoted touch, NOT on the all-in cost, and the two differ by
    the fee -- so the slack below is a LOWER BOUND on what it would really take.
    """
    out: Dict[str, Any] = {}
    for venue, rows in by_venue.items():
        vals: List[float] = []
        for (iid, _v), e in rows.items():
            i = intents.get(iid)
            if not i or e.get("available_price") is None:
                continue
            try:
                px = D(str(e["available_price"]))
                cap = D(str(i["max_acceptable_price"]))
            except Exception:                                 # noqa: BLE001
                continue
            vals.append(float(px - cap))
        if not vals:
            out[venue] = {"n": 0}
            continue
        inside = [v for v in vals if v <= 0]
        out[venue] = {
            "n": len(vals),
            "already_at_or_under_ceiling": len(inside),
            "rate_at_or_under": round(len(inside) / len(vals), 4),
            "slack_needed": q([v for v in vals if v > 0]),
            "cumulative_executable_at_slack": {
                st: round(sum(1 for v in vals if v <= float(st)) / len(vals), 4)
                for st in SLACK_STEPS},
            "basis": ("quoted touch minus the intent ceiling (L5's own achieved "
                      "price on the wanted side). Positive means the market was "
                      "above what L5 paid. Fees are NOT included, so this is a "
                      "LOWER bound on the concession required."),
            "vs_l5_edge": _vs_edge(vals, by_venue, venue, intents),
        }
    return out


# L5's measured trading drag, from his own receipts: -0.037% per dollar. It is
# the yardstick that decides whether a price concession is affordable, and it
# is tiny.
L5_DRAG_PER_DOLLAR = 0.00037


def _vs_edge(vals: Sequence[float], by_venue: Dict[str, dict], venue: str,
             intents: Dict[str, dict]) -> Dict[str, Any]:
    """A cent sounds small. Against L5's edge it is not, and that is the point.

    L5's measured trading drag is 0.037% per dollar. On a 50c contract one cent
    of price concession is 2% of notional -- roughly FIFTY-FOUR TIMES his whole
    edge. So "we would only need a cent" is not a small ask; it is the strategy
    gone. Expressed here as multiples rather than left for the reader to work
    out, because the absolute number is misleadingly reassuring.
    """
    ceilings = []
    for (iid, _v), _e in (by_venue.get(venue) or {}).items():
        i = intents.get(iid)
        if i:
            try:
                ceilings.append(float(i["max_acceptable_price"]))
            except Exception:                                 # noqa: BLE001
                continue
    if not ceilings:
        return {"n": 0}
    med_px = sorted(ceilings)[len(ceilings) // 2]
    pos = [v for v in vals if v > 0]
    med_slack = sorted(pos)[len(pos) // 2] if pos else 0.0
    def mult(cents):
        return (round((cents / med_px) / L5_DRAG_PER_DOLLAR, 1)
                if med_px > 0 else None)
    return {
        "l5_drag_per_dollar": L5_DRAG_PER_DOLLAR,
        "median_intent_price": round(med_px, 4),
        "median_slack_needed": round(med_slack, 4),
        "one_cent_as_multiple_of_l5_edge": mult(0.01),
        "median_slack_as_multiple_of_l5_edge": mult(med_slack),
        "reads": ("a concession of one cent at the median intent price costs "
                  "this many times L5's entire per-dollar edge. A number well "
                  "above 1 means matching his price is not a preference, it is "
                  "the strategy."),
    }


def _coverage(out_dir: str) -> Dict[str, Any]:
    """The supervisor's coverage markers, read from the state root above us.

    Reported inline because the maker numbers below are the ones most easily
    misread: "0 evidenced fills" is a finding only if we were present for the
    window it covers.
    """
    root = os.path.dirname(os.path.abspath(out_dir.rstrip("/")))
    try:
        from analysis.l5 import coverage as C
        rep = C.report(root)
    except Exception as exc:                                  # noqa: BLE001
        return {"available": False,
                "reason": f"coverage not readable: {type(exc).__name__}: {exc}",
                "warning": ("with no coverage record, an absence of fills "
                            "CANNOT be distinguished from an absence of "
                            "observation")}
    if not rep.get("markers"):
        return {"available": False, "state_root": root,
                "reason": rep.get("verdict"),
                "warning": ("with no coverage record, an absence of fills "
                            "CANNOT be distinguished from an absence of "
                            "observation")}
    return {"available": True, "state_root": root,
            "span_s": rep.get("span_s"), "boots": rep.get("boots"),
            "exits": rep.get("exits"), "restarts": rep.get("restarts"),
            "exec_shadow": (rep.get("per_stream") or {}).get("exec_shadow"),
            "recent_gaps": (rep.get("gaps") or [])[-5:]}


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
        # a re-read inside the intent's own validity is the only one where a
        # price coming to us is still the trade the engine asked for
        age = r.get("since_intent_s")
        valid = r.get("valid_for_s")
        live = (age is not None and valid is not None and age <= float(valid))
        for v in ("PMUS", "GEMINI"):
            if (r.get(v) or {}).get("crossed_ceiling"):
                s[v] = True
                if live:
                    s[f"{v}_within_validity"] = True
    if not per:
        return {"intents_rechecked": 0}
    within = {"PMUS": 0, "GEMINI": 0}
    for s in per.values():
        for v in ("PMUS", "GEMINI"):
            if s.get(f"{v}_within_validity"):
                within[v] += 1
    return {
        "intents_rechecked": len(per),
        "checks": sum(s["checks"] for s in per.values()),
        "crossed_ceiling_later_PMUS": sum(1 for s in per.values() if s["PMUS"]),
        "crossed_ceiling_later_GEMINI": sum(1 for s in per.values()
                                            if s["GEMINI"]),
        "crossed_WHILE_STILL_AUTHORIZED_PMUS": within["PMUS"],
        "crossed_WHILE_STILL_AUTHORIZED_GEMINI": within["GEMINI"],
        "note": ("'crossed later' means the venue's best price on the wanted "
                 "side reached the intent's ceiling at some later re-read. It "
                 "is not a fill and does not account for queue. The "
                 "WHILE_STILL_AUTHORIZED counts restrict that to re-reads "
                 "inside the intent's own validity window -- a price that "
                 "comes to us after the intent has expired is not patience "
                 "paying off, it is a different trade."),
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
