"""Balanced pairs vs favorite-weighted vs underdog-weighted accumulation.

THE QUESTION. Phase 3 asks for probability-aware favorite accumulation and
complementary underdog hedging, with unequal inventory allowed when its
conservative EV and worst case justify it. That is a strategy change, so it
is measured BEFORE it is implemented, and rejected if it does not pay.

THE ARITHMETIC FIRST, because it says what to look for. Let the market's own
implied probability of UP be `p`, and rest each leg `delta` behind its touch,
so own prices are `u = p - delta` and `d = (1 - p) - delta`. For `n_up` UP
and `n_dn` DOWN contracts settling at `S` in {0, 1}:

    net = n_up*S + n_dn*(1 - S) - n_up*u - n_dn*d

    BALANCED (1,1):  net = 1 - (u + d) = 2*delta, for EITHER outcome.
    FAVORITE (2,1):  EV  = p + 1 - 2u - d        = 3*delta.
    worst case (2,1) when the favorite loses:     = 3*delta - p.

So under a FAIR market every leg earns its own `delta` and expectancy scales
with the TOTAL number of contracts, balanced or not. Unequal inventory buys
no extra expectancy per contract -- it buys VARIANCE, and a worst case that
grows with the favorite's own price. That is the whole case against it, and
it does not need a replay.

WHAT A REPLAY CAN STILL CHANGE. The fair-market step assumes the leg that
fills is not selected against us. It is: `QUEUE_DEPLETION_FINDINGS.md`
measures -$0.2440 per leg that fills ALONE at the deployed depth. So the
honest question is not "does weighting add expectancy" but "is the
accumulated leg's realized outcome better or worse than `delta`". This
script measures that on the same recorded books, under three weightings and
two independent signals.

SIGNALS. `mid` uses the market's own mid, which is the only calibrated
probability in this data -- so a "favorite" by that signal carries no
information the price does not already have, and that is the point of
including it as the null. `imb` uses top-of-book size imbalance, which is
genuinely independent of the mid and is the only outside signal these
recordings contain.

CONSERVATIVE THROUGHOUT. A leg fills only when PROVEN (the opposite touch
reached our price), which over-samples the adverse case. Settlement is the
window's last mid, labelled a PROXY and never a realized result. Maker fills
pay no fee under the published schedule; no rebate is assumed credited.
NOTHING HERE IS REALIZED MONEY.
"""
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from src import l5_queue_depletion as QD                   # noqa: E402
from analysis.l5.issue195.queue_depletion_report import (  # noqa: E402
    IN_SAMPLE, OUT_OF_SAMPLE, _load, _mid, _touches)

TICK = D("0.01")
DEPTH = 2
TTL_FRAMES = 60

#: (label, n_favoured, n_other, signal). `None` signal = no weighting.
VARIANTS = (
    ("balanced 1:1", 1, 1, None),
    ("favorite 2:1  (mid)", 2, 1, "mid"),
    ("underdog 1:2  (mid)", 1, 2, "mid"),
    ("favorite 2:1  (imbalance)", 2, 1, "imb"),
    ("underdog 1:2  (imbalance)", 1, 2, "imb"),
)


def _depth_sizes(frame):
    """Total displayed size on each side of the ladder."""
    b = sum(D(s) for _, s in frame["bids"])
    a = sum(D(s) for _, s in frame["asks"])
    return b, a


def _favoured_side(frame, signal):
    """Which side the signal favours, or None when it is silent."""
    t = _touches(frame)
    if t is None:
        return None
    if signal == "mid":
        m = (t[0] + t[1]) / 2
        if m == D("0.5"):
            return None
        return "UP" if m > D("0.5") else "DOWN"
    if signal == "imb":
        # more size resting on the BID than the ASK means buyers are
        # queued up: an UP-favouring imbalance. Independent of the mid.
        bs, as_ = _depth_sizes(frame)
        if bs == as_:
            return None
        return "UP" if bs > as_ else "DOWN"
    return None


def _settle(n_up, n_dn, up_px, dn_px, s):
    """Exact binary payout. `s` is the UP settlement, 0 or 1."""
    payout = (D(n_up) * s) + (D(n_dn) * (D(1) - s))
    return payout - (D(n_up) * up_px) - (D(n_dn) * dn_px)


def measure(group, n_fav, n_oth, signal):
    agg = {"n": 0, "pairs": 0, "singles": 0, "none": 0,
           "net": D(0), "contracts": D(0), "worst": D(0),
           "pair_net": D(0), "single_net": D(0), "signal_silent": 0,
           "decisive": 0, "indecisive": 0}
    for path, shape in group:
        for slug, frames in sorted(_load(path, shape).items()):
            if len(frames) < 10:
                continue
            fm = None
            for f in reversed(frames):
                fm = _mid(f)
                if fm is not None:
                    break
            if fm is None:
                continue
            # the settlement PROXY, and whether it is decisive at all
            s = D(1) if fm >= D("0.5") else D(0)
            if fm > D("0.9") or fm < D("0.1"):
                agg["decisive"] += 1
            else:
                agg["indecisive"] += 1
            for i, f in enumerate(frames[:-1]):
                tt = _touches(f)
                if tt is None:
                    continue
                bb, ba = tt
                up = bb - DEPTH * TICK
                dn = (D(1) - ba) - DEPTH * TICK
                if up <= 0 or dn <= 0:
                    continue
                if signal is None:
                    n_up, n_dn = n_fav, n_oth
                else:
                    fav = _favoured_side(f, signal)
                    if fav is None:
                        agg["signal_silent"] += 1
                        continue
                    n_up, n_dn = ((n_fav, n_oth) if fav == "UP"
                                  else (n_oth, n_fav))
                agg["n"] += 1
                vu = QD.fill_verdict("UP", up, frames, start=i)
                vd = QD.fill_verdict("DOWN", dn, frames, start=i)
                fu = (vu.get("proven_at_frame")
                      if vu.get("verdict") == QD.PROVEN else None)
                fd = (vd.get("proven_at_frame")
                      if vd.get("verdict") == QD.PROVEN else None)
                if fu is not None and fu - i > TTL_FRAMES:
                    fu = None
                if fd is not None and fd - i > TTL_FRAMES:
                    fd = None
                got_up = n_up if fu is not None else 0
                got_dn = n_dn if fd is not None else 0
                if not got_up and not got_dn:
                    agg["none"] += 1
                    continue
                net = _settle(got_up, got_dn, up, dn, s)
                agg["net"] += net
                agg["contracts"] += D(got_up + got_dn)
                agg["worst"] = min(agg["worst"], net)
                if got_up and got_dn:
                    agg["pairs"] += 1
                    agg["pair_net"] += net
                else:
                    agg["singles"] += 1
                    agg["single_net"] += net
    return agg


def table(group, label):
    print("=" * 78)
    print(label)
    print("=" * 78)
    print("  %-26s %6s %6s %7s %9s %9s %9s"
          % ("variant", "attempts", "pairs", "singles",
             "net/att", "net/contr", "worst att"))
    for name, nf, no, sig in VARIANTS:
        a = measure(group, nf, no, sig)
        if not a["n"]:
            continue
        print("  %-26s %8d %6d %7d %9s %9s %9s"
              % (name, a["n"], a["pairs"], a["singles"],
                 _q(a["net"] / a["n"]),
                 _q(a["net"] / a["contracts"]) if a["contracts"] else "n/a",
                 _q(a["worst"])))
    a = measure(group, 1, 1, None)
    print("  settlement proxy: %d windows decisive (final mid outside "
          "0.1-0.9), %d not" % (a["decisive"], a["indecisive"]))


def _q(v):
    return str(v.quantize(D("0.0001")))


if __name__ == "__main__":
    table(IN_SAMPLE, "IN SAMPLE   (2026-10-28 x3 windows, 2026-11-06 0300z)")
    print()
    table(OUT_OF_SAMPLE,
          "OUT OF SAMPLE   (2026-10-25 x12 windows, 2026-11-06 1130z)")
    print()
    print("PROVEN fills only, so pairing is a LOWER bound. Settlement is")
    print("the window's last mid -- a PROXY, not a realized result.")
    print("Overlapping entries share futures: correlated observations,")
    print("not independent trades, and the totals are not a P&L.")
