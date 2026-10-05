"""Evaluate the preregistered independent hypotheses on one state set.

Rules, exits, fees and statistics are fixed in
analysis/pmus_independent/PREREGISTRATION.md; this file implements them and
nothing else. Stdlib only so the identical file runs in Actions on sealed C.

Executability (from the preregistration):
  * UP buy at up_ask; DOWN buy at down_px (= 1 - UP bid). Touch qty only.
  * Pre-settlement exit = buy the complement; P&L = 1 - entry - complement - fees.
  * Taker fee 0.0695 p(1-p) at 4dp on every leg; no maker fills, no rebates.
  * Decision seconds require book_age_s <= 2.0.
  * One trade per window per rule (first qualifying second).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import math
import random
import statistics as st
from collections import defaultdict

THETA = 0.0695
FRESH = 2.0
LO_SR, HI_SR = 120, 870
CHECKPOINTS = (840, 600, 450, 300, 180, 120, 90, 60, 30)


def fee(p: float) -> float:
    return round(THETA * p * (1 - p), 4)


def phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ------------------------------------------------------------------ loading

def load(path: str):
    W = defaultdict(list)
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as f:
        for r in csv.DictReader(f):
            ask = float(r["up_ask"])
            dpx = float(r["down_px"])
            row = {
                "t": int(r["t"]), "sr": int(r["seconds_remaining"]),
                "z": float(r["z"]), "sig": float(r["sigma_1s"]),
                "npub": int(r["n_published"]),
                "ua": ask, "uaq": float(r["up_ask_qty"]),
                "da": dpx, "daq": float(r["down_qty"]),
                "spr": float(r["spread"]), "age": float(r["book_age_s"]),
                "tr60": int(r["trades_60s"]),
                "out": int(r["outcome_up"]),
            }
            row["pu"] = phi(row["z"])
            row["mid"] = (ask + (1 - dpx)) / 2
            W[r["slug"]].append(row)
    for s in W:
        W[s].sort(key=lambda x: x["t"])
    return dict(W)


class Win:
    def __init__(self, slug, rows):
        self.slug = slug
        self.rows = rows
        self.by_t = {r["t"]: r for r in rows}
        self.out = rows[0]["out"]
        self.ws = int(dt.datetime.strptime(slug.split("-", 4)[4],
                                           "%Y-%m-%d-%H%Mz")
                      .replace(tzinfo=dt.timezone.utc).timestamp())

    def back(self, t, lag, tol=5):
        """Row at the latest second in [t-lag-tol, t-lag]."""
        for k in range(t - lag, t - lag - tol - 1, -1):
            if k in self.by_t:
                return self.by_t[k]
        return None


def side_px(r, side):
    return (r["ua"], r["uaq"]) if side == "UP" else (r["da"], r["daq"])


def side_phi(r, side):
    return r["pu"] if side == "UP" else 1 - r["pu"]


def comp(side):
    return "DOWN" if side == "UP" else "UP"


def settle_pnl(w, side, px):
    win = (w.out == 1) == (side == "UP")
    return (1 - px if win else -px), fee(px), win


def trade(w, r, side, exit_h=None):
    px, q = side_px(r, side)
    if px <= 0 or px >= 1:
        return None
    rec = {"slug": w.slug, "side": side, "t": r["t"], "sr": r["sr"],
           "entry": px, "touch_qty": q, "sig": r["sig"],
           "hour": dt.datetime.fromtimestamp(r["t"], dt.timezone.utc).hour
           + dt.datetime.fromtimestamp(r["t"], dt.timezone.utc).minute / 60}
    if exit_h is None:
        g, f, win = settle_pnl(w, side, px)
        rec.update(gross=g, fees=f, net=g - f, win=win, fallback=False)
        return rec
    tgt = r["t"] + exit_h
    for x in w.rows:
        if x["t"] >= tgt and x["age"] <= FRESH:
            cp, _ = side_px(x, comp(side))
            if 0 < cp < 1:
                g = 1 - px - cp
                f = fee(px) + fee(cp)
                rec.update(gross=g, fees=f, net=g - f, win=g - f > 0,
                           fallback=False, exit_sr=x["sr"])
                return rec
    g, f, win = settle_pnl(w, side, px)
    rec.update(gross=g, fees=f, net=g - f, win=win, fallback=True)
    return rec


def fresh(r):
    return r["age"] <= FRESH


# ------------------------------------------------------------------- rules
# Each rule: (window) -> (trade or None, n_qualifying_seconds)

def r_baseline(side):
    def f(w):
        for r in w.rows:
            if r["sr"] <= 450 and fresh(r):
                return trade(w, r, side), 1
        return None, 0
    return f


def r_h1(T):
    def f(w):
        for r in w.rows:
            if r["sr"] <= T and fresh(r):
                best = None
                for s in ("UP", "DOWN"):
                    px, _ = side_px(r, s)
                    gap = side_phi(r, s) - px - fee(px)
                    if gap >= 0.05 and (best is None or gap > best[1]):
                        best = (s, gap)
                if best:
                    return trade(w, r, best[0]), 1
                return None, 0          # one checkpoint, no search forward
        return None, 0
    return f


def r_h1x(T, h):
    """POST-D addendum: H1 with a fixed-time complement exit (mechanism check)."""
    def f(w):
        for r in w.rows:
            if r["sr"] <= T and fresh(r):
                best = None
                for s in ("UP", "DOWN"):
                    px, _ = side_px(r, s)
                    gap = side_phi(r, s) - px - fee(px)
                    if gap >= 0.05 and (best is None or gap > best[1]):
                        best = (s, gap)
                if best:
                    return trade(w, r, best[0], h), 1
                return None, 0
        return None, 0
    return f


def beta_table(wins):
    """POST-D addendum: OLS of (outcome - mid) on (Phi - mid), one obs per
    window per checkpoint, HC0 robust se. Threshold-free."""
    out = {}
    for T in (840, 780, 720, 660, 600, 540, 480, 420, 360, 300, 240, 180, 120, 90, 60):
        xs, ys = [], []
        for w in wins:
            r = next((x for x in w.rows if x["sr"] <= T and fresh(x)), None)
            if r is None or r["sr"] < T - 15:
                continue
            xs.append(r["pu"] - r["mid"])
            ys.append(w.out - r["mid"])
        if len(xs) < 10:
            continue
        mx, my = st.mean(xs), st.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        a = my - b * mx
        se = math.sqrt(sum(((x - mx) ** 2) * (y - a - b * x) ** 2
                           for x, y in zip(xs, ys))) / sxx
        out[str(T)] = {"n": len(xs), "beta": round(b, 3), "se": round(se, 3),
                       "t": round(b / se, 2)}
    return out


def _disp(w, cond, pick, exit_h):
    first, n = None, 0
    for r in w.rows:
        if not (LO_SR <= r["sr"] <= HI_SR) or not fresh(r):
            continue
        b = w.back(r["t"], 30)
        if b is None:
            continue
        dm = r["mid"] - b["mid"]
        dp = r["pu"] - b["pu"]
        if abs(dm) >= 0.10 and cond(dm, dp):
            n += 1
            if first is None:
                cheaper = "UP" if dm < 0 else "DOWN"
                side = cheaper if pick == "cheaper" else comp(cheaper)
                first = trade(w, r, side, exit_h)
    return first, n


def r_h2_unexpl(h):
    return lambda w: _disp(w, lambda dm, dp: abs(dp) < 0.03, "cheaper", h)


def r_h2_all(h):
    return lambda w: _disp(w, lambda dm, dp: True, "cheaper", h)


def r_h3_all(h):
    return lambda w: _disp(w, lambda dm, dp: True, "dearer", h)


def r_h3_expl(h):
    return lambda w: _disp(
        w, lambda dm, dp: abs(dp) >= 0.05 and (dp > 0) == (dm > 0),
        "dearer", h)


def r_h4(h):
    def f(w):
        first, n = None, 0
        for r in w.rows:
            if not (LO_SR <= r["sr"] <= HI_SR) or not fresh(r):
                continue
            b = w.back(r["t"], 3, tol=0)
            if b is None:
                continue
            for s in ("UP", "DOWN"):
                a1, _ = side_px(r, s)
                a0, _ = side_px(b, s)
                if a1 <= a0 - 0.05 and abs(side_phi(r, s) - side_phi(b, s)) < 0.02:
                    n += 1
                    if first is None:
                        first = trade(w, r, s, h)
                    break
        return first, n
    return f


def r_h5(h):
    def f(w):
        first, n = None, 0
        for r in w.rows:
            if not (LO_SR <= r["sr"] <= HI_SR) or not fresh(r):
                continue
            if abs(r["spr"] - 0.01) > 1e-9:
                continue
            bq, aq = r["daq"], r["uaq"]
            if bq + aq <= 0:
                continue
            imb = (bq - aq) / (bq + aq)
            s = "UP" if imb >= 0.9 else ("DOWN" if imb <= -0.9 else None)
            if s:
                n += 1
                if first is None:
                    first = trade(w, r, s, h)
        return first, n
    return f


def r_h7(T, lo, hi, which):
    def f(w):
        for r in w.rows:
            if r["sr"] <= T and fresh(r):
                fav = "UP" if r["ua"] > r["da"] else "DOWN"   # dearer = favorite
                s = fav if which == "fav" else comp(fav)
                px, _ = side_px(r, s)
                if lo <= px <= hi:
                    return trade(w, r, s), 1
                return None, 0
        return None, 0
    return f


RULES = [
    ("H0-UP-450", r_baseline("UP")),
    ("H0-DOWN-450", r_baseline("DOWN")),
    ("H1-600", r_h1(600)), ("H1-300", r_h1(300)), ("H1-120", r_h1(120)),
    ("H2-UNEXPL+60s", r_h2_unexpl(60)), ("H2-UNEXPL-settle", r_h2_unexpl(None)),
    ("H2-ALL+60s", r_h2_all(60)), ("H2-ALL-settle", r_h2_all(None)),
    ("H3-ALL+60s", r_h3_all(60)), ("H3-ALL-settle", r_h3_all(None)),
    ("H3-EXPL+60s", r_h3_expl(60)), ("H3-EXPL-settle", r_h3_expl(None)),
    ("H4+30s", r_h4(30)), ("H4-settle", r_h4(None)),
    ("H5+30s", r_h5(30)), ("H5-settle", r_h5(None)),
    ("H7-300", r_h7(300, 0.70, 0.95, "fav")),
    ("H7-120", r_h7(120, 0.70, 0.95, "fav")),
    ("H7U-300", r_h7(300, 0.05, 0.30, "dog")),
    ("H7U-120", r_h7(120, 0.05, 0.30, "dog")),
    # ---- POST-D addendum, frozen before C was opened (see PREREGISTRATION.md)
    ("H1-660", r_h1(660)), ("H1-540", r_h1(540)), ("H1-480", r_h1(480)),
    ("H1-600+120s", r_h1x(600, 120)), ("H1-600+240s", r_h1x(600, 240)),
]


# ------------------------------------------------------------------- stats

def summarize(trades, n_windows, n_qual, seed=7):
    out = {"eligible_windows": n_windows, "trades": len(trades),
           "qualifying_seconds_per_window": round(n_qual / max(1, n_windows), 2)}
    if not trades:
        return out
    nets = [x["net"] for x in trades]
    m = st.mean(nets)
    sd = st.stdev(nets) if len(nets) > 1 else 0.0
    rng = random.Random(seed)
    bs = []
    for _ in range(10000):
        bs.append(sum(nets[rng.randrange(len(nets))] for _ in nets) / len(nets))
    bs.sort()
    cum = peak = dd = 0.0
    run = mrun = 0
    for x in sorted(trades, key=lambda r: r["t"]):
        cum += x["net"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        run = run + 1 if x["net"] <= 0 else 0
        mrun = max(mrun, run)
    q = sorted(x["touch_qty"] for x in trades)
    freq = len(trades) / max(1, n_windows)
    out.update({
        "wins": sum(1 for x in trades if x["win"]),
        "losses": sum(1 for x in trades if not x["win"]),
        "mean_entry": round(st.mean(x["entry"] for x in trades), 4),
        "gross": round(sum(x["gross"] for x in trades), 4),
        "fees": round(sum(x["fees"] for x in trades), 4),
        "net": round(sum(nets), 4),
        "net_per_trade": round(m, 5),
        "ci95": [round(bs[249], 5), round(bs[9749], 5)],
        "t": round(m / (sd / math.sqrt(len(nets))), 3) if sd > 0 else None,
        "max_drawdown": round(dd, 4),
        "max_consec_losses": mrun,
        "trades_per_window": round(freq, 3),
        "usd_per_96_windows_1unit": round(m * freq * 96, 4),
        "touch_qty_median": q[len(q) // 2],
        "touch_qty_p10": q[len(q) // 10],
        "settle_fallbacks": sum(1 for x in trades if x.get("fallback")),
        "up_side_share": round(sum(1 for x in trades if x["side"] == "UP")
                               / len(trades), 3),
    })
    return out


def regimes(trades, sig_med):
    def sub(f):
        xs = [x for x in trades if f(x)]
        if not xs:
            return {"n": 0}
        return {"n": len(xs), "net_per_trade": round(st.mean(x["net"] for x in xs), 5),
                "net": round(sum(x["net"] for x in xs), 4)}
    us = lambda x: 13.5 <= x["hour"] < 20
    return {"us_hours": sub(us), "other_hours": sub(lambda x: not us(x)),
            "sigma_high": sub(lambda x: x["sig"] > sig_med),
            "sigma_low": sub(lambda x: x["sig"] <= sig_med)}


def wilson(k, n, zc=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + zc * zc / n
    c = (p + zc * zc / (2 * n)) / d
    h = zc * math.sqrt(p * (1 - p) / n + zc * zc / (4 * n * n)) / d
    return [round(c - h, 3), round(c + h, 3)]


def calibration(wins):
    """H1 descriptive: one observation per window per checkpoint per side."""
    edges = [0.0, 0.05, 0.15, 0.30, 0.45, 0.55, 0.70, 0.85, 0.95, 1.01]
    out = {}
    for T in CHECKPOINTS:
        cells = defaultdict(lambda: {"n": 0, "win": 0, "px": 0.0, "phi": 0.0})
        for w in wins:
            r = next((x for x in w.rows if x["sr"] <= T and fresh(x)), None)
            if r is None or r["sr"] < T - 15:
                continue
            for s in ("UP", "DOWN"):
                px, _ = side_px(r, s)
                b = next(i for i in range(len(edges) - 1)
                         if edges[i] <= px < edges[i + 1])
                c = cells["%.2f-%.2f" % (edges[b], edges[b + 1])]
                c["n"] += 1
                c["win"] += int((w.out == 1) == (s == "UP"))
                c["px"] += px
                c["phi"] += side_phi(r, s)
        out[str(T)] = {k: {"n": v["n"], "win_rate": round(v["win"] / v["n"], 3),
                           "wilson95": wilson(v["win"], v["n"]),
                           "mean_ask": round(v["px"] / v["n"], 3),
                           "mean_phi": round(v["phi"] / v["n"], 3),
                           "win_minus_ask": round(v["win"] / v["n"] - v["px"] / v["n"], 3)}
                       for k, v in sorted(cells.items())}
    return out


def phi_calibration(wins):
    edges = [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001]
    cells = defaultdict(lambda: [0, 0, 0.0])
    for w in wins:
        for T in (600, 300, 120):
            r = next((x for x in w.rows if x["sr"] <= T and fresh(x)), None)
            if r is None:
                continue
            p = r["pu"]
            b = next(i for i in range(10) if edges[i] <= p < edges[i + 1])
            c = cells["T%d %.1f-%.1f" % (T, edges[b], edges[b + 1])]
            c[0] += 1
            c[1] += w.out
            c[2] += p
    return {k: {"n": v[0], "up_rate": round(v[1] / v[0], 3),
                "mean_phi": round(v[2] / v[0], 3)} for k, v in sorted(cells.items())}


def wide_spread(wins):
    n_states = n_all = 0
    d_up = []
    d_dn = []
    mids = []
    for w in wins:
        for r in w.rows:
            if not (LO_SR <= r["sr"] <= HI_SR):
                continue
            n_all += 1
            if r["spr"] >= 0.04 - 1e-9 and fresh(r):
                n_states += 1
                f = w.by_t.get(r["t"] + 10)
                if f:
                    d_up.append(f["ua"] - r["ua"])
                    d_dn.append(f["da"] - r["da"])
                mids.append((r["mid"], w.out))
    res = {"share_of_seconds": round(n_states / max(1, n_all), 4),
           "states": n_states,
           "windows_with_any": sum(1 for w in wins if any(
               LO_SR <= r["sr"] <= HI_SR and r["spr"] >= 0.04 - 1e-9 for r in w.rows))}
    if d_up:
        res.update(mean_up_ask_change_10s=round(st.mean(d_up), 4),
                   mean_down_ask_change_10s=round(st.mean(d_dn), 4))
    if mids:
        res.update(mean_mid=round(st.mean(m for m, _ in mids), 4),
                   up_rate=round(st.mean(o for _, o in mids), 4),
                   note="state-weighted (autocorrelated); descriptive only")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sigma-median-from", default=None)
    a = ap.parse_args()

    W = load(a.state)
    wins = [Win(s, rows) for s, rows in sorted(W.items())]
    if a.sigma_median_from:
        sig_med = json.load(open(a.sigma_median_from))["sigma_median"]
    else:
        sig_med = st.median(r["sig"] for w in wins for r in w.rows)
    res = {"label": a.label, "windows": len(wins),
           "first": wins[0].slug, "last": wins[-1].slug,
           "up_rate": round(st.mean(w.out for w in wins), 4),
           "sigma_median": sig_med, "rules": {}}
    for name, fn in RULES:
        trades, nq = [], 0
        for w in wins:
            tr, n = fn(w)
            nq += n
            if tr:
                trades.append(tr)
        s = summarize(trades, len(wins), nq)
        s["regimes"] = regimes(trades, sig_med)
        res["rules"][name] = s
    res["H1_calibration"] = calibration(wins)
    res["H1_phi_calibration"] = phi_calibration(wins)
    res["H6_wide_spread"] = wide_spread(wins)
    res["H1_beta_postD"] = beta_table(wins)
    with open(a.out, "w") as fh:
        json.dump(res, fh, indent=1)
    print("%s windows=%d %s..%s up_rate=%.3f" % (a.label, len(wins), res["first"],
                                                 res["last"], res["up_rate"]))
    hdr = "%-18s %4s %5s %7s %8s %19s %6s %6s %5s %7s"
    print(hdr % ("rule", "n", "win%", "entry", "net/tr", "ci95", "t", "maxDD", "mcl", "$/day"))
    for k, s in res["rules"].items():
        if not s.get("trades"):
            print("%-18s %4d" % (k, 0))
            continue
        print("%-18s %4d %5.1f %7.3f %8.4f [%8.4f,%8.4f] %6s %6.2f %5d %7.3f" % (
            k, s["trades"], 100 * s["wins"] / s["trades"], s["mean_entry"],
            s["net_per_trade"], s["ci95"][0], s["ci95"][1], s["t"],
            s["max_drawdown"], s["max_consec_losses"], s["usd_per_96_windows_1unit"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
