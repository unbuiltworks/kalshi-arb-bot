"""Preregistered 6-arm comparison: CONTROL, REPLICATION (frozen) and V1-V4.

Preregistration: analysis/l5/V1_V4_PREREGISTRATION.md, committed before the tape
was exported. The frozen runner `l5_run_two_sided_comparison.py` is NOT modified;
this is a separate script so the Batch-2 benchmark stays reproducible.

Reports every metric the preregistration lists, plus the four incremental deltas:
  REPLICATION -> V1  value/cost of permitting same-side acquisition
  V1 -> V2           value/cost of dropping basis discipline
  V2 <-> V4          EXPECTED EQUIVALENCE (a regression assertion, not a contrast)
  V2 -> V3           old-level persistence vs post-fill price re-selection

    python3 scripts/l5_run_variant_comparison.py <raw.jsonl> [...]
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from typing import Any, Dict

sys.path.insert(0, ".")

from src.l5_continuous import V1, V2, V3, V4, build as build_variant  # noqa: E402
from src.l5_two_sided import (ARM_CONTROL, ARM_REPLICATION,  # noqa: E402
                              TwoSided)
from scripts.l5_window_manifest import manifest  # noqa: E402

ARMS = (ARM_CONTROL, ARM_REPLICATION, V1, V2, V3, V4)
VARIANT_NAMES = (V1, V2, V3, V4)

METRICS = ("cycles_started", "cycles_completed", "maker_fills", "taker_fills",
           "partial_fills", "matched_contracts", "matched_gross_edge",
           "taker_fees_cashflow", "maker_rebates_modeled_cashflow",
           "residual_settlement_pnl", "flatten_pnl_shadow",
           "source_settled_total", "shadow_total_with_flatten",
           "residual_now", "max_directional_residual", "max_dollars_committed",
           "time_one_sided_s", "queue_forfeited", "queue_consumed",
           "places", "requotes", "cancels", "takes", "holds",
           "adverse_markout_mean", "pairs_per_min",
           "source_settled_dollars_per_min", "decision_opportunity",
           "blocked_reasons")

#: the deltas the preregistration fixed, in order
STEPS = ((ARM_REPLICATION, V1, "permitting same-side acquisition"),
         (V1, V2, "dropping basis discipline"),
         (V2, V4, "EXPECTED EQUIVALENCE (regression assertion)"),
         (V2, V3, "old-level persistence vs post-fill re-selection"))


def build(arm: str, clip: D) -> TwoSided:
    if arm in (ARM_CONTROL, ARM_REPLICATION):
        return TwoSided(arm=arm, clip=clip)
    return build_variant(arm, clip=clip)


def run_window(paths, slug, winner, start, clip) -> Dict[str, Any]:
    import scripts.l5_run_causal_ws as W
    W.SLICE = list(paths)
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
                c.on_trade(row.get("px"), row.get("qty"), t,
                            row.get("taker_side"))
        r = c.report(winner=winner)
        d = {k: r.get(k) for k in METRICS}
        if arm in VARIANT_NAMES:
            d["residual_cap_observer"] = r.get("residual_cap_observer")
            d["replenishes"] = r.get("replenishes")
        out[arm] = d
    return out


def agg(rows, arm, key):
    v = [D(str(r[arm][key])) for r in rows if r[arm].get(key) is not None]
    return sum(v, D(0)) if v else None


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    man = manifest(argv[1:])
    usable = [w for w in man["windows"] if w["complete"]]
    print(json.dumps({"usable": man["usable"], "total_seen": man["total_seen"],
                      "usable_slugs": man["usable_slugs"]}, indent=1))
    if len(usable) < 10:
        print(f"\nREFUSED: {len(usable)} complete windows; >= 10 required.")
        for w in man["windows"]:
            if not w["complete"]:
                print(f"  {w['slug']}: {w.get('exclusion')}")
        return 1
    rows = [run_window(w["raw_paths"], w["slug"], w["winner"],
                       w["window_start"], D(1)) for w in usable]
    print(json.dumps({"windows": rows}, indent=1, default=str))

    n = D(len(rows))
    print(f"\n============ PER-WINDOW MEANS over {len(rows)} windows ============")
    cols = ("matched_contracts", "matched_gross_edge", "taker_fees_cashflow",
            "residual_settlement_pnl", "flatten_pnl_shadow",
            "source_settled_total", "shadow_total_with_flatten",
            "max_dollars_committed", "max_directional_residual",
            "time_one_sided_s")
    print("%-26s" % "arm" + "".join("%14s" % c[:13] for c in cols))
    for arm in ARMS:
        print("%-26s" % arm + "".join(
            "%14.4f" % (agg(rows, arm, c) / n) if agg(rows, arm, c) is not None
            else "%14s" % "-" for c in cols))

    print("\n%-26s%10s%10s%10s%10s%10s%10s" %
          ("arm", "mkFills", "tkFills", "places", "cancels", "takes", "replen"))
    for arm in ARMS:
        print("%-26s%10s%10s%10s%10s%10s%10s" % (
            arm, agg(rows, arm, "maker_fills"), agg(rows, arm, "taker_fills"),
            agg(rows, arm, "places"), agg(rows, arm, "cancels"),
            agg(rows, arm, "takes"),
            agg(rows, arm, "replenishes") if arm in VARIANT_NAMES else "-"))

    print("\n============ RESIDUAL-CAP OBSERVER (totals) ============")
    for arm in VARIANT_NAMES:
        tot: Dict[str, Any] = {}
        first_binds = []
        for r in rows:
            o = r[arm].get("residual_cap_observer") or {}
            for k, v in o.items():
                if isinstance(v, (int, float)):
                    tot[k] = tot.get(k, 0) + v
            if o.get("residual_at_first_bind") is not None:
                first_binds.append(D(o["residual_at_first_bind"]))
        print(f"\n  {arm}")
        for k in ("acquisitions_attempted", "blocked_by_cap", "blocked_up",
                  "blocked_down", "seconds_cap_bound", "entries_into_cap",
                  "exits_from_cap", "touch_available_while_cap_bound",
                  "reopened_by_opposite_fill"):
            print(f"    {k:<38} {tot.get(k, 0)}")
        print(f"    {'windows that ever hit the cap':<38} {len(first_binds)}"
              f" of {len(rows)}")
        if first_binds:
            fb = sorted(first_binds)
            print(f"    {'median residual at first bind':<38} "
                  f"{fb[len(fb)//2]}")

    print("\n============ INCREMENTAL ATTRIBUTION ============")
    for a, b, why in STEPS:
        print(f"\n  {a} -> {b}   [{why}]")
        for c in ("matched_contracts", "matched_gross_edge",
                  "source_settled_total", "residual_settlement_pnl",
                  "flatten_pnl_shadow", "shadow_total_with_flatten",
                  "max_dollars_committed", "maker_fills"):
            va, vb = agg(rows, a, c), agg(rows, b, c)
            if va is None or vb is None:
                continue
            print(f"    {c:<28} {va/n:>12.4f} -> {vb/n:>12.4f}   "
                  f"delta {(vb-va)/n:>+12.4f}")
        same = all(str(r[a][k]) == str(r[b][k]) for r in rows for k in METRICS)
        print(f"    IDENTICAL ON ALL {len(METRICS)} METRICS: {same}")

    print("\n============ WORST TEN by source_settled_total ============")
    for arm in ARMS:
        worst = sorted(rows,
                       key=lambda r: D(str(r[arm]["source_settled_total"] or 0)))[:10]
        print(f"\n  {arm}")
        for r in worst:
            x = r[arm]
            print(f"    {r['slug'][-5:]}: src={x['source_settled_total']} "
                  f"edge={x['matched_gross_edge']} "
                  f"resid_settl={x['residual_settlement_pnl']} "
                  f"flat={x['flatten_pnl_shadow']} "
                  f"matched={x['matched_contracts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
