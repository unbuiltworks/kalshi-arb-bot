"""P36 -- the faithful L5 engine against the preserved realtime evidence.

Runs the three-layer engine over the five authenticated windows and prints
the eleven-point fidelity report, plus the same run with SAFETY_OVERLAY
disabled so the venue's own limit is visible separately from ours, plus the
frozen q1/maker-only baseline for comparison.

Read-only. The running observer and the authenticated evidence collection are
untouched.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from decimal import Decimal as D
from typing import Any, Dict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from analysis.pmus import p34_amplitude_report as P34        # noqa: E402
from analysis.pmus import p35_translation_tracks as P35      # noqa: E402
from src import l5_execution_translations as X               # noqa: E402
from src import l5_faithful_translation as F                 # noqa: E402
from src import l5_fidelity_report as R                      # noqa: E402

EVID = "analysis/pmus/evidence/observer_rows_settled_windows.jsonl"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    rows = [json.loads(l) for l in open(EVID) if l.strip()]
    rows = [r for r in rows
            if r.get("kind") == "l5_action" and not r.get("backlog")]
    auth = P34.authenticated_map(rows)
    lam = X.lambda_ref(rows)
    sett = P35.settlements()

    by_w = collections.defaultdict(list)
    for r in rows:
        by_w[r["window_start_utc"][11:16]].append(r)

    VENUE_ONLY = F.SafetyLimits(enabled=False)
    WITH_OVERLAY = F.SafetyLimits()

    out: Dict[str, Any] = {
        "label": "L5_FAITHFUL_FIDELITY_V1",
        "phase": "PHASE_1_SHADOW_ONLY",
        "lambda_ref": str(lam),
        "translation_rule": ("parameter-free: reproduce every L5 action by "
                             "the closest executable means at our receipt "
                             "instant and MEASURE the cost. No threshold, "
                             "band, ceiling or discretionary branch exists "
                             "in it, so nothing in it can be tuned to these "
                             "windows."),
        "windows": {},
    }
    agg = collections.Counter()
    aggv = collections.Counter()
    print("lambda = %s   (median L5 action -> 1 share)\n" % lam)

    for w, rs in sorted(by_w.items()):
        yv = sett.get(w)
        venue = F.run_window(rs, auth, lam=lam, limits=VENUE_ONLY)
        full = F.run_window(rs, auth, lam=lam, limits=WITH_OVERLAY)
        rv = R.report(venue, yes_value=yv, lam=lam)
        rf = R.report(full, yes_value=yv, lam=lam)
        out["windows"][w] = {"venue_only": rv, "with_safety_overlay": rf,
                             "settlement_yes_value": str(yv)}

        for k, v in venue["by_layer"].items():
            aggv[k] += v
        for k, v in full["by_layer"].items():
            agg[k] += v
        aggv["reproduced"] += venue["reproduced"]
        aggv["actions"] += venue["n_actions"]
        agg["reproduced"] += full["reproduced"]
        agg["actions"] += full["n_actions"]
        for k, v in venue["mode_mix"].items():
            aggv["mode_" + k] += v
        aggv["slip_x10000"] += int(round(float(venue["slippage_usd"]) * 10000))
        aggv["total_x10000"] += int(round(
            rv["complete_window_economics"]["total_settlement_implied_shadow_usd"]
            * 10000))
        agg["total_x10000"] += int(round(
            rf["complete_window_economics"]["total_settlement_implied_shadow_usd"]
            * 10000))

        z = rv["zero_cross_overshoot"]
        print("== %s  repro %3d/%3d (%5s%%)  mix %s  | xzero native %-5s "
              "track %-5s %s | overshoot %6.2f vs %6.2f | corr %s | "
              "slip %+7.4f | TOTAL %+8.4f"
              % (w, rv["action_reproduction"]["reproduced"],
                 rv["action_reproduction"]["l5_actions"],
                 rv["action_reproduction"]["rate_pct"],
                 rv["maker_taker_mix"], z["native_crossed"],
                 z["track_crossed"], z["direction"],
                 z["track_overshoot"], z["native_overshoot"],
                 rv["inventory_path_agreement"]["path_correlation"],
                 rv["incremental_translation_cost"]["slippage_usd_vs_l5"],
                 rv["complete_window_economics"][
                     "total_settlement_implied_shadow_usd"]))
        print("      refusals venue-only: %s" % rv["irreducible_failures"]["detail"])
        print("      with overlay: repro %d/%d, overlay refused %d"
              % (rf["action_reproduction"]["reproduced"],
                 rf["action_reproduction"]["l5_actions"],
                 rf["irreducible_failures"]["by_layer"].get("SAFETY_OVERLAY", 0)))

    out["aggregate"] = {
        "venue_only": {
            "reproduced": aggv["reproduced"], "actions": aggv["actions"],
            "rate_pct": round(100.0 * aggv["reproduced"] / aggv["actions"], 1)
            if aggv["actions"] else None,
            "by_layer": {k: aggv[k] for k in F.LAYERS if aggv[k]},
            "maker": aggv["mode_MAKER"], "taker": aggv["mode_TAKER"],
            "slippage_usd": round(aggv["slip_x10000"] / 10000.0, 4),
            "total_settlement_implied_shadow_usd":
                round(aggv["total_x10000"] / 10000.0, 4),
        },
        "with_safety_overlay": {
            "reproduced": agg["reproduced"], "actions": agg["actions"],
            "rate_pct": round(100.0 * agg["reproduced"] / agg["actions"], 1)
            if agg["actions"] else None,
            "by_layer": {k: agg[k] for k in F.LAYERS if agg[k]},
            "total_settlement_implied_shadow_usd":
                round(agg["total_x10000"] / 10000.0, 4),
        },
    }
    print("\n== AGGREGATE venue-only:      repro %d/%d (%.1f%%)  maker %d "
          "taker %d  slip %+.4f  TOTAL %+.4f"
          % (aggv["reproduced"], aggv["actions"],
             100.0 * aggv["reproduced"] / max(aggv["actions"], 1),
             aggv["mode_MAKER"], aggv["mode_TAKER"],
             aggv["slip_x10000"] / 10000.0, aggv["total_x10000"] / 10000.0))
    print("   layers: %s" % {k: aggv[k] for k in F.LAYERS if aggv[k]})
    print("== AGGREGATE with overlay:    repro %d/%d (%.1f%%)  TOTAL %+.4f"
          % (agg["reproduced"], agg["actions"],
             100.0 * agg["reproduced"] / max(agg["actions"], 1),
             agg["total_x10000"] / 10000.0))
    print("   layers: %s" % {k: agg[k] for k in F.LAYERS if agg[k]})

    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(out, indent=1, default=str))
        print("\nwrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
