"""The favorite rule, tested on the last five minutes of recorded PMUS BTC 15m books.

THE RULE AS THE OWNER DESCRIBED IT FOR BTC 5m: buy the currently favored
outcome when its best bid is 0.51 to 0.90, with a passive order 2-4 ticks
below that bid, reassessing every 10-30 seconds.

WHY THESE BOOKS. No BTC 5m book has been recorded yet. A binary on BTC
against a strike is described by its price and the time left, so the last
five minutes of a 15m market (300 s or less to settlement, favorite between
0.51 and 0.90) are the closest recorded states on this venue. It is a proxy,
and it stops where the recording stops (median T+816 s of 900).

WHAT IS MODELLED. Only fills. "through": a resting buy fills when the
favored side's ask trades BELOW its price before the next reassessment.
"touch": it fills when the ask reaches the price -- the generous bound. Maker
fee zero (confirmed on this account); no rebate (none is credited at this
clip). Shares are held to settlement; the favorite can flip and the rule then
buys the other side. Replay, not realized.
"""
from __future__ import annotations

import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOKS = json.loads((ROOT / "data" / "l5" / "recent" / "wide_20261111"
                    / "books_380.json").read_text())
START_S, EVERY_S, CLIP = 600.0, 20.0, 0.10
BAND = (0.51, 0.90)


def favorite(bid, ask):
    """(side, its bid, its ask) of the outcome priced higher."""
    up_bid, up_ask, dn_bid, dn_ask = bid, ask, 1 - ask, 1 - bid
    return ("UP", up_bid, up_ask) if up_bid >= dn_bid else ("DOWN", dn_bid, dn_ask)


def side_ask(side, bid, ask):
    return ask if side == "UP" else 1 - bid


def calibration():
    cells = {}
    for slug, w in BOOKS.items():
        seen = set()
        for t, b, a in w["rows"]:
            if t < START_S:
                continue
            slot = int((t - START_S) // 30)
            if slot in seen:
                continue
            seen.add(slot)
            side, fb, fa = favorite(float(b), float(a))
            if not BAND[0] <= fb <= BAND[1]:
                continue
            lo = min(int((fb - 0.50) * 10), 3)
            c = cells.setdefault(lo, [0, 0, 0.0, 0.0])
            c[0] += 1
            c[1] += w["winner"] == side
            c[2] += fb
            c[3] += fa
    print("1. DOES THE FAVORITE WIN MORE OFTEN THAN IT COSTS? "
          "(one sample per window per 30 s, 300 s or less to settlement)")
    for lo in sorted(cells):
        n, wins, sb, sa = cells[lo]
        p = wins / n
        se = (p * (1 - p) / n) ** 0.5
        print(f"   favorite bid {0.5 + lo / 10:.2f}-{0.6 + lo / 10:.2f}  "
              f"samples {n:5d}  won {p:5.1%} (+/-{1.96 * se:4.1%})  "
              f"mean bid {sb / n:.3f}  mean ask {sa / n:.3f}  "
              f"edge buying at the bid {100 * (p - sb / n):+5.1f}c  "
              f"at the ask {100 * (p - sa / n):+5.1f}c per share")


def run(depth_ticks, fill):
    out = []
    for slug in sorted(BOOKS):
        rows = [(t, float(b), float(a)) for t, b, a in BOOKS[slug]["rows"]
                if t >= START_S]
        if len(rows) < 5:
            continue
        pos = {"UP": 0.0, "DOWN": 0.0}
        cost = 0.0
        resting = None                      # (side, price)
        nxt = START_S
        orders = 0
        for t, b, a in rows:
            if resting is not None:
                side, px = resting
                ask_now = side_ask(side, b, a)
                if (ask_now < px - 1e-9) if fill == "through" else \
                        (ask_now <= px + 1e-9):
                    pos[side] += CLIP
                    cost += CLIP * px
                    resting = None
            if t >= nxt:
                nxt = t + EVERY_S
                resting = None              # cancel and reassess
                side, fb, _ = favorite(b, a)
                if BAND[0] <= fb <= BAND[1]:
                    resting = (side, round(fb - depth_ticks * 0.01, 2))
                    orders += 1
        if pos["UP"] + pos["DOWN"] <= 0:
            out.append({"slug": slug, "filled": False, "orders": orders})
            continue
        pnl = pos[BOOKS[slug]["winner"]] - cost
        out.append({"slug": slug, "filled": True, "orders": orders,
                    "shares": pos["UP"] + pos["DOWN"], "cost": cost,
                    "both_sides": pos["UP"] > 0 and pos["DOWN"] > 0,
                    "pnl": pnl})
    return out


def report(tag, res):
    f = [r for r in res if r["filled"]]
    if not f:
        print(f"   {tag}: no fills")
        return
    pnl = [r["pnl"] for r in f]
    shares = sum(r["shares"] for r in f)
    mean = st.mean(pnl)
    se = st.stdev(pnl) / len(pnl) ** 0.5 if len(pnl) > 1 else 0.0
    run_, worst = 0.0, 0.0
    peak = 0.0
    for p in pnl:
        run_ += p
        peak = max(peak, run_)
        worst = min(worst, run_ - peak)
    print(f"   {tag:34s} windows with a fill {len(f):3d}/{len(res)}  "
          f"orders {sum(r['orders'] for r in res):5d}  shares {shares:6.1f}  "
          f"ahead {sum(1 for p in pnl if p > 0) / len(f):4.0%}  "
          f"total {sum(pnl):+6.2f}  per window {100 * mean:+5.2f}c "
          f"[{100 * (mean - 1.96 * se):+5.2f}, {100 * (mean + 1.96 * se):+5.2f}]"
          f"  per share {100 * sum(pnl) / shares:+5.1f}c  "
          f"both sides held {sum(1 for r in f if r['both_sides']) / len(f):4.0%}"
          f"  worst run {worst:+5.2f}")


def main():
    calibration()
    print(f"\n2. THE RULE: every {EVERY_S:.0f} s from T+{START_S:.0f}, a "
          f"{CLIP} share buy below the favorite's bid, held to settlement")
    for depth in (2, 3, 4):
        for fill in ("through", "touch"):
            res = run(depth, fill)
            report(f"{depth} ticks below the bid, {fill} fills", res)
    print("   halves, 3 ticks below the bid:")
    for fill in ("through", "touch"):
        res = run(3, fill)
        h = len(res) // 2
        report(f"  first half, {fill}", res[:h])
        report(f"  second half, {fill}", res[h:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
