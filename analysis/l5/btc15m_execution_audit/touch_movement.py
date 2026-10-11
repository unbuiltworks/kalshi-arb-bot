"""How often does the best bid actually move, and by how much?

READ-ONLY. This parameterises one number: the MATERIALITY threshold under
which a resting acquisition quote should keep its queue position instead of
being cancelled and reposted.

WHY IT IS NEEDED. The canonical engine's resting rule is a strict equality --
`touch != px_` cancels an OPEN/EXTEND -- so ANY one-tick move of the bid ends
the quote. Whether that matters is an empirical question about the book, not
an opinion, and this measures it on the recorded tape over the policy's own
quoting band.

VENUE SCOPE, STATED BECAUSE IT LIMITS THE ANSWER. This reads the Gemini
BTC15m two-sided recorder tape. The engine it informs trades PMUS. The
cadence comparison carries across (a book is a book), but the TICK
DISTRIBUTION does not automatically: PMUS best-bid change sizes must be
measured on a PMUS book tape before a threshold is set there.
`analysis/pmus/recorder_memory/bbo_history_rate.json` gives PMUS book-UPDATE
rates (p50 2.86/s over 17 seated markets) but not best-bid change sizes, and
its own SAMPLING_BIAS note says it establishes nothing about unseated
markets.
"""
from __future__ import annotations

import collections
import os
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maker_arms import OPEN_TTE_HI, OPEN_TTE_LO, load  # noqa: E402

TICK = D("0.01")


def measure(tape_dir: str) -> Dict[str, Any]:
    moves = []
    gaps = []
    obs = 0
    windows = 0
    for _tk, w in sorted(load(tape_dir).items()):
        if not w.get("fully_covered"):
            continue          # a partly recorded window would bias the rate
        windows += 1
        E = w["expiry"]
        prev = None
        last = None
        for e in w["timeline"]:
            if e["type"] != "book" or e["bid"] is None:
                continue
            if not (OPEN_TTE_LO <= E - e["t"] <= OPEN_TTE_HI):
                continue
            obs += 1
            b = e["bid"]
            if prev is not None and b != prev:
                moves.append(abs(int((b - prev) / TICK)))
                if last is not None:
                    gaps.append(e["t"] - last)
                last = e["t"]
            elif prev is None:
                last = e["t"]
            prev = b
    c = collections.Counter(moves)
    one = c.get(1, 0)
    return {
        "label": "BEST_BID_MOVEMENT_IN_QUOTING_BAND",
        "venue": "gemini BTC15m recorder tape",
        "fully_covered_windows": windows,
        "in_band_observations": obs,
        "bid_changes": len(moves),
        "observations_per_change": (round(obs / len(moves), 2)
                                    if moves else None),
        "move_size_ticks": dict(sorted(c.items())),
        "share_exactly_one_tick": (round(one / len(moves), 4)
                                   if moves else None),
        "share_two_or_more_ticks": (round(1 - one / len(moves), 4)
                                    if moves else None),
        "seconds_between_changes": {
            "median": round(statistics.median(gaps), 2) if gaps else None,
            "p90": round(sorted(gaps)[int(.9 * len(gaps))], 2) if gaps else None,
            "max": round(max(gaps), 2) if gaps else None},
        "reading": (
            "A one-tick tolerance would decline %d of %d cancel/replace "
            "cycles (%.0f%%) while still reacting to every move of two ticks "
            "or more. With a median %.1f s between changes, a quote required "
            "to sit EXACTLY at the touch is cancelled about that often and "
            "can never accumulate queue position."
            % (one, len(moves), 100 * one / len(moves) if moves else 0,
               statistics.median(gaps) if gaps else 0)),
    }


def main() -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("tape_dir")
    a = ap.parse_args()
    print(json.dumps(measure(a.tape_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
