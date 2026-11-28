"""Does BRTI movement lead PMUS BTC5m repricing by enough to trade on?

TIMESTAMP QUALITY IS THE CRUX OF THIS TEST, so it is stated first.

The PMUS quote series is `BTC5M_QUOTE` from `l5-btc5m-public-recorder`: a
POLL of the public gateway roughly every 2 seconds, stamped with OUR receipt
time, not the venue's quote time. Measured cadence on this sample: median
2.03s, p99 2.08s, max 60.87s. Consequences, all of which bias this test
TOWARD finding that BRTI leads:

  - any true lead shorter than one poll interval is unresolvable here;
  - our receipt time is later than the venue's quote time by an unknown
    network and poll delay, which makes the quote look staler than it is;
  - so a lead measured at horizon h is an UPPER BOUND on the real lead, and
    a lead that fails to appear at h is a real absence at h.

The BRTI series is the venue's own 1-second index history
(`/v1/asset-prices/history`, bucket ASSET_PRICE_BUCKET_1S). Each bucket's
`close` carries tickCount 60, i.e. it IS the 60-second trailing average, and
the bucket ending at a window's end settles that window against
`priceToBeat`. That makes it the right value series and a HEAVILY SMOOTHED
signal series: a 5-second change in a 60-second average is roughly a twelfth
of the underlying 5-second spot move. We do not hold spot BTC here, so no
claim is made about spot.

WHAT IS TESTED, and only this. At each poll instant we hold a quote and an
index. Does the index's recent change predict the NEXT quote change, and is
the predicted move big enough to pay the spread and the taker fee?

MAKER ENTRIES ARE NOT TESTED HERE, deliberately. A maker markout needs queue
position, which this feed does not carry, and assuming priority is exactly
the invented-fill error this brief forbids. Only TAKER entries are scored:
we pay the displayed offer, so the fill needs no assumption at all.

NO COMPETITOR COMPARISON IS POSSIBLE. The only competitor fill set we hold
(0x8dxd) is Polymarket, 2026-03..08, and the only trade tape we hold covers
23 windows in 2026-09..11 and contains zero 0x8dxd rows. There is no
contemporaneous executable price for any competitor fill, so their acquisition
markout cannot be computed at any horizon. And our own BTC5m lane has never
traded (origin.realized.BTC5M: 0 markets), so there are no L5 BTC5m fills to
compare either. Both absences are findings, not gaps to fill with estimates.

Uncertainty is bootstrapped over WINDOWS. Polls inside a window share one
settlement and one price path.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import random
import statistics as st
import sys

TAKER_COEF = 0.095
#: poll instants scored. Leaves room for the longest horizon and skips the
#: first seconds, where the window's book is still forming.
T_LO, T_HI = 30.0, 240.0
MOM_K = (5, 15, 30)
HORIZ = (5, 15, 30, 60)
random.seed(20261119)


def taker_fee(p):
    p = min(max(p, 0.0), 1.0)
    return TAKER_COEF * p * (1.0 - p)


def window_start(slug):
    p = slug.split("-")
    hm = p[-1].rstrip("zZ")
    return int(dt.datetime(int(p[-4]), int(p[-3]), int(p[-2]),
                           int(hm[:2]), int(hm[2:]),
                           tzinfo=dt.timezone.utc).timestamp())


def build(quotes_path, brti_path):
    brti = {int(t): float(v) for t, v in json.load(open(brti_path))}
    by = collections.defaultdict(list)
    for r in json.load(open(quotes_path)):
        by[r["m"]].append(r)
    out, skipped = [], collections.Counter()
    for slug, v in sorted(by.items()):
        v.sort(key=lambda r: r["t"])
        start = window_start(slug)
        end = start + 300
        strike = next((r["to_beat"] for r in v if r.get("to_beat")), None)
        if strike is None:
            skipped["no_strike"] += 1
            continue
        if (end - 1) not in brti:
            skipped["no_settlement_bucket"] += 1
            continue
        truth = 1 if brti[end - 1] >= strike else 0
        #: quote lookup by second, last poll at or before each second
        grid = {}
        for r in v:
            grid[int(r["t"])] = r
        def q_at(ts):
            for back in range(0, 8):
                if int(ts) - back in grid:
                    return grid[int(ts) - back], back
            return None, None
        rows = []
        for r in v:
            t = float(r["t"])
            if not (T_LO <= t <= T_HI):
                continue
            now = start + int(t)
            if now not in brti:
                continue
            rec = {"t": t, "bid": r["bid"], "ask": r["ask"],
                   "mid": (r["bid"] + r["ask"]) / 2.0,
                   "spread": round(r["ask"] - r["bid"], 4),
                   "brti": brti[now], "strike": strike, "truth": truth,
                   "t_left": 300.0 - t}
            ok = True
            for k in MOM_K:
                if (now - k) not in brti:
                    ok = False
                    break
                rec["m%d" % k] = brti[now] - brti[now - k]
            if not ok:
                continue
            for h in HORIZ:
                fq, age = q_at(t + h)
                if fq is None or fq["t"] <= t:
                    rec["mid%d" % h] = None
                    rec["bid%d" % h] = None
                    rec["ask%d" % h] = None
                else:
                    rec["mid%d" % h] = (fq["bid"] + fq["ask"]) / 2.0
                    rec["bid%d" % h] = fq["bid"]
                    rec["ask%d" % h] = fq["ask"]
            rows.append(rec)
        if rows:
            out.append({"slug": slug, "rows": rows, "truth": truth})
        else:
            skipped["no_scoreable_polls"] += 1
    return out, skipped


def pearson(xs, ys):
    n = len(xs)
    if n < 10:
        return None
    mx, my = st.fmean(xs), st.fmean(ys)
    sx = st.pstdev(xs) or 1e-12
    sy = st.pstdev(ys) or 1e-12
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n * sx * sy)


def boot_mean(vals, iters=10000):
    n = len(vals)
    m = sum(vals) / n
    b = sorted(sum(random.choices(vals, k=n)) / n for _ in range(iters))
    return m, b[int(0.025 * iters)], b[int(0.975 * iters)]


def lead_lag(wins):
    print("=" * 78)
    print("LEAD-LAG: does recent BRTI change predict the NEXT quote change?")
    print("=" * 78)
    print("%6s %8s %10s %10s %10s %s"
          % ("mom k", "horizon", "corr", "n polls", "windows", "reverse corr"))
    for k in MOM_K:
        for h in HORIZ:
            xs, ys, rv = [], [], []
            for w in wins:
                for r in w["rows"]:
                    if r.get("mid%d" % h) is None:
                        continue
                    xs.append(r["m%d" % k])
                    ys.append(r["mid%d" % h] - r["mid"])
                    #: CONTROL: does the quote's own recent change predict
                    #: the index's next change? If the "lead" is really just
                    #: autocorrelation, this shows up here too.
                    rv.append(r["brti"] - r["m%d" % k])
            c = pearson(xs, ys)
            cr = pearson(ys, rv)
            print("%6d %8d %10s %10d %10d %s"
                  % (k, h, ("%+0.4f" % c) if c is not None else "-",
                     len(xs), len(wins),
                     ("%+0.4f" % cr) if cr is not None else "-"))
    print()


def taker_scan(wins, h=15):
    """Taker entries conditioned on BRTI momentum. Guaranteed fill."""
    print("=" * 78)
    print("TAKER ACQUISITION, momentum-conditioned (fill at the OFFER, h=%ds)"
          % h)
    print("=" * 78)
    #: thresholds from the sample's own momentum distribution, so the
    #: buckets are populated rather than chosen to flatter
    allm = [abs(r["m15"]) for w in wins for r in w["rows"]]
    allm.sort()
    qs = [allm[int(f * len(allm))] for f in (0.50, 0.75, 0.90, 0.95)]
    print("|BRTI 15s change| quantiles: p50 %.2f  p75 %.2f  p90 %.2f  p95 %.2f USD"
          % tuple(qs))
    print()
    print("%10s %7s %7s %11s %11s %-22s %11s"
          % ("min |m15|", "n", "wins", "markout", "settle P&L", "95% CI settle",
             "mean px"))
    for thr in qs:
        mk, se, px = [], [], []
        for w in wins:
            for r in w["rows"]:
                m = r["m15"]
                if abs(m) < thr:
                    continue
                if r.get("bid%d" % h) is None:
                    continue
                if m > 0:                      # index up -> buy UP
                    pay = r["ask"]
                    exitbid = r["bid%d" % h]
                    won = r["truth"] == 1
                else:                          # index down -> buy DOWN
                    pay = 1.0 - r["bid"]
                    exitbid = 1.0 - r["ask%d" % h]
                    won = r["truth"] == 0
                if not (0.0 < pay < 1.0):
                    continue
                fee = taker_fee(pay)
                mk.append(exitbid - pay - fee)
                se.append((1.0 if won else 0.0) - pay - fee)
                px.append(pay)
        if len(mk) < 30:
            print("%10.2f %7d  too few" % (thr, len(mk)))
            continue
        m1, _, _ = boot_mean(mk)
        m2, lo, hi = boot_mean(se)
        print("%10.2f %7d %7.1f%% %+11.4f %+11.4f [%+0.4f, %+0.4f] %11.4f"
              % (thr, len(mk),
                 100.0 * sum(1 for x in se if x > 0) / len(se),
                 m1, m2, lo, hi, st.fmean(px)))
    print()
    print("NOTE: these are POLL-level numbers and overstate precision --")
    print("window-clustered intervals follow.")
    print()


def window_clustered(wins, h=15, thr=None):
    """One P&L per WINDOW, which is the unit of independence."""
    allm = sorted(abs(r["m15"]) for w in wins for r in w["rows"])
    thr = thr if thr is not None else allm[int(0.90 * len(allm))]
    per = []
    for w in wins:
        got = []
        for r in w["rows"]:
            m = r["m15"]
            if abs(m) < thr or r.get("bid%d" % h) is None:
                continue
            pay = r["ask"] if m > 0 else 1.0 - r["bid"]
            won = (r["truth"] == 1) if m > 0 else (r["truth"] == 0)
            if not (0.0 < pay < 1.0):
                continue
            got.append((1.0 if won else 0.0) - pay - taker_fee(pay))
        if got:
            per.append(sum(got) / len(got))      # mean per share in-window
    print("=" * 78)
    print("WINDOW-CLUSTERED settlement economics (|m15| >= %.2f USD, h=%ds)"
          % (thr, h))
    print("=" * 78)
    if len(per) < 20:
        print("  only %d qualifying windows -- TOO FEW TO JUDGE" % len(per))
        return
    m, lo, hi = boot_mean(per)
    print("  qualifying windows %d | mean per share %+0.4f | 95%% CI [%+0.4f, %+0.4f] %s"
          % (len(per), m, lo, hi, "POSITIVE" if lo > 0 else "INCLUDES ZERO"))
    print("  sd %0.4f | se %0.4f" % (st.pstdev(per), st.pstdev(per) / len(per) ** 0.5))
    print()


def conditional(wins, h=5):
    """The breakdowns the brief asks for, at the horizon where the lead-lag
    correlation was largest (h=5). If a tradeable pocket exists it is here."""
    print("=" * 78)
    print("CONDITIONAL TAKER SETTLEMENT P&L (h=%ds), window-clustered" % h)
    print("=" * 78)
    allm = sorted(abs(r["m15"]) for w in wins for r in w["rows"])
    thr = allm[int(0.75 * len(allm))]

    def score(keep, label):
        per = []
        for w in wins:
            got = []
            for r in w["rows"]:
                m = r["m15"]
                if abs(m) < thr or r.get("bid%d" % h) is None:
                    continue
                if not keep(r, m):
                    continue
                pay = r["ask"] if m > 0 else 1.0 - r["bid"]
                won = (r["truth"] == 1) if m > 0 else (r["truth"] == 0)
                if not (0.0 < pay < 1.0):
                    continue
                got.append((1.0 if won else 0.0) - pay - taker_fee(pay))
            if got:
                per.append(sum(got) / len(got))
        if len(per) < 20:
            print("  %-34s %4d windows  TOO FEW" % (label, len(per)))
            return
        m, lo, hi = boot_mean(per)
        print("  %-34s %4d windows  %+0.4f  [%+0.4f, %+0.4f] %s"
              % (label, len(per), m, lo, hi,
                 "POSITIVE" if lo > 0 else ""))

    score(lambda r, m: True, "all qualifying")
    print("  -- by DIRECTION of the BRTI move --")
    score(lambda r, m: m > 0, "index UP   -> buy UP")
    score(lambda r, m: m < 0, "index DOWN -> buy DOWN")
    print("  -- by TIME REMAINING --")
    for lo_, hi_ in ((210, 270), (150, 210), (90, 150), (60, 90)):
        score(lambda r, m, a=lo_, b=hi_: a <= r["t_left"] < b,
              "t_left %d-%ds" % (lo_, hi_))
    print("  -- by SPREAD --")
    for sp in (0.01, 0.02):
        score(lambda r, m, s_=sp: abs(r["spread"] - s_) < 1e-9,
              "spread %.2f" % sp)
    score(lambda r, m: r["spread"] >= 0.03, "spread >= 0.03")
    print("  -- by SIZE of the BRTI move --")
    big = allm[int(0.95 * len(allm))]
    score(lambda r, m, b=big: abs(m) >= b, "|m15| >= p95 (%.1f USD)" % big)
    print("  -- by ENTRY PRICE (is this just buying favourites?) --")
    score(lambda r, m: (r["ask"] if m > 0 else 1 - r["bid"]) <= 0.40,
          "entry <= 0.40 (longshot side)")
    score(lambda r, m: (r["ask"] if m > 0 else 1 - r["bid"]) > 0.60,
          "entry > 0.60 (favourite side)")
    print()


def latency(wins, h=15):
    """ORDER LATENCY. The scan above pays `ask(t)` on a signal observed at
    `t`, which no real system can do: seeing the index move at t and having
    an order accepted at t costs zero time. With latency D we pay whatever
    the offer is at t+D, and the signal's own information is D seconds older
    by then.

    This is the honest version of the entry, and it can only be worse than
    the instantaneous one -- which is the point of measuring it rather than
    asserting it. D=0 is the unrealistic baseline, kept for comparison.
    """
    print("=" * 78)
    print("ORDER LATENCY: pay the offer at t+D, not at t (h=%ds after entry)"
          % h)
    print("=" * 78)
    allm = sorted(abs(r["m15"]) for w in wins for r in w["rows"])
    thr = allm[int(0.90 * len(allm))]
    print("  signal |m15| >= %.2f USD; D=0 is unachievable, shown for"
          " comparison" % thr)
    print("%8s %7s %8s %11s %11s %-22s"
          % ("latency", "windows", "n", "markout", "settle P&L", "95% CI settle"))
    for D in (0, 1, 2, 5):
        per_mk, per_se, n = [], [], 0
        for w in wins:
            gm, gs = [], []
            #: a second-indexed view so t+D can be looked up
            grid = {int(r["t"]): r for r in w["rows"]}
            for r in w["rows"]:
                if abs(r["m15"]) < thr:
                    continue
                #: the quote we can actually hit, D seconds later
                ent = None
                for back in range(0, 4):
                    cand = grid.get(int(r["t"]) + D + back)
                    if cand is not None:
                        ent = cand
                        break
                if ent is None or ent.get("bid%d" % h) is None:
                    continue
                m = r["m15"]
                if m > 0:
                    pay, exitbid = ent["ask"], ent["bid%d" % h]
                    won = ent["truth"] == 1
                else:
                    pay, exitbid = 1.0 - ent["bid"], 1.0 - ent["ask%d" % h]
                    won = ent["truth"] == 0
                if not (0.0 < pay < 1.0):
                    continue
                fee = taker_fee(pay)
                gm.append(exitbid - pay - fee)
                gs.append((1.0 if won else 0.0) - pay - fee)
                n += 1
            if gs:
                per_mk.append(sum(gm) / len(gm))
                per_se.append(sum(gs) / len(gs))
        if len(per_se) < 20:
            print("%8d %7d  too few" % (D, len(per_se)))
            continue
        m1, _, _ = boot_mean(per_mk)
        m2, lo, hi = boot_mean(per_se)
        print("%8ds %7d %8d %+11.4f %+11.4f [%+0.4f, %+0.4f] %s"
              % (D, len(per_se), n, m1, m2, lo, hi,
                 "POSITIVE" if lo > 0 else ""))
    print()
    print("  EXECUTABLE DEPTH IS NOT MEASURABLE on this feed: BTC5M_QUOTE")
    print("  carries bid and ask prices and no size field, so the quantity")
    print("  available at the offer is unknown. Every number above therefore")
    print("  assumes the displayed offer absorbs our whole clip, which is the")
    print("  most favourable possible assumption about liquidity.")
    print()


def main(quotes_path, brti_path):
    wins, skipped = build(quotes_path, brti_path)
    nq = sum(len(w["rows"]) for w in wins)
    print("=" * 78)
    print("DATA COVERAGE")
    print("=" * 78)
    print("  scoreable windows %d | scored poll instants %d" % (len(wins), nq))
    for k, n in sorted(skipped.items()):
        print("  excluded %-26s %d" % (k, n))
    sp = collections.Counter(r["spread"] for w in wins for r in w["rows"])
    print("  spread at poll: " + "  ".join(
        "%.2f:%d" % (k, sp[k]) for k in sorted(sp)[:5]))
    print("  PMUS quotes are OUR 2s polls of a public gateway, receipt-stamped.")
    print("  BRTI buckets are the venue's own 60s-average index, 1s grid.")
    print()
    lead_lag(wins)
    taker_scan(wins)
    window_clustered(wins)
    conditional(wins)
    latency(wins)
    print("Hypothetical executions against recorded public data."
          " NOT fills, NOT realized P&L.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
