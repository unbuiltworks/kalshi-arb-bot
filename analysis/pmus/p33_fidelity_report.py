"""P33 -- Phase 1 replication fidelity, per window.

Can PMUS reproduce L5's actual BTC15m sequential behaviour? Venue capability
and our own safety controls are reported on separate axes and never combined.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.l5_replication_fidelity import fidelity            # noqa: E402

OBS = "data/l5_pmus_realtime/l5_pmus_realtime.jsonl"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", default=OBS)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    by = collections.defaultdict(list)
    for line in open(a.tape):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "l5_action" or r.get("backlog"):
            continue
        by[r["window_start_utc"][11:16]].append(r)

    rep = {"label": "PMUS_L5_REPLICATION_FIDELITY_V1",
           "phase": "1 -- replication only; no economics, no optimization",
           "windows": {w: fidelity(rs) for w, rs in sorted(by.items())}}
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1, default=str)

    print("window | L5 acts | PMUS repro | fidelity | venue blocks           "
          "| overlay would refuse")
    for w, f in rep["windows"].items():
        r = f["reproduction"]
        print("%-6s | %-7s | %-10s | %-8s | %-22s | %s (%s%%)"
              % (w, r["l5_actions"], r["pmus_reproducible"],
                 str(r["fidelity_pct"]) + "%", json.dumps(r["venue_blocks"]),
                 f["SAFETY_OVERLAY"]["actions_our_controls_would_refuse"],
                 f["SAFETY_OVERLAY"]["pct"]))
    print("\nsequence fidelity (NATIVE -> PERMITTED)")
    for w, f in rep["windows"].items():
        s = f["sequence_fidelity"]
        print("  %-6s opposite %s->%s  longest_run %s->%s  crossed %s->%s  "
              "overshoot %s->%s  terminal_resid %s->%s"
              % (w, s["opposite_acquired_native"],
                 s["opposite_acquired_permitted"], s["longest_run_native"],
                 s["longest_run_permitted"], s["crossed_zero_native"],
                 s["crossed_zero_permitted"], s["overshoot_native"],
                 s["overshoot_permitted"], s["terminal_residual_native"],
                 s["terminal_residual_permitted"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
