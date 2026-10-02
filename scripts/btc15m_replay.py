#!/usr/bin/env python3
"""Polymarket BTC Up/Down 15-minute replay: lead-hold calibration + trade rule.

PART 1 calibrates P(leading side holds) by |lead| bin at 8/10/12 minutes
elapsed, and splits the 10-minute mark by trailing 1-hour volatility.
PART 2 applies the stated trade rule to the calibration.

EVIDENCE CLASS, up front and non-negotiable: this is a MINUTE-RESOLUTION
COINBASE SPOT PROXY. It is not a settlement replay. Chainlink's 60s TWAP and
CF Benchmarks BRTI are unreachable here, Binance returns 451, and no
reachable venue serves sub-minute history. The 60-second opening and closing
averages the rules specify are approximated by OHLC4 of the first and last
minute of the window.

That approximation is weakest exactly where the test is most sensitive -- the
<2 bp bin and the tie rule -- so the smallest bins carry the most proxy
error, and any disagreement with the published 1-second Binance table there
is as likely to be my resolution as their venue.

Window: [T, T+15) for T at :00/:15/:30/:45.
  price to beat P0   OHLC4 of the minute starting at T
  final F            OHLC4 of the minute starting at T+14
  Up wins iff F >= P0 (a tie is Up)
  lead at e minutes  close of the minute ending at T+e, vs P0, in bp
  leading side       Up if lead >= 0 else Down  (a zero lead leads Up, by the tie rule)
"""
from __future__ import annotations

import json, os, sys, math, statistics, collections
from datetime import datetime, timezone, timedelta

CANDLES = "data/btc15m/coinbase_1m.jsonl"
OUT = "analysis/btc15m/replay.json"
ELAPSED = (8, 10, 12)
BINS = ((0, 2), (2, 5), (5, 10), (10, 15), (15, 25), (25, 40), (40, math.inf))
BIN_LABELS = ("<2", "2-5", "5-10", "10-15", "15-25", "25-40", ">40")
PUBLISHED = {
    8:  (58.8, 69.5, 80.8, 87.7, 92.9, 96.3, 97.8),
    10: (60.2, 74.0, 86.1, 91.4, 95.6, 97.4, 99.3),
    12: (62.0, 81.1, 90.9, 95.8, 98.6, 99.6, 99.4),
}
PUBLISHED_VOL = {            # at 10 min elapsed = 5 minutes left: (calm, volatile)
    "<2": (63.9, 52.1), "2-5": (77.7, 67.4), "5-10": (90.6, 80.1),
    "10-15": (94.9, 88.5), "15-25": (97.8, 94.7), "25-40": (98.8, 97.0),
}
TAKER = 0.07
MIN_EDGE = 0.03
CLIP = 50


def binof(x):
    for i, (lo, hi) in enumerate(BINS):
        if lo <= x < hi:
            return i
    return len(BINS) - 1


def load():
    by = {}
    for line in open(CANDLES):
        c = json.loads(line)
        # [time, low, high, open, close, volume]
        t, lo, hi, op, cl, vol = c
        by[int(t)] = (float(lo), float(hi), float(op), float(cl), float(vol))
    return by


def ohlc4(c):
    lo, hi, op, cl, _ = c
    return (op + hi + lo + cl) / 4.0


def main():
    by = load()
    if not by:
        print("no candles"); return 1
    tmin, tmax = min(by), max(by)
    print(f"candles {len(by)}  {datetime.fromtimestamp(tmin, timezone.utc)} -> "
          f"{datetime.fromtimestamp(tmax, timezone.utc)}")

    # minute returns in bp, for the trailing-volatility split
    mins = sorted(by)
    ret = {}
    for i in range(1, len(mins)):
        a, b = mins[i - 1], mins[i]
        if b - a != 60:
            continue
        pa, pb = by[a][3], by[b][3]
        if pa > 0:
            ret[b] = abs(pb - pa) / pa * 1e4

    windows = []
    t = tmin - (tmin % 900)
    while t + 15 * 60 <= tmax:
        need = [t + 60 * k for k in (0, 14)] + [t + 60 * (e - 1) for e in ELAPSED]
        if any(k not in by for k in need):
            t += 900
            continue
        p0 = ohlc4(by[t])
        f = ohlc4(by[t + 14 * 60])
        up_wins = f >= p0
        # trailing 1h realized volatility, bp/min, strictly before the window
        prior = [ret[k] for k in range(t - 3600, t, 60) if k in ret]
        vol = (sum(prior) / len(prior)) if len(prior) >= 30 else None
        w = {"t": t, "p0": p0, "f": f, "up_wins": up_wins, "vol": vol}
        for e in ELAPSED:
            px = by[t + 60 * (e - 1)][3]          # close of the minute ENDING at T+e
            lead_bp = (px - p0) / p0 * 1e4
            up_leads = lead_bp >= 0
            w[f"lead{e}"] = lead_bp
            w[f"hold{e}"] = (up_leads == up_wins)
            w[f"bin{e}"] = binof(abs(lead_bp))
        windows.append(w)
        t += 900
    print(f"windows replayed: {len(windows)}")

    res = {
        "evidence_class": "MINUTE-RESOLUTION COINBASE SPOT PROXY -- NOT SETTLEMENT",
        "settlement_sources_unavailable": ["Chainlink BTC/USD 60s TWAP",
                                           "CF Benchmarks BRTI"],
        "binance_status": "api.binance.com HTTP 451 from this environment; "
                          "api.binance.us quotes a near-dead book",
        "sixty_second_average_proxy": "OHLC4 of the first and last minute",
        "candles": len(by), "windows": len(windows),
        "span": [datetime.fromtimestamp(tmin, timezone.utc).isoformat(),
                 datetime.fromtimestamp(tmax, timezone.utc).isoformat()],
        "part1": {}, "part1_vol": {}, "part2": {},
    }

    # ---------------- PART 1
    print("\nPART 1  lead-hold calibration (pooled Up/Down)")
    for e in ELAPSED:
        print(f"\n elapsed {e} min")
        print(f"   {'bin':>7s} {'n':>6s} {'hold%':>7s} {'pub%':>6s} {'gap':>7s}")
        rows = []
        for i, lab in enumerate(BIN_LABELS):
            sel = [w for w in windows if w[f"bin{e}"] == i]
            n = len(sel)
            h = 100.0 * sum(w[f"hold{e}"] for w in sel) / n if n else float("nan")
            pub = PUBLISHED[e][i]
            gap = h - pub if n else float("nan")
            print(f"   {lab:>7s} {n:>6d} {h:>7.1f} {pub:>6.1f} {gap:>+7.1f}")
            rows.append({"bin": lab, "n": n, "hold_pct": h, "published_pct": pub,
                         "gap_pct": gap})
        res["part1"][str(e)] = rows

    # ---------------- PART 1 volatility split at 10 min
    vols = [w["vol"] for w in windows if w["vol"] is not None]
    med = statistics.median(vols) if vols else None
    print(f"\n trailing 1h volatility: median {med:.3f} bp/min "
          f"(published reference ~3.9), n={len(vols)}")
    print(f"   {'bin':>7s} {'n_calm':>7s} {'calm%':>7s} {'pub':>6s} "
          f"{'n_vol':>7s} {'vol%':>7s} {'pub':>6s}")
    for i, lab in enumerate(BIN_LABELS):
        if lab not in PUBLISHED_VOL:
            continue
        calm = [w for w in windows if w["vol"] is not None and w["vol"] <= med
                and w["bin10"] == i]
        volat = [w for w in windows if w["vol"] is not None and w["vol"] > med
                 and w["bin10"] == i]
        hc = 100.0 * sum(w["hold10"] for w in calm) / len(calm) if calm else float("nan")
        hv = 100.0 * sum(w["hold10"] for w in volat) / len(volat) if volat else float("nan")
        pc, pv = PUBLISHED_VOL[lab]
        print(f"   {lab:>7s} {len(calm):>7d} {hc:>7.1f} {pc:>6.1f} "
              f"{len(volat):>7d} {hv:>7.1f} {pv:>6.1f}")
        res["part1_vol"][lab] = {"median_vol_bp_min": med,
                                 "n_calm": len(calm), "calm_pct": hc, "pub_calm": pc,
                                 "n_vol": len(volat), "vol_pct": hv, "pub_vol": pv}

    # ---------------- PART 2
    # Chronological split: calibrate on the first 60%, trade the last 40%.
    # The stated rule reuses Part 1's rate to both choose and score, which is
    # in-sample; both are reported and the out-of-sample one is the honest read.
    print("\n\nPART 2  trade rule")
    cut = int(len(windows) * 0.6)
    train, test = windows[:cut], windows[cut:]
    print(f" train {len(train)} windows, test {len(test)} windows "
          f"(chronological 60/40)")

    def calib(ws, e):
        out = {}
        for i in range(len(BINS)):
            sel = [w for w in ws if w[f"bin{e}"] == i]
            out[i] = (sum(w[f"hold{e}"] for w in sel) / len(sel)) if sel else None
        return out

    for e in ELAPSED:
        cal_in = calib(windows, e)
        cal_tr = calib(train, e)
        for mode, cal, scored in (("IN_SAMPLE", cal_in, windows),
                                  ("OUT_OF_SAMPLE", cal_tr, test)):
            for ask_mode in ("published", "published+2c"):
                qual, pnl, asks, wins = [], [], [], 0
                for w in scored:
                    i = w[f"bin{e}"]
                    if i == 0:                       # skip leads under 2 bp
                        continue
                    rate = cal[i]
                    if rate is None:
                        continue
                    ask = PUBLISHED[e][i] / 100.0
                    if ask_mode == "published+2c":
                        ask += 0.02
                    ask = min(0.99, round(ask, 2))   # 1-cent tick
                    fee = TAKER * ask * (1 - ask)
                    edge = rate - ask - fee
                    if edge < MIN_EDGE:
                        continue
                    qual.append(w); asks.append(ask)
                    won = w[f"hold{e}"]
                    wins += won
                    pnl.append(CLIP * ((1.0 if won else 0.0) - ask - fee))
                n = len(qual)
                rec = {
                    "elapsed": e, "mode": mode, "ask_mode": ask_mode,
                    "scored_windows": len(scored), "qualified": n,
                    "qualify_frac": n / len(scored) if scored else 0.0,
                    "hit_rate": wins / n if n else None,
                    "avg_ask": sum(asks) / n if n else None,
                    "expectancy_per_share": (sum(pnl) / n / CLIP) if n else None,
                    "total_pnl_50clip": sum(pnl) if n else 0.0,
                    "pnl_mean_50clip": (sum(pnl) / n) if n else None,
                    "pnl_p05": (sorted(pnl)[int(.05 * n)] if n else None),
                    "pnl_median": (sorted(pnl)[n // 2] if n else None),
                    "pnl_p95": (sorted(pnl)[min(n - 1, int(.95 * n))] if n else None),
                }
                res["part2"][f"{e}_{mode}_{ask_mode}"] = rec
                ev = rec["expectancy_per_share"]
                print(f"  e={e:>2d} {mode:<14s} ask={ask_mode:<12s} "
                      f"qual {n:>5d} ({rec['qualify_frac']*100:>5.1f}%)  "
                      f"hit {('%.3f'%rec['hit_rate']) if n else '  -  '}  "
                      f"avg_ask {('%.3f'%rec['avg_ask']) if n else '  -  '}  "
                      f"EV/share {('%+.4f'%ev) if n else '   -   '}  "
                      f"tot50 {rec['total_pnl_50clip']:>+9.2f}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), indent=2, default=str)
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
