"""P0A. Does our SYMMETRIC frozen policy produce L5-like persistence by itself?

If it does, L5's 70.3% EXTEND-after-EXTEND is queue mechanics and there is no
side-selection signal to add. If ours stays near 50/50 while L5 sits at
70/30, mode-switch becomes a serious candidate.

METHOD IS DEEPERATELY IDENTICAL to scripts/l5_switching_model.py: walk the
fill sequence in order, carry inventory, label each fill EXTEND_OVERWEIGHT or
BUY_UNDERWEIGHT against the residual held BEFORE it, and condition on the
previous label. The only difference is whose fills they are.

Ours has one thing L5's public data does not: ORDER IDENTITY. So we can also
say whether consecutive same-side fills came from the same resting order, a
replenished order at the same price, or a repriced order -- which is exactly
the mechanism question.
"""
import collections
import statistics
from decimal import Decimal as D

from scripts.audit_economic_complete import load, USABLE
from src import pmus_exec_ladder as X

RUNG = "R2_ONE_FILL_PER_ORDER"          # the $5.9220629591 control


def label(fills):
    """EXTEND vs UNDERWEIGHT per fill, against inventory before it."""
    q = {"up": D(0), "down": D(0)}
    rows = []
    prev = None
    run, last_side = 0, None
    for f in fills:
        s = f["side"]
        resid = q["up"] - q["down"]
        held = "up" if resid > 0 else ("down" if resid < 0 else None)
        if held is not None:
            y = 1 if s == held else 0
            rows.append({"y": y, "prev": prev, "run": run,
                         "abs_resid": abs(resid),
                         "oid": f["oid"], "side": s,
                         "partial_same_order": f["partial_of_same_order"],
                         "px": f["px"]})
            prev = y
        run = run + 1 if s == last_side else 1
        last_side = s
        q[s] += f["qty"]
    return rows


def main():
    tape = load()
    allrows, per_order, seq = [], collections.Counter(), []
    for slug, w in USABLE.items():
        r = X.replay_window(tape[slug], winner=w, rung=RUNG)
        fl = r["obs"]["fill_log"]
        seq.append(fl)
        for f in fl:
            per_order[f["oid"]] += 1
        allrows += label(fl)

    n = len(allrows)
    after_ext = [r for r in allrows if r["prev"] == 1]
    after_reb = [r for r in allrows if r["prev"] == 0]
    def pct(rs):
        return (100.0 * sum(r["y"] for r in rs) / len(rs)) if rs else float("nan")

    print("OUR frozen symmetric LOCK5, execution-aware rung %s, %d windows"
          % (RUNG, len(USABLE)))
    print("  imbalanced decisions n=%d" % n)
    print()
    print("%-42s %8s %8s" % ("", "OURS", "L5"))
    print("%-42s %7.1f%% %7.1f%%" % ("P(EXTEND | prev EXTEND)",
                                     pct(after_ext), 70.3))
    print("%-42s %7.1f%% %7.1f%%" % ("P(EXTEND | prev REBALANCED)",
                                     pct(after_reb), 29.9))
    print("%-42s %7.1f%% %7.1f%%" % ("unconditional EXTEND",
                                     pct(allrows), 53.8))
    ext = [r for r in allrows if r["y"] == 1]
    cont = sum(1 for r in ext if r["prev"] == 1)
    print("%-42s %7.1f%% %7.1f%%" % ("extensions continuing a run",
                                     100.0 * cont / len(ext) if ext else 0,
                                     77.4))
    print("  (n after EXTEND=%d, after REBALANCE=%d)"
          % (len(after_ext), len(after_reb)))
    print()
    print("-- same-side run-length distribution --")
    runs = collections.Counter()
    for fl in seq:
        last, ln = None, 0
        for f in fl:
            if f["side"] == last:
                ln += 1
            else:
                if last is not None:
                    runs[min(ln, 6)] += 1
                ln = 1
            last = f["side"]
        if last is not None:
            runs[min(ln, 6)] += 1
    tot = sum(runs.values())
    for k in sorted(runs):
        print("   run len %s%-2s  %5d  %5.1f%%"
              % (">=" if k == 6 else "  ", k, runs[k], 100.0 * runs[k] / tot))
    print()
    print("-- fills per resting order --")
    c = list(per_order.values())
    print("   orders=%d fills=%d  mean %.2f  median %.1f  max %d"
          % (len(c), sum(c), statistics.mean(c), statistics.median(c), max(c)))
    dist = collections.Counter(c)
    for k in sorted(dist):
        print("   %d fill(s): %d orders (%.1f%%)"
              % (k, dist[k], 100.0 * dist[k] / len(c)))
    print()
    print("-- mechanism of consecutive same-side fills --")
    same_order = repl_same_px = repriced = 0
    for fl in seq:
        for a, b in zip(fl, fl[1:]):
            if a["side"] != b["side"]:
                continue
            if b["oid"] == a["oid"]:
                same_order += 1
            elif b["px"] == a["px"]:
                repl_same_px += 1
            else:
                repriced += 1
    t = same_order + repl_same_px + repriced
    print("   consecutive same-side pairs n=%d" % t)
    if t:
        print("   same existing order (partial)   %5d  %5.1f%%"
              % (same_order, 100.0 * same_order / t))
        print("   replenished at the SAME price   %5d  %5.1f%%"
              % (repl_same_px, 100.0 * repl_same_px / t))
        print("   repriced order                  %5d  %5.1f%%"
              % (repriced, 100.0 * repriced / t))


if __name__ == "__main__":
    main()
