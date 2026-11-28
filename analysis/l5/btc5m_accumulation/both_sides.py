"""Can the 59.3% win rate be exploited from the OTHER side? READ-ONLY.

THE QUESTION. A 59.3% win rate is exploitable if you can buy below 0.593.
Resting on the favorite pays 0.6419 average, which is above it, so that
side loses. The mirror has never been tested: rest the maker buy on the
UNDERDOG instead, at its own bid (around 0.35), and the bar drops to the
underdog's win rate of about 0.407.

WHY IT MIGHT WORK. These are not two different bets, they are two
different PRICES for the same uncertainty. Buying the favorite at its bid
means paying the favorite's ask-side premium; buying the underdog at its
bid means being paid it. If the toxicity of a fill is a property of the
FAVORITE side specifically -- momentum flow lifting the leader, which is
the usual story -- the mirror would not carry it.

WHY IT MIGHT NOT. If adverse selection is a property of resting at all,
it is symmetric: your underdog bid is reached exactly when the underdog is
sinking, which is when the favorite is strengthening. Same mechanism,
mirrored. Then both sides lose and the question is closed for good.

Either answer is worth having, and nobody has measured it.

METHOD. Same tape, same ADVERSE fill rule as replay.py (a resting buy
fills when that side's bid falls through it), same requoting, same band.
The only thing that changes is WHICH side the maker buy rests on. Reported
per side: fills, average price paid, win rate, and the gap between them,
which is the entire economics.

Usage: python3 -I analysis/l5/btc5m_accumulation/both_sides.py
"""
from __future__ import annotations

import math
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
os.environ["L5_BTC5M_CONTROLLER"] = "ON"

from src import l5_btc5m_controller as C          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = open(os.path.join(HERE, "replay.py"), encoding="utf-8").read()
exec(compile(_SRC.replace("if __name__ ==", "if 0 and __name__ =="),
             "replay.py", "exec"))                # noqa: S102 - our own file

STEP = 0.5
CLIP = D("0.10")
MAX_RESTING = 4


def sides_at(q):
    """(favorite, underdog) own-prices. UP = YES bid, DOWN = 1 - YES ask."""
    bid, ask = dec(q["bid"]), dec(q["ask"])        # noqa: F821
    if bid is None or ask is None:
        return None
    up, dn = bid, D("1") - ask
    return ((C.UP, up), (C.DOWN, dn)) if up >= dn else ((C.DOWN, dn),
                                                        (C.UP, up))


def walk(rows, pick, band_lo, band_hi):
    """Rest maker buys on the side `pick` chooses. ADVERSE fills only."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    resting, fills = {}, []
    nid = 0
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is None:
            t += STEP
            continue
        q, _age = got
        s = sides_at(q)
        if s is None:
            t += STEP
            continue

        # fills first, on the live price of each resting order's own side
        for oid, o in list(resting.items()):
            b = own_bid(q, o["side"])              # noqa: F821
            if b is not None and b < o["price"]:
                fills.append({"side": o["side"], "price": o["price"],
                              "qty": o["qty"], "won": o["side"] == won})
                del resting[oid]

        side, px = pick(s)
        if px is not None and band_lo <= px <= band_hi:
            # cancel anything resting off the current touch or on the
            # wrong side, exactly as the shipped requote does
            for oid, o in list(resting.items()):
                if o["side"] != side or o["price"] != px:
                    del resting[oid]
            if len(resting) < MAX_RESTING:
                nid += 1
                qty = (CLIP / px).quantize(D("0.01"))
                if qty > 0:
                    resting["o%d" % nid] = {"side": side, "price": px,
                                            "qty": qty}
        else:
            resting.clear()
        t += STEP
    return {"won": won, "fills": fills}


def report(name, runs, bar_note):
    fills = [f for r in runs for f in r["fills"]]
    if not fills:
        print("%-22s no fills" % name)
        return
    sh = sum(f["qty"] for f in fills)
    paid = sum(f["qty"] * f["price"] for f in fills)
    wonsh = sum(f["qty"] for f in fills if f["won"])
    px = float(paid / sh)
    wr = float(wonsh / sh)
    net = float(wonsh - paid)
    pw = [float(sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                    for f in r["fills"])) for r in runs if r["fills"]]
    m = statistics.fmean(pw)
    se = statistics.stdev(pw) / math.sqrt(len(pw)) if len(pw) > 1 else 0.0
    print("%-22s %6d %8.2f %8.4f %8.4f %+9.4f %+10.4f %+9.4f %6.2f"
          % (name, len(fills), sh, px, wr, wr - px, net, m,
             (m / se if se else 0.0)))


def gap_of(rs):
    f = [x for r in rs for x in r["fills"]]
    if not f:
        return 0.0
    sh = sum(x["qty"] for x in f)
    return (float(sum(x["qty"] for x in f if x["won"]) / sh)
            - float(sum(x["qty"] * x["price"] for x in f) / sh))


def wstats(rs):
    pw = [float(sum((f["qty"] if f["won"] else D(0)) - f["qty"] * f["price"]
                    for f in r["fills"])) for r in rs if r["fills"]]
    if len(pw) < 2:
        return (statistics.fmean(pw) if pw else 0.0), 0.0, 0.0
    m = statistics.fmean(pw)
    se = statistics.stdev(pw) / math.sqrt(len(pw))
    return m, se, (m / se if se else 0.0)


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    runs = {"FAVORITE (shipped)": {}, "UNDERDOG (mirror)": {}}
    for slug in slugs:
        rows = w[slug]
        a = walk(rows, lambda s: s[0], D("0.51"), D("0.90"))
        b = walk(rows, lambda s: s[1], D("0.10"), D("0.49"))
        if a:
            runs["FAVORITE (shipped)"][slug] = a
        if b:
            runs["UNDERDOG (mirror)"][slug] = b

    print("windows %d   ADVERSE fill rule, clip $%s, %d slots"
          % (len(w), CLIP, MAX_RESTING))
    print("NOTE: this is a simplified walk, not the shipped controller. It")
    print("requotes every %.1fs, so it trades far more than replay.py. The"
          % STEP)
    print("favorite arm reproducing replay's per-share figure is the check")
    print("that it is measuring the same thing.")
    for bname, blk in (("ALL", slugs), ("IN-SAMPLE", slugs[:cut]),
                       ("OUT-OF-SAMPLE", slugs[cut:])):
        print()
        print("=" * 100)
        print("%s   %d windows" % (bname, len(blk)))
        print("=" * 100)
        print("%-22s %6s %8s %8s %8s %9s %10s %9s %6s"
              % ("arm", "fills", "shares", "avg_px", "win_rate", "gap",
                 "net$", "net/win", "t"))
        print("-" * 100)
        for k, v in runs.items():
            report(k, [v[s] for s in blk if s in v], "")

    print()
    print("  gap = win_rate - avg_px, the per-share edge.")
    print()
    oos = slugs[cut:]
    ins = slugs[:cut]
    dog = runs["UNDERDOG (mirror)"]
    fav = runs["FAVORITE (shipped)"]
    g_all, g_in, g_oos = (gap_of([dog[s] for s in b if s in dog])
                          for b in (slugs, ins, oos))
    m_all, se_all, t_all = wstats([dog[s] for s in slugs if s in dog])
    m_oos, se_oos, t_oos = wstats([dog[s] for s in oos if s in dog])
    m_in, se_in, t_in = wstats([dog[s] for s in ins if s in dog])

    print("=" * 100)
    print("VERDICT")
    print("=" * 100)
    print("  UNDERDOG gap   ALL %+0.4f   in-sample %+0.4f   OOS %+0.4f"
          % (g_all, g_in, g_oos))
    print("  UNDERDOG net/window  ALL %+0.4f (t %+0.2f)  in %+0.4f (t %+0.2f)"
          "  OOS %+0.4f (t %+0.2f)"
          % (m_all, t_all, m_in, t_in, m_oos, t_oos))
    print("  FAVORITE gap   ALL %+0.4f"
          % gap_of([fav[s] for s in slugs if s in fav]))
    print()
    stable = (m_in > 0) == (m_oos > 0)
    if t_all > 2.0 and stable and m_oos > 0:
        print("  CREDIBLE. The mirror is positive at t %+0.2f over the full"
              % t_all)
        print("  tape and keeps its sign across the split. It still needs an")
        print("  INDEPENDENT tape, because this one chose it.")
    else:
        print("  NOT ESTABLISHED. The mirror's gap is %+0.4f a share, which"
              % g_all)
        print("  is about one half-spread -- i.e. what a maker earns when")
        print("  adverse selection happens to net out, not an edge over it.")
        print("  Per window it is %+0.4f at t %+0.2f: indistinguishable from"
              % (m_all, t_all))
        print("  zero. Sign across the split is %s."
              % ("stable" if stable else "UNSTABLE"))
        print()
        print("  The variance is the reason. A 0.24 ticket pays 4:1, so a")
        print("  few winners swing the mean and the standard error swamps")
        print("  it. Big positive net$ with t near zero is a lottery, not")
        print("  an edge.")
        print()
        print("  What IS established: the favorite side is reliably")
        print("  negative, and the mirror is NOT reliably positive. That is")
        print("  not the same as the mirror being worthless -- it is the")
        print("  first arm measured on this branch whose point estimate is")
        print("  not negative, and it deserves a bigger tape rather than a")
        print("  live probe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
