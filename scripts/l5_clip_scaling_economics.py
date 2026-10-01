"""Does V1 have the edge AND capacity to reach >= $3 net per 15-minute window?

FRESH OOS. Windows never used by Batch 1, Batch 2, the V1-V4 OOS or the Case-2
forensics. Under CONSERVATIVE_CASE1: the unobserved crossed-resting fill is
REFUSED, not granted, because the 2026-10-01 live calibration failed on its own
instrument and demonstrated nothing.

Sweeps clip size q = 1, 2, 5, 10 and separates, per the owner's list:
  fill rate | matched quantity | residual exposure | capital required | fees |
  net $/window

NOTHING HERE IS REALIZED PROFIT. Replay on recorded tape, source-settled
labelling, modelled maker rebates EXCLUDED from the settled total per CLAUDE.md.

    python3 scripts/l5_clip_scaling_economics.py <raw.jsonl> [...]
"""
from __future__ import annotations

import json
import math
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

sys.path.insert(0, ".")

from src.l5_continuous import V1, build as build_variant          # noqa: E402
from src.l5_two_sided import (ARM_CONTROL, ARM_REPLICATION,       # noqa: E402
                              CONSERVATIVE_CASE1, TwoSided)
from scripts.l5_window_manifest import manifest                   # noqa: E402

ARMS = (ARM_CONTROL, ARM_REPLICATION, V1)
CLIPS = (D(1), D(2), D(5), D(10))
TARGET = D("3.00")

KEYS = ("maker_fills", "taker_fills", "partial_fills", "places", "cancels",
        "matched_contracts", "matched_gross_edge", "taker_fees_cashflow",
        "maker_rebates_modeled_cashflow", "residual_settlement_pnl",
        "source_settled_total", "max_dollars_committed",
        "max_directional_residual", "decision_opportunity",
        "case1_unresolved_no_fill", "case1_unresolved_qty",
        "unresolved_prints", "queue_forfeited", "queue_consumed")


def build(arm: str, clip: D) -> TwoSided:
    if arm in (ARM_CONTROL, ARM_REPLICATION):
        return TwoSided(arm=arm, clip=clip)
    return build_variant(arm, clip=clip)


def run_window(paths, slug, winner, start) -> Dict[str, Any]:
    import scripts.l5_run_causal_ws as W
    W.SLICE = list(paths)
    events = W.events(slug)
    out: Dict[str, Any] = {"slug": slug, "events": len(events),
                           "winner": winner}
    for clip in CLIPS:
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
            out[f"q{clip}|{arm}"] = {k: r.get(k) for k in KEYS}
    return out


def stats(xs):
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else 0.0), None, None
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(v / n)
    if se == 0:
        return m, None, None
    return m, m / se, se


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    man = manifest(argv[1:])
    usable = [w for w in man["windows"] if w["complete"]]
    print(json.dumps({"usable": man["usable"], "total_seen": man["total_seen"],
                      "conservative_case1": CONSERVATIVE_CASE1,
                      "usable_slugs": man["usable_slugs"]}, indent=1))
    if len(usable) < 10:
        print(f"\nREFUSED: {len(usable)} complete windows; >= 10 required.")
        return 1
    rows = [run_window(w["raw_paths"], w["slug"], w["winner"],
                       w["window_start"]) for w in usable]
    n = len(rows)
    with open("analysis/l5/economics/CLIP_SCALING_ROWS.json", "w") as fh:
        json.dump({"windows": rows, "conservative_case1": CONSERVATIVE_CASE1},
                  fh, indent=1, default=str)

    def col(key, metric):
        return [float(r[key][metric] or 0) for r in rows]

    print(f"\n{'='*118}")
    print(f"FRESH OOS, {n} windows, CONSERVATIVE_CASE1={CONSERVATIVE_CASE1}. "
          f"Replay -- NOT realized profit.")
    print(f"{'='*118}")
    hdr = ("%-24s%5s%11s%11s%10s%11s%11s%11s%11s" %
           ("arm", "q", "net $/win", "t", "matched", "fillrate",
            "capital", "fees", "resid"))
    print(hdr)
    best: Dict[str, Any] = {}
    for arm in ARMS:
        for clip in CLIPS:
            key = f"q{clip}|{arm}"
            net = col(key, "source_settled_total")
            m, t, _ = stats(net)
            mk = sum(col(key, "maker_fills"))
            pl = sum(col(key, "places"))
            matched = sum(col(key, "matched_contracts")) / n
            cap = sum(col(key, "max_dollars_committed")) / n
            fees = sum(col(key, "taker_fees_cashflow")) / n
            res = sum(col(key, "max_directional_residual")) / n
            print("%-24s%5s%11.4f%11s%11.1f%10.3f%11.4f%11.4f%11.2f" % (
                arm, clip, m, "%.2f" % t if t is not None else "-",
                matched, (mk / pl if pl else 0.0), cap, fees, res))
            best[key] = m
        print()

    print(f"{'='*118}")
    print("SCALING: is net $/window linear in clip, and how far from the "
          f"${TARGET}/window target?")
    print(f"{'='*118}")
    for arm in ARMS:
        base = best[f"q1|{arm}"]
        print(f"\n  {arm}   q1 = {base:+.4f}")
        for clip in CLIPS:
            m = best[f"q{clip}|{arm}"]
            lin = base * float(clip)
            eff = (m / lin) if lin else float("nan")
            need = (float(TARGET) / m) if m > 0 else float("nan")
            print("    q%-3s net %+10.4f   linear would be %+10.4f   "
                  "efficiency %6.2f   clip needed for $%s: %s" % (
                      clip, m, lin, eff, TARGET,
                      ("%.0f" % (float(clip) * need)) if m > 0 else "never"))

    print(f"\n{'='*118}")
    print("CASE-1 REFUSALS (fills NOT granted because the case is unobserved)")
    print(f"{'='*118}")
    for arm in ARMS:
        for clip in CLIPS:
            key = f"q{clip}|{arm}"
            print("  %-24s q%-3s refused %6.0f events, %8.1f contracts" % (
                arm, clip, sum(col(key, "case1_unresolved_no_fill")),
                sum(col(key, "case1_unresolved_qty"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
