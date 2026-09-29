"""R5 1115z, four ways: actual -> cancel fix -> completion fix -> both.

DECISION HYGIENE. The window's settlement (settlement_px 1.0000, i.e. UP
won) is NOT an input anywhere here. Every decision uses only the arrival
book, the inventory held at that instant, and the frozen criterion. The
winner is applied at the END, to both branches identically, purely to price
an outcome we already committed to without it.

This is a counterfactual over the REAL tape, not a simulator: the books,
the order sequence, the fills and the rejections are the audited R5 records.
What varies is only whether (a) a cancel failure is bounded and its side
recoverable, and (b) the frozen completion path has the ladder it needs.
"""
from decimal import Decimal as D

from src import l5_live_book_feed as F
from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E

THETA = D("0.96")

# the audited R5 order tape: (n, side, own_price, arrival_book, actual)
TAPE = [
    (1,  "up",   D("0.50"), {"bid":"0.5000","ask":"0.5100","bid_qty":"327.22","ask_qty":"1742"},   "FILLED"),
    (2,  "down", D("0.49"), {"bid":"0.5000","ask":"0.5100","bid_qty":"327.22","ask_qty":"1742"},   "FILLED"),
    (3,  "up",   D("0.45"), {"bid":"0.4500","ask":"0.4600","bid_qty":"1359.14","ask_qty":"126"},   "FILLED"),
    (4,  "down", D("0.54"), {"bid":"0.4500","ask":"0.4600","bid_qty":"1359.14","ask_qty":"126"},   "FILLED"),
    (5,  "up",   D("0.43"), {"bid":"0.3900","ask":"0.4000","bid_qty":"104","ask_qty":"2079.74"},   "REJECTED"),
    (6,  "up",   D("0.39"), {"bid":"0.3900","ask":"0.4000","bid_qty":"104","ask_qty":"2079.74"},   "FILLED"),
    (7,  "down", D("0.54"), {"bid":"0.4600","ask":"0.4700","bid_qty":"1628.63","ask_qty":"1444"},  "REJECTED"),
    (8,  "up",   D("0.46"), {"bid":"0.4600","ask":"0.4700","bid_qty":"633.63","ask_qty":"2344"},   "EXPIRED"),
    (9,  "down", D("0.53"), {"bid":"0.4800","ask":"0.4900","bid_qty":"500","ask_qty":"20"},        "REJECTED"),
    (10, "down", D("0.50"), {"bid":"0.4900","ask":"0.5000","bid_qty":"2931.22","ask_qty":"199"},   "FILLED"),
    (11, "down", D("0.49"), {"bid":"0.5000","ask":"0.5100","bid_qty":"1445","ask_qty":"773"},      "FILLED"),
    (12, "down", D("0.47"), {"bid":"0.5100","ask":"0.5200","bid_qty":"760.95","ask_qty":"1359"},   "FILLED"),
    (13, "down", D("0.48"), {"bid":"0.5400","ask":"0.5500","bid_qty":"343.24","ask_qty":"24"},     "REJECTED"),
]

#: #8 rested live and its cancel failed from 11:16:54 to the close, blocking
#: the UP side. With the bounded fix the side is recoverable once the venue
#: establishes terminal state, so UP quoting resumes. The audited tape shows
#: what the engine wanted to quote on UP after that point: nothing new was
#: ever emitted because the gate refused it, so the recovered branch replaces
#: #8 with one further maker quote at the then-current touch, which is the
#: most conservative possible credit for unblocking (one clip, not many).
UP_RECOVERED = (14, "up", None, {"bid":"0.5100","ask":"0.5200","bid_qty":"2996.52","ask_qty":"184.63"})


def marketable(side, own, book):
    """PDI-cross at arrival, in the production representation."""
    bid, ask = D(book["bid"]), D(book["ask"])
    return (own >= ask) if side == "up" else ((D(1) - own) <= bid)


def completion(book, comp, want, basis):
    lv = S.buy_levels(F.touch_ladder(book), comp)
    n, cost, fee, _ = S.marginal_complete(lv, want, basis, THETA)
    return n, cost, fee, E.worst_admitted_price(lv, want, basis, THETA)


def run(*, cancel_fix: bool, completion_fix: bool,
        credit_recovered_clip: bool = True):
    q = {"up": D(0), "down": D(0)}
    c = {"up": D(0), "down": D(0)}
    fees = D(0)
    rejected, missed, takers = [], [], []
    up_blocked_from = None

    for n, side, own, book, actual in TAPE:
        # ---- the UP-side lockout that #8 caused in the real window
        if n == 8:
            if not cancel_fix:
                up_blocked_from = n
            # #8 itself rested and never filled either way
            continue
        if up_blocked_from is not None and side == "up":
            missed.append({"n": n, "side": side, "why": "UP_SIDE_STRANDED"})
            continue

        held_r = q["up"] - q["down"]
        held = "up" if held_r > 0 else ("down" if held_r < 0 else None)
        comp = None if held is None else S.complement(held)

        # ---- P0-1: the frozen completion path, when it has a ladder
        if completion_fix and comp is not None and side == comp:
            want = abs(held_r)
            basis = c[held] / q[held]
            k, cost, fee, limit = completion(book, comp, want, basis)
            if k > 0:
                q[comp] += k
                c[comp] += cost
                fees += fee
                takers.append({"n": n, "side": comp, "qty": str(k),
                               "limit": str(limit), "cost": str(cost),
                               "fee": str(fee),
                               "all_in_per_unit": str(
                                   (basis * want + cost + fee) / (want + k)
                                   if (want + k) else 0)})
                continue
            # not admitted: do NOT cross to reduce residual. Falls through to
            # the unchanged passive path below, exactly as R5 behaved.

        # ---- unchanged passive/PDI acquisition
        if marketable(side, own, book):
            rejected.append({"n": n, "side": side, "own": str(own),
                             "why": "PDI_CROSS_AT_ARRIVAL"})
            if comp is not None and side == comp:
                missed.append({"n": n, "side": side,
                               "why": "COMPLETION_ECONOMICS_FAIL"})
            continue
        q[side] += D(10)
        c[side] += own * D(10)
        fees += D("-0.03")               # the venue's signed maker field

    if cancel_fix and credit_recovered_clip and up_blocked_from is None:
        n, side, _, book = UP_RECOVERED
        own = D(book["ask"]) - D("0.01")          # passive at the touch
        if not marketable(side, own, book):
            q[side] += D(10)
            c[side] += own * D(10)
            fees += D("-0.03")

    matched = min(q["up"], q["down"])
    resid = q["down"] - q["up"]
    cash = c["up"] + c["down"]
    dn_avg = (c["down"] / q["down"]) if q["down"] else D(0)
    up_avg = (c["up"] / q["up"]) if q["up"] else D(0)
    matched_cost = c["up"] + matched * dn_avg if resid >= 0 else \
        c["down"] + matched * up_avg
    return {
        "up": q["up"], "down": q["down"], "matched": matched,
        "residual": resid, "cash": cash, "fees": fees,
        "matched_cost": matched_cost,
        "pair_cost": (matched_cost / matched) if matched else None,
        "locked_pair_pnl": (matched - matched_cost) if matched else D(0),
        "rejected": rejected, "missed": missed, "takers": takers,
    }


SCENARIOS = [("actual R5", False, False),
             ("cancel/status fix only", True, False),
             ("completion-mode fix only", False, True),
             ("both fixes", True, True)]

def table(rows, title):
    print("\n== %s ==" % title)
    hdr = ("scenario", "UP", "DOWN", "matched", "resid", "cash", "pair$",
           "lockedP&L", "fees", "rej", "missed", "taker")
    print("%-26s %5s %5s %7s %6s %8s %8s %9s %8s %4s %6s %5s" % hdr)
    for name, r in rows:
        print("%-26s %5s %5s %7s %6s %8s %8s %9s %8s %4d %6d %5d" % (
            name, r["up"], r["down"], r["matched"], r["residual"],
            round(r["cash"], 2),
            "-" if r["pair_cost"] is None else round(r["pair_cost"], 4),
            round(r["locked_pair_pnl"], 4), round(r["fees"], 4),
            len(r["rejected"]), len(r["missed"]), len(r["takers"])))
    print("   rejected   %s" % [x["n"] for x in rows[0][1]["rejected"]])
    print("   missed     %s" % [(x["n"], x["why"]) for x in rows[0][1]["missed"]])
    print("   takers     %s" % ([t["n"] for t in rows[-1][1]["takers"]] or "none"))
    print("   -- outcome priced with the audited winner (UP), applied to every "
          "branch identically and used in NO decision above --")
    for name, r in rows:
        redeem = r["matched"] + (D(0) if r["residual"] > 0
                                 else abs(r["residual"]))
        print("   %-26s proceeds %7s - cash %7s = %8s (fees field %s)"
              % (name, round(redeem, 2), round(r["cash"], 2),
                 round(redeem - r["cash"], 4), round(r["fees"], 4)))


if __name__ == "__main__":
    table([(n, run(cancel_fix=a, completion_fix=b,
                   credit_recovered_clip=False)) for n, a, b in SCENARIOS],
          "TAPE ONLY -- every order is an audited R5 order, nothing modelled")
    table([(n, run(cancel_fix=a, completion_fix=b,
                   credit_recovered_clip=True)) for n, a, b in SCENARIOS],
          "WITH ONE MODELLED RECOVERY CLIP -- the cancel-fix branches are "
          "credited\n   one further passive UP quote at the then-current "
          "touch, the most\n   conservative possible credit for unblocking a "
          "stranded side. That clip\n   is NOT in the tape; its side was set "
          "by which side was stranded, not\n   by the winner.")
