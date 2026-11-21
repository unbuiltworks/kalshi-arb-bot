"""THE FROZEN HYPOTHESIS, and the harness that judges it. Nothing is tuned here.

WHAT IS FROZEN, declared before any forward window was looked at, and
committed before the forward sample was fetched:

    at t = 180.0 s into a BTC5m window, take the CHEAPER side at its own
    OFFER if that offer is <= 0.30, pay the taker fee, hold to settlement.

Every one of those numbers comes from the discovery sample
(`TAKER_LONGSHOT_RESULT.txt`, 226 settled windows ending
2026-11-19T04:35Z) and NONE of them may be re-chosen on the validation
sample. The grid searched in discovery was t in {60,120,180,240} x maxpx in
{0.20,0.30,0.40}; t=180/0.30 is selected because it was the only cell whose
sign AND magnitude held across a chronological split of the discovery data
(+0.0216 first half, +0.0339 second), and because the neighbouring price caps
agreed. If the forward sample disagrees, the hypothesis is WRONG -- the
answer is not a different t.

DISCOVERY CUTOFF. 2026-11-19T04:40:00Z. The discovery sample's last window
opened at 0435z. Any window opening at or after the cutoff is forward and
untouched; the harness refuses to score a window before it, so an
accidentally mixed input cannot quietly inflate n.

WHY A TAKER. Every maker variant on the discovery tape loses to one measured
cost: a passive buy fills exactly when the price moves against it, worth
6.0-7.2 cents a share. The taker fee is 0.095*p*(1-p) -- 1.8 cents at 0.25.
Crossing is 3-6x cheaper than resting. A taker entry also carries no queue
model, no partial-fill model and no cancellation risk.

WHAT THIS HARNESS WILL NOT DO. It will not sweep parameters, will not report
a "best" cell, and will not pool discovery with validation. It answers one
question with one number and an interval around it.

NOTHING HERE IS A FILL. Hypothetical executions against a recorded public
quote tape. Not realized P&L.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import random
import statistics as st
import sys

# ----------------------------------------------------------- FROZEN
ENTRY_T_S = 180.0
MAX_PRICE = 0.30
TAKER_COEF = 0.095
#: `pmus_fees.ACCOUNT_TAKER_TIER` is 1 and 0.095 is the Tier-1 taker
#: coefficient; verified against the module rather than assumed here.
DISCOVERY_CUTOFF_UTC = "2026-11-19T04:40:00Z"
#: settlement proxy: the venue converges by expiry, and this resolved 226 of
#: 230 discovery windows. Undecided windows are discarded and COUNTED.
DECISIVE_HI, DECISIVE_LO = 0.9, 0.1
#: a quote older than this at the decision instant is not tradeable evidence
MAX_QUOTE_AGE_S = 6.0
# ------------------------------------------------------------------

CUTOFF_EPOCH = dt.datetime.fromisoformat(
    DISCOVERY_CUTOFF_UTC.replace("Z", "+00:00")).timestamp()


def taker_fee(price: float) -> float:
    p = min(max(price, 0.0), 1.0)
    return TAKER_COEF * p * (1.0 - p)


def window_start_epoch(slug: str) -> float:
    """`cpc-btc-updown-5m-YYYY-MM-DD-HHMMz` -> epoch seconds, or nan."""
    try:
        parts = slug.split("-")
        y, mo, da, hm = parts[-4], parts[-3], parts[-2], parts[-1].rstrip("zZ")
        return dt.datetime(int(y), int(mo), int(da), int(hm[:2]), int(hm[2:]),
                           tzinfo=dt.timezone.utc).timestamp()
    except Exception:
        return float("nan")


def load(path):
    by = collections.defaultdict(list)
    for r in json.load(open(path)):
        by[r["m"]].append(r)
    return by


def partition(by):
    """(forward, excluded) -- forward is strictly at/after the cutoff."""
    forward, excluded = {}, collections.Counter()
    for m, v in sorted(by.items()):
        start = window_start_epoch(m)
        if start != start:                      # nan
            excluded["unparseable_slug"] += 1
            continue
        if start < CUTOFF_EPOCH:
            excluded["before_discovery_cutoff"] += 1
            continue
        v = sorted(v, key=lambda r: r["t"])
        if len(v) < 8:
            excluded["too_few_quotes"] += 1
            continue
        forward[m] = v
    return forward, excluded


def settle(v):
    last = v[-1]["bid"]
    if last >= DECISIVE_HI:
        return 1
    if last <= DECISIVE_LO:
        return 0
    return None


def decision_quote(v):
    """The last quote at or before the frozen entry time, and its age."""
    best = None
    for r in v:
        if r["t"] <= ENTRY_T_S:
            best = r
        else:
            break
    if best is None:
        return None, None
    return best, ENTRY_T_S - float(best["t"])


def score(forward):
    """One row per window the frozen rule would have traded, plus reasons."""
    traded, skipped = [], collections.Counter()
    for m, v in sorted(forward.items()):
        res = settle(v)
        if res is None:
            skipped["undecided_settlement"] += 1
            continue
        q, age = decision_quote(v)
        if q is None:
            skipped["no_quote_by_t180"] += 1
            continue
        if age > MAX_QUOTE_AGE_S:
            skipped["quote_stale_at_t180"] += 1
            continue
        up_ask, dn_ask = q["ask"], 1.0 - q["bid"]
        if up_ask <= dn_ask:
            side, px, won = "UP", up_ask, res == 1
        else:
            side, px, won = "DOWN", dn_ask, res == 0
        if not (0.0 < px <= MAX_PRICE):
            skipped["no_side_at_or_below_maxpx"] += 1
            continue
        fee = taker_fee(px)
        traded.append({"market": m, "side": side, "price": px,
                       "quote_age_s": round(age, 2), "fee": fee,
                       "won": bool(won),
                       "pnl": (1.0 if won else 0.0) - px - fee})
    return traded, skipped


def interval(pnls, iters=20000, seed=20261119):
    rnd = random.Random(seed)
    n = len(pnls)
    mean = sum(pnls) / n
    sd = st.pstdev(pnls)
    boot = sorted(sum(rnd.choices(pnls, k=n)) / n for _ in range(iters))
    return mean, sd, sd / (n ** 0.5), boot[int(0.025 * iters)], \
        boot[int(0.975 * iters)]


def drawdown(pnls):
    """Worst peak-to-trough of the cumulative per-share series, in order."""
    peak = cum = worst = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def report(path, shares=1.0, halt_usd=None):
    by = load(path)
    forward, excluded = partition(by)
    traded, skipped = score(forward)

    print("=" * 78)
    print("FROZEN HYPOTHESIS -- FORWARD VALIDATION")
    print("=" * 78)
    print("frozen rule     : t=%.0fs, take cheaper side at its offer if"
          " <= %.2f, hold to settlement" % (ENTRY_T_S, MAX_PRICE))
    print("discovery cutoff: %s (windows before it are REFUSED)"
          % DISCOVERY_CUTOFF_UTC)
    print("windows in file : %d" % len(by))
    for k, n in sorted(excluded.items()):
        print("  excluded %-26s %d" % (k, n))
    print("forward windows : %d" % len(forward))
    for k, n in sorted(skipped.items()):
        print("  not traded %-24s %d" % (k, n))
    print("QUALIFYING WINDOWS (n): %d" % len(traded))
    if not traded:
        print("\nNO FORWARD TRADES YET -- no verdict is available.")
        return 2

    pnls = [t["pnl"] for t in traded]
    wonn = sum(t["won"] for t in traded)
    mean, sd, se, lo, hi = interval(pnls)
    print()
    print("realized outcomes : %d of %d won (%.1f%%)"
          % (wonn, len(traded), 100.0 * wonn / len(traded)))
    print("mean entry price  : %.4f   mean taker fee: %.4f"
          % (sum(t["price"] for t in traded) / len(traded),
             sum(t["fee"] for t in traded) / len(traded)))
    print("mean quote age    : %.2fs (max allowed %.1fs)"
          % (sum(t["quote_age_s"] for t in traded) / len(traded),
             MAX_QUOTE_AGE_S))
    print()
    print("NET per share after taker fee")
    print("  mean            : %+0.4f" % mean)
    print("  sd / se         : %0.4f / %0.4f" % (sd, se))
    print("  95%% bootstrap CI : [%+0.4f, %+0.4f]  %s"
          % (lo, hi, "POSITIVE" if lo > 0 else "INCLUDES ZERO"))
    print("  worst drawdown  : %+0.4f per share (cumulative, in order)"
          % drawdown(pnls))
    if shares != 1.0:
        print()
        print("at %g shares a window:" % shares)
        print("  mean/window     : %+0.4f   sd/window: %0.4f"
              % (mean * shares, sd * shares))
        print("  worst drawdown  : %+0.2f" % (drawdown(pnls) * shares))
        if halt_usd:
            days = collections.defaultdict(float)
            for t in traded:
                days[t["market"][-16:-6]] += t["pnl"] * shares
            tripped = sum(1 for v in days.values() if v <= -abs(halt_usd))
            print("  days in sample  : %d, of which daily loss <= -$%g: %d"
                  % (len(days), abs(halt_usd), tripped))
    print()
    print("Hypothetical executions against a recorded public quote tape."
          " NOT fills, NOT realized P&L, NOT confirmed earnings.")
    return 0 if lo > 0 else 1


if __name__ == "__main__":
    shares = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    halt = float(sys.argv[3]) if len(sys.argv) > 3 else None
    raise SystemExit(report(sys.argv[1], shares, halt))
