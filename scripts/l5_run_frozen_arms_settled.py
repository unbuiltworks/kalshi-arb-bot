"""Run ALL frozen arms on one complete settled print-accurate window.

Refuses to print economics unless the window passes the completeness and
settlement gates in src/l5_settled_scoreboard.py. Usage:

    python3 scripts/l5_run_frozen_arms_settled.py <slice.jsonl.gz> <slug> \
        <window_start_iso> <winner:up|down>
"""
from __future__ import annotations

import sys
from decimal import Decimal as D

sys.path.insert(0, ".")

import scripts.l5_run_causal_ws as W                            # noqa: E402
from src.l5_causal_sim import CausalSim                          # noqa: E402
from src.l5_frozen_arms import ARMS                              # noqa: E402
from src.l5_settled_scoreboard import (                          # noqa: E402
    WindowNotComplete, WindowNotSettled, assert_complete, assert_settled,
    scoreboard)

CLIPS = (10, 20, 30, 50)
ROWS = ("matched_contracts", "unmatched_residual", "pair_cost", "maker_fills",
        "taker_completions", "fees", "confirmed_incentives",
        "sourced_incentives", "settlement_pnl", "net_per_window",
        "peak_capital", "worst_residual", "n_fills",
        "extra_fills_vs_control", "extra_matched_vs_control",
        "matched_per_extra_fill")


def main(argv) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 2
    path, slug, start_iso, winner = argv[1:]
    W.SLICE = path
    ev = W.events(slug)
    start = W._ep(start_iso)
    try:
        cov = assert_complete(ev, window_start=start)
        winner = assert_settled(winner)
    except (WindowNotComplete, WindowNotSettled) as exc:
        print(f"REFUSED: {exc}")
        print("No $/window is reported. Mechanics only: "
              "scripts/l5_run_frozen_arms_mechanics.py")
        return 1
    print(f"window {slug}  coverage {cov['coverage_min']:.2f} min  "
          f"settled winner={winner}  events={len(ev)}")
    for clip in CLIPS:
        print(f"\n===== clip {clip} =====")
        control = CausalSim(clip=D(clip), **ARMS["CONTROL"]).run(ev, winner=winner)
        cols, boards = [], []
        for name, flags in ARMS.items():
            r = CausalSim(clip=D(clip), **flags).run(ev, winner=winner)
            cols.append(name)
            boards.append(scoreboard(r, control=control))
        print(f"{'metric':<26}" + "".join(f"{c[:20]:>22}" for c in cols))
        for m in ROWS:
            vals = ["-" if b.get(m) is None else str(b.get(m)) for b in boards]
            print(f"{m:<26}" + "".join(f"{v:>22}" for v in vals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
