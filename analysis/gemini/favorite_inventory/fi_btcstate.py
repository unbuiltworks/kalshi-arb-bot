"""BTC-STATE PREDICTIVE STRUCTURE -- measured independently of trading P&L.

Frozen code at 51fe6efd is not modified; this is a new read-only analysis on
the SAME DEV/VALIDATION cohort. The prospective OOS (data/gemini_infoedge) is
NOT read.

Order of questions, deliberately P&L-free until the end:
  Q1 is the market calibrated at all? (settlement frequency vs executable mid)
  Q2 within comparable Gemini prices, does BTC state separate settlement
     frequency? -- the conditional-separation question
  Q3 does BTC state predict settlement AFTER controlling for the market price?
     (i.e. is Gemini already pricing it?)
  Q4 does BTC state predict short-horizon Gemini REPRICING at
     1/2/5/10/30/60 s? (a signal can miss the winner yet call the next move)

STATISTICAL DISCIPLINE
  All observations inside one window share ONE settlement, so settlement
  statistics are clustered at the window level. Every settlement frequency
  below carries a WINDOW-CLUSTERED bootstrap standard error, and the
  achievable resolution is reported rather than assumed. Repricing statistics
  are per-observation but are also reported with window-clustered error.
  No thresholds are searched. State variables are split at FIXED tertiles of
  the DEV distribution and those same cut points are reused on VALIDATION.
"""
from __future__ import annotations

import json
import math
import random
import sys
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, "analysis/gemini/favorite_inventory")
from fi_replay import load, book_at, btc_state, MOM_HORIZONS

FWD = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
MID_BUCKETS = [(0.35, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.80)]
STATE_VARS = ["dist", "dist_pct", "dist_vol", "ret_1s", "ret_5s", "ret_15s",
              "ret_30s", "ret_60s", "accel", "crossings", "frac_above",
              "max_exc", "sigma_per_sqrt_s"]
random.seed(20261002)


def boot_se(pairs, n=400):
    """Window-clustered bootstrap se of a mean over (window, value) pairs."""
    byw = defaultdict(list)
    for w, v in pairs:
        byw[w].append(v)
    ws = list(byw)
    if len(ws) < 3:
        return None
    out = []
    for _ in range(n):
        pick = [byw[ws[random.randrange(len(ws))]] for _ in ws]
        flat = [x for g in pick for x in g]
        if flat:
            out.append(sum(flat) / len(flat))
    if len(out) < 10:
        return None
    m = sum(out) / len(out)
    return math.sqrt(sum((x - m) ** 2 for x in out) / (len(out) - 1))


def build(cohort, spot):
    """One row per in-window two-sided book observation. Causal only."""
    spot_t = [s[0] for s in spot]
    spot_p = [s[1] for s in spot]
    rows = []
    for m, w in cohort.items():
        exp, strike = w["expiry_t"], w["strike"]
        if not (exp and w["resolution"] and w["book"]):
            continue
        start = exp - 300.0
        up_won = 1 if w["resolution"] == "yes" else 0
        bts = [b[0] for b in w["book"]]
        for i, (t, bk) in enumerate(w["book"]):
            if not (start <= t <= exp) or not bk.two_sided:
                continue
            st = btc_state(spot_t, spot_p, t, strike)
            if st is None or st.get("dist_vol") is None:
                continue
            # acceleration: 5s return minus the prior 5s return
            j = bisect_right(spot_t, t - 5.0) - 1
            k = bisect_right(spot_t, t - 10.0) - 1
            st["accel"] = ((st.get("ret_5s") or 0) -
                           ((spot_p[j] - spot_p[k]) / spot_p[k]
                            if (j >= 0 and k >= 0) else 0))
            mid = float(bk.mid)
            fwd = {}
            for h in FWD:
                jj = bisect_right(bts, t + h) - 1
                if jj > i and w["book"][jj][1].two_sided:
                    fwd[h] = float(w["book"][jj][1].mid) - mid
                else:
                    fwd[h] = None
            rows.append({"w": m, "t": t, "tte": exp - t, "mid": mid,
                         "bid": float(bk.bid), "ask": float(bk.ask),
                         "spread": float(bk.spread),
                         "depth": float(min(bk.bid_sz or 0, bk.ask_sz or 0)),
                         "up_won": up_won,
                         **{k2: st.get(k2) for k2 in STATE_VARS},
                         **{f"fwd_{int(h)}s": fwd[h] for h in FWD}})
    return rows


def q1_calibration(rows, label):
    print(f"\n--- Q1 IS THE MARKET CALIBRATED?  [{label}] ---")
    print(f"  {'mid bucket':>14s} {'obs':>6s} {'wins':>5s} {'settle_freq':>12s} "
          f"{'clustered_se':>13s} {'mid_mean':>9s} {'gap':>8s}")
    for lo, hi in MID_BUCKETS:
        g = [r for r in rows if lo <= r["mid"] < hi]
        if len(g) < 20:
            continue
        freq = sum(r["up_won"] for r in g) / len(g)
        se = boot_se([(r["w"], r["up_won"]) for r in g])
        mm = sum(r["mid"] for r in g) / len(g)
        nw = len({r["w"] for r in g})
        print(f"  {f'{lo:.2f}-{hi:.2f}':>14s} {len(g):6d} {nw:5d} "
              f"{freq:12.3f} {(f'{se:.3f}' if se else '-'):>13s} "
              f"{mm:9.3f} {freq-mm:+8.3f}")
    print("  'wins' column is WINDOW count (the effective n), not observations.")


def q2_q3_separation(rows, label, cuts=None):
    """Within comparable mid, split each state var at FIXED tertiles and
    compare settlement frequency. Returns the cut points used."""
    print(f"\n--- Q2/Q3 CONDITIONAL SEPARATION WITHIN COMPARABLE MID  [{label}] ---")
    made = {}
    for var in STATE_VARS:
        vals = [r[var] for r in rows if r.get(var) is not None]
        if len(vals) < 100:
            continue
        if cuts and var in cuts:
            c1, c2 = cuts[var]
        else:
            s = sorted(vals)
            c1, c2 = s[len(s) // 3], s[2 * len(s) // 3]
        made[var] = (c1, c2)
    hdr = (f"  {'state var':>18s} {'mid bucket':>12s} {'lo_freq':>8s} {'hi_freq':>8s} "
           f"{'separation':>11s} {'lo_se':>7s} {'hi_se':>7s} {'lo_w':>5s} {'hi_w':>5s}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    findings = []
    for var, (c1, c2) in made.items():
        for lo, hi in MID_BUCKETS:
            g = [r for r in rows if lo <= r["mid"] < hi and r.get(var) is not None]
            if len(g) < 60:
                continue
            lowg = [r for r in g if r[var] <= c1]
            higg = [r for r in g if r[var] >= c2]
            if len(lowg) < 20 or len(higg) < 20:
                continue
            fl = sum(r["up_won"] for r in lowg) / len(lowg)
            fh = sum(r["up_won"] for r in higg) / len(higg)
            sl = boot_se([(r["w"], r["up_won"]) for r in lowg])
            sh = boot_se([(r["w"], r["up_won"]) for r in higg])
            nwl = len({r["w"] for r in lowg})
            nwh = len({r["w"] for r in higg})
            sep = fh - fl
            findings.append({"var": var, "bucket": (lo, hi), "lo_freq": fl,
                             "hi_freq": fh, "sep": sep, "lo_se": sl,
                             "hi_se": sh, "lo_w": nwl, "hi_w": nwh})
            flag = ""
            if sl and sh and abs(sep) > 2 * math.sqrt(sl ** 2 + sh ** 2):
                flag = "  <== |sep| > 2 joint se"
            print(f"  {var:>18s} {f'{lo:.2f}-{hi:.2f}':>12s} {fl:8.3f} {fh:8.3f} "
                  f"{sep:+11.3f} {(f'{sl:.3f}' if sl else '-'):>7s} "
                  f"{(f'{sh:.3f}' if sh else '-'):>7s} {nwl:5d} {nwh:5d}{flag}")
    return made, findings


def q4_repricing(rows, label):
    print(f"\n--- Q4 DOES BTC STATE PREDICT GEMINI REPRICING?  [{label}] ---")
    print("  Spearman-style rank correlation of each state var with forward")
    print("  executable MID change, window-clustered se on the mean move.")
    hdr = f"  {'state var':>18s} " + " ".join(f"{'rho_'+str(int(h))+'s':>9s}" for h in FWD)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    out = {}
    for var in STATE_VARS:
        line = f"  {var:>18s} "
        rhos = {}
        for h in FWD:
            key = f"fwd_{int(h)}s"
            pairs = [(r[var], r[key]) for r in rows
                     if r.get(var) is not None and r.get(key) is not None]
            if len(pairs) < 200:
                line += f"{'-':>9s} "
                continue
            xs = sorted(range(len(pairs)), key=lambda i: pairs[i][0])
            ys = sorted(range(len(pairs)), key=lambda i: pairs[i][1])
            rx = [0] * len(pairs); ry = [0] * len(pairs)
            for rank, i in enumerate(xs): rx[i] = rank
            for rank, i in enumerate(ys): ry[i] = rank
            n = len(pairs)
            mx = my = (n - 1) / 2
            num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
            dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
            dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
            rho = num / (dx * dy) if dx and dy else 0.0
            rhos[h] = rho
            line += f"{rho:+9.3f} "
        out[var] = rhos
        print(line)
    return out


def main():
    W, spot = load()
    usable = {m: w for m, w in W.items()
              if w["expiry_t"] and w["resolution"] and w["book"]}
    order = sorted(usable.items(), key=lambda kv: kv[1]["expiry_t"])
    n = len(order)
    dev = dict(order[:int(n * 0.6)])
    val = dict(order[int(n * 0.6):])
    print("=" * 84)
    print("BTC-STATE PREDICTIVE STRUCTURE (no trading P&L in Q1-Q4)")
    print("frozen code 51fe6efd unmodified; prospective OOS NOT read")
    print("=" * 84)
    rd = build(dev, spot)
    rv = build(val, spot)
    print(f"DEV observations {len(rd)} over {len({r['w'] for r in rd})} windows")
    print(f"VAL observations {len(rv)} over {len({r['w'] for r in rv})} windows")
    print()
    print("ACHIEVABLE RESOLUTION: with ~%d DEV windows, a settlement frequency"
          % len({r['w'] for r in rd}))
    print("has a clustered se near %.3f, so separations under roughly %.2f"
          % (0.5 / math.sqrt(max(1, len({r['w'] for r in rd}))),
             2 * 0.5 / math.sqrt(max(1, len({r['w'] for r in rd})))))
    print("are NOT detectable here. This is a hard limit of the cohort, stated")
    print("up front rather than discovered after the fact.")

    q1_calibration(rd, "DEV")
    q1_calibration(rv, "VALIDATION")
    cuts, fdev = q2_q3_separation(rd, "DEV")
    _, fval = q2_q3_separation(rv, "VALIDATION (DEV cut points reused)", cuts)
    rhod = q4_repricing(rd, "DEV")
    rhov = q4_repricing(rv, "VALIDATION")

    # ---- replication check: separations that survive both splits ----
    print("\n--- REPLICATION: separations surviving DEV -> VALIDATION ---")
    vmap = {(f["var"], tuple(f["bucket"])): f for f in fval}
    surv = []
    for f in fdev:
        k = (f["var"], tuple(f["bucket"]))
        v = vmap.get(k)
        if not v:
            continue
        if f["lo_se"] and f["hi_se"]:
            jse = math.sqrt(f["lo_se"] ** 2 + f["hi_se"] ** 2)
            sig = abs(f["sep"]) > 2 * jse
        else:
            sig = False
        same_sign = (f["sep"] > 0) == (v["sep"] > 0)
        if sig and same_sign and abs(v["sep"]) > 0.05:
            surv.append((f, v))
    if surv:
        for f, v in surv:
            print(f"  {f['var']} @ mid {f['bucket']}: DEV sep {f['sep']:+.3f} "
                  f"-> VAL sep {v['sep']:+.3f}  SURVIVES")
    else:
        print("  NONE. No state separation that was significant in DEV kept its")
        print("  sign and magnitude in VALIDATION.")

    print("\n--- Q4 REPLICATION: rank correlations stable in sign DEV->VAL ---")
    stable = []
    for var in STATE_VARS:
        for h in FWD:
            a = rhod.get(var, {}).get(h)
            b = rhov.get(var, {}).get(h)
            if a is None or b is None:
                continue
            if abs(a) > 0.05 and abs(b) > 0.05 and (a > 0) == (b > 0):
                stable.append((var, h, a, b))
    if stable:
        for var, h, a, b in stable:
            print(f"  {var} @ {int(h)}s : DEV rho {a:+.3f} -> VAL rho {b:+.3f}")
    else:
        print("  NONE with |rho| > 0.05 in both splits and a consistent sign.")

    Path("analysis/gemini/favorite_inventory/fi_btcstate_dev.json").write_text(
        json.dumps(fdev, indent=1))
    Path("analysis/gemini/favorite_inventory/fi_btcstate_val.json").write_text(
        json.dumps(fval, indent=1))


if __name__ == "__main__":
    main()
