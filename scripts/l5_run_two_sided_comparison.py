"""PREREGISTERED comparison: CONTROL vs the two two-sided arms.

Registered BEFORE any result exists, which is the point: the arms, the metrics
and the advancement criteria are fixed here so a later number cannot quietly
redefine what counted as success.

ARMS, all on the SAME event stream, the same causal fill model and the same
settlement set:

  * CONTROL              -- one leg, then its complement, then done. The shape
                            the live q1 canary actually ran.
  * L5_REPLICATION       -- simultaneous two-sided, patient, minimal forced
                            flattening.
  * L5_ADAPTED_ECONOMIC  -- same substrate, but WAIT/COMPLETE/EXIT may take the
                            economically superior executable action.

ADVANCEMENT REQUIRES ALL OF:
  1. positive out-of-sample SETTLED net -- not mark-to-market;
  2. the improvement is NOT obtained mainly by carrying materially larger
     residual tails (max_directional_residual and worst-window residual are
     reported for exactly this);
  3. better matched-contract throughput OR lower one-sided exposure than
     CONTROL;
  4. no material lifecycle/accounting mismatch;
  5. behaviour still recognizably L5-like rather than an unrelated arb.

Reported per window and in aggregate, plus THE WORST TEN WINDOWS -- a mean over
windows hides exactly the residual tail that criterion 2 exists to catch.

    python3 scripts/l5_run_two_sided_comparison.py <window.jsonl.gz> [...]
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

sys.path.insert(0, ".")

from src.l5_two_sided import ARM_ADAPTED, ARM_REPLICATION, TwoSided  # noqa: E402
from scripts.l5_window_manifest import (manifest, window_start_from_slug)  # noqa: E402

CONTROL = "CONTROL_ONE_LEG"
ARMS = (CONTROL, ARM_REPLICATION, ARM_ADAPTED)

#: the metrics the comparison is judged on, fixed before any run
METRICS = ("cycles_started", "cycles_completed", "maker_fills", "taker_fills",
           "matched_contracts", "residual_now", "matched_gross_edge",
           "taker_fees_cashflow", "maker_rebates_modeled_cashflow",
           "flatten_pnl_shadow", "residual_settlement_pnl", "settled_net",
           "max_dollars_committed", "max_directional_residual",
           "time_one_sided_s", "queue_forfeited", "queue_consumed",
           "cancels", "requotes", "takes", "adverse_markout_mean",
           "pairs_per_min", "dollars_per_min")


def build(arm: str, clip: D) -> TwoSided:
    """CONTROL is the same engine with two-sided quoting switched OFF, so the
    difference measured is the POLICY and not a second implementation."""
    if arm == CONTROL:
        c = TwoSided(arm=ARM_REPLICATION, clip=clip, max_cycles=1)
        c.one_leg_only = True                    # marker for the report
        return c
    return TwoSided(arm=arm, clip=clip)


def run_window(path: str, slug: str, winner: str, start: float,
               clip: D) -> Dict[str, Any]:
    import scripts.l5_run_causal_ws as W
    W.SLICE = path
    events = W.events(slug)
    out: Dict[str, Any] = {"slug": slug, "events": len(events),
                           "winner": winner}
    for arm in ARMS:
        c = build(arm, clip)
        c.window_end = start + 900.0
        for t, kind, row in events:
            if kind == "book":
                c.on_book(row, t)
            else:
                c.on_trade(row.get("px"), row.get("qty"), t)
        r = c.report(winner=winner)
        out[arm] = {k: r.get(k) for k in METRICS}
    return out


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    paths = argv[1:]
    man = manifest(paths)
    usable = [w for w in man["windows"] if w["complete"]]
    print(json.dumps({"manifest_usable": man["usable"],
                      "manifest_total": man["total_seen"],
                      "usable_slugs": man["usable_slugs"]}, indent=1))
    if len(usable) < 10:
        print(f"\nREFUSED: {len(usable)} complete windows; the preregistered "
              f"comparison requires >= 10. A mean over fewer is not evidence, "
              f"and a worst-ten table cannot exist.")
        print("Excluded windows and why:")
        for w in man["windows"]:
            if not w["complete"]:
                print(f"  {w['slug']}: {w.get('exclusion')}")
        return 1
    rows = [run_window(w["raw_sources"][0], w["slug"], w["winner"],
                       w["window_start"], D(1)) for w in usable]
    print(json.dumps({"windows": rows}, indent=1, default=str))
    # aggregate + worst ten by settled net
    for arm in ARMS:
        nets = [D(r[arm]["settled_net"]) for r in rows
                if r[arm]["settled_net"] is not None]
        print(f"\n{arm}: n={len(nets)} total={sum(nets, D(0))} "
              f"mean={sum(nets, D(0)) / D(len(nets)) if nets else None}")
        worst = sorted(rows, key=lambda r: D(r[arm]["settled_net"] or 0))[:10]
        print("  worst ten:")
        for r in worst:
            a = r[arm]
            print(f"    {r['slug']}: net={a['settled_net']} "
                  f"resid={a['residual_now']} "
                  f"max_resid={a['max_directional_residual']} "
                  f"matched={a['matched_contracts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
