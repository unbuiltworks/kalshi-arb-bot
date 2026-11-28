"""Can cutting an unmatched leg save book-relative two-sided quoting?

THE QUESTION, AND WHY IT IS THE ONLY ONE LEFT. `book_relative_policy.py`
measured, on 226 settled BTC5m windows of real recorded quotes, that resting
a maker buy on each side at distance `d` behind its own touch decomposes
exactly as the owner described it:

    d=0.00  conservative   pairs 145/226   pair P&L +0.0352   lone P&L -0.4302
    d=0.20  conservative   pairs  76/226   pair P&L +0.4359   lone P&L -0.2391

Every completed pair makes money at every distance. Every distance still
loses overall, because the unmatched legs lose six to twelve times what the
pairs make. So the strategy is not short of edge on pairs -- it is long
unmatched inventory, and the entire question is whether that inventory can be
cut for less than the pairs earn.

THE BAR, ARITHMETICALLY. At d=0 conservative: pairs are 145/226 = 64.2% of
windows at +0.0352, so they contribute +0.0226 a window. The other 35.8% must
therefore cost no more than 0.0226/0.358 = 0.063 a leg, against -0.430 if held
to settlement. Cutting has to recover about 85% of the loss to break even.

WHAT IS MEASURED HERE. Three exit policies on the same tape and the same
conservative fill rule, so the only thing that changes is what happens after
one side fills alone:

  HOLD        carry the unmatched leg to settlement. The baseline above.
  CUT_NOW     the first snapshot at which we hold an unmatched leg, sell it
              at the prevailing opposing bid. A TAKER sale, charged the
              venue's taker fee, because a maker sale that does not fill is
              not an exit.
  CUT_STOP(x) carry it until its mark is x below what we paid, then sell at
              the prevailing bid, same taker charge. Settles if never hit.
  CUT_AFTER(T) carry the unmatched leg until T seconds into the window, THEN
              sell it at the prevailing bid as a taker. This is the policy
              the per-unit decomposition points at: CUT_NOW destroyed every
              pair because it fired the instant a leg was unmatched, but
              pairing happens EARLY and settlement loss is taken LATE, so a
              time-gated exit keeps the pairs and drops the settlement loss.
  COMPLETE(c) do not sell at all -- rest a MAKER BUY on the other side at
              `c - paid`, so the pair costs c if it fills. This is what the
              controller actually does (`straddle_completion_pair_cost`,
              `_completion_cross`) and it is strictly better than selling
              when it fills, because it keeps the pair upside instead of
              paying the spread to give it up. Settles the lone leg if the
              completion never fills.

A COMPLETED PAIR IS CHARGED WHAT IT ACTUALLY COST, which is not what the
entry ladder quoted. My first COMPLETE run booked every pair at the original
d-based prices and ignored the completion price entirely, so COMPLETE at a
pair cost of 1.00 -- which pays exactly nothing before fees -- reported
+0.44 a pair and +0.2321 a window. The payoff now uses what was paid for
each leg.

EVERY POLICY IS CAUSAL, AND MY FIRST VERSION WAS NOT. It scanned the whole
window for both fill times first, and cut only the legs that -- with
hindsight -- never paired. That reported CUT_NOW at +0.1088 a window, which
is not a policy anyone can run: at the instant one side fills you do not know
whether the other will. Deciding with that knowledge is the same error as
reading the settlement before placing the order. Here each snapshot sees only
what happened up to itself, so CUTTING AN UNMATCHED LEG DESTROYS THE PAIR
THAT LEG MIGHT HAVE BECOME, and that cost is the whole trade-off being
measured. A pair now requires both legs to fill before the exit fires.

HONEST LIMITS, and they all push the same way -- these numbers are OPTIMISTIC
about cutting. The sale is assumed to fill at the visible bid for our whole
size, with no queue, no partial fill, no market impact and no slippage, at the
first snapshot we would have wanted it. Our own exit is exactly the trade the
rest of the book is also trying to do when the price has just run, so in
reality it fills worse or not at all. If cutting does not clear the bar HERE,
it does not clear it live.

NOTHING HERE IS A FILL. These are hypothetical executions against a recorded
public quote tape. No order was placed and none of this is realized P&L.
"""
from __future__ import annotations

import collections
import json
import sys

#: maker fee and maker rebate are both zero on this venue for this account:
#: Tier 1 maker rebate is ZERO and Tier 2 needs $10,000,000 of 30-day maker
#: volume against ~$38,880/month (`src/pmus_fees.py`). Crediting an
#: uncredited rebate is the error that made an earlier model look profitable.
MAKER_FEE = 0.0
MAKER_REBATE = 0.0
#: the exit is a TAKER sale: 0.095 * p * (1-p) per share (`l5_btc5m_controller`
#: header, and TAKER_COEF in pmus_fees as Tier-4/Tier-1 respectively).
TAKER_COEF = 0.095
DECISIVE_HI, DECISIVE_LO = 0.9, 0.1


def taker_fee(price: float) -> float:
    p = min(max(price, 0.0), 1.0)
    return TAKER_COEF * p * (1.0 - p)


def windows(path):
    rows = json.load(open(path))
    by = collections.defaultdict(list)
    for r in rows:
        by[r["m"]].append(r)
    out = {}
    for m, v in by.items():
        v.sort(key=lambda r: r["t"])
        if len(v) >= 8:
            out[m] = v
    return out


def settled(path_):
    last = path_[-1]["bid"]
    if last >= DECISIVE_HI:
        return 1
    if last <= DECISIVE_LO:
        return 0
    return None


def own_prices(row):
    """(UP bid, UP ask, DOWN bid, DOWN ask) in each side's own terms."""
    return row["bid"], row["ask"], 1.0 - row["ask"], 1.0 - row["bid"]


def simulate(path_, d, policy, stop=None):
    """One window, decided snapshot by snapshot. (pnl, kind).

    Fill rule is TRADED_THRU: the opposing price must go strictly BELOW our
    level, so the level had to clear before the market moved on. That is the
    closest proxy for our queue position that quotes alone support.

    CAUSAL. At each snapshot we know only this snapshot and the ones before
    it. Cutting an unmatched leg cancels the other resting order with it, so
    a pair is only possible while we have not yet cut.
    """
    first = path_[0]
    up_bid0, _, dn_bid0, _ = own_prices(first)
    up_px = round(up_bid0 - d, 4)
    dn_px = round(dn_bid0 - d, 4)
    res = settled(path_)
    if up_px <= 0 and dn_px <= 0:
        return 0.0, "none"
    held = {"UP": False, "DOWN": False}
    resting = {"UP": up_px > 0, "DOWN": dn_px > 0}
    paid = {"UP": up_px, "DOWN": dn_px}

    for r in path_[1:]:
        up_bid, up_ask, dn_bid, dn_ask = own_prices(r)
        ask = {"UP": up_ask, "DOWN": dn_ask}
        bid = {"UP": up_bid, "DOWN": dn_bid}
        #: 1. fills first, both sides at this same snapshot
        for s_ in ("UP", "DOWN"):
            if resting[s_] and ask[s_] < paid[s_]:
                resting[s_] = False
                held[s_] = True
        #: 2. a pair is locked and nothing can adversely select it
        if held["UP"] and held["DOWN"]:
            return (1.0 - paid["UP"] - paid["DOWN"] - 2 * MAKER_FEE
                    + 2 * MAKER_REBATE), "pair"
        #: 3. otherwise, do we exit the one leg we hold?
        if policy == "HOLD":
            continue
        one = "UP" if held["UP"] else ("DOWN" if held["DOWN"] else None)
        if one is None:
            continue
        if policy == "CUT_AFTER" and r["t"] < stop:
            continue
        if policy == "COMPLETE":
            #: REPRICE the other side's resting order to whatever completes
            #: the pair at `stop` total, and keep waiting. No sale, so the
            #: pair upside is kept; the risk is that it never fills.
            other = "DOWN" if one == "UP" else "UP"
            want = round(stop - paid[one], 4)
            if want <= 0:
                continue
            paid[other] = want
            resting[other] = True
            continue
        mark = bid[one]
        if policy == "CUT_STOP" and mark > paid[one] - stop:
            continue
        #: sell our share at the prevailing bid, charged the taker fee, and
        #: the other order goes with it -- we are flat and done for this
        #: window. THIS IS WHERE THE PAIR WE MIGHT HAVE HAD IS GIVEN UP.
        return (mark - paid[one] - MAKER_FEE - taker_fee(mark)), "cut"

    #: the window ended: settle whatever we are holding
    if held["UP"] and held["DOWN"]:
        return (1.0 - paid["UP"] - paid["DOWN"] - 2 * MAKER_FEE
                + 2 * MAKER_REBATE), "pair"
    one = "UP" if held["UP"] else ("DOWN" if held["DOWN"] else None)
    if one is None:
        return 0.0, "none"
    won = (res == 1) if one == "UP" else (res == 0)
    return ((1.0 if won else 0.0) - paid[one] - MAKER_FEE
            + MAKER_REBATE), "lone"


def run(path):
    wins = windows(path)
    usable = {m: p for m, p in wins.items() if settled(p) is not None}
    print("=" * 76)
    print("BTC5m UNMATCHED-LEG EXIT VALUE -- %d settled windows" % len(usable))
    print("quotes: recorded BTC5M_QUOTE tape; fill rule TRADED_THRU;"
          " exits are TAKER sales at the visible bid")
    print("=" * 76)
    policies = [("HOLD", None), ("CUT_NOW", None), ("COMPLETE", 0.96),
                ("CUT_AFTER", 120.0), ("CUT_AFTER", 180.0),
                ("CUT_AFTER", 240.0), ("CUT_AFTER", 270.0)]
    print("%6s %-13s %6s %5s %5s %5s %10s %10s %10s"
          % ("dist", "policy", "wins", "pair", "cut", "lone",
             "mean P&L", "pair P&L", "resid P&L"))
    best = None
    for d in (0.00, 0.08, 0.20, 0.30, 0.40):
        for pol, stop in policies:
            n = pair = cut = lone = 0
            tot = pair_tot = resid_tot = 0.0
            for m, p in sorted(usable.items()):
                pnl, kind = simulate(p, d, pol, stop)
                n += 1
                tot += pnl
                if kind == "pair":
                    pair += 1
                    pair_tot += pnl
                elif kind == "cut":
                    cut += 1
                    resid_tot += pnl
                elif kind == "lone":
                    lone += 1
                    resid_tot += pnl
            label = pol if stop is None else "%s %.2f" % (pol, stop)
            mean = tot / max(1, n)
            print("%6.2f %-13s %6d %5d %5d %5d %10.4f %10.4f %10.4f"
                  % (d, label, n, pair, cut, lone, mean,
                     pair_tot / max(1, pair),
                     resid_tot / max(1, cut + lone)))
            if best is None or mean > best[2]:
                best = (d, label, mean)
        print()
    print("=" * 76)
    print("BEST OF ALL: d=%.2f %s at %+0.4f per window -> %s"
          % (best[0], best[1], best[2],
             "POSITIVE" if best[2] > 0 else "NEGATIVE"))
    print("Hypothetical executions against a recorded quote tape."
          " NOT fills, NOT realized P&L.")
    return 0 if best[2] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
