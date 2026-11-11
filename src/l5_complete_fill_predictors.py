"""What predicts an accepted COMPLETE filling rather than expiring?

OWNER: "use the existing COMPLETE tape to determine what predicts an
accepted COMPLETE actually filling versus expiring unfilled. Stratify by
distance from authenticated touch, rest time, subsequent touch movement,
inventory imbalance, event progress/TTE, attempt number and pair-at-touch
economics. Compare the strongest predictors against historical L5 completion
behavior. The goal is to identify the next L5-fidelity correction from
evidence, not propose another strategy."

716 accepted COMPLETEs (rejections excluded -- a rejection is a failure to
transmit an order, not a failure to fill one). 127 filled, 589 expired:
a 17.7% fill rate.

THREE OF THE REQUESTED STRATA CANNOT BE COMPUTED, and saying which is part
of the answer rather than a caveat on it:

  DISTANCE FROM AUTHENTICATED TOUCH -- opp_ask_authenticated appears on 8 of
    716 accepted completes (1.1%). The authenticated book is only attached to
    the #93 control rows. This stratum is UNAVAILABLE, and the gap is itself
    a finding: the one book we trust is not recorded on the orders whose
    pricing we most want to explain.
  EVENT PROGRESS / TTE -- DEGENERATE. Every one of the 871 acted decisions
    carries the same TTE bucket, tte>10m. The state key's TTE buckets were
    cut for 5- and 15-minute crypto windows; on multi-hour sports events they
    saturate, so the clone cannot tell early from late. That matters because
    time remaining is L5's STRONGEST predictor on his own history.
  REST TIME -- not a predictor and not offered as one. rest_s is determined
    by the outcome: a fill ends the rest, no fill runs to the deadline. Its
    apparent power (median 7.9s filled against 30.7s unfilled) is the
    deadline showing up in the data. It is reported under REALIZED.

WHAT IS ACTUALLY MEASURABLE splits the same way it did in the residual-state
replay, and for the same reason:

  PLACEMENT -- known when the order goes on, so it could inform behaviour:
    attempt number, price band, inventory imbalance, residual size,
    pair-at-touch.
  REALIZED -- observed while the order rested, so it can describe an outcome
    but never select one: how close the book came, whether the touch moved.

READ-ONLY. Nothing here changes a live treatment, and the strongest finding
is offered as a candidate for review, not as a change.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from src import l5_completion_economics as ce
from src import l5_completion_extension_eval as ev

MIN_STRATUM_N = 8


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except Exception:                                  # noqa: BLE001
        return None


# --------------------------------------------------------------- extraction
def rows(paths: Iterable[Any]) -> List[dict]:
    """One row per accepted COMPLETE, with the state that existed at placement.

    Inventory comes from the decision's own inventory_before rather than a
    reconstruction: 349 of 497 sessions RESUME a position held across a
    restart, so a running total built only from in-session fills would read
    a residual of zero on exactly the orders that matter most."""
    out: List[dict] = []
    for s in ev._sessions(paths):
        acted = [x for x in (s.get("decisions") or [])
                 if x.get("acted") and x.get("acted") != "hold"]
        orders = s.get("orders") or []
        joined = len(acted) == len(orders)
        attempt = 0
        for i, o in enumerate(orders):
            if o.get("relation") != "complete":
                continue
            if str(o.get("end_reason") or "").startswith("rejected"):
                continue
            attempt += 1
            dec = acted[i] if joined else None
            inv = (dec or {}).get("inventory_before") or {}
            state = str((dec or {}).get("state") or "")
            parts = state.split("|")
            resid = _d(inv.get("residual"))
            avg_up, avg_dn = _d(inv.get("avg_up")), _d(inv.get("avg_dn"))
            touch = _d(o.get("l5_touch_px"))
            # completing the side we are short of: long UP -> buy DN
            basis = (avg_up if (resid is not None and resid > 0) else avg_dn)
            rb = o.get("rest_book") or {}
            rec = o.get("recorder_book") or {}
            out.append({
                "slug": o.get("slug") or s.get("slug"),
                "filled": ce.outcome_of(o) == "filled",
                # --- placement
                "attempt": attempt,
                "price_band": parts[0] if len(parts) == 3 else None,
                "tte_bucket": parts[1] if len(parts) == 3 else None,
                "imbalance": parts[2] if len(parts) == 3 else None,
                "residual_units": abs(resid) if resid is not None else None,
                "pair_at_touch": ((basis + touch)
                                  if (basis is not None and touch is not None)
                                  else None),
                "gateway_ask_at_placement": _d(o.get("fresh_opp_ask")),
                "authenticated_ask": _d(rec.get("opp_ask_authenticated")),
                "px": _d(o.get("px")),
                # --- realized
                "rest_s": o.get("rest_s"),
                "min_opp_ask": _d(rb.get("min_opp_ask")),
                "max_touch": _d(rb.get("max_touch")),
            })
    return out


# ------------------------------------------------------------------ strata
def attempt_bucket(r: Mapping[str, Any]) -> Optional[str]:
    a = r.get("attempt")
    return None if not a else (f"{a}" if a <= 3 else "4+")


def pair_bucket(r: Mapping[str, Any]) -> Optional[str]:
    p = r.get("pair_at_touch")
    if p is None:
        return None
    if p < D("0.95"):
        return "a <0.95"
    if p < D("1.00"):
        return "b 0.95-1.00"
    if p < D("1.05"):
        return "c 1.00-1.05 (above par)"
    return "d >=1.05 (well above par)"


def residual_bucket(r: Mapping[str, Any]) -> Optional[str]:
    x = r.get("residual_units")
    if x is None:
        return None
    return ("a <2" if x < 2 else "b 2-5" if x < 5
            else "c 5-10" if x < 10 else "d 10+")


def book_gap_bucket(r: Mapping[str, Any]) -> Optional[str]:
    """REALIZED. How close the book came while we rested, in ticks."""
    mo, px = r.get("min_opp_ask"), r.get("px")
    if mo is None or px is None:
        return None
    t = int((mo - px) / D("0.01"))
    return ("a <=0" if t <= 0 else "b 1" if t == 1 else "c 2-3" if t <= 3
            else "d 4-8" if t <= 8 else "e 9+")


PLACEMENT: Dict[str, Callable] = {
    "attempt_number": attempt_bucket,
    "price_band": lambda r: r.get("price_band"),
    "inventory_imbalance": lambda r: r.get("imbalance"),
    "residual_units": residual_bucket,
    "pair_at_touch": pair_bucket,
}
REALIZED: Dict[str, Callable] = {
    "closest_book_gap_ticks": book_gap_bucket,
}


def stratify(rs: List[dict], keyfn: Callable) -> dict:
    g: Dict[str, List[dict]] = defaultdict(list)
    for r in rs:
        k = keyfn(r)
        if k is not None:
            g[k].append(r)
    out = {}
    for k, v in sorted(g.items()):
        f = sum(1 for x in v if x["filled"])
        row = {"n": len(v), "filled": f, "fill_rate": round(f / len(v), 4)}
        if len(v) < MIN_STRATUM_N:
            row["fill_rate"] = None
            row["suppressed_below_min_n"] = MIN_STRATUM_N
        out[k] = row
    return out


def _spread(table: Mapping[str, Mapping[str, Any]]) -> Optional[float]:
    v = [r["fill_rate"] for r in table.values() if r.get("fill_rate") is not None]
    return round(max(v) - min(v), 4) if len(v) >= 2 else None


def coverage(rs: List[dict]) -> dict:
    n = len(rs)

    def have(k):
        c = sum(1 for r in rs if r.get(k) is not None)
        return {"n": c, "share": round(c / n, 4) if n else None}
    return {
        "accepted_completes": n,
        "filled": sum(1 for r in rs if r["filled"]),
        "fill_rate": round(sum(1 for r in rs if r["filled"]) / n, 4) if n else None,
        "authenticated_ask": have("authenticated_ask"),
        "gateway_ask_at_placement": have("gateway_ask_at_placement"),
        "state_key": have("price_band"),
        "pair_at_touch": have("pair_at_touch"),
        "rest_book": have("min_opp_ask"),
    }


def report(paths: Iterable[Any]) -> dict:
    rs = rows(paths)
    cov = coverage(rs)
    place = {k: stratify(rs, f) for k, f in PLACEMENT.items()}
    real = {k: stratify(rs, f) for k, f in REALIZED.items()}
    tte = stratify(rs, lambda r: r.get("tte_bucket"))
    ranked = sorted(
        ((k, _spread(v)) for k, v in place.items() if _spread(v) is not None),
        key=lambda kv: -kv[1])
    return {
        "coverage": cov,
        "unavailable_strata": {
            "distance_from_authenticated_touch": (
                f"opp_ask_authenticated is on {cov['authenticated_ask']['n']} "
                f"of {cov['accepted_completes']} accepted completes "
                f"({cov['authenticated_ask']['share']:.1%}). UNAVAILABLE. The "
                "authenticated book is attached only to the #93 control rows, "
                "so the one book we trust is not recorded on the orders whose "
                "pricing we most want to explain."),
            "event_progress_tte": (
                "DEGENERATE: " + ", ".join(
                    f"{k} {v['n']}" for k, v in tte.items()) +
                ". The state key's TTE buckets were cut for 5- and 15-minute "
                "crypto windows; on multi-hour sports events they saturate, "
                "so the clone cannot distinguish early from late."),
            "rest_time": (
                "NOT A PREDICTOR. rest_s is determined by the outcome -- a "
                "fill ends the rest, no fill runs to the deadline -- so its "
                "apparent power is the deadline, not a cause."),
        },
        "placement_features": place,
        "realized_features": real,
        "realized_means": (
            "observed while the order rested. Describes an outcome, never "
            "selects one."),
        "ranked_by_spread": [{"feature": k, "fill_rate_spread": v}
                             for k, v in ranked],
        "above_par_concentration": ABOVE_PAR,
        "requote_crosscheck": REQUOTE_CROSSCHECK,
        "l5_comparison": L5_COMPARISON,
        "candidate_fidelity_correction": CANDIDATE,
        "read_only": (
            "No live treatment changes. The candidate below is offered for "
            "review, not applied."),
    }


L5_COMPARISON = {
    "source": "src/l5_residual_state_replay.py over 2,216 L5 windows",
    "l5_strongest_predictor": (
        "TIME TO WORK IT. L5 pairs 93.3% of 15-minute windows against 65.7% "
        "of 5-minute ones, and within a window 82.9% when he opens with "
        "80-100% of it left against 38.0% with under 20% left -- monotone "
        "across five buckets."),
    "ours_on_the_same_axis": (
        "UNMEASURABLE. All 871 acted decisions sit in one TTE bucket."),
    "l5_second_predictor": (
        "IMBALANCE. A balanced book (0.35-0.65) pairs 74.6% on 5-minute "
        "windows and 97.7% on 15-minute; lopsided (<0.20 or >0.80) pairs "
        "49.7% and 33.3%."),
    "ours_on_the_same_axis_2": (
        "AGREES IN DIRECTION. Our fill rate peaks in the p45-55c band and "
        "falls away on both sides, which is the same shape measured on a "
        "different quantity (our own fill, not L5's completion)."),
}

ABOVE_PAR = {
    "completes_with_a_pair_at_touch": 500,
    "above_par": 353,
    "above_par_share": 0.706,
    "above_par_fill_rate": 0.108,
    "below_par_fill_rate": 0.456,
    "reading": (
        "70.6% of our completion attempts are placed at a pair cost at or "
        "above $1.00, and those fill 10.8% of the time against 45.6% below "
        "par. That single split is most of the 17.7% headline fill rate."),
    "l5_for_comparison": (
        "the PMUS shadow's complete counterfactual puts L5's own pair>1.00 "
        "share at 35.7% over 284,920 complete draws with inventory. Different "
        "denominator, so not a like-for-like ratio -- but our attempts sit "
        "above par about twice as often as his distribution implies."),
    "the_gate_already_says_this": (
        "the live fidelity gate fails action_distribution_relation and "
        "post_fill_complete_gap on exactly this: our ACTED complete share is "
        "0.3959 against L5's 0.2858, and post-fill 0.6104 against 0.4375, a "
        "17.3pp gap. The SAMPLED distribution matches L5 to L1 0.017 and "
        "PASSES -- the model is drawing correctly and the safety shell is "
        "converting other relations into completes "
        "(cap_fallback_attribution: ALL_CAP_FORCED)."),
    "what_that_means_and_does_not": (
        "the over-completion is a consequence of caps that exist for good "
        "reasons, and this module does NOT propose changing any of them. It "
        "reports that the shell's forced completes land in the regime where "
        "completes do not fill, so the cost of those caps is now measured "
        "rather than assumed. What to do about it is an owner decision."),
}

REQUOTE_CROSSCHECK = {
    "claim": (
        "this weakens the COMPLETE re-quote proposal I made earlier, and it "
        "should be read as a correction to it."),
    "why": (
        "the re-quote replay found 66.7% of its counterfactual completions "
        "would land above par. Above-par completes fill at 10.8%. So the "
        "re-quote would concentrate new attempts in precisely the regime "
        "with the worst fill rate, which means the conversion assumption in "
        "that replay -- even at its 25% rung -- was probably too generous."),
    "what_it_does_not_overturn": (
        "the replay's SIGN argument stands on the per-unit comparison "
        "(-3.2 cents locked against -33.1 cents stranded), not on the "
        "conversion rate. This lowers the expected magnitude, not the sign, "
        "and it strengthens the case for settling it with the bounded A/B "
        "rather than the replay."),
}

CANDIDATE = {
    "name": ("the completes we place are concentrated above par, where they "
             "do not fill"),
    "classification": "BEHAVIORAL-FIDELITY FIX (evidence; the remedy is an "
                      "owner decision)",
    "why_this_one": (
        "pair-at-touch is the strongest measured predictor on the tape by a "
        "wide margin -- a 36-point fill-rate spread with a sharp cliff "
        "exactly at par -- and unlike TTE it is measurable, and unlike rest "
        "time it is known at placement."),
    "evidence": [
        "Below par the fill rate is 45.6%; at or above par it is 10.8%.",
        "70.6% of our completes are placed above par, so the bad regime is "
        "where most of our attempts live.",
        "The live fidelity gate independently fails on the same thing: acted "
        "complete share 0.3959 vs L5's 0.2858, post-fill gap 17.3pp, while "
        "the SAMPLED distribution passes at L1 0.017.",
        "The gate attributes the acted-vs-sampled gap to the safety shell "
        "(ALL_CAP_FORCED), not to the model.",
    ],
    "why_it_is_fidelity_and_not_strategy": (
        "the model already draws L5's relation mix correctly. The clone "
        "diverges when the shell converts a draw into a completion. Bringing "
        "acted behaviour back toward sampled behaviour is cloning L5 more "
        "closely, not inventing anything."),
    "what_this_does_not_claim": (
        "it does not claim the caps are wrong, and it proposes no change to "
        "any of them. Caps exist for reasons that predate this measurement. "
        "What is new is the COST: forced completes land where completes fill "
        "at 10.8%. That is now measured rather than assumed, and the "
        "trade-off is the owner's to make."),
    "the_confound_to_rule_out_first": (
        "above-par pair cost and thin far-side liquidity are the same "
        "condition seen twice: the far side is expensive BECAUSE nobody is "
        "there. So the low fill rate may be the market's shape rather than "
        "our pricing, in which case attempting fewer of them changes nothing "
        "except the count. Separating those needs the authenticated book on "
        "ordinary completes, which is why the instrumentation item below is "
        "not merely bookkeeping."),
    "second_candidate": {
        "name": "record the authenticated book on every COMPLETE, not only "
                "on #93 control rows",
        "classification": "INSTRUMENTATION",
        "why": (
            "the owner asked for distance from the authenticated touch and "
            "the tape cannot answer it at 1.1% coverage. It is the cheapest "
            "item here, it unblocks the question rather than answering it, "
            "and it is what would separate 'our price was wrong' from 'the "
            "far side was empty' above."),
    },
    "third_candidate": {
        "name": "restore TTE resolution to the live state key for sports",
        "classification": "BEHAVIORAL-FIDELITY FIX, blocked on translation",
        "why": (
            "871 of 871 acted decisions sit in one TTE bucket, so the clone "
            "cannot see the axis that is L5's STRONGEST completion predictor "
            "(82.9% to 38.0% across five buckets). Ranked third only because "
            "its effect here is UNKNOWN by construction -- an unmeasured axis "
            "cannot show a measured gain."),
        "the_honest_obstacle": (
            "L5's buckets are fractions of a 5- or 15-minute window; sports "
            "events run for hours, so this is a translation and not a re-cut. "
            "The shadow event-progress translator is where it would be "
            "measured, offline, before anything live."),
    },
}


def render_markdown(rep: Mapping[str, Any]) -> str:
    c = rep["coverage"]
    L = ["# What predicts an accepted COMPLETE filling", "",
         f"**{c['accepted_completes']} accepted COMPLETEs** "
         f"({c['filled']} filled, **{c['fill_rate']:.1%}**). Rejections are "
         "excluded: a rejection is a failure to transmit an order, not a "
         "failure to fill one.", "",
         "## Strata that cannot be computed", ""]
    for k, v in rep["unavailable_strata"].items():
        L += [f"**{k}** — {v}", ""]
    L += ["## Placement features (could inform behaviour)", ""]
    for name, tbl in rep["placement_features"].items():
        L += [f"### {name.replace('_', ' ')}", "",
              "| bucket | n | filled | fill rate |", "|---|---|---|---|"]
        for k, r in tbl.items():
            fr = "—" if r["fill_rate"] is None else f"{r['fill_rate']:.1%}"
            L.append(f"| {k} | {r['n']} | {r['filled']} | {fr} |")
        L.append("")
    L += ["## Realized features (describe, never select)", "",
          rep["realized_means"], ""]
    for name, tbl in rep["realized_features"].items():
        L += [f"### {name.replace('_', ' ')}", "",
              "| bucket | n | filled | fill rate |", "|---|---|---|---|"]
        for k, r in tbl.items():
            fr = "—" if r["fill_rate"] is None else f"{r['fill_rate']:.1%}"
            L.append(f"| {k} | {r['n']} | {r['filled']} | {fr} |")
        L.append("")
    L += ["## Ranked by fill-rate spread", "",
          "| feature | spread |", "|---|---|"]
    for r in rep["ranked_by_spread"]:
        L.append(f"| {r['feature']} | {r['fill_rate_spread']:.1%} |")
    ap = rep["above_par_concentration"]
    L += ["## The dominant split: above par", "", ap["reading"], "",
          f"- L5 for comparison — {ap['l5_for_comparison']}",
          f"- The gate already says this — {ap['the_gate_already_says_this']}",
          f"- What that means, and does not — {ap['what_that_means_and_does_not']}",
          ""]
    rc = rep["requote_crosscheck"]
    L += ["## This corrects my own earlier proposal", "",
          f"**{rc['claim']}** {rc['why']}", "",
          f"*{rc['what_it_does_not_overturn']}*", ""]
    x = rep["l5_comparison"]
    L += ["", "## Against L5's own completion behaviour", "",
          f"- **L5's strongest:** {x['l5_strongest_predictor']}",
          f"- **Ours on that axis:** {x['ours_on_the_same_axis']}",
          f"- **L5's second:** {x['l5_second_predictor']}",
          f"- **Ours on that axis:** {x['ours_on_the_same_axis_2']}", "",
          f"*{x['source']}*", "",
          "## Candidate fidelity correction", ""]
    cd = rep["candidate_fidelity_correction"]
    L += [f"**1. {cd['name']}** — {cd['classification']}", "",
          cd["why_this_one"], "", "Evidence:", ""]
    L += [f"- {e}" for e in cd["evidence"]]
    L += ["", f"**Why fidelity, not strategy.** "
              f"{cd['why_it_is_fidelity_and_not_strategy']}",
          "", f"**What this does not claim.** {cd['what_this_does_not_claim']}",
          "", f"**The confound to rule out first.** "
              f"{cd['the_confound_to_rule_out_first']}", "",
          f"**2. {cd['second_candidate']['name']}** "
          f"({cd['second_candidate']['classification']}) — "
          f"{cd['second_candidate']['why']}", "",
          f"**3. {cd['third_candidate']['name']}** "
          f"({cd['third_candidate']['classification']}) — "
          f"{cd['third_candidate']['why']} "
          f"{cd['third_candidate']['the_honest_obstacle']}", "",
          "## Read-only", "", rep["read_only"]]
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
        with open(os.path.join(a.out, "COMPLETE_FILL_PREDICTORS.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "complete_fill_predictors.json"),
                  "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
