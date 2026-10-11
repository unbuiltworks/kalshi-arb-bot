"""Does the rule matter, or only the volume? The control that answers it.

Each treatment is matched on realised take rate by a state-blind random
suppressor over several seeds. If random does as well, the treatment is
decoration.
"""
import json, collections
from decimal import Decimal as D

from src.l5_expectancy_replay import (Native, ResidualCap, ResidualAge,
                                      ComplementRepair, RandomSuppress)
from scripts.l5_treatment_oos import episodes, day_incentive, run_day, fmt

OOS = ("2026-09-24", "2026-09-25")
SEEDS = (11, 23, 37, 41, 59)


def main():
    doc = json.load(open("data/l5/raw/sep/l5_sep23_25_daily.json"))
    prepared = {}
    for day in OOS:
        eps, _ = episodes(doc[day])
        prepared[day] = (eps, day_incentive(doc[day]))

    treatments = [ResidualCap(D("1")), ResidualAge(120), ComplementRepair()]
    nat = {d: run_day(*prepared[d][:1], Native(), prepared[d][1])
           if False else run_day(prepared[d][0], Native(), prepared[d][1])
           for d in OOS}
    nat_total = sum((nat[d]["total"] for d in OOS), D(0))
    nat_trading = sum((nat[d]["trading"] for d in OOS), D(0))
    print("NATIVE held out: trading %s  total %s"
          % (fmt(nat_trading), fmt(nat_total)))
    print()
    h = ("%-22s %7s %11s %11s | %11s %11s %9s"
         % ("arm", "take", "trading", "TOTAL", "rand trading", "rand TOTAL",
            "edge"))
    print(h); print("-" * len(h))
    for pol in treatments:
        tr = {d: run_day(prepared[d][0], pol, prepared[d][1]) for d in OOS}
        t_trading = sum((tr[d]["trading"] for d in OOS), D(0))
        t_total = sum((tr[d]["total"] for d in OOS), D(0))
        taken = sum(tr[d]["counts"]["taken"] for d in OOS)
        offered = sum(tr[d]["counts"]["offered"] for d in OOS)
        rate = taken / offered
        rt, rtot = [], []
        for s in SEEDS:
            rr = {d: run_day(prepared[d][0], RandomSuppress(rate, s),
                             prepared[d][1]) for d in OOS}
            rt.append(sum((rr[d]["trading"] for d in OOS), D(0)))
            rtot.append(sum((rr[d]["total"] for d in OOS), D(0)))
        mr = sum(rt, D(0)) / len(rt)
        mtot = sum(rtot, D(0)) / len(rtot)
        print("%-22s %6.1f%% %11s %11s | %11s %11s %9s"
              % (pol.name, rate * 100, fmt(t_trading), fmt(t_total),
                 fmt(mr), fmt(mtot), fmt(t_trading - mr)))
        print("%-22s %7s %11s %11s | rand trading spread %s .. %s"
              % ("", "", "", "", fmt(min(rt)), fmt(max(rt))))


if __name__ == "__main__":
    main()
