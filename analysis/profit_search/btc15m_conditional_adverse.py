"""Conditional adverse selection on BTC15m — is there a tradeable sub-regime?

WHY THIS AND NOT ANOTHER AGGREGATE. The measured figure that killed the maker
strategy is an AGGREGATE: -$0.0827 per filled contract over 956 fills
(ADVERSE_SELECTION_MEASUREMENT_20261024.md), against $0.0081 of capture. An
aggregate can hide structure, and the one dimension nobody has conditioned on is
TIME TO EXPIRY -- which matters here because the market's own sharpness varies
enormously across a 15-minute window (Brier 0.1299 at 300-900s falling to
0.00032 at 15-30s, measured on venue-native labels).

So the question this answers is narrow and decidable: **is there a (tte, price)
cell of BTC15m where half the spread exceeds the adverse selection a resting
maker actually suffers?** If yes, that is an executable sub-regime of the lane
we are already instrumented for. If no, BTC15m maker execution is dead at every
tte, not just on average, and that is worth knowing with the same certainty.

WHAT IS MEASURED, AND WHAT IS NOT ASSUMED
-----------------------------------------
A maker resting AT THE TOUCH is filled only by volume that actually reaches it,
using the identical rule as replay_b.consumes -- never by a print merely passing
nearby:

    resting UP   at yes_bid  <- filled by a 'no' taker print at px <= yes_bid
    resting DOWN at no_bid   <- filled by a 'yes' taker print at px >= yes_ask

Adverse selection is then the subsequent move of the MID against the side we
were filled on, per contract, at several horizons. No settlement label is used
or needed: this is a book-and-tape measurement, so all 48 KXBTC15M windows are
eligible regardless of how their outcome was labelled. That sidesteps the
cross-venue label contamination entirely.

Capture is half the spread AT THE FILL, which is what a maker at the touch earns
if the mid is fair. The comparison is per contract and fee-free on both sides,
because PMUS charges a maker nothing and the credited rebate (<= $0.02/print,
rounding DOWN below ~$0.008) is too small to change any sign here.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))

#: the aggregate this is conditioning, for reference
AGG_ADVERSE = 0.0827
AGG_SOURCE = "analysis/l5/ADVERSE_SELECTION_MEASUREMENT_20261024.md"

TTE_BUCKETS = [(0, 30), (30, 60), (60, 120), (120, 300), (300, 600), (600, 900)]
HORIZONS = (10.0, 30.0, 60.0)


def mid_at(frames, t):
    """Last book at or before t -> YES mid. None before the first frame."""
    best = None
    for fr in frames:
        if fr[0] <= t:
            best = fr
        else:
            break
    if best is None:
        return None
    return (best[1] + (1.0 - best[3])) / 2.0


def main():
    ws = json.load(open(os.path.join(HERE, "windows.json")))
    ws = [w for w in ws if w["series"] == "KXBTC15M"]
    print(f"KXBTC15M windows: {len(ws)}  "
          f"(settlement labels NOT used -- this is a book-and-tape measurement)")

    fills = []
    for w in ws:
        frames = [(t, float(a), float(b), float(c), float(d))
                  for t, a, b, c, d in w["bbo"]]
        prints = [(t, float(p), float(q), sd) for t, p, q, sd in w["prints"]]
        if not frames or not prints:
            continue
        close = w["close"]
        fi = 0
        for pt, px, pq, sd in prints:
            while fi + 1 < len(frames) and frames[fi + 1][0] <= pt:
                fi += 1
            t, yb, ybq, nb, nbq = frames[fi]
            if t > pt:
                continue
            ya = 1.0 - nb
            spread = ya - yb
            if spread <= 0 or yb <= 0 or nb <= 0:
                continue
            mid0 = (yb + ya) / 2.0
            tte = close - pt
            if tte < 0:
                continue
            # which resting side would this print have filled?
            if sd == "no" and px <= yb + 1e-9:
                side, own = "UP", yb
            elif sd == "yes" and px >= ya - 1e-9:
                side, own = "DOWN", nb
            else:
                continue
            # QUEUE POSITION IS THE WHOLE GAME. A print that merely touches our
            # level fills us only if we are at the FRONT of the queue. A print
            # that CONSUMES the level fills us wherever we sit. The committed
            # measurement found 98.0% of its real fills came from a sweep, so
            # the sweep subset is the realistic fill condition and the
            # touch subset is an optimistic upper bound on how benign our
            # counterparty flow is.
            touch_qty = ybq if side == "UP" else nbq
            swept = bool(touch_qty > 0 and pq >= touch_qty)
            row = {"tte": tte, "side": side, "own": own, "qty": pq,
                   "spread": spread, "mid0": mid0, "px": px,
                   "swept": swept, "touch_qty": touch_qty}
            for h in HORIZONS:
                m1 = mid_at(frames, pt + h)
                if m1 is None:
                    row[f"adv{int(h)}"] = None
                    continue
                # adverse = mid moved AGAINST the side we were filled on
                row[f"adv{int(h)}"] = (mid0 - m1) if side == "UP" else (m1 - mid0)
            fills.append(row)

    print(f"maker fills reconstructed (volume actually reaching the touch): "
          f"{len(fills)}")
    if not fills:
        return 1
    tq = sum(f["qty"] for f in fills)
    print(f"total contracts {tq:.0f}   "
          f"UP {sum(1 for f in fills if f['side']=='UP')} / "
          f"DOWN {sum(1 for f in fills if f['side']=='DOWN')}")

    print(f"\nAGGREGATE, for comparison with the committed figure "
          f"(-${AGG_ADVERSE} per filled contract):")
    for h in HORIZONS:
        v = [f[f"adv{int(h)}"] for f in fills if f[f"adv{int(h)}"] is not None]
        if v:
            print(f"  adverse at +{int(h):3d}s : mean ${statistics.mean(v):+.4f}  "
                  f"median ${statistics.median(v):+.4f}  n={len(v)}")
    sp = [f["spread"] for f in fills]
    print(f"  half-spread at fill   : mean ${statistics.mean(sp)/2:.4f}  "
          f"median ${statistics.median(sp)/2:.4f}")

    print("\nBY TIME TO EXPIRY -- the question this exists to answer")
    hdr = (f"{'tte bucket':>13s} {'fills':>6s} {'ctr':>8s} {'half_spr':>9s} "
           f"{'adv+30s':>9s} {'NET':>9s} {'adv+60s':>9s} {'NET60':>9s}")
    print(hdr); print("-" * len(hdr))
    out = {}
    for lo, hi in TTE_BUCKETS:
        g = [f for f in fills if lo <= f["tte"] < hi]
        if len(g) < 5:
            continue
        hs = statistics.mean([f["spread"] for f in g]) / 2.0
        a30 = [f["adv30"] for f in g if f["adv30"] is not None]
        a60 = [f["adv60"] for f in g if f["adv60"] is not None]
        m30 = statistics.mean(a30) if a30 else float("nan")
        m60 = statistics.mean(a60) if a60 else float("nan")
        out[f"{lo}-{hi}s"] = dict(fills=len(g), hs=hs, adv30=m30, adv60=m60,
                                  net30=hs - m30, net60=hs - m60)
        print(f"{lo:>5d}-{hi:<7d} {len(g):6d} "
              f"{sum(f['qty'] for f in g):8.0f} {hs:9.4f} "
              f"{m30:+9.4f} {hs-m30:+9.4f} {m60:+9.4f} {hs-m60:+9.4f}")

    print("\nBY PRICE BAND (|mid - 0.50|)")
    hdr2 = (f"{'band':>13s} {'fills':>6s} {'half_spr':>9s} {'adv+30s':>9s} "
            f"{'NET':>9s}")
    print(hdr2); print("-" * len(hdr2))
    for lo, hi in [(0.0, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 0.50)]:
        g = [f for f in fills if lo <= abs(f["mid0"] - 0.5) < hi]
        if len(g) < 5:
            continue
        hs = statistics.mean([f["spread"] for f in g]) / 2.0
        a = [f["adv30"] for f in g if f["adv30"] is not None]
        m = statistics.mean(a) if a else float("nan")
        print(f"{lo:>5.2f}-{hi:<7.2f} {len(g):6d} {hs:9.4f} {m:+9.4f} "
              f"{hs-m:+9.4f}")

    # ---- the realistic fill condition: sweeps only
    sw = [f for f in fills if f["swept"]]
    print(f"\nSWEEP-CONDITIONED -- the realistic fill condition "
          f"({len(sw)} of {len(fills)} = {100.0*len(sw)/len(fills):.1f}% of "
          f"touch-reaching prints consume the level)")
    print("  The committed measurement found 98.0% of its REAL fills were "
          "sweeps, so this\n  subset, not the one above, is what a resting "
          "order actually gets.")
    if sw:
        hdr3 = (f"{'tte bucket':>13s} {'fills':>6s} {'half_spr':>9s} "
                f"{'adv+30s':>9s} {'NET':>9s} {'adv+60s':>9s} {'NET60':>9s}")
        print(hdr3); print("-" * len(hdr3))
        for lo, hi in TTE_BUCKETS:
            g = [f for f in sw if lo <= f["tte"] < hi]
            if len(g) < 5:
                continue
            hs = statistics.mean([f["spread"] for f in g]) / 2.0
            a30 = [f["adv30"] for f in g if f["adv30"] is not None]
            a60 = [f["adv60"] for f in g if f["adv60"] is not None]
            m30 = statistics.mean(a30) if a30 else float("nan")
            m60 = statistics.mean(a60) if a60 else float("nan")
            print(f"{lo:>5d}-{hi:<7d} {len(g):6d} {hs:9.4f} "
                  f"{m30:+9.4f} {hs-m30:+9.4f} {m60:+9.4f} {hs-m60:+9.4f}")
        a = [f["adv30"] for f in sw if f["adv30"] is not None]
        hs = statistics.mean([f["spread"] for f in sw]) / 2.0
        print(f"  ALL sweeps: half-spread ${hs:.4f}  "
              f"adverse+30s ${statistics.mean(a):+.4f}  "
              f"NET ${hs-statistics.mean(a):+.4f}  n={len(a)}")

    pos = [k for k, v in out.items() if v["net30"] > 0]
    print(f"\ntte cells with POSITIVE net at +30s: "
          f"{pos if pos else 'NONE'}")
    json.dump(dict(windows=len(ws), fills=len(fills), contracts=tq,
                   aggregate_reference=AGG_ADVERSE, source=AGG_SOURCE,
                   by_tte=out), open(os.path.join(
                       HERE, "inventory", "btc15m_conditional_adverse.json"),
                       "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
