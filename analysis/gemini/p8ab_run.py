"""P8-A/B: the extracted pair rule's DEV response surface, its frozen
evaluation on validation, and the WAIT study -- all on corrected data.

Everything here loads through `p8_dataset`, so the duration and mirrored-twin
corrections are applied by construction rather than remembered.

    python3 analysis/gemini/p8ab_run.py <raw_bulk.json>

DEV is 09-16..18, VALIDATION 09-19..20. 09-21..22 is PREVIOUSLY EXPOSED and is
not touched here. 09-23 onward is the sealed forward set and is not read.
"""
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p8_dataset as DS                                        # noqa: E402
from src.gemini_execution import ev_replay as R                # noqa: E402

DEV = {"2026-09-16", "2026-09-17", "2026-09-18"}
VAL = {"2026-09-19", "2026-09-20"}

#: frozen on DEV before validation was scored, one per lane
CAND = {"5m": (F(90, 100), F(92, 100)), "15m": (F(90, 100), F(98, 100))}
ACQ = (F(85, 100), F(88, 100), F(90, 100), F(93, 100), F(95, 100), F(100, 100))
COMP = (F(88, 100), F(92, 100), F(98, 100), F(112, 100), F(130, 100))


def split_of(day):
    return "dev" if day in DEV else ("val" if day in VAL else None)


def run(contracts, split, **kw):
    rows = []
    for c in contracts:
        if not c.get("prints") or split_of(c["day"]) != split:
            continue
        r = R.replay(c, policy="pair", max_legs=12, max_residual=4, **kw)
        if r.get("verdict") == "SETTLED":
            rows.append(r)
    return rows


def stats(rows):
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (r["day"], r["hour"], r["symbol"]))
    net = sum(F(r["net"]) for r in rows)
    spend = sum(F(r["spend"]) for r in rows)
    fills = sum(r["fills"] for r in rows)
    matched = sum(r["matched"] for r in rows)
    eq = peak = dd = F(0)
    byday = defaultdict(F)
    for r in rows:
        eq += F(r["net"]); peak = max(peak, eq); dd = max(dd, peak - eq)
        byday[r["day"]] += F(r["net"])
    traded = [r for r in rows if r["fills"] > 0]
    return {"mkts": len(traded), "fills": fills, "spend": float(spend),
            "net": float(net), "matched": float(sum(F(r["matched_pnl"]) for r in rows)),
            "resid": float(sum(F(r["residual_pnl"]) for r in rows)),
            "roi": (float(net / spend) if spend else 0.0),
            "compl": (matched * 2 / fills) if fills else 0.0,
            "residq": sum(r["residual"] for r in rows), "dd": float(dd),
            "worst_mkt": min((float(F(r["net"])) for r in traded), default=0.0),
            "worst_day": float(min(byday.values(), default=F(0)))}


HDR = (f"{'acq':>5} {'comp':>5} {'mkts':>5} {'fills':>6} {'spend':>9} "
       f"{'matched$':>9} {'resid$':>9} {'NET$':>9} {'ROI':>8} {'compl':>6} "
       f"{'residQ':>7} {'maxDD':>7} {'worstMkt':>9} {'worstDay':>9}")


def line(a, c, s):
    print(f"{float(a):>5.2f} {float(c):>5.2f} {s['mkts']:>5} {s['fills']:>6} "
          f"{s['spend']:>9.2f} {s['matched']:>9.2f} {s['resid']:>9.2f} "
          f"{s['net']:>9.2f} {s['roi']:>8.2%} {s['compl']:>6.2f} {s['residq']:>7} "
          f"{s['dd']:>7.2f} {s['worst_mkt']:>9.2f} {s['worst_day']:>9.2f}", flush=True)


def main(path):
    rows, rep = DS.load([path])
    rep.pop("quarantined_detail", None)
    print(rep)
    by = defaultdict(list)
    for c in rows:
        by[c["duration"]].append(c)

    for dur in ("5m", "15m"):
        print(f"\n=== P8-A DEV response surface, BTC{dur}, capture 1.00 ===")
        print(HDR)
        for a in ACQ:
            for c in COMP:
                s = stats(run(by[dur], "dev", acq_max=a, comp_max=c))
                if s:
                    line(a, c, s)

    print("\n=== P8-A frozen candidates: DEV then VALIDATION ===")
    for dur, (a, c) in CAND.items():
        for split in ("dev", "val"):
            for cap in (F(1), F(1, 2), F(1, 3)):
                s = stats(run(by[dur], split, acq_max=a, comp_max=c, capture=cap))
                if s:
                    print(f"BTC{dur:>4} {split} capture {float(cap):.2f}", end=" ")
                    line(a, c, s)


if __name__ == "__main__":
    main(sys.argv[1])
