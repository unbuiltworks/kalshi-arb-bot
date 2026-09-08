"""Ranked improvements, each with its evidence and a bounded test.

OWNER item 7: "Identify improvements as either behavioral-fidelity fixes,
execution-layer improvements, or new strategy ideas. Do not quietly turn L5
into generic market making or arbitrage. Return the highest-impact
opportunities ranked by expected improvement in realized dollars per
bankroll-dollar-hour, with evidence and a proposed bounded test for each."

THE COMMON CURRENCY. Every candidate is scored in the same unit: realized
dollars per capital-dollar-hour. Because the measured per-leg economics are
linear in completion rate, one percentage point of leg completion is worth
$0.0677 per dollar-hour, or about $1.67/day at the current turnover. The lane
is 1.10 points below break-even. That single conversion makes otherwise
incomparable proposals comparable, and it is why almost everything below
resolves to the same lever.

WHAT THE CLASSIFICATION IS FOR. The owner drew a line: a fidelity fix makes
the clone execute L5, an execution-layer fix makes it execute the same
decision better, and a new strategy is us inventing something L5 does not
do. The third category is not forbidden, but it must never be smuggled in
wearing the first one's clothes. Each entry below is labelled, and the
labelling is the point rather than decoration -- "reprice to where L5 moved"
is fidelity; "pay another cent to get filled" would be a new strategy, and
they are one tick apart in code and a world apart in kind.

WHAT IS DELIBERATELY RANKED LAST. More bankroll is not an opportunity: mean
capital in use is $1.03 against about $111. Higher concurrency is not
rankable at all yet: its P&L arm is UNVERIFIABLE and, more to the point, the
slope through the current rung is negative, so scaling multiplies the loss.
Both appear at the bottom with those reasons attached rather than being
omitted, because a missing option looks like an oversight.

EVERY EXPECTED VALUE HERE IS AN UPPER BOUND OR A RANGE. None is a forecast.
The evidence supports "this many opportunities existed", never "this many
would have converted", and each entry says which of the two it is.

READ-ONLY. Nothing here is deployed, and no cap, gate, threshold, band or
concurrency setting moves without the owner's explicit go.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

from src import l5_capacity_curve as cc

FIDELITY = "BEHAVIORAL_FIDELITY_FIX"
EXECUTION = "EXECUTION_LAYER_IMPROVEMENT"
NEW_STRATEGY = "NEW_STRATEGY_IDEA"
NOT_AN_OPPORTUNITY = "NOT_AN_OPPORTUNITY_ON_THIS_EVIDENCE"


def usd_per_dollar_hour_per_point() -> float:
    """What one percentage point of leg completion is worth."""
    return round((cc.PAIR_PROFIT_PER_MATCHED_PAIR
                  - cc.RESIDUAL_LOSS_PER_RESIDUAL_UNIT)
                 * cc.legs_per_dollar_hour() / 100.0, 6)


def usd_per_day_per_point() -> float:
    return round(usd_per_dollar_hour_per_point() * cc.DOLLAR_HOURS_PER_DAY, 4)


def points_to_breakeven() -> float:
    return round((cc.breakeven_completion_rate()
                  - cc.OBSERVED_COMPLETION_RATE) * 100, 3)


def ladder(points: tuple = (1.0, 3.0, 6.0, 10.0)) -> List[dict]:
    """The same scenario ladder every entry is scored against."""
    return [{"completion_points_gained": p,
             "usd_per_dollar_hour": round(usd_per_dollar_hour_per_point() * p,
                                          6),
             "usd_per_day": round(usd_per_day_per_point() * p, 2),
             "clears_breakeven": p >= points_to_breakeven()}
            for p in points]


OPPORTUNITIES: List[Dict[str, Any]] = [
    {
        "rank": 1,
        "id": "follow_l5s_touch_while_the_complete_rests",
        "title": "Reprice a resting COMPLETE when L5's own touch moves",
        "classification": FIDELITY,
        "why_that_classification": (
            "L5's touch moved and our clone did not follow it. Following L5's "
            "price is cloning him. Paying up to get filled would be a new "
            "strategy, and this proposal is explicitly not that: the new "
            "price is whatever the model says, never a tick more."),
        "evidence": [
            "THE MECHANISM ALREADY EXISTS AND IS SWITCHED OFF FOR COMPLETE. "
            "The live session re-quotes when L5's touch moves, under "
            "`if relation_ in (\"open\", \"extend\") and touch != px_`. In "
            "the tape 36 OPEN and 35 EXTEND orders exited via "
            "repriced:touch_moved and 0 of 868 COMPLETEs did.",
            "The crossing objection is already handled by an existing guard: "
            "`px >= opp_ask -> would_cross` runs ahead of the touch check for "
            "every relation.",
            "404 accepted-but-unfilled COMPLETEs carry a rest book. In 164 "
            "(40.6%) L5's touch moved above where we sat, and in 96 of those "
            "the opposite ask reached that new touch.",
            "27 of the 35 markets with an unfilled COMPLETE (77%) had at "
            "least one such moment.",
            "Placement is not the defect: we price AT L5's touch in 70.3% of "
            "unfilled completes.",
            "Distance, not patience, separates outcomes: filled completes sit "
            "a median 1 tick from the ask, unfilled 3 ticks (p75 8, max 48).",
            "On L5's own history, 98.5% of his multi-fill completions happen "
            "at more than one price.",
        ],
        "evidence_source": "src/l5_complete_no_fill_forensics.py, "
                           "src/l5_completion_behavior.py",
        "expected_improvement": (
            "UPPER BOUND, not a forecast: 23.8% of unfilled completes had an "
            "L5-consistent reprice the book would have met. Conversion is "
            "unknown -- reaching a price is not filling it, and a re-quote "
            "loses queue position. At 3 points of completion this is "
            "+$0.20/dollar-hour and +$5.02/day; 1.10 points clears "
            "break-even. NOT AN EFFECT SIZE: EXTEND (which reprices) dies at "
            "the deadline 28.5% of the time against COMPLETE's 64.9%, but "
            "that gap is confounded by relation and must not be read as what "
            "the change would deliver."),
        "bounded_test": {
            "change": (
                "add \"complete\" to the relation tuple on the existing "
                "touch_moved re-quote condition -- one condition on a code "
                "path that is already live and already proven on OPEN and "
                "EXTEND. Nothing new is built."),
            "explicitly_not": (
                "no price the model did not return; no crossing; no widening "
                "of any cap"),
            "bounds_unchanged": ["~$2.50 per action", "$7.50 per market",
                                 "concurrency 1", "maker-only / post-only",
                                 "daily loss stop", "the frozen model sha"],
            "new_bound_required": (
                "a per-session cap on reprices, and the existing consecutive-"
                "rejection breaker must cover reprices too -- more orders on "
                "the #93 surface is the risk this carries, and #93 "
                "concentrates on COMPLETE. Repricing also surrenders queue "
                "position, which is a real cost the upper bound ignores: 2% "
                "of unfilled completes had the ask reach our price and still "
                "did not fill."),
            "sample_before_reading": (
                "pre-register it the way the completion extension was: "
                "forward hours, sessions where it fired, settled "
                "venue-confirmed markets"),
            "what_would_make_it_fail": (
                "reprices become rejections (the #93 surface), or completion "
                "rises while pair profit per matched pair falls -- the "
                "capacity curve's half-profit sensitivity moves break-even "
                "from 84.5% to above 93%, so a cheaper fill that costs price "
                "is not a win"),
            "rollback": "revert; it restores prior behaviour and costs "
                        "nothing",
        },
        "blocked_by": (
            "the COMPLETE extension is live under a frozen gate that needs 48 "
            "forward hours and 25 firings. Two changes to the same surface at "
            "once would make neither attributable. This waits for that "
            "verdict."),
    },
    {
        "rank": 2,
        "id": "select_for_time_to_work_and_a_balanced_book",
        "title": "Prefer markets with time left and two live sides",
        "classification": EXECUTION,
        "why_that_classification": (
            "the model still decides what to do; this changes only which "
            "markets are put in front of it. That is selection, not "
            "behaviour -- but it is a real intervention and is not labelled "
            "a fidelity fix, because L5's own selection is not observable."),
        "evidence": [
            "L5 pairs 93.3% of 15-minute windows against 65.7% of 5-minute "
            "ones.",
            "Within a window, 82.9% when he opens with 80-100% of it left "
            "against 38.0% with under 20% left -- monotone across five "
            "buckets.",
            "Balanced books (0.35-0.65) pair 74.6% on 5-minute windows and "
            "97.7% on 15-minute ones; lopsided (<0.20 or >0.80) pair 49.7% "
            "and 33.3%.",
        ],
        "evidence_source": "src/l5_residual_state_replay.py",
        "expected_improvement": (
            "RANGE, and transfer-limited: these magnitudes are measured on "
            "L5's crypto history and our lane trades sports. The mechanisms "
            "-- time to work a position, liquidity on the far side -- are not "
            "crypto-specific, but the sizes are not sports measurements. "
            "Direction is well supported; magnitude is not."),
        "bounded_test": {
            "change": ("add time-to-settlement and book balance to the "
                       "admission filter, shadow-only first"),
            "explicitly_not": ("no change to what the model decides once a "
                               "market is admitted"),
            "bounds_unchanged": ["every money cap", "concurrency 1",
                                 "the frozen model sha"],
            "new_bound_required": (
                "measure the candidate supply cost first: 50% of flat cycles "
                "already admit nothing, and a tighter filter that starves the "
                "lane trades a completion gain for an opportunity loss"),
            "sample_before_reading": (
                "run it as a shadow filter beside the live one and compare "
                "admitted sets before anything is enforced"),
            "what_would_make_it_fail": (
                "admitted-candidate count falls materially, or the sports "
                "transfer does not hold and completion is flat"),
            "rollback": "shadow-only until it earns enforcement",
        },
    },
    {
        "rank": 3,
        "id": "why_the_venue_rejects_non_crossing_completes",
        "title": "Close #93 for EXTEND and COMPLETE",
        "classification": EXECUTION,
        "why_that_classification": (
            "a rejected order is not a behaviour, it is a failure to "
            "transmit one"),
        "evidence": [
            "17.9% of COMPLETEs die by venue rejection, against 68.5% by "
            "no-fill.",
            "OPEN is solved and the fix shipped: the authenticated book "
            "caught real mispricings live.",
            "EXTEND (median +1 tick, 18 rows) and COMPLETE (median +2 ticks, "
            "16 rows) rejections are NON-crossing on the venue's own book, "
            "and both still read NO_CONTROL_YET -- zero accepted controls in "
            "those stages.",
        ],
        "evidence_source": "src/l5_rejection_forensics.py",
        "expected_improvement": (
            "NOT YET QUANTIFIABLE. Without accepted controls in those stages "
            "there is no comparison to make, and the honest answer is that "
            "the size of this is unknown rather than small."),
        "bounded_test": {
            "change": "none yet -- accumulate accepted EXTEND/COMPLETE "
                      "controls until within_stage leaves NO_CONTROL_YET",
            "explicitly_not": "no pricing change on a hypothesis",
            "bounds_unchanged": ["everything"],
            "new_bound_required": None,
            "sample_before_reading": "enough accepted controls in each stage "
                                     "for a matched comparison",
            "what_would_make_it_fail": "n/a -- this is measurement",
            "rollback": "n/a",
        },
    },
    {
        "rank": 4,
        "id": "raise_concurrency",
        "title": "More than one market at a time",
        "classification": NOT_AN_OPPORTUNITY,
        "why_that_classification": (
            "premature rather than wrong. The slope through the current rung "
            "is negative, so scaling multiplies the loss; and the P&L arm is "
            "UNVERIFIABLE because on held cycles the selector never ran, so "
            "the universe a second slot would have drawn from was never "
            "recorded."),
        "evidence": [
            "41.9% of flat selection cycles admitted two or more markets, so "
            "SUPPLY would support a second slot -- but that is supply, not "
            "profit.",
            "At 83.4% completion the lane runs at -$1.84/day. Two slots would "
            "run at roughly twice that.",
            "Stranded residual already consumes 44% of capital-hours while "
            "being 25% of markets.",
        ],
        "evidence_source": "src/l5_capacity_curve.py, "
                           "src/l5_concurrency_counterfactual.py",
        "expected_improvement": (
            "NEGATIVE at the current completion rate. Revisit once realized "
            "dollars per dollar-hour is positive, not before."),
        "bounded_test": {
            "change": "none",
            "explicitly_not": "do not scale a negative unit economic",
            "bounds_unchanged": ["everything"],
            "new_bound_required": None,
            "sample_before_reading": "n/a",
            "what_would_make_it_fail": "n/a",
            "rollback": "n/a",
        },
    },
    {
        "rank": 5,
        "id": "more_bankroll",
        "title": "Add capital",
        "classification": NOT_AN_OPPORTUNITY,
        "why_that_classification": (
            "the money is not what is scarce"),
        "evidence": [
            "Mean capital in use is $1.03 against a bankroll of about $111 "
            "-- under 1%.",
            "The largest single position ever committed $19.31, 17% of it.",
        ],
        "evidence_source": "src/l5_capital_efficiency.py",
        "expected_improvement": "ZERO. More money buys nothing while "
                                "concurrency is 1 and the slope is negative.",
        "bounded_test": {
            "change": "none", "explicitly_not": "do not request funding",
            "bounds_unchanged": ["everything"], "new_bound_required": None,
            "sample_before_reading": "n/a", "what_would_make_it_fail": "n/a",
            "rollback": "n/a",
        },
    },
]


def report() -> dict:
    return {
        "common_currency": {
            "usd_per_dollar_hour_per_completion_point":
                usd_per_dollar_hour_per_point(),
            "usd_per_day_per_completion_point": usd_per_day_per_point(),
            "observed_completion_rate": cc.OBSERVED_COMPLETION_RATE,
            "breakeven_completion_rate": cc.breakeven_completion_rate(),
            "points_to_breakeven": points_to_breakeven(),
            "ladder": ladder(),
            "why_one_unit": (
                "measured per-leg economics are linear in completion rate, so "
                "otherwise incomparable proposals become comparable -- and it "
                "is why almost everything resolves to the same lever"),
        },
        "opportunities": OPPORTUNITIES,
        "classification_key": {
            FIDELITY: "makes the clone execute L5",
            EXECUTION: "executes the same decision better",
            NEW_STRATEGY: "something L5 does not do",
            NOT_AN_OPPORTUNITY: "kept in the list with its reason attached, "
                                "because a missing option looks like an "
                                "oversight",
        },
        "the_line_that_must_not_be_crossed": (
            "'Reprice to where L5 moved' is fidelity. 'Pay another cent to "
            "get filled' is a new strategy. They are one tick apart in code "
            "and a world apart in kind, and no proposal here may drift from "
            "the first into the second."),
        "every_number_is_a_bound": (
            "the evidence supports 'this many opportunities existed', never "
            "'this many would have converted'. Each entry says which it is."),
        "read_only": (
            "Nothing here is deployed. No cap, gate, threshold, band or "
            "concurrency setting moves without the owner's explicit go, and "
            "the frozen COMPLETE experiment is untouched."),
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    c = rep["common_currency"]
    L = ["# Ranked improvements", "",
         f"One percentage point of leg completion is worth "
         f"**${c['usd_per_dollar_hour_per_completion_point']} per "
         f"dollar-hour** (~**${c['usd_per_day_per_completion_point']}/day**). "
         f"Observed completion **{c['observed_completion_rate']:.2%}**, "
         f"break-even **{c['breakeven_completion_rate']:.2%}**, gap "
         f"**{c['points_to_breakeven']:+.2f} points**.", "",
         "| completion points gained | $/dollar-hour | $/day | clears "
         "break-even |", "|---|---|---|---|"]
    for r in c["ladder"]:
        L.append(f"| +{r['completion_points_gained']:.0f} | "
                 f"{r['usd_per_dollar_hour']:+.4f} | {r['usd_per_day']:+.2f} "
                 f"| {'yes' if r['clears_breakeven'] else 'no'} |")
    L += ["", "> " + c["why_one_unit"], "",
          "> " + rep["the_line_that_must_not_be_crossed"], ""]
    for o in rep["opportunities"]:
        L += [f"## {o['rank']}. {o['title']}", "",
              f"**{o['classification']}** — {o['why_that_classification']}", "",
              "**Evidence**", ""]
        L += [f"- {e}" for e in o["evidence"]]
        L += ["", f"*Source: {o['evidence_source']}*", "",
              f"**Expected improvement.** {o['expected_improvement']}", ""]
        if o.get("blocked_by"):
            L += [f"**Blocked by.** {o['blocked_by']}", ""]
        t = o["bounded_test"]
        L += ["**Bounded test**", "",
              f"- change — {t['change']}",
              f"- explicitly NOT — {t['explicitly_not']}",
              f"- unchanged — {', '.join(t['bounds_unchanged'])}"]
        if t.get("new_bound_required"):
            L.append(f"- new bound required — {t['new_bound_required']}")
        L += [f"- sample before reading — {t['sample_before_reading']}",
              f"- what would make it fail — {t['what_would_make_it_fail']}",
              f"- rollback — {t['rollback']}", ""]
    L += ["## Every number here is a bound", "", rep["every_number_is_a_bound"],
          "", "## Read-only", "", rep["read_only"]]
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = report()
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "IMPROVEMENT_RANKING.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "improvement_ranking.json"), "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
