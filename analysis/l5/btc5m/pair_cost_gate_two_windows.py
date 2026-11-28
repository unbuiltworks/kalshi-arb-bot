"""What a completion pair-cost gate would have done to the two live windows.

Inputs are the venue-confirmed fills of cpc-btc-updown-5m-2026-11-21-0620z
and -1420z (all maker, fee 0). The gate is the controller's existing
`straddle_max_completed_pair_cost` rule: a fill on the LIGHTER side (one that
completes held inventory) is refused when its price plus the average cost of
the heavier side exceeds the cap.

LIMITS, stated so the output is not over-read:
  * a refused fill changes the inventory, so later orders would have been
    different; the replay keeps the later REAL fills and applies the same
    gate to them against the counterfactual inventory. It cannot invent
    fills that never happened (e.g. a completion resting lower and filling
    later), so it understates what a resting-at-cap quote might add.
  * two windows. This shows mechanics and both settlement outcomes; it is
    not an estimate of expected value.
Accounting: total redemption minus total cost, over every fill kept.
"""
from decimal import Decimal as D

W1 = ("0620z", "DOWN", [
    ("DOWN", "0.08", "0.40"), ("UP", "0.07", "0.66"), ("DOWN", "0.07", "0.33"),
    ("DOWN", "0.06", "0.20"), ("UP", "0.06", "0.79"), ("UP", "0.06", "0.81"),
    ("UP", "0.06", "0.79"), ("DOWN", "0.06", "0.20"), ("UP", "0.06", "0.82"),
    ("DOWN", "0.08", "0.39"), ("UP", "0.08", "0.60"), ("DOWN", "0.08", "0.61"),
    ("DOWN", "0.06", "0.72"), ("DOWN", "0.07", "0.67"), ("UP", "0.05", "0.10"),
    ("DOWN", "0.05", "0.93"), ("UP", "0.05", "0.06")])
W2 = ("1420z", "UP", [
    ("DOWN", "0.08", "0.60"), ("DOWN", "0.08", "0.60"), ("UP", "0.08", "0.38"),
    ("DOWN", "0.08", "0.58"), ("UP", "0.09", "0.45"), ("UP", "0.05", "0.94"),
    ("UP", "0.05", "0.93"), ("UP", "0.05", "0.93"), ("DOWN", "0.05", "0.06"),
    ("DOWN", "0.05", "0.06"), ("UP", "0.05", "0.97"), ("DOWN", "0.05", "0.06")])


UNMATCHED_CAP = D("0.10")


def run(fills, cap):
    q = {"UP": D(0), "DOWN": D(0)}
    c = {"UP": D(0), "DOWN": D(0)}
    blocked = []
    for side, qty, px in fills:
        qty, px = D(qty), D(px)
        o = "DOWN" if side == "UP" else "UP"
        if cap is not None and q[o] > q[side]:        # completes held inventory
            if px + c[o] / q[o] > cap:
                blocked.append((side, str(qty), str(px),
                                str((px + c[o] / q[o]).quantize(D("0.001")))))
                continue
        #: THE UNMATCHED CEILING STILL APPLIES ($0.10 in both windows). With
        #: completions refused the heavy side cannot keep growing: a fill
        #: that would leave more than the ceiling unmatched (excess quantity
        #: at the heavy side's average cost) is refused too. Without this
        #: the replay would hold exposure the live limits forbid.
        nq, nc = q[side] + qty, c[side] + qty * px
        if cap is not None and nq > q[o]:
            if (nq - q[o]) * (nc / nq) > UNMATCHED_CAP:
                blocked.append((side, str(qty), str(px), "unmatched cap"))
                continue
        q[side], c[side] = nq, nc
    return q, c, blocked


def line(q, c, winner):
    cost = c["UP"] + c["DOWN"]
    m = min(q["UP"], q["DOWN"])
    pair = (None if m == 0 else c["UP"] / q["UP"] + c["DOWN"] / q["DOWN"])
    return {"up": q["UP"], "down": q["DOWN"], "cost": cost,
            "matched": m, "pair_cost": pair,
            "net_if_UP": q["UP"] - cost, "net_if_DOWN": q["DOWN"] - cost,
            "net_actual_outcome": q[winner] - cost}


def fmt(x):
    return "   n/a" if x is None else "%+.4f" % x if isinstance(x, D) and (
        x < 0 or True) else str(x)


if __name__ == "__main__":
    tot = {}
    for name, winner, fills in (W1, W2):
        print("== %s (settled %s) ==" % (name, winner))
        for cap in (None, D("1.00"), D("0.98"), D("0.96")):
            q, c, blocked = run(fills, cap)
            r = line(q, c, winner)
            tag = "no gate (actual)" if cap is None else "cap %s" % cap
            print("%-17s UP %.2f DOWN %.2f cost %.4f matched %.2f pair %s | "
                  "net if UP %+.4f, if DOWN %+.4f | actual outcome %+.4f | "
                  "blocked %d" % (
                      tag, r["up"], r["down"], r["cost"], r["matched"],
                      "n/a" if r["pair_cost"] is None else
                      "%.3f" % r["pair_cost"],
                      r["net_if_UP"], r["net_if_DOWN"],
                      r["net_actual_outcome"], len(blocked)))
            tot.setdefault(tag, D(0))
            tot[tag] += r["net_actual_outcome"]
            if cap in (D("0.98"), D("0.96")):
                for b in blocked:
                    print("      blocked %s %s @ %s (pair %s)" % b)
    print("== both windows, actual outcomes ==")
    for k, v in tot.items():
        print("%-17s %+.4f" % (k, v))
