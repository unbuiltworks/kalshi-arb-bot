"""BLIND counter for the R2 statistical gate. Emits a COUNT and nothing else.

WHY THIS EXISTS SEPARATELY. The next statistical read of R2 happens at exactly
100 independent prospective R2 windows and nowhere else. But somebody has to
know when 100 is reached, and asking `r2_frozen.py` would print correctness,
EV and the bootstrap every time -- which is an interim performance read by
another name, and interim reads are optional stopping.

So this tool applies the frozen R2 trigger to decide WHICH windows qualify,
and then deliberately throws away everything except the count. It never reads
`correct`, never touches price, never computes EV. The qualification logic is
imported from `r2_frozen` rather than reimplemented, so the two cannot drift.

WHAT IT PRINTS: the number of qualifying independent windows, and the gate
status. That is all. If you want performance before the gate, the answer is
no.

PROBE WINDOWS ARE EXCLUDED, decided here and now rather than later. A live
execution-validation probe places a real taker order into the book it is
measuring. At q=1 against a median displayed size of 681 the effect on the
touch is negligible, but "negligible" is an assumption and the statistical
cohort should not rest on one. Any window listed in `probe_windows.json` is
excluded from the count and from the eventual statistical read, so the
statistical validation stays pristine and blind. Cost: a handful of windows.
Worth it.
"""
from __future__ import annotations

import json
from pathlib import Path

GATE = 100
PROBE_WINDOWS = Path("analysis/gemini/info_edge/probe_windows.json")


def probe_windows() -> set:
    """Windows touched by a live probe. Excluded from the statistical cohort."""
    try:
        d = json.loads(PROBE_WINDOWS.read_text())
        return set(d.get("tickers") or [])
    except FileNotFoundError:
        return set()


def main() -> int:
    ns = {}
    exec(open("analysis/gemini/info_edge/r2_frozen.py").read()
         .split("def main")[0], ns)
    inst, eligible = ns["extract"]("PROSPECTIVE")
    hits = ns["r2"](inst)

    excluded = probe_windows()
    windows = {r["ticker"] for r in hits} - excluded
    n = len(windows)

    # EVERYTHING ELSE IS DISCARDED HERE, ON PURPOSE.
    del inst, hits

    print("R2 STATISTICAL GATE -- BLIND COUNT")
    print("  eligible prospective windows   : %d" % len(eligible))
    print("  independent R2 windows         : %d" % n)
    print("  excluded as live-probe windows : %d" % len(excluded & set(excluded)))
    print("  gate                           : %d" % GATE)
    if n >= GATE:
        print("  STATUS: GATE REACHED -- the single statistical read is now due.")
    else:
        print("  STATUS: %d to go. No performance figure is available before"
              " the gate, by design." % (GATE - n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
