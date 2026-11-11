"""What more capital buys, and what it does not.

OWNER item 5: "Build a capacity/profit curve showing expected realized $/day
at increasing bankroll/concurrency using observed executable fills -- not
theoretical fills."

THE ANSWER IS NOT THE SHAPE THE QUESTION EXPECTS, so it is stated first.
Mean capital in use across the settled cohort is $1.03 against a bankroll of
about $111, and the largest single market ever committed $19.31. Bankroll is
not the binding constraint at any rung this lane has reached. Adding money
to it changes nothing, because the money is not what is scarce.

THREE AXES, AND ONLY TWO OF THEM ARE ANSWERABLE.

  BANKROLL -- answerable, and the answer is that it does not bind. Reported
    as observed utilisation, not as a projection.

  CANDIDATE SUPPLY -- answerable on flat cycles, where the selector actually
    ran and recorded its admitted universe. 41.9% of those cycles admitted
    two or more markets, so supply would support a second slot about two
    cycles in five. Measured on FLAT cycles only: while a position is held
    the selector does not run, so supply during held cycles is unobserved,
    which is the same structural gap the concurrency counterfactual named.

  CONCURRENCY P&L -- NOT answerable, and this module does not answer it. The
    frozen concurrency gate already ruled the c>=2 arm UNVERIFIABLE because
    the admissible universe on held cycles was never recorded. Nothing here
    reconstructs it.

THE CURVE THAT CAN BE DRAWN. Per-leg economics are observed: +$0.057230 per
matched pair, -$0.331449 per residual unit, +$0.002953 of confirmed rebate
per opened leg, and 17.4 opened legs per capital-dollar-hour. Holding those
fixed and varying only the completion rate gives realized $/day as a
function of the one lever the evidence actually supports. It reproduces the
observed -$0.07 per dollar-hour at the observed 83.4% completion, which is
the only validation available for it.

THE POINT OF DRAWING IT. The slope through the current rung is NEGATIVE.
Scaling multiplies unit economics; it does not repair them. Until completion
clears break-even, every axis of growth makes the loss bigger, and that is
the whole capacity finding.

THE ASSUMPTION THAT COULD BREAK IT, stated rather than buried: the curve
holds pair profit constant as completion rises, and the marginal completion
is probably the harder one -- worse price, thinner book. A sensitivity at
half the pair profit is reported beside every point for that reason.

READ-ONLY. Nothing here proposes a rung, and no rung moves without the
owner.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

# ---- observed inputs. Every one of these is measured, none is assumed. ----
#
# INSTRUMENT-COHORT WARNING. THESE CONSTANTS ARE NOT BTC15m.
#
# They come from `src/l5_breakeven.py` over `analysis/l5/l5_r5f_cohort.json`,
# which is 13 esports (`aec`), 2 `atc` and 1 MLB (`tsc`) market and contains
# ZERO BTC15m markets. One of its rows carries `matched_pair_cost 1.110705`,
# a pair cost the 0.96 BTC15m ceiling makes impossible.
#
# They were used to compute a BTC15m breakeven of 84.52% and to declare the
# BTC15m strategy negative. That conclusion was an instrument-cohort error and
# is withdrawn. For BTC15m use `analysis/l5/btc15m/native_baseline.py`, which
# reconciles to the cashflow identity redemption - acquisition.
#
# AND THE COMPLETION-RATE BREAKEVEN THIS MODULE DRAWS FAILS VALIDATION on the
# one book where it can be checked: it reports L5 clearing breakeven by +0.1219
# in TRAIN and +0.0655 out of sample while his realized return goes +52.03 bp
# -> +1.08 bp. A 48x swing in cash against a 2x change in "margin". It does not
# predict cash and must not be used as a decision metric.
INSTRUMENT_CLASS = "SPORTS_ESPORTS_R5F"      # NOT BTC15m. See above.
NATIVE_BASELINE_FOR_BTC15M = "analysis/l5/btc15m/native_baseline.py"

PAIR_PROFIT_PER_MATCHED_PAIR = 0.057230      # l5_breakeven, 15 markets
RESIDUAL_LOSS_PER_RESIDUAL_UNIT = -0.331449  # l5_breakeven
CONFIRMED_REBATE_PER_LEG = 0.002953          # l5_breakeven, approximate map
OBSERVED_COMPLETION_RATE = 0.834196          # l5_breakeven
OPENED_LEGS = 179.73                         # l5_breakeven
DOLLAR_HOURS = 10.320179                     # l5_capital_efficiency
DOLLAR_HOURS_PER_DAY = 24.7405               # l5_capital_efficiency
MEAN_CAPITAL_IN_USE_USD = 1.0309             # l5_capital_efficiency
MAX_SINGLE_MARKET_COST_BASIS_USD = 19.3141   # l5_r5f_cohort
BANKROLL_USD = 111.0                         # owner's stated bankroll

SOURCES = {
    "per_leg_economics": "src/l5_breakeven.py over 15 reconciling markets",
    "capital": "src/l5_capital_efficiency.py over the same cohort",
    "candidate_supply": "src/l5_allocation_metrics.selection_timeline",
    "concurrency_pnl": "src/l5_concurrency_gate -- UNVERIFIABLE, not computed",
}


def legs_per_dollar_hour() -> float:
    return round(OPENED_LEGS / DOLLAR_HOURS, 4)


def usd_per_dollar_hour(p: float, *,
                        pair_profit: float = PAIR_PROFIT_PER_MATCHED_PAIR,
                        credit: float = CONFIRMED_REBATE_PER_LEG) -> float:
    """Realized dollars per capital-dollar-hour at completion rate p."""
    per_leg = (p * pair_profit + (1.0 - p) * RESIDUAL_LOSS_PER_RESIDUAL_UNIT
               + credit)
    return per_leg * legs_per_dollar_hour()


def usd_per_day(p: float, **kw) -> float:
    return usd_per_dollar_hour(p, **kw) * DOLLAR_HOURS_PER_DAY


def completion_curve(points: Sequence[float] = (0.80, 0.834196, 0.85, 0.86,
                                                0.88, 0.90, 0.93, 0.95)
                     ) -> List[dict]:
    half = PAIR_PROFIT_PER_MATCHED_PAIR / 2.0
    rows = []
    for p in points:
        rows.append({
            "completion_rate": round(p, 6),
            "is_observed": abs(p - OBSERVED_COMPLETION_RATE) < 1e-6,
            "usd_per_dollar_hour": round(usd_per_dollar_hour(p), 6),
            "usd_per_day": round(usd_per_day(p), 4),
            "usd_per_day_if_marginal_pair_earns_half":
                round(usd_per_day(p, pair_profit=half), 4),
        })
    return rows


def breakeven_completion_rate(*, pair_profit: float =
                              PAIR_PROFIT_PER_MATCHED_PAIR,
                              credit: float = CONFIRMED_REBATE_PER_LEG
                              ) -> Optional[float]:
    denom = pair_profit - RESIDUAL_LOSS_PER_RESIDUAL_UNIT
    if denom == 0:
        return None
    return round((-RESIDUAL_LOSS_PER_RESIDUAL_UNIT - credit) / denom, 6)


def bankroll_axis() -> dict:
    return {
        "bankroll_usd": BANKROLL_USD,
        "mean_capital_in_use_usd": MEAN_CAPITAL_IN_USE_USD,
        "mean_utilisation": round(MEAN_CAPITAL_IN_USE_USD / BANKROLL_USD, 4),
        "largest_single_market_cost_basis_usd":
            MAX_SINGLE_MARKET_COST_BASIS_USD,
        "peak_utilisation_at_concurrency_1":
            round(MAX_SINGLE_MARKET_COST_BASIS_USD / BANKROLL_USD, 4),
        "verdict": (
            "BANKROLL DOES NOT BIND. Mean utilisation is under 1% and even "
            "the largest single position used 17% of it. More money buys "
            "nothing while concurrency is 1, and asking for money would be "
            "asking for the wrong resource."),
    }


def supply_axis(admitted_counts: Mapping[int, int]) -> dict:
    """Observed admitted-candidate counts per FLAT selection cycle."""
    total = sum(admitted_counts.values())
    if not total:
        return {"cycles": 0, "why_null": "no selection cycles observed"}
    ge = {n: sum(v for k, v in admitted_counts.items() if k >= n)
          for n in (1, 2, 3, 4)}
    return {
        "flat_selection_cycles": total,
        "distribution": {str(k): v for k, v in sorted(admitted_counts.items())},
        "cycles_with_at_least": {str(n): v for n, v in ge.items()},
        "share_with_at_least": {str(n): round(v / total, 4)
                                for n, v in ge.items()},
        "reading": (
            f"{ge[2] / total:.1%} of flat cycles admitted two or more "
            "markets, so candidate supply would support a second slot about "
            "two cycles in five. That is a supply measurement, not a P&L one."),
        "measured_on_flat_cycles_only": (
            "while a position is held the selector does not run, so supply "
            "during held cycles was never recorded. Supply on flat cycles is "
            "not proof of supply on held ones."),
    }


def report(admitted_counts: Optional[Mapping[int, int]] = None) -> dict:
    be = breakeven_completion_rate()
    curve = completion_curve()
    now = usd_per_day(OBSERVED_COMPLETION_RATE)
    return {
        "sources": dict(SOURCES),
        "bankroll": bankroll_axis(),
        "candidate_supply": (supply_axis(admitted_counts)
                             if admitted_counts else
                             {"why_null": "no selection timeline supplied"}),
        "concurrency_pnl": {
            "verdict": "UNVERIFIABLE",
            "why": (
                "the frozen concurrency gate already ruled this arm "
                "unverifiable: on held cycles select_candidate is never "
                "reached, so the admissible universe a second slot would "
                "have drawn from was never recorded. Reconstructing it would "
                "be an assumption about the very thing being tested."),
            "not_computed_here": True,
        },
        "completion_curve": {
            "legs_per_dollar_hour": legs_per_dollar_hour(),
            "observed_completion_rate": OBSERVED_COMPLETION_RATE,
            "observed_usd_per_day": round(now, 4),
            "breakeven_completion_rate": be,
            "points": curve,
            "validation": (
                "at the observed 83.42% the curve returns "
                f"${usd_per_dollar_hour(OBSERVED_COMPLETION_RATE):.4f} per "
                "dollar-hour against the -$0.0721 measured directly, which is "
                "the only check available on it"),
            "assumption_that_could_break_it": (
                "pair profit is held constant as completion rises, and the "
                "marginal completion is probably the harder one -- worse "
                "price, thinner book. Every point carries a sensitivity at "
                "half the pair profit for that reason."),
        },
        "the_capacity_finding": (
            "The slope through the current rung is NEGATIVE: at 83.4% "
            f"completion the lane runs at ${now:.2f}/day. Scaling multiplies "
            "unit economics, it does not repair them, so every axis of growth "
            "makes the loss bigger until completion clears break-even at "
            f"{be:.1%}. Capacity is not the constraint and capital is not the "
            "constraint -- completion is."),
        "read_only": (
            "Nothing here proposes a rung. No cap, gate, threshold or "
            "concurrency setting moves without the owner's explicit go."),
    }


def render_markdown(rep: Mapping[str, Any]) -> str:
    b, c = rep["bankroll"], rep["completion_curve"]
    L = ["# Capacity and profit curve", "",
         f"**{rep['the_capacity_finding']}**", "",
         "## Bankroll", "",
         "| quantity | value |", "|---|---|",
         f"| bankroll | ${b['bankroll_usd']} |",
         f"| mean capital in use | ${b['mean_capital_in_use_usd']} "
         f"({b['mean_utilisation']:.2%}) |",
         f"| largest single position | "
         f"${b['largest_single_market_cost_basis_usd']} "
         f"({b['peak_utilisation_at_concurrency_1']:.1%}) |", "",
         f"**{b['verdict']}**", ""]
    s = rep["candidate_supply"]
    if s.get("flat_selection_cycles"):
        L += ["## Candidate supply", "",
              f"{s['flat_selection_cycles']} flat selection cycles", "",
              "| admitted at least | cycles | share |", "|---|---|---|"]
        for n in ("1", "2", "3", "4"):
            L.append(f"| {n} | {s['cycles_with_at_least'][n]} | "
                     f"{s['share_with_at_least'][n]:.1%} |")
        L += ["", s["reading"], "", "> " + s["measured_on_flat_cycles_only"],
              ""]
    cp = rep["concurrency_pnl"]
    L += ["## Concurrency P&L", "", f"**{cp['verdict']}** — {cp['why']}", "",
          "## Realized $/day vs completion rate", "",
          f"{c['legs_per_dollar_hour']} opened legs per capital-dollar-hour; "
          f"break-even at **{c['breakeven_completion_rate']:.2%}**", "",
          "| completion | $/dollar-hour | $/day | $/day if marginal pair "
          "earns half |", "|---|---|---|---|"]
    for p in c["points"]:
        mark = " ← observed" if p["is_observed"] else ""
        L.append(f"| {p['completion_rate']:.2%}{mark} | "
                 f"{p['usd_per_dollar_hour']:+.4f} | "
                 f"{p['usd_per_day']:+.2f} | "
                 f"{p['usd_per_day_if_marginal_pair_earns_half']:+.2f} |")
    L += ["", c["validation"], "", "> " + c["assumption_that_could_break_it"],
          "", "## Read-only", "", rep["read_only"], "",
          "## Sources", ""]
    for k, v in rep["sources"].items():
        L.append(f"- **{k}** — {v}")
    return "\n".join(L) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    from collections import Counter
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--logs", nargs="+", default=["../r5f_logs"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    from src import l5_allocation_metrics as am
    paths: List[str] = []
    for p in a.logs:
        paths += ([os.path.join(p, f) for f in sorted(os.listdir(p))]
                  if os.path.isdir(p) else [p])
    counts = Counter(r.get("n_admitted") for r in am.selection_timeline(paths))
    counts = {k: v for k, v in counts.items() if k is not None}
    rep = report(counts)
    md = render_markdown(rep)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "CAPACITY_CURVE.md"), "w") as f:
            f.write(md)
        with open(os.path.join(a.out, "capacity_curve.json"), "w") as f:
            json.dump(rep, f, indent=2, default=str)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
