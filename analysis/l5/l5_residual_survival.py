"""Historical L5 residual survival, same definition as the PMUS curve.

  t=0    first acquisition that takes inventory from FLAT to a directional
         residual
  event  inventory returns to flat (|residual| == 0)
  censor observation of that window ends with residual still open

STRUCTURAL CAVEAT, STATED BEFORE ANY NUMBER. The L5 corpus is entirely BTC/ETH
5-minute and 15-minute markets: the longest observed fill span in any window is
888s and the median is 153s. An L5 residual therefore CANNOT outlive a ~900s
settlement clock -- the market resolves and the position pays out. PMUS live
sports markets run for hours. P(flat by 1200s) is not merely unmeasured for L5,
it is undefined, and horizons beyond the market's own life are reported as such
rather than as zeros.

CENSORING CHOICE, and why it is the conservative one. A window is observed to
its SETTLEMENT (timestamp of last fill + its seconds_to_settlement), not merely
to the last fill, so a residual still open when L5 stops trading is censored at
settlement rather than being scored as resolved. Scoring it resolved would
credit L5 with a flatten it never traded.
"""
import gzip, csv, collections

CORPUS = "/home/user/kalshi-arb-bot/analysis/l5/l5_canonical_sequence.csv.gz"
HOR = [30, 60, 120, 300, 600, 900, 1200]


def episodes():
    rows = list(csv.DictReader(gzip.open(CORPUS, "rt")))
    w = collections.defaultdict(list)
    for r in rows:
        w[r["window_id"]].append(r)
    out = []
    for k, v in w.items():
        v.sort(key=lambda r: (int(r["trade_seq"]), float(r["timestamp"])))
        t_end = float(v[-1]["timestamp"]) + float(v[-1]["seconds_to_settlement"])
        open_t = None
        run = 1
        for i, r in enumerate(v):
            resid = float(r["residual_qty_signed"])
            if i and v[i]["outcome"] == v[i - 1]["outcome"]:
                run += 1
            elif i:
                run = 1
            if open_t is None and resid != 0:
                open_t = float(r["timestamp"]); open_run = run
            elif open_t is not None and resid == 0:
                out.append({"dur": float(r["timestamp"]) - open_t, "event": 1,
                            "run": open_run, "window": k})
                open_t = None
        if open_t is not None:
            out.append({"dur": max(0.0, t_end - open_t), "event": 0,
                        "run": open_run, "window": k})
    return out


def km(eps, label, hor=HOR, market_life=None):
    obs = sorted((e["dur"], e["event"]) for e in eps)
    n = len(obs)
    if not n:
        print("%-38s n=0" % label); return
    times = sorted(set(t for t, _ in obs))
    S = 1.0; atrisk = n; cum = {}
    for t in times:
        d = sum(1 for tt, ev in obs if tt == t and ev == 1)
        c = sum(1 for tt, ev in obs if tt == t and ev == 0)
        if atrisk > 0 and d:
            S *= (1 - d / atrisk)
        atrisk -= (d + c)
        cum[t] = 1 - S
    def at(h):
        v = 0.0
        for t in times:
            if t <= h:
                v = cum[t]
        return v, sum(1 for tt, _ in obs if tt >= h)
    ev = sum(e["event"] for e in eps)
    print("\n%-38s n=%d resolved=%d censored=%d" % (label, n, ev, n - ev))
    print("   %-12s" % "horizon" + "".join("%11s" % ("%ds" % h) for h in hor))
    cells = []
    for h in hor:
        if market_life is not None and h > market_life:
            cells.append("undefined")
        else:
            cells.append("%.1f%%" % (100 * at(h)[0]))
    print("   %-12s" % "cum P(flat)" + "".join("%11s" % c for c in cells))
    print("   %-12s" % "at risk" + "".join(
        "%11s" % (at(h)[1] if (market_life is None or h <= market_life) else "-")
        for h in hor))
    med = None
    for t in times:
        if cum[t] >= 0.5:
            med = t; break
    print("   median time to flat: %s" % ("%.0fs" % med if med else "NOT REACHED"))
