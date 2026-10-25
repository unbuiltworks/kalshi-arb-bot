"""PMUS BTC15m: is the 0.96 completion cap, or the market, responsible for
matched-0 windows?

VENUE: PMUS. Only PMUS. (Both sessions working this repo have now been bitten by
an unlabelled venue, so it is in the filename, this header, and every line of
output.)

THE TEST. A maker completion costs
    pair_cost = bid + (1 - ask) = 1 - spread
because buying YES as a maker rests at the bid and buying NO as a maker rests at
the NO bid, which is 1 - ask. No trade tape is needed: this is a property of the
book alone, which is why it is answerable on PMUS data where fill-time adverse
selection is not.

Against it stand two thresholds that are NOT the same number:
  * the controller's configured completion ceiling, 0.96 (TS.max_pair_cost, read
    from the live process at boot);
  * the EMPIRICAL break-even ceiling, 1.004-1.008, established in
    analysis/l5/PMUS_INCENTIVE_BREAKEVEN.md from 25 real credited maker rebates
    and the venue's cent rounding. Above ~1.008 a completion loses money at
    every size and price studied even with every observed credit counted.

If PMUS pair costs sit mostly BETWEEN those two numbers, the cap is refusing
completions the venue's own credited economics say are worth taking, and the
matched-0 windows are our doing. If they sit above 1.008, the market is
responsible and completion closes too.
"""
from __future__ import annotations

import json
import os
import statistics
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))

CONTROLLER_CAP = D("0.96")        # TS.max_pair_cost, observed in the live boot
EMPIRICAL_FLOOR = D("1.004")      # PMUS_INCENTIVE_BREAKEVEN.md, lower of the range
EMPIRICAL_CEIL = D("1.008")       # upper of the range


def main():
    ws = json.load(open(os.path.join(HERE, "pmus_windows.json")))
    pcs, by_w = [], []
    for w in ws:
        wp = []
        for fr in w["bbo"]:
            yb, nb = D(fr[1]), D(fr[3])     # yes_bid, no_bid (= 1 - yes_ask)
            pc = yb + nb                    # == 1 - spread
            if pc <= 0 or pc >= D("1.5"):
                continue
            pcs.append(pc)
            wp.append(pc)
        if wp:
            by_w.append((w["market"], min(wp), statistics.median(wp)))

    f = [float(x) for x in pcs]
    f.sort()
    n = len(f)
    print(f"VENUE: PMUS  ({len(ws)} cpc-btc-updown-15m windows, {n} book frames)")
    print(f"maker pair cost = bid + (1-ask) = 1 - spread\n")
    print(f"  min {f[0]:.4f}   p05 {f[int(.05*n)]:.4f}   median "
          f"{statistics.median(f):.4f}   p95 {f[int(.95*n)]:.4f}   max {f[-1]:.4f}")

    def share(lo, hi):
        c = sum(1 for x in f if lo <= x < hi)
        return c, 100.0 * c / n

    print("\nWHERE PMUS PAIR COSTS ACTUALLY SIT")
    bands = [(0.0, 0.96, "<= controller cap 0.96 -- the only ones it accepts"),
             (0.96, 1.004, "REFUSED by the cap, INSIDE empirical break-even"),
             (1.004, 1.008, "the empirical break-even range itself"),
             (1.008, 2.0, "above empirical break-even -- correctly refused")]
    for lo, hi, label in bands:
        c, p = share(lo, hi)
        print(f"  {lo:6.3f} - {hi:<6.3f}  {c:6d} frames  {p:5.1f}%   {label}")

    acc, _ = share(0.0, 0.96)
    gap, gp = share(0.96, 1.004)
    bad, bp = share(1.008, 2.0)
    print("\nVERDICT")
    print(f"  frames the 0.96 cap would ACCEPT:                     {100.0*acc/n:5.1f}%")
    print(f"  frames it REFUSES that credited economics would take: {gp:5.1f}%")
    print(f"  frames correctly refused (> 1.008):                   {bp:5.1f}%")
    if gp > 50:
        print("\n  => THE CAP, NOT THE MARKET. The modal PMUS completion is refused")
        print("     by a configured ceiling while sitting inside the break-even the")
        print("     venue's own credited rebates establish. matched-0 is our doing,")
        print("     and completion is raisable WITHOUT new data, capital or orders.")
    elif bp > 50:
        print("\n  => THE MARKET. Completion closes on PMUS too.")
    else:
        print("\n  => MIXED; neither explanation dominates.")

    print("\nPER-WINDOW CHEAPEST COMPLETION (did ANY frame clear 0.96?)")
    cleared = sum(1 for _, mn, _ in by_w if mn <= CONTROLLER_CAP)
    print(f"  windows with at least one frame <= 0.96: {cleared} of {len(by_w)}")
    print(f"  windows whose CHEAPEST frame still exceeded 0.96: "
          f"{len(by_w)-cleared} of {len(by_w)}")
    print("  (a window in the second group cannot complete under the cap at all,")
    print("   however patient the controller is -- it is a structural refusal)")

    json.dump(dict(venue="PMUS", windows=len(ws), frames=n,
                   controller_cap=str(CONTROLLER_CAP),
                   empirical_breakeven=[str(EMPIRICAL_FLOOR), str(EMPIRICAL_CEIL)],
                   median_pair_cost=statistics.median(f),
                   pct_accepted_by_cap=100.0*acc/n,
                   pct_refused_but_inside_breakeven=gp,
                   pct_above_breakeven=bp,
                   windows_with_any_frame_under_cap=cleared,
                   windows_total=len(by_w)),
              open(os.path.join(HERE, "inventory",
                                "pmus_pair_cost_distribution.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
