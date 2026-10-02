"""E = EXIT/REPRICING model and C = CONTINUOUS INVENTORY, frozen rules.

Runs on the FULL 176-window ref_lag cohort (book + settlement). Neither test
needs the strike, so neither is limited by the 38-window strike constraint
that made the BTC-state settlement test underpowered.

E -- EXIT: a settlement prediction is not required if the contract reprices.
  Round-trip arithmetic, stated before measurement:
    taker in / taker out  = buy ask, sell bid  -> -spread before any move
    maker in / maker out  = buy bid, sell ask  -> +spread, needs 2 queue fills
  So a TAKER round trip is profitable only if the mid moves MORE than the
  spread in our favour. That is the quantity measured here.

C -- CONTINUOUS INVENTORY: do favorite flips ever produce maker/maker matched
  inventory at useful frequency? A maker/maker pair is the ONLY matched
  construction with positive economics (cost 1 - spread). Requires a
  defensible maker fill on BOTH sides within one window.
"""
from __future__ import annotations

import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, "analysis/gemini/favorite_inventory")
from fi_replay import load, PHASES
from fi_core import Book, Inventory, make_fill, ONE

FWD = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
LAT = (0.25, 1.0)          # realistic submission latencies, seconds


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def main():
    W, spot = load()
    usable = {m: w for m, w in W.items()
              if w["expiry_t"] and w["resolution"] and w["book"]}
    order = sorted(usable.items(), key=lambda kv: kv[1]["expiry_t"])
    n = len(order)
    splits = {"DEV": dict(order[:int(n * 0.6)]),
              "VALIDATION": dict(order[int(n * 0.6):])}
    print("=" * 80)
    print("E = EXIT/REPRICING   and   C = CONTINUOUS INVENTORY   (frozen rules)")
    print("full cohort: book + settlement; strike NOT required")
    print("=" * 80)

    for sname, cohort in splits.items():
        # ---------------- E: forward executable repricing ----------------
        moves = defaultdict(list)
        rt = defaultdict(list)
        spreads = []
        nw = 0
        for m, w in cohort.items():
            exp = w["expiry_t"]; start = exp - 300.0
            bts = [b[0] for b in w["book"]]
            nw += 1
            for i, (t, bk) in enumerate(w["book"]):
                if not (start <= t <= exp) or not bk.two_sided:
                    continue
                spreads.append(float(bk.spread))
                mid0 = float(bk.mid)
                # taker entry on the favorite side, then taker exit later
                fav = "UP" if bk.mid > D("0.5") else "DOWN"
                entry = bk.price_for(fav, "taker")
                for h in FWD:
                    for lat in LAT:
                        j = bisect_right(bts, t + h + lat) - 1
                        if j <= i or not w["book"][j][1].two_sided:
                            continue
                        b2 = w["book"][j][1]
                        mv = float(b2.mid) - mid0
                        if lat == LAT[0]:
                            moves[h].append(mv if fav == "UP" else -mv)
                        # taker round trip: exit by SELLING what we bought
                        exit_px = (b2.bid if fav == "UP"
                                   else (ONE - b2.ask))
                        if exit_px is None or entry is None:
                            continue
                        rt[(h, lat)].append(float(exit_px - entry))
        sp50 = pct(spreads, 0.5)
        print(f"\n--- E  [{sname}]  windows={nw}  spread p50={sp50:.3f} ---")
        print(f"  {'horizon':>8s} {'n':>7s} {'fav_move_p50':>13s} {'p90':>8s} "
              f"{'P(move>spread)':>15s}")
        for h in FWD:
            v = moves[h]
            if len(v) < 50:
                continue
            frac = sum(1 for x in v if x > sp50) / len(v)
            print(f"  {int(h):>7d}s {len(v):7d} {pct(v,.5):+13.4f} "
                  f"{pct(v,.9):+8.4f} {frac*100:14.1f}%")
        print(f"\n  TAKER ROUND-TRIP P&L per contract (buy ask -> sell bid):")
        print(f"  {'horizon':>8s} {'lat':>6s} {'n':>7s} {'p50':>9s} {'mean':>9s} "
              f"{'P(>0)':>8s} {'total':>10s}")
        for h in FWD:
            for lat in LAT:
                v = rt[(h, lat)]
                if len(v) < 50:
                    continue
                print(f"  {int(h):>7d}s {lat:6.2f} {len(v):7d} {pct(v,.5):+9.4f} "
                      f"{sum(v)/len(v):+9.4f} {sum(1 for x in v if x>0)/len(v)*100:7.1f}% "
                      f"{sum(v):+10.2f}")

        # ---------------- C: do flips yield maker/maker matched? ----------
        flips = []
        mm_windows = 0
        mm_matched = D("0")
        mm_pnl = D("0")
        for m, w in cohort.items():
            exp = w["expiry_t"]; start = exp - 300.0
            fav_prev = None
            nf = 0
            for t, bk in w["book"]:
                if not (start <= t <= exp) or not bk.two_sided:
                    continue
                fav = "UP" if bk.mid > D("0.5") else "DOWN"
                if fav_prev and fav != fav_prev:
                    nf += 1
                fav_prev = fav
            flips.append(nf)
            # defensible maker fill on BOTH sides within the window?
            got = {}
            for side in ("UP", "DOWN"):
                q_ahead = None
                ref = None
                traded = D("0")
                for t, bk in w["book"]:
                    if not (start <= t <= exp) or not bk.two_sided:
                        continue
                    if ref is None:
                        ref = bk.price_for(side, "maker")
                        q_ahead = bk.depth_for(side, "maker") or D("0")
                        break
                if ref is None:
                    continue
                for (tt, ppx, pq, bim) in w["ptrade"]:
                    if not (start <= tt <= exp):
                        continue
                    passive_up = bim is True
                    if side == "UP" and not passive_up:
                        continue
                    if side == "DOWN" and passive_up:
                        continue
                    cmp_px = ref if side == "UP" else (ONE - ref)
                    through = (ppx < cmp_px) if side == "UP" else (ppx > cmp_px)
                    at = ppx == cmp_px
                    if through:
                        got[side] = (ref, min(pq, D("10")), "through_price")
                        break
                    if at:
                        traded += pq
                        if traded > q_ahead:
                            got[side] = (ref, min(traded - q_ahead, D("10")),
                                         "same_price_fifo")
                            break
            if len(got) == 2:
                mm_windows += 1
                inv = Inventory()
                for side, (px, qty, basis) in got.items():
                    inv.add(make_fill(0, side, "maker", px, qty, basis))
                inv.assert_identities()
                mm_matched += inv.matched
                mm_pnl += inv.matched_pnl() - inv.fees + inv.rebates
        print(f"\n--- C  [{sname}] favorite flips and maker/maker matched ---")
        print(f"  flips per window: p50={pct(flips,.5)} p90={pct(flips,.9)} "
              f"max={max(flips) if flips else 0}  zero-flip windows="
              f"{sum(1 for x in flips if x==0)}/{len(flips)}")
        print(f"  windows with a DEFENSIBLE maker fill on BOTH sides : "
              f"{mm_windows}/{nw}")
        print(f"  total maker/maker matched contracts                : {mm_matched}")
        print(f"  their net P&L after fees and rebates               : ${mm_pnl:+.2f}")
        need = (D("5") / D(str(sp50))) if sp50 else None
        if need:
            print(f"  contracts needed for +$5 at spread {sp50:.3f}        : "
                  f"{need:.1f} matched  ({need*2:.1f} maker fills)")


if __name__ == "__main__":
    main()
