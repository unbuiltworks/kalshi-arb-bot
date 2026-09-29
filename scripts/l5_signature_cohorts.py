"""L5's high- vs low-completion signature, as the target our clone must hit.

The 501-window L5 history is the reference distribution. This reduces it to
the persistence/frequency quantities the shadow runner emits, split by
completion tercile, so a shadow window can be scored against a cohort rather
than against a hope.

WHAT IS COMPARABLE AND WHAT IS NOT, stated plainly:
  * actions/min, median dt_prev, median matched, median residual, streaks,
    last-action seconds-to-expiry, extend/rebalance fractions -- comparable,
    same arithmetic both sides via l5_persistence_signature;
  * NO-INTENT TICK FRACTION IS NOT COMPARABLE. L5's public history is a FILL
    tape: a tick on which L5 considered and declined is invisible in it. Our
    no_intent fraction has no L5 counterpart and is reported as a
    clone-internal diagnostic only. Comparing them would be comparing our
    decisions against L5's executions.
  * MAKER/TAKER is absent from the L5 dataset and is not proxied.

Read-only. No parameters, no fitting.
"""
from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional

import scripts.l5_completion_behavior as B

#: L5 acts in shares; our clip-10 clone acts in clips. Action COUNTS and
#: TIMINGS compare directly; share quantities do not, and are reported in
#: both units rather than rescaled.
WINDOW_S = 900.0


def cohorts() -> Dict[str, Any]:
    recs = B.build()["windows"]
    recs.sort(key=lambda r: r["first_t"])
    comp = sorted(r["out_completion"] for r in recs)
    hi = comp[2 * len(comp) // 3]
    lo = comp[len(comp) // 3]
    good = [r for r in recs if r["out_completion"] >= hi]
    bad = [r for r in recs if r["out_completion"] <= lo]

    def summarise(rows: List[dict], name: str) -> Dict[str, Any]:
        def med(key):
            v = [r[key] for r in rows if r.get(key) is not None]
            return round(statistics.median(v), 4) if v else None

        def pct(key, p):
            v = sorted(r[key] for r in rows if r.get(key) is not None)
            return round(v[int(p * len(v))], 4) if v else None
        return {
            "cohort": name, "n": len(rows),
            "completion_median": med("out_completion"),
            "residual_frac_median": med("out_residual_frac"),
            # ---- the signature quantities
            "actions_per_min_median": (
                round(statistics.median([r["n_actions"] * 60.0 / WINDOW_S
                                         for r in rows]), 4) if rows else None),
            "n_actions_median": med("n_actions"),
            "n_actions_p10": pct("n_actions", 0.10),
            "n_actions_p90": pct("n_actions", 0.90),
            "median_dt_prev_median": med("median_dt_prev"),
            "median_matched_median": med("median_matched"),
            "median_abs_imbalance_median": med("median_abs_imbalance"),
            "last_action_s_to_expiry_median": med("median_s_to_expiry"),
            "extend_frac_median": med("extend_frac"),
            "rebalance_frac_median": med("rebalance_frac"),
            "max_streak_median": med("max_streak"),
            "same_price_frac_median": med("same_price_frac"),
        }

    return {
        "source": "analysis/l5/l5_window_sequences.csv, BTC 15m",
        "windows": len(recs),
        "tercile_cuts": {"high_completion_at_or_above": round(hi, 4),
                         "low_completion_at_or_below": round(lo, 4)},
        "high": summarise(good, "HIGH_COMPLETION"),
        "low": summarise(bad, "LOW_COMPLETION"),
        "all": summarise(recs, "ALL"),
        "not_comparable": {
            "no_intent_tick_frac": ("L5's public history is a FILL tape; a "
                                    "tick L5 declined is invisible. Our "
                                    "figure is a clone-internal diagnostic."),
            "maker_taker": "absent from the L5 dataset; not proxied",
            "share_quantities": ("L5 sizes freely; our clip is fixed at 10. "
                                 "Counts and timings compare, sizes do not."),
        },
    }


if __name__ == "__main__":
    print(json.dumps(cohorts(), indent=2, default=str))
