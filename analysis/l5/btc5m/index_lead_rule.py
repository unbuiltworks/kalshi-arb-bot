"""Does our venue's own settlement index tell us the outcome before our
quotes reprice? A strict causal test of ONE executable taker rule.

INPUTS  the venue's one-second settlement index (the 60-second average
        the market settles on; public gateway history) and the public
        recorder's quotes (UP bid/ask every ~2 s, receipt-stamped).

WHAT IS KNOWN AT A QUOTE STAMPED T
  the quote itself; the window's price-to-beat K; index buckets up to
  T - INDEX_LAG seconds (live, the index arrives 0.1-1.0 s old; 2 s used).
  Nothing later. The outcome is used only to fit (FIT days) and to score.

MODEL (fitted on FIT days only, then frozen)
  P(UP) = logistic(b0 + b1 * gap + b2 * tilt), separately for five
  bands of seconds remaining, where
    gap  = (index - K) / scale
    tilt = 30 x (index slope over the last 10 s) / scale
           (a rising 60-second average means spot is already above it)
    scale = typical 60-second index change over the prior 30 minutes
            x sqrt((seconds remaining + 30) / 60)

THE RULE
  One entry per window, one share. At the first quote between 10 s and
  290 s where  model P(side) - ask(side) - fee(ask) >= THETA, send a buy
  limited to that ask. It is matched against the first quote at least
  DELAY seconds later: filled at that quote's ask if it is not above the
  limit, otherwise MISSED (and the rule may try again later in the
  window). Held to settlement. Fee 0.095 x p x (1 - p). THETA is chosen
  on the FIT days and frozen.

ACCOUNTING  research_accounting.Ledger: totals over every fill and every
window run. Size at the ask is not recorded: one share assumed fillable.
"""
import bisect
import collections
import datetime as dt
import gzip
import json
import math
import sys

import numpy as np

sys.path.insert(0, "analysis/l5/btc5m")
from research_accounting import Ledger  # noqa: E402

K_FEE = 0.095
INDEX_LAG = 2
TAU_BANDS = ((180, 400), (120, 180), (60, 120), (30, 60), (0, 30))
THETAS = (0.0, 0.02, 0.04, 0.06, 0.10, 0.15)


def fee(p):
    return K_FEE * p * (1 - p)


def epoch(utc):
    return dt.datetime.fromisoformat(utc).timestamp()


def load_index(paths):
    ts, a = [], []
    for p in paths:
        for line in gzip.open(p, "rt"):
            r = json.loads(line)
            if isinstance(r, dict):
                ts.append(int(r["t"]))
                a.append(float(r["a"]))
            else:
                ts.append(int(r[0]))
                a.append(float(r[1]))
    o = np.argsort(ts)
    ts = np.array(ts)[o]
    a = np.array(a)[o]
    keep = np.concatenate(([True], np.diff(ts) > 0))
    return ts[keep], a[keep]


class Index:
    def __init__(self, ts, a):
        self.ts, self.a = ts, a

    def at(self, t, max_age=5):
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0 or t - self.ts[i] > max_age:
            return None
        return float(self.a[i])

    def scale60(self, t0):
        i0 = bisect.bisect_left(self.ts, t0 - 1800)
        i1 = bisect.bisect_right(self.ts, t0 - 1)
        if i1 - i0 < 1200:
            return None
        seg = self.a[i0:i1]
        d = seg[60:] - seg[:-60]
        return max(float(np.sqrt(np.mean(d * d))), 0.5)


def load_quotes(paths):
    q = collections.defaultdict(dict)
    tb = {}
    for p in paths:
        op = gzip.open if p.endswith(".gz") else open
        for line in op(p, "rt"):
            r = json.loads(line)
            if r.get("to_beat") is not None:
                tb.setdefault(r["slug"], float(r["to_beat"]))
            if r.get("closed") or r.get("bid") is None \
                    or r.get("ask") is None:
                continue
            q[r["slug"]][r["utc"]] = (epoch(r["utc"]), float(r["bid"]),
                                      float(r["ask"]))
    return {s: sorted(v.values()) for s, v in q.items()}, tb


def start_of(slug):
    return dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz").replace(
        tzinfo=dt.timezone.utc).timestamp()


def nxt(slug):
    d = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz") \
        + dt.timedelta(minutes=5)
    return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")


def features(idx, slug, K, scale, T):
    t0 = start_of(slug)
    tau = t0 + 300 - T
    a = idx.at(int(T) - INDEX_LAG)
    a10 = idx.at(int(T) - INDEX_LAG - 10)
    if a is None or a10 is None or tau <= 0:
        return None
    s = scale * math.sqrt((tau + 30) / 60.0)
    band = next(i for i, (lo, hi) in enumerate(TAU_BANDS) if lo <= tau < hi)
    return band, (a - K) / s, 30 * (a - a10) / 10.0 / s


def fit_logit(X, y, iters=30):
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ b, -30, 30)))
        g = X.T @ (y - p) - 1e-3 * b
        H = (X * (p * (1 - p))[:, None]).T @ X + 1e-3 * np.eye(len(b))
        b = b + np.linalg.solve(H, g)
    return b


def main():
    args = sys.argv[1:]
    idx_paths = [a for a in args if "brti" in a or "index" in a]
    q_paths = [a for a in args if a not in idx_paths]
    idx = Index(*load_index(idx_paths))
    quotes, tb = load_quotes(q_paths)
    res, scale = {}, {}
    for s in quotes:
        if s in tb and nxt(s) in tb:
            sc = idx.scale60(start_of(s))
            if sc is not None:
                res[s] = "UP" if tb[nxt(s)] >= tb[s] else "DOWN"
                scale[s] = sc
    day = lambda s: s[-16:-6]
    FIT = sorted(s for s in res if day(s) in ("2026-11-17", "2026-11-18"))
    TEST = sorted(s for s in res if day(s) in ("2026-11-19", "2026-11-20"))
    gaps = sorted(b[0] - a[0] for s in TEST for a, b in
                  zip(quotes[s], quotes[s][1:]))
    print("windows: fit %d, test %d; test quote spacing median %.1f s, "
          "p90 %.1f s" % (len(FIT), len(TEST), gaps[len(gaps) // 2],
                          gaps[int(.9 * len(gaps))]))

    # ---- fit
    rows = collections.defaultdict(list)
    for s in FIT:
        for T, b, a in quotes[s]:
            f = features(idx, s, tb[s], scale[s], T)
            if f:
                rows[f[0]].append((1.0, f[1], f[2],
                                   1.0 if res[s] == "UP" else 0.0))
    coef = {}
    for band, r in rows.items():
        M = np.array(r)
        coef[band] = fit_logit(M[:, :3], M[:, 3])
        print("   %3d-%3d s left: b0 %+.3f  gap %+.3f  tilt %+.3f  (n %d)" % (
            TAU_BANDS[band][0], TAU_BANDS[band][1], *coef[band], len(r)))

    def p_up(s, T):
        f = features(idx, s, tb[s], scale[s], T)
        if f is None or f[0] not in coef:
            return None
        z = float(np.dot(coef[f[0]], (1.0, f[1], f[2])))
        return 1 / (1 + math.exp(-max(min(z, 30), -30)))

    # ---- is the model better than the quote, and do quotes follow it?
    def diag(name, ws):
        bm = bq = n = 0.0
        lead = collections.defaultdict(lambda: [0.0, 0])
        for s in ws:
            qs = quotes[s]
            y = 1.0 if res[s] == "UP" else 0.0
            for i, (T, b, a) in enumerate(qs):
                p = p_up(s, T)
                if p is None:
                    continue
                mid = (a + b) / 2
                bm += (p - y) ** 2
                bq += (mid - y) ** 2
                n += 1
                d = p - mid
                k = "model above quote by 3c+" if d >= .03 else \
                    "model below quote by 3c+" if d <= -.03 else "within 3c"
                for h in (2, 6, 10):
                    j = bisect.bisect_left(qs, (T + h - 0.3,))
                    if j < len(qs) and qs[j][0] - T <= h + 3:
                        c = lead[(k, h)]
                        c[0] += (qs[j][1] + qs[j][2]) / 2 - mid
                        c[1] += 1
        print("   %s: Brier model %.4f, quote mid %.4f (%d quotes)" % (
            name, bm / n, bq / n, n))
        for k in ("model above quote by 3c+", "within 3c",
                  "model below quote by 3c+"):
            print("      %-26s quote mid moves %s" % (k, "  ".join(
                "%+.4f in %2d s" % (lead[(k, h)][0] / max(lead[(k, h)][1],
                                                          1), h)
                for h in (2, 6, 10))) + "   (n %d)" % lead[(k, 2)][1])

    print("\n== model against the quote ==")
    diag("FIT", FIT)
    diag("TEST", TEST)

    # ---- the rule
    def run(ws, theta, delay):
        led = Ledger(ws, size_known=False)
        missed = sent = 0
        for s in ws:
            qs = quotes[s]
            t0 = start_of(s)
            i = 0
            while i < len(qs):
                T, b, a = qs[i]
                i += 1
                if not (10 <= T - t0 <= 290):
                    continue
                p = p_up(s, T)
                if p is None:
                    continue
                pick = None
                for oc, pr, ask in (("UP", p, a), ("DOWN", 1 - p, 1 - b)):
                    if 0.02 < ask < 0.98 and pr - ask - fee(ask) >= theta:
                        pick = (oc, ask)
                if pick is None:
                    continue
                sent += 1
                j = bisect.bisect_left(qs, (T + delay,)) if delay > 0 \
                    else i - 1
                if j >= len(qs) or qs[j][0] - T > delay + 6:
                    missed += 1
                    i = max(i, j)
                    continue
                _T2, b2, a2 = qs[j]
                px = a2 if pick[0] == "UP" else 1 - b2
                if px > pick[1] + 1e-9 or not (0.01 < px < 0.995):
                    missed += 1
                    i = max(i, j)
                    continue
                led.fill(s, 1.0, px, fee(px), res[s] == pick[0])
                break
        return led, sent, missed

    for delay in (2, 4, 0):
        print("\n== THE RULE, order matched %d s after the signal%s ==" % (
            delay, "  (NOT executable: same instant, shown as a ceiling)"
            if delay == 0 else ""))
        best = None
        for th in THETAS:
            led, sent, missed = run(FIT, th, delay)
            t = led.totals()
            print("   FIT  theta %.2f: traded %4d of %d windows, missed "
                  "%4d of %5d orders, NET $%+.2f, per window run %+.4f" % (
                      th, t["windows_traded"], t["windows_run"], missed,
                      sent, t["net"], t["net"] / t["windows_run"]))
            if t["fills"] >= 50 and (best is None or t["net"] > best[1]):
                best = (th, t["net"])
        if best is None:
            print("   no threshold trades enough on the fit days")
            continue
        led, sent, missed = run(TEST, best[0], delay)
        print("   chosen on FIT: theta %.2f.  HELD-OUT 11-19/20, orders "
              "sent %d, missed %d:" % (best[0], sent, missed))
        print("   " + led.report("held-out result").replace("\n", "\n   "))
        for th in THETAS:
            if th == best[0]:
                continue
            l2, s2, m2 = run(TEST, th, delay)
            t = l2.totals()
            print("   (other theta %.2f on held-out: traded %d, NET $%+.2f)"
                  % (th, t["windows_traded"], t["net"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
