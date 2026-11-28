"""Do the INCIDENTAL pairs cost us? Measured. READ-ONLY.

The shipped strategy is directional: hedge_enabled is False and it never
tries to form a pair. But on a favorite reversal it cancels the old side
and begins accumulating the NEW favorite while still holding the old one,
so a pair forms anyway -- and because both legs were bought at >= 0.51,
that pair costs more than 1.00 and locks a loss that no outcome undoes.

Live on 2026-11-12 every pair it formed was above par: 1.1759, 1.2197 and
1.2262, which locked -$0.4267 of the -$1.5521 settled loss. 27.5%, and
deterministic rather than variance.

THE ALTERNATIVE, which is a one-line behavioural change: once a side has
filled, never open the other one. If the held side stops being the
favorite, stop trading the window and carry the directional position to
settlement instead of pairing out of it.

  SWITCH (shipped)  follow the favorite wherever it goes; pairs form
  LOCKED            commit to the first side filled; no pair can form

LOCKED is not obviously better. It gives up the chance to accumulate the
new favorite at a good price for the rest of the window, and it leaves a
naked directional position exactly when the market has turned against it.
That is the trade this measures.

Usage: python3 -I analysis/l5/btc5m_accumulation/no_switch.py
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
BAND_LO, BAND_HI = D("0.51"), D("0.90")


def favourite(q):
    bid, ask = dec(q["bid"]), dec(q["ask"])        # noqa: F821
    if bid is None or ask is None:
        return None
    up, dn = bid, D("1") - ask
    return (C.UP, up) if up >= dn else (C.DOWN, dn)


def walk(rows, locked):
    """`locked` True = never open a second side once one has filled."""
    won = outcome(rows)                            # noqa: F821
    if won is None:
        return None
    inv = {C.UP: [], C.DOWN: []}
    rest = {}
    held = None
    t = 0.0
    while t < WINDOW_S - CANCEL_LEAD_S:            # noqa: F821
        got = quote_at(rows, t)                    # noqa: F821
        if got is None:
            t += STEP
            continue
        q, _age = got
        f = favourite(q)
        if f is None:
            t += STEP
            continue
        side, px = f

        for s in (C.UP, C.DOWN):                   # fills first
            p = rest.get(s)
            b = own_bid(q, s)                      # noqa: F821
            if p is not None and b is not None and b < p:
                inv[s].append(p)
                if held is None:
                    held = s
                del rest[s]

        allowed = (not locked) or held is None or side == held
        if allowed and BAND_LO <= px <= BAND_HI and len(inv[side]) < MAX_RESTING:
            for s in list(rest):
                if s != side:
                    del rest[s]
            rest[side] = px
        else:
            rest.clear()
        t += STEP

    up, dn = sorted(inv[C.UP]), sorted(inv[C.DOWN])
    npair = min(len(up), len(dn))
    pairs = [(up[i], dn[i]) for i in range(npair)]
    pair_pnl = float(sum(D("1") - (a + c) for a, c in pairs)) * float(CLIP)
    res_side = C.UP if len(up) > npair else C.DOWN
    res = up[npair:] if len(up) > npair else dn[npair:]
    res_pnl = float(sum((D("1") if res_side == won else D(0)) - p
                        for p in res))
    # clip sizing: each entry is CLIP dollars, so qty = CLIP/price
    res_pnl = float(sum(((D("1") if res_side == won else D(0)) - p)
                        * (CLIP / p) for p in res))
    pair_pnl = float(sum((D("1") - (a + c)) * (CLIP / a) for a, c in pairs))
    return {"pairs": pairs, "pair_pnl": pair_pnl, "res_pnl": res_pnl,
            "net": pair_pnl + res_pnl, "n_res": len(res)}


def stats(v):
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, (m / se if se else 0.0)


def line(name, rs):
    if not rs:
        return
    npair = sum(len(r["pairs"]) for r in rs)
    pcost = [float(a + c) for r in rs for a, c in r["pairs"]]
    pp = sum(r["pair_pnl"] for r in rs)
    rp = sum(r["res_pnl"] for r in rs)
    m, se, t = stats([r["net"] for r in rs])
    print("%-22s %5d %7d %9s %9.4f %+9.4f %+9.4f %+9.4f %6.2f"
          % (name, len(rs), npair,
             ("%d above par" % sum(1 for p in pcost if p > 1.0))
             if pcost else "-",
             statistics.fmean(pcost) if pcost else 0.0, pp, rp, m, t))


def main():
    w = load(TAPE)                                 # noqa: F821
    slugs = sorted(w)
    cut = int(len(slugs) * 0.67)
    arms = {"SWITCH (shipped)": {}, "LOCKED (no 2nd side)": {}}
    for s in slugs:
        a = walk(w[s], locked=False)
        b = walk(w[s], locked=True)
        if a:
            arms["SWITCH (shipped)"][s] = a
        if b:
            arms["LOCKED (no 2nd side)"][s] = b

    print("windows %d   clip $%s, %d slots, band %s-%s"
          % (len(slugs), CLIP, MAX_RESTING, BAND_LO, BAND_HI))
    for bname, blk in (("ALL", slugs), ("IN-SAMPLE", slugs[:cut]),
                       ("OUT-OF-SAMPLE", slugs[cut:])):
        print()
        print("=" * 104)
        print("%s   %d windows" % (bname, len(blk)))
        print("=" * 104)
        print("%-22s %5s %7s %9s %9s %9s %9s %9s %6s"
              % ("arm", "wins", "pairs", "", "pair_cost", "pair$",
                 "resid$", "net/win", "t"))
        print("-" * 104)
        print("%-22s %5d %7d %9s %9.4f %+9.4f %+9.4f %+9.4f %6.2f"
              % ("NO-TRADE", len(blk), 0, "-", 0.0, 0.0, 0.0, 0.0, 0.0))
        for k, v in arms.items():
            line(k, [v[s] for s in blk if s in v])

    sw = arms["SWITCH (shipped)"]
    lk = arms["LOCKED (no 2nd side)"]
    both = [s for s in slugs if s in sw and s in lk]
    d = [lk[s]["net"] - sw[s]["net"] for s in both]
    m, se, t = stats(d)
    di = stats([lk[s]["net"] - sw[s]["net"]
                for s in slugs[:cut] if s in sw and s in lk])
    do = stats([lk[s]["net"] - sw[s]["net"]
                for s in slugs[cut:] if s in sw and s in lk])
    print()
    print("=" * 104)
    print("LOCKED minus SWITCH, paired on the same windows")
    print("=" * 104)
    print("  ALL           %+0.4f a window   se %.4f   t %+0.2f   n %d"
          % (m, se, t, len(d)))
    print("  IN-SAMPLE     %+0.4f (t %+0.2f)" % (di[0], di[2]))
    print("  OUT-OF-SAMPLE %+0.4f (t %+0.2f)" % (do[0], do[2]))
    better = sum(1 for x in d if x > 1e-9)
    print("  LOCKED better in %d windows, worse in %d, equal in %d"
          % (better, sum(1 for x in d if x < -1e-9),
             len(d) - better - sum(1 for x in d if x < -1e-9)))
    print()
    stable = (di[0] > 0) == (do[0] > 0)
    if m > 0 and t > 2.0 and stable:
        print("  REFUSING THE SECOND SIDE HELPS, and the sign holds across")
        print("  the split. The incidental pair is a real, avoidable cost.")
        print("  Note what it does NOT claim: both arms may still be")
        print("  negative. Removing a locked loss raises the floor; it does")
        print("  not create an edge.")
    else:
        print("  NOT ESTABLISHED: %+0.4f a window at t %+0.2f, sign %s."
              % (m, t, "stable" if stable else "UNSTABLE"))
        print("  Refusing the second side removes the locked pair loss but")
        print("  gives up the rest of the window, and on this tape those")
        print("  roughly cancel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
