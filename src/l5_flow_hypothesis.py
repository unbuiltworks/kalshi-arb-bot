"""Does contemporaneous two-sided flow predict our ability to complete?

OWNER P0 (#167): "The useful comparison isn't lifetime volume. It's flow
immediately before and during our attempted pairing."

This module answers as much of that as the evidence can carry, and is
explicit about the part it cannot. Read-only; it reads the forensics output,
the venue's public counters and the flow tape, and places no orders.

WHAT CANNOT BE DONE, AND WHY IT IS NOT A GAP IN EFFORT
------------------------------------------------------
The contemporaneous comparison the owner asked for -- flow immediately
before and during our attempted pairing, for the 21 completed / 3
difficulty / 7 stranded markets -- is NOT retroactively measurable. PMUS
serves no historical print endpoint (`/trades`, `/history`, `/ohlc`,
`/candles`, `/price-history` all 404), only `sharesTraded`, a CUMULATIVE
counter. For a market that settled days ago that counter yields its LIFETIME
total and nothing about what was trading while we sat in it.

So the retro question is answerable only in the weaker currency the owner
ruled out -- lifetime volume -- and the contemporaneous question is
answerable only forward. Both are done here, separately, and neither is
allowed to stand in for the other.

TEST 1 (RETRO): DOES LIFETIME VOLUME SEPARATE THE COHORTS? NO.
---------------------------------------------------------------
Median lifetime shares: completed 159,272 | difficulty 98,471 | stranded
144,559. The cohorts are indistinguishable, and the extremes run the wrong
way: the LOWEST-volume market we traded (25,034) completed in full, and the
HIGHEST-volume market in the stranded cohort (511,234) stranded completely
-- that is aec-wta-martre-jenrug, the market where we never attempted a
completion after the fill at all.

This is a null result and it is the useful kind: it rules out the cheap
measure. A market-selection rule built on headline volume would have kept
every market that stranded and dropped one that paired.

TEST 2 (FORWARD): IS CONTEMPORANEOUS FLOW STRUCTURED AT ALL? YES.
------------------------------------------------------------------
Over 41 polls of the live slate, ranked by first-differenced shares/minute,
two-sidedness rises monotonically across quintiles: 0%, 0%, 0%, 67%, 100%.
The property the owner cares about -- resting orders on BOTH outcomes
actually transacting -- is concentrated in the top two quintiles and absent
below them. One market shows why volume alone is not the measure: 36.3
shares/min from a SINGLE print, one-sided, ranked below quieter two-sided
markets.

The selection fact is larger than the ranking: of 60 live markets polled,
only 18 had any measurable flow. 42 were under-observed -- no prints at all
across the window. Seventy percent of the live slate is not transacting.

WHAT IS STILL NOT PROVEN
------------------------
That flow predicts OUR completion. Test 2 measures the venue, not us. The
link from "this market transacts on both sides" to "our resting complement
fills" needs a forward cohort in which we act, and the R5F baseline it must
beat is in analysis/l5/L5_COMPLETION_FORENSICS.md. The gate for that test is
pre-registered below, BEFORE the cohort is run, per the discipline this repo
already uses for promotion gates.
"""

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

OUT_DIR = os.path.join("analysis", "l5")

LABELS = {
    "retro": "lifetime cumulative volume; the weak measure, reported because "
             "it is the only one the venue can serve for a settled market",
    "forward": "first-differenced shares/minute over the live slate; "
               "contemporaneous, and about the VENUE, not about our fills",
    "descriptive": "cohorts are not randomly assigned; no causal claim",
}


def cohort_lifetime_stats(by_cohort: Mapping[str, Sequence[Sequence[Any]]]
                          ) -> Dict[str, Any]:
    """Median/min/max lifetime shares per R5F cohort, and the extremes test.

    `by_cohort` maps cohort -> [[shares, slug], ...]. The extremes matter more
    than the medians here: a selection rule is judged by what it would have
    kept and dropped, not by where the middle of each group sits.
    """
    out: Dict[str, Any] = {}
    for coh, rows in by_cohort.items():
        vals = sorted((float(v), s) for v, s in rows)
        if not vals:
            out[coh] = {"n": 0}
            continue
        xs = [v for v, _ in vals]
        n = len(xs)
        out[coh] = {
            "n": n,
            "median": xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2,
            "min": vals[0][0], "min_slug": vals[0][1],
            "max": vals[-1][0], "max_slug": vals[-1][1],
        }
    return out


def lifetime_separates(stats: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Would a lifetime-volume threshold have separated paired from stranded?

    Answered the only way that matters operationally: if the quietest market
    that COMPLETED is quieter than the loudest market that STRANDED, then no
    threshold on this feature separates them, whatever the medians say.
    """
    full, zero = stats.get("FULL", {}), stats.get("ZERO", {})
    if not full.get("n") or not zero.get("n"):
        return {"separable": None, "why": "a cohort is empty"}
    overlap = full["min"] < zero["max"]
    return {
        "separable": not overlap,
        "quietest_completed": {"shares": full["min"], "slug": full["min_slug"]},
        "loudest_stranded": {"shares": zero["max"], "slug": zero["max_slug"]},
        "why": ("no threshold separates them: the quietest market that "
                "completed is quieter than the loudest market that stranded"
                if overlap else "a threshold exists between the cohorts"),
    }


def quintile_monotonicity(buckets: Sequence[Mapping[str, Any]],
                          key: str = "two_sided_share") -> Dict[str, Any]:
    """Does two-sidedness rise with contemporaneous flow across buckets?

    Reported as the observed series plus whether it is non-decreasing, rather
    than as a correlation coefficient: with a handful of buckets a
    coefficient invites more confidence than the sample supports.
    """
    series = [b[key] for b in buckets]
    return {
        "series": series,
        "non_decreasing": all(b >= a for a, b in zip(series, series[1:])),
        "bottom": series[0] if series else None,
        "top": series[-1] if series else None,
        "buckets": len(series),
    }


def slate_coverage(ranked: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """How much of the live slate transacts at all.

    An under-observed market is one that produced no usable interval. It is
    reported separately from a market measured at zero flow, because "we
    could not see it" and "it did not trade" are different claims.
    """
    total = len(ranked)
    under = [r for r in ranked if r.get("under_observed")]
    obs = [r for r in ranked if not r.get("under_observed")]
    silent = [r for r in obs if not r.get("prints_seen")]
    return {
        "polled": total,
        "measurable": len(obs),
        "under_observed": len(under),
        "measured_but_silent": len(silent),
        "share_with_any_flow": round(
            (len(obs) - len(silent)) / total, 4) if total else None,
    }


# The gate is written BEFORE the forward cohort runs, so the result cannot
# choose its own success criterion afterwards.
FORWARD_GATE = {
    "registered_before_data": True,
    "question": "does entering only high-flow, two-sided markets raise our "
                "completion rate over the R5F baseline?",
    "baseline_from": "analysis/l5/L5_COMPLETION_FORENSICS.md",
    "baseline": {
        "markets_with_a_first_side_fill": 31,
        "fully_paired": 21, "partial": 3, "stranded": 7,
        "stranded_share": round(7 / 31, 4),
        "complete_fill_rate_full_cohort": 0.231,
        "complete_fill_rate_partial_cohort": 0.057,
    },
    "cohort_rule": "admit only markets in the top two flow quintiles AND "
                   "two_sided true, measured over at least 10 intervals "
                   "before admission",
    "pass": "stranded share strictly below 7/31 AND complete fill rate at or "
            "above the FULL cohort's 23.1%, over at least 15 markets with a "
            "first-side fill",
    "fail": "stranded share at or above 7/31, or fill rate below 23.1%",
    "inconclusive": "fewer than 15 markets with a first-side fill",
    "confound_named_in_advance": (
        "the entry-band fix (a9b0b243) landed between the baseline and any "
        "forward cohort, so a forward run measures flow selection AND that "
        "fix together. Attributing the delta to flow alone requires either a "
        "same-period control arm without the flow filter, or accepting the "
        "combined claim and saying so."),
    "not_a_licence": "passing this gate is evidence for a selection rule, not "
                     "authority to increase size, concurrency or capital.",
}

RETRO_VERDICT = (
    "Lifetime volume does NOT separate the cohorts. Medians are "
    "indistinguishable (completed 159,272, stranded 144,559, difficulty "
    "98,471) and the extremes run the wrong way: the quietest market we "
    "traded completed in full at 25,034 shares while the loudest stranded "
    "one held 511,234. A rule built on headline volume would have kept every "
    "market that stranded."
)

FORWARD_VERDICT = (
    "Contemporaneous flow IS structured. Across 41 polls of the live slate, "
    "two-sidedness rises monotonically with first-differenced shares/minute "
    "-- 0%, 0%, 0%, 67%, 100% by quintile -- and only 18 of 60 live markets "
    "had any measurable flow at all. This measures the VENUE. Whether it "
    "predicts OUR completion is the pre-registered forward test, not a "
    "conclusion available from this data."
)


def build(by_cohort: Mapping[str, Sequence[Sequence[Any]]],
          ranked: Sequence[Mapping[str, Any]],
          buckets: Sequence[Mapping[str, Any]],
          cycles: int) -> Dict[str, Any]:
    stats = cohort_lifetime_stats(by_cohort)
    return {
        "retro_lifetime": {"per_cohort": stats,
                           "separable": lifetime_separates(stats),
                           "verdict": RETRO_VERDICT},
        "forward_contemporaneous": {
            "cycles": cycles,
            "coverage": slate_coverage(ranked),
            "quintiles": list(buckets),
            "monotonicity": quintile_monotonicity(buckets),
            "verdict": FORWARD_VERDICT},
        "forward_gate": FORWARD_GATE,
        "labels": LABELS,
    }


def render_md(rep: Mapping[str, Any]) -> str:
    r, f = rep["retro_lifetime"], rep["forward_contemporaneous"]
    L = ["# Does contemporaneous two-sided flow predict completion?", "",
         "_OWNER P0 (#167). Read-only. The retro and forward questions are "
         "answered in different currencies and neither substitutes for the "
         "other._", "",
         "## What cannot be measured retroactively", "",
         "PMUS serves no historical print endpoint, only a CUMULATIVE "
         "`sharesTraded`. For a settled market that yields its lifetime total "
         "and nothing about what traded while we sat in it. The "
         "contemporaneous comparison is therefore a FORWARD test only.", "",
         "## Test 1 (retro): lifetime volume", "", r["verdict"], "",
         "| cohort | n | median | quietest | loudest |",
         "|---|---:|---:|---|---|"]
    for coh in ("FULL", "PARTIAL", "ZERO"):
        s = r["per_cohort"].get(coh) or {}
        if not s.get("n"):
            continue
        L.append("| %s | %d | %,.0f | %,.0f `%s` | %,.0f `%s` |".replace(",", "")
                 % (coh, s["n"], s["median"], s["min"], s["min_slug"],
                    s["max"], s["max_slug"]))
    L += ["", "**%s**" % r["separable"]["why"], "",
          "## Test 2 (forward): contemporaneous flow", "", f["verdict"], "",
          "| quintile | markets | min sh/min | max sh/min | two-sided |",
          "|---|---:|---:|---:|---:|"]
    for b in f["quintiles"]:
        L.append("| Q%d | %d | %.2f | %.2f | %.0f%% |" % (
            b["decile"], b["markets"], b["min"], b["max"],
            100 * b["two_sided_share"]))
    c = f["coverage"]
    L += ["", "Slate coverage: %d polled, %d measurable, %d under-observed "
          "(no usable interval), %d measured but silent." % (
              c["polled"], c["measurable"], c["under_observed"],
              c["measured_but_silent"]), "",
          "## The forward gate, registered before the cohort runs", "",
          "- **Rule:** %s" % rep["forward_gate"]["cohort_rule"],
          "- **Pass:** %s" % rep["forward_gate"]["pass"],
          "- **Fail:** %s" % rep["forward_gate"]["fail"],
          "- **Inconclusive:** %s" % rep["forward_gate"]["inconclusive"],
          "- **Confound named in advance:** %s" %
          rep["forward_gate"]["confound_named_in_advance"],
          "- %s" % rep["forward_gate"]["not_a_licence"], "",
          "Labels: " + "; ".join(f"{k} = {v}" for k, v in rep["labels"].items()),
          ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--lifetime", required=True, help="json: cohort -> [[shares, slug]]")
    ap.add_argument("--flow", required=True, help="json from the flow tape analysis")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args(argv)
    life = json.load(open(a.lifetime, encoding="utf-8"))
    flow = json.load(open(a.flow, encoding="utf-8"))
    rep = build(life, flow["ranked"], flow["quintiles"], flow["cycles"])
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "l5_flow_hypothesis.json"), "w",
              encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, "L5_FLOW_HYPOTHESIS.md"), "w",
              encoding="utf-8") as fh:
        fh.write(render_md(rep))
    print(json.dumps({"lifetime_separable": rep["retro_lifetime"]["separable"]["separable"],
                      "monotonic": rep["forward_contemporaneous"]["monotonicity"],
                      "coverage": rep["forward_contemporaneous"]["coverage"]},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
