"""Does the repo's BRTI settlement model beat the market's own price?

SEPARATE FROM THE FROZEN TAKER HYPOTHESIS, deliberately. That hypothesis is
about a price level; this is about whether `l5_btc_reference.probability_up`
-- the production settlement model -- carries information the quoted price
does not. A no-go on one says nothing about the other.

THE TEST. At t=180s into each window, read the model's P(settles UP) from the
1-second BRTI series up to that instant, the window's own `priceToBeat` and
the time remaining, and compare it with the market mid at the same instant.
Then score both against the TRUE settlement.

TRUE SETTLEMENT, not a proxy. The venue settles UP when the 60-second BRTI
average at the window end is at or above `priceToBeat`. A 1-second bucket's
`close` with tickCount 60 IS that average (verified 32/32 across four assets,
`l5_series_registry`), so the bucket ending at the window end settles the
window exactly. This also lets the quote-based proxy used elsewhere in these
files -- last bid >= 0.9 => UP -- be checked rather than trusted.

WHAT WOULD CONSTITUTE AN EDGE. Lower Brier score than the market, AND a
positive realized return from taking the side the model disagrees with the
market about, after the taker fee. The first without the second is not money.

NOTHING HERE IS A FILL.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import random
import statistics as st
import sys

sys.path.insert(0, ".")
from src import l5_btc_reference as REF        # noqa: E402

ENTRY_T_S = 180.0
WINDOW_S = 300.0
TAKER_COEF = 0.095
#: the model needs history to estimate volatility; give it the whole window
#: so far plus the minute before it, which is what a live reader would hold
HISTORY_S = 360.0
random.seed(20261119)


def taker_fee(p):
    p = min(max(p, 0.0), 1.0)
    return TAKER_COEF * p * (1.0 - p)


def window_start(slug):
    p = slug.split("-")
    hm = p[-1].rstrip("zZ")
    return dt.datetime(int(p[-4]), int(p[-3]), int(p[-2]),
                       int(hm[:2]), int(hm[2:]),
                       tzinfo=dt.timezone.utc).timestamp()


def main(quotes_path, brti_path, cutoff_utc="2026-11-19T04:40:00Z"):
    brti = {int(t): float(v) for t, v in json.load(open(brti_path))}
    series_sorted = sorted(brti)
    cutoff = dt.datetime.fromisoformat(
        cutoff_utc.replace("Z", "+00:00")).timestamp()

    by = collections.defaultdict(list)
    for r in json.load(open(quotes_path)):
        by[r["m"]].append(r)

    rows, skipped = [], collections.Counter()
    for m, v in sorted(by.items()):
        start = window_start(m)
        if start < cutoff:
            skipped["before_cutoff"] += 1
            continue
        v.sort(key=lambda r: r["t"])
        end = start + WINDOW_S
        strike = next((r["to_beat"] for r in v if r.get("to_beat")), None)
        if strike is None:
            skipped["no_strike"] += 1
            continue
        #: TRUE settlement: the 60s-average bucket at the window end
        settle_bucket = int(end) - 1
        if settle_bucket not in brti:
            skipped["no_settlement_bucket"] += 1
            continue
        truth = 1 if brti[settle_bucket] >= strike else 0

        #: the quote-proxy settlement, for comparison
        last = v[-1]["bid"]
        proxy = 1 if last >= 0.9 else (0 if last <= 0.1 else None)

        #: the decision instant
        now = start + ENTRY_T_S
        q = None
        for r in v:
            if r["t"] <= ENTRY_T_S:
                q = r
            else:
                break
        if q is None:
            skipped["no_quote_at_t180"] += 1
            continue
        hist = [(t, brti[t]) for t in series_sorted
                if now - HISTORY_S <= t <= now - 1]
        if len(hist) < 60:
            skipped["too_little_brti_history"] += 1
            continue
        got = REF.probability_up(series=hist, strike=strike,
                                 window_end=end, now=now)
        if not got.get("ok") or got.get("p_up") is None:
            skipped["model_refused:" + str(got.get("reason"))] += 1
            continue
        rows.append({"market": m, "p_model": float(got["p_up"]),
                     "mid": (q["bid"] + q["ask"]) / 2.0,
                     "bid": q["bid"], "ask": q["ask"],
                     "truth": truth, "proxy": proxy})

    print("=" * 76)
    print("BRTI SETTLEMENT MODEL vs MARKET PRICE -- forward windows only")
    print("=" * 76)
    for k, n in sorted(skipped.items()):
        print("  skipped %-34s %d" % (k, n))
    print("scored windows: %d" % len(rows))
    if len(rows) < 20:
        print("\nTOO FEW TO JUDGE.")
        return 2

    agree = sum(1 for r in rows if r["proxy"] is not None
                and r["proxy"] == r["truth"])
    have = sum(1 for r in rows if r["proxy"] is not None)
    print("\nquote-proxy settlement vs TRUE settlement: %d/%d agree (%.1f%%)"
          % (agree, have, 100.0 * agree / max(1, have)))

    bm = st.fmean((r["p_model"] - r["truth"]) ** 2 for r in rows)
    bk = st.fmean((r["mid"] - r["truth"]) ** 2 for r in rows)
    print("\nBrier score (lower is better)")
    print("  model : %.4f" % bm)
    print("  market: %.4f" % bk)
    print("  -> %s" % ("MODEL better" if bm < bk else "MARKET better"))

    #: take the side the model disagrees with the market about, by `edge`
    print("\nTaking the model's disagreement, at the offer, net of taker fee")
    print("%8s %7s %9s %9s %-20s" % ("min edge", "n", "mean/sh", "se",
                                     "95% CI"))
    for need in (0.05, 0.10, 0.20, 0.30):
        pnls = []
        for r in rows:
            d = r["p_model"] - r["mid"]
            if abs(d) < need:
                continue
            if d > 0:
                px, won = r["ask"], r["truth"] == 1        # buy UP
            else:
                px, won = 1.0 - r["bid"], r["truth"] == 0  # buy DOWN
            if not (0.0 < px < 1.0):
                continue
            pnls.append((1.0 if won else 0.0) - px - taker_fee(px))
        if len(pnls) < 15:
            print("%8.2f %7d %9s %9s  too few" % (need, len(pnls), "-", "-"))
            continue
        n = len(pnls)
        mean = sum(pnls) / n
        se = st.pstdev(pnls) / (n ** 0.5)
        boot = sorted(sum(random.choices(pnls, k=n)) / n
                      for _ in range(10000))
        print("%8.2f %7d %+9.4f %9.4f [%+0.4f, %+0.4f] %s"
              % (need, n, mean, se, boot[250], boot[9750],
                 "POSITIVE" if boot[250] > 0 else ""))
    print("\nHypothetical executions. NOT fills, NOT realized P&L.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
