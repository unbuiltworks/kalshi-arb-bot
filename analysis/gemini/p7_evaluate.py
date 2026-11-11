"""Evaluate arms pre-rebate, split chronologically, with the tail reported.

SPLIT IS CHRONOLOGICAL AND FIXED BEFORE ANY ARM IS SCORED:
    development  2026-09-16 .. 09-18
    validation   2026-09-19 .. 09-20
    OOS          2026-09-21 .. 09-22   <- not optimized against
"""
import json, statistics, sys
from collections import defaultdict
from fractions import Fraction as F

sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src.gemini_execution import ev_replay as R

DEV = {"2026-09-16", "2026-09-17", "2026-09-18"}
VAL = {"2026-09-19", "2026-09-20"}
OOS = {"2026-09-21", "2026-09-22"}


def split_of(day):
    return "dev" if day in DEV else ("val" if day in VAL else
                                     ("oos" if day in OOS else None))


def nearest_money_1(contracts):
    """Causal: within one hourly event keep the strike whose FIRST print is
    nearest $0.50. Uses only that first print."""
    best, bestd = None, None
    for c in contracts:
        pr = sorted(c.get("prints") or [], key=lambda r: int(r[0]))
        if not pr:
            continue
        p0 = F(pr[0][2])
        d = abs(p0 - F(1, 2))
        if bestd is None or d < bestd:
            best, bestd = c, d
    return [best] if best else []


def run_arm(contracts, *, label, duration, policy, hourly_select=False, **kw):
    if hourly_select:
        by_ev = defaultdict(list)
        for c in contracts:
            by_ev[c["event_ticker"]].append(c)
        contracts = [x for ev in by_ev.values() for x in nearest_money_1(ev)]
    rows = []
    for c in contracts:
        if not c.get("prints"):
            continue
        r = R.replay(c, policy=policy, **kw)
        if r.get("verdict") == "SETTLED":
            rows.append(r)
    return {"label": label, "duration": duration, "rows": rows}


def score(arm, split=None):
    rows = [r for r in arm["rows"] if split is None or split_of(r["day"]) == split]
    rows.sort(key=lambda r: (r["day"], r["hour"], r["symbol"]))
    if not rows:
        return None
    net = sum(F(r["net"]) for r in rows)
    mp = sum(F(r["matched_pnl"]) for r in rows)
    rp = sum(F(r["residual_pnl"]) for r in rows)
    spend = sum(F(r["spend"]) for r in rows)
    fills = sum(r["fills"] for r in rows)
    matched = sum(r["matched"] for r in rows)
    resid = sum(r["residual"] for r in rows)
    traded = [r for r in rows if r["fills"] > 0]
    eq = F(0); peak = F(0); dd = F(0)
    nets = []
    for r in rows:
        v = F(r["net"]); nets.append(float(v))
        eq += v; peak = max(peak, eq); dd = max(dd, peak - eq)
    days = len({r["day"] for r in rows})
    streak = worst_streak = 0
    for v in nets:
        if v < 0:
            streak += 1; worst_streak = max(worst_streak, streak)
        else:
            streak = 0
    nets_sorted = sorted(nets)
    def pct(p):
        if not nets_sorted: return None
        i = int(p * (len(nets_sorted) - 1))
        return nets_sorted[i]
    return {
        "markets": len(rows), "traded": len(traded),
        "net": float(net), "per_market": float(net) / len(rows),
        "roc": (float(net / spend) if spend else None),
        "matched_pnl": float(mp), "residual_pnl": float(rp),
        "spend": float(spend), "fills": fills,
        "matched_qty": matched, "residual_qty": resid,
        "completion": (matched * 2 / fills) if fills else 0,
        "max_dd": float(dd), "days": days,
        "markets_per_day": len(rows) / days if days else 0,
        "worst_market": min(nets) if nets else 0,
        "p05": pct(0.05), "p25": pct(0.25), "median": pct(0.50),
        "losing_streak": worst_streak,
        "pct_losing": sum(1 for v in nets if v < 0) / len(nets),
    }


HDR = (f"{'arm':>34} {'split':>5} {'mkts':>5} {'trd':>5} {'fills':>6} {'spend':>8} "
       f"{'matched$':>9} {'resid$':>9} {'NET$':>9} {'/mkt':>7} {'RoC':>7} "
       f"{'maxDD':>7} {'compl':>6} {'resQty':>7}")


def show(arm, splits=("dev", "val", "oos")):
    for sp in splits:
        s = score(arm, sp)
        if not s:
            continue
        roc = "-" if s["roc"] is None else f"{s['roc']:.2%}"
        print(f"{arm['label']:>34} {sp:>5} {s['markets']:>5} {s['traded']:>5} "
              f"{s['fills']:>6} {s['spend']:>8.2f} {s['matched_pnl']:>9.2f} "
              f"{s['residual_pnl']:>9.2f} {s['net']:>9.2f} {s['per_market']:>7.4f} "
              f"{roc:>7} {s['max_dd']:>7.2f} {s['completion']:>6.2f} {s['residual_qty']:>7}",
              flush=True)


def tail(arm, split):
    s = score(arm, split)
    if not s:
        return None
    return {"label": arm["label"], "split": split, "worst_market": s["worst_market"],
            "p05": s["p05"], "p25": s["p25"], "median": s["median"],
            "losing_streak": s["losing_streak"], "pct_losing": s["pct_losing"],
            "max_dd": s["max_dd"], "residual_qty": s["residual_qty"]}


# --------------------------------------------------------------------------
# calibration of the IMPORTED p_win curve against Gemini's own resolutions
# --------------------------------------------------------------------------
# p_win comes from L5's Polymarket history. Whether it describes GEMINI is a
# separate question and this answers it directly: every print makes one leg
# buyable at a price; the venue's own resolutionSide says whether that leg
# won. NOT RECALIBRATED ON OOS -- the OOS column is reported and nothing is
# fitted to it.
from src.gemini_execution import ev_policy as EV

CAL_BANDS = ((F(0), F(35, 100), "<0.35"), (F(35, 100), F(50, 100), "0.35-0.50"),
             (F(50, 100), F(65, 100), "0.50-0.65"),
             (F(65, 100), F(80, 100), "0.65-0.80"), (F(80, 100), F(10), ">0.80"))


def cal_band(p):
    for lo, hi, nm in CAL_BANDS:
        if lo <= p < hi:
            return nm
    return ">0.80"


def calibrate(contracts, split):
    obs = defaultdict(lambda: {"n": 0, "won": 0, "px": F(0)})
    for c in contracts:
        if split_of(c.get("day")) != split:
            continue
        res = str(c.get("resolution") or "").strip().lower()
        won = "YES" if res in ("yes", "up", "true") else (
            "NO" if res in ("no", "down", "false") else None)
        if won is None:
            continue
        for t, side, px_s, _q in (c.get("prints") or []):
            px = F(px_s)
            offered = "YES" if side == "sell" else ("NO" if side == "buy" else None)
            if offered is None:
                continue
            unit = px if offered == "YES" else (F(1) - px)
            if unit <= 0 or unit >= 1:
                continue
            a = obs[cal_band(unit)]
            a["n"] += 1
            a["px"] += unit
            if offered == won:
                a["won"] += 1
    return obs


def print_calibration(contracts, label=""):
    print(f"\n=== calibration of the imported L5 p_win curve on Gemini {label} ===")
    print("    (every print = one leg buyable at a price; did that leg win?)")
    hdr = f"{'band':>11} " + " ".join(
        f"{s:>26}" for s in ("dev", "val", "OOS (untouched)"))
    print(hdr)
    print(f"{'':>11} " + " ".join(f"{'n':>7}{'actual':>9}{'model':>10}"
                                  for _ in range(3)))
    for _, _, nm in CAL_BANDS:
        cells = []
        for sp in ("dev", "val", "oos"):
            a = calibrate(contracts, sp).get(nm)
            if not a or not a["n"]:
                cells.append(f"{'-':>7}{'-':>9}{'-':>10}")
                continue
            mean_px = a["px"] / a["n"]
            cells.append(f"{a['n']:>7}{a['won'] / a['n']:>9.1%}"
                         f"{float(EV.p_win(mean_px)):>10.1%}")
        print(f"{nm:>11} " + " ".join(f"{c:>26}" for c in cells))
