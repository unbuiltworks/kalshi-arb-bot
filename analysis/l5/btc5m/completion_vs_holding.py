"""Completing a pair versus holding the unmatched leg: total-window result.

The owner's question (2026-11-21): instead of a fixed pair-cost ceiling,
compare the expected loss from HOLDING unmatched inventory with the cost of
COMPLETING it. This measures it directly rather than modelling it: the same
controller, the same recorded quotes, the same fill rule, with only the
completing (lighter-side) bids changed.

  AS LIVE   complete at the book's bid whenever the controller wants to
  NEVER     never bid the lighter side: every unmatched share is held
  CHEAP<=x  complete only when the completing side's bid is <= x
  DEAR>=x   complete only when the completing side's bid is >= x

A completing share bought at price c is worth, at settlement, 1 if its side
wins and 0 otherwise -- whatever the other leg cost. So "complete or hold"
is exactly "is this share worth more than c", and the price of the leg
already held does not enter. CHEAP/DEAR split that by where c sits.

Replay, limits and caveats as in first_leg_entry_quality.py: STRICT fills
(the fill model only sees fills where the price moved through the bid, so
per-share results are biased downward and fills are under-counted); lane
decides every ~6 s; exposure limits unchanged ($0.30 a side, $0.10
unmatched), so NEVER stops opening once $0.10 is unmatched.
Accounting: settlement proceeds minus cost over every fill and every window.
PAIRED = variant minus AS LIVE in the same window, 95% by bootstrap.
"""
import collections
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, ".")
sys.path.insert(0, "analysis/l5/btc5m")
import first_leg_entry_quality as F                 # noqa: E402

VARIANTS = [("AS LIVE", None), ("NEVER", ("COMPLETE", "NEVER", 0.0)),
            ("CHEAP<=0.30", ("COMPLETE", "CHEAP", 0.30)),
            ("CHEAP<=0.50", ("COMPLETE", "CHEAP", 0.50)),
            ("DEAR>=0.50", ("COMPLETE", "DEAR", 0.50)),
            ("DEAR>=0.70", ("COMPLETE", "DEAR", 0.70))]


def main():
    lines = []

    def P(s=""):
        print(s, flush=True)
        lines.append(s)

    rows, res = F.load()
    slugs = sorted(rows)
    sets = (("11-17/18", [s for s in slugs if s[-16:-6] in
                          ("2026-11-17", "2026-11-18")]),
            ("11-19/20", [s for s in slugs if s[-16:-6] in
                          ("2026-11-19", "2026-11-20")]),
            ("11-11..16 (10 s quotes)", [s for s in slugs
                                         if s[-16:-6] < "2026-11-17"]))
    cfgs = [(False, 6.0, r) for _n, r in VARIANTS]
    out = collections.defaultdict(dict)
    with Pool(os.cpu_count() or 2) as pool:
        for slug, cfg, fills in pool.imap_unordered(
                F.run_window, [(s, rows[s], c) for s in slugs for c in cfgs],
                chunksize=8):
            out[cfg][slug] = fills

    def pnl(f, s):
        return f["qty"] * ((1.0 if res[s] == f["side"] else 0.0)
                           - f["price"])

    base = cfgs[0]
    for name, ws in sets:
        P("== %s  windows %d ==" % (name, len(ws)))
        for (lab, _r), cfg in zip(VARIANTS, cfgs):
            net = sum(pnl(f, s) for s in ws for f in out[cfg][s])
            sh = sum(f["qty"] for s in ws for f in out[cfg][s])
            inc = sum(pnl(f, s) for s in ws for f in out[cfg][s]
                      if f["kind"] == "INCREASING")
            red = sum(pnl(f, s) for s in ws for f in out[cfg][s]
                      if f["kind"] == "REDUCING")
            shr = sum(f["qty"] for s in ws for f in out[cfg][s]
                      if f["kind"] == "REDUCING")
            per = [sum(pnl(f, s) for f in out[cfg][s]) for s in ws]
            ci = F.boot_sum(per)
            diff = [sum(pnl(f, s) for f in out[cfg][s])
                    - sum(pnl(f, s) for f in out[base][s]) for s in ws]
            cd = F.boot_sum(diff)
            worst = min(per)
            P("   %-12s net $%+7.2f [%+.2f,%+.2f]  shares %5.0f  per share "
              "%+.4f | first legs $%+.2f, completing $%+.2f on %.0f sh | "
              "worst window $%+.3f | PAIRED $%+.2f [%+.2f,%+.2f]" % (
                  lab, net, ci[0], ci[1], sh, net / sh if sh else 0, inc,
                  red, shr, worst, sum(diff), cd[0], cd[1]))
    open("analysis/l5/btc5m/COMPLETION_VS_HOLDING_RESULT.txt", "w").write(
        "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
