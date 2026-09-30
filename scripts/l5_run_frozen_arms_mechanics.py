"""Frozen arms, MECHANICS ONLY, on the partial print-accurate slice.

This script REFUSES to print any $/window figure. The slice is 5.01 of 15
minutes, is unsettled, and any net number derived from it would be fiction.
Economics run only once a complete settled print-accurate window exists.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D

sys.path.insert(0, ".")

from scripts.l5_run_causal_ws import events                     # noqa: E402
from src.l5_causal_sim import CausalSim                          # noqa: E402
from src.l5_frozen_arms import ARMS, PRE_REGISTERED              # noqa: E402

SLUG = "cpc-btc-updown-15m-2026-09-30-1330z"
CLIPS = (10, 20, 30, 50)

BANNED = ("net", "realistic_net", "matched_pnl", "resid_pnl", "fees")


def main() -> int:
    ev = events(SLUG)
    print(f"slice: {SLUG}  events={len(ev)}  "
          f"prints={sum(1 for e in ev if e[1] == 'trade')}  "
          f"coverage=5.01 of 15.00 min  MECHANICS ONLY")
    print(f"pre-registered, not run for economics: {', '.join(PRE_REGISTERED)}\n")
    base = None
    hdr = ("arm", "clip", "rq_oe", "avoided", "holds_oe", "q_forfeit",
           "q_consumed", "rest_s", "fills", "partial", "inv_up", "inv_dn",
           "adv_ord", "adv_fill", "max_adv")
    print(f"{'arm':<32}" + "".join(f"{h:>11}" for h in hdr[1:]))
    for name, flags in ARMS.items():
        for clip in CLIPS:
            r = CausalSim(clip=D(clip), **flags).run(ev, winner=None)
            for b in BANNED:
                r.pop(b, None)
            oe = r["requotes_by_type"]["open_or_extend"]
            if base is None:
                base = oe
            row = (name, clip, oe, base - oe,
                   r["holds_by_type"]["open_or_extend"],
                   r["queue_forfeited"],
                   r["queue_consumed"], round(r["held_s"]),
                   r["n_fills"], r["partial_fills"],
                   r["inventory_up"], r["inventory_down"],
                   r["adverse_orders"], r["fills_while_adverse"],
                   r["max_adverse_ticks"])
            print(f"{row[0]:<32}" + "".join(f"{str(v):>11}" for v in row[1:]))
    print("\nNO $/window is reported from this slice, by construction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
