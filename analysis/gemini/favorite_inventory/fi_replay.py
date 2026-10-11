"""Favorite-inventory replay. Smallest correct version.

COHORT: data/ref_lag/ -- the only historical dataset with ALL FOUR of
executable quotes + depth, the strike, BTC underlying, and SETTLEMENT GROUND
TRUTH (resolution_side). 179 BTC05M windows over 24.6 h.

LEAKAGE DISCIPLINE
  * the book used for a decision at t is the last pbook with t_ns <= t
  * BTC state at t uses only spot trades with t_ns <= t
  * settlement is used ONLY to score, never to decide
  * no midpoint fills, no touch=fill, no invented queue

EXECUTION PATHS, never pooled (instruction 2):
  A directional taker     B directional maker
  C maker->maker inventory  D maker->taker   E taker->maker
  F taker->taker  = NEGATIVE CONTROL (must be <= 0 by identity)

KNOWN LIMIT, stated: ref_lag `pbook` is bookTicker, i.e. TOP OF BOOK ONLY.
queue_ahead is therefore the displayed BBO size; deeper ladder is unobservable
here. Maker fills use the corrected methodology: a print strictly THROUGH our
price fills regardless of queue; a print AT our price fills only once observed
traded volume exceeds the displayed queue we joined behind. Cancels do not
advance us.
"""
from __future__ import annotations

import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, "analysis/gemini/favorite_inventory")
from fi_core import Book, Inventory, make_fill, ONE

PHASES = [(0, 15), (15, 30), (30, 45), (45, 60), (60, 90), (90, 120),
          (120, 180), (180, 240), (240, 270), (270, 285), (285, 300)]
BANDS = [(51, 55), (55, 60), (60, 65), (65, 70), (70, 75), (75, 80),
         (80, 85), (85, 90)]
SPREAD_BUCKETS = [("<=2c", D("0"), D("0.02")), ("3-5c", D("0.02"), D("0.05")),
                  ("6-10c", D("0.05"), D("0.10")), ("11-20c", D("0.10"), D("0.20")),
                  (">20c", D("0.20"), D("10"))]
TARGET = D("5")
MOM_HORIZONS = (1.0, 5.0, 15.0, 30.0, 60.0)


def pct(v, q):
    if not v:
        return None
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def spread_bucket(s):
    for name, lo, hi in SPREAD_BUCKETS:
        if lo < s <= hi or (lo == 0 and s <= hi):
            return name
    return ">20c"


def load():
    """Per-window: books, prediction trades, BTC spot, strike, settlement."""
    W = defaultdict(lambda: {"book": [], "ptrade": [], "strike": None,
                             "expiry_t": None, "resolution": None,
                             "settle_value": None})
    spot = []
    for f in sorted(Path("data/ref_lag").glob("reflag_*.jsonl")):
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = r.get("kind")
            if k == "spot_trade" and r.get("sym") == "BTCUSD":
                spot.append((r["t_ns"] / 1e9, float(r["price"])))
                continue
            m = r.get("market") or ""
            if "BTC05M" not in m:
                continue
            w = W[m]
            t = r["t_ns"] / 1e9
            if k == "window":
                try:
                    w["strike"] = float(r["strike"])
                except Exception:
                    pass
                if r.get("expiry"):
                    from datetime import datetime, timezone
                    w["expiry_t"] = datetime.fromisoformat(
                        r["expiry"].replace("Z", "+00:00")).replace(
                        tzinfo=timezone.utc).timestamp()
            elif k == "pbook":
                b = r.get("b") or None
                a = r.get("a") or None
                if b in ("", None) or a in ("", None):
                    return_book = None
                try:
                    bb = D(str(b)) if b not in ("", None) else None
                    aa = D(str(a)) if a not in ("", None) else None
                    bs = D(str(r.get("B"))) if r.get("B") not in ("", None) else None
                    az = D(str(r.get("A"))) if r.get("A") not in ("", None) else None
                except Exception:
                    continue
                w["book"].append((t, Book(bb, bs, aa, az)))
            elif k == "ptrade":
                try:
                    w["ptrade"].append((t, D(str(r["price"])), D(str(r["qty"])),
                                        bool(r.get("buyer_is_maker"))))
                except Exception:
                    pass
            elif k == "settlement":
                w["resolution"] = r.get("resolution_side")
                try:
                    w["settle_value"] = float(r.get("settlement_value"))
                except Exception:
                    pass
    spot.sort()
    for m in W:
        W[m]["book"].sort(key=lambda x: x[0])
        W[m]["ptrade"].sort(key=lambda x: x[0])
    return W, spot


def book_at(books, t):
    i = bisect_right([b[0] for b in books], t) - 1
    return books[i][1] if i >= 0 else None


def btc_state(spot_t, spot_p, t, strike, vol_trail=300.0):
    """BTC state using ONLY spot with time <= t. No leakage."""
    i = bisect_right(spot_t, t) - 1
    if i < 0:
        return None
    px = spot_p[i]
    st = {"btc": px, "dist": None, "dist_pct": None, "dist_vol": None,
          "crossings": 0, "frac_above": None, "max_exc": None}
    if strike:
        st["dist"] = px - strike
        st["dist_pct"] = (px - strike) / strike
    # momentum over fixed horizons
    for h in MOM_HORIZONS:
        j = bisect_right(spot_t, t - h) - 1
        st[f"ret_{int(h)}s"] = (px - spot_p[j]) / spot_p[j] if j >= 0 else None
    # trailing realized vol (dollars per sqrt-second) and path stats
    j0 = bisect_right(spot_t, t - vol_trail)
    seg = spot_p[j0:i + 1]
    segt = spot_t[j0:i + 1]
    if len(seg) > 2:
        difs = [(seg[k + 1] - seg[k]) for k in range(len(seg) - 1)]
        dts = [max(1e-6, segt[k + 1] - segt[k]) for k in range(len(seg) - 1)]
        var = sum(d * d / dt for d, dt in zip(difs, dts)) / len(difs)
        sigma = math.sqrt(max(var, 1e-12))
        st["sigma_per_sqrt_s"] = sigma
        if strike and sigma > 0:
            st["dist_vol"] = (px - strike) / sigma
        if strike:
            above = [1 if p > strike else 0 for p in seg]
            st["crossings"] = sum(1 for k in range(len(above) - 1)
                                  if above[k] != above[k + 1])
            st["frac_above"] = sum(above) / len(above)
            st["max_exc"] = max(abs(p - strike) for p in seg)
        st["hi"] = max(seg)
        st["lo"] = min(seg)
    return st


# ---------------- SIMPLE $5 DIRECTIONAL BASELINE (instruction 5) -----------
def simple_baseline(W, spot, mode):
    """At each preregistered phase, take the favorite in each band, sized to
    target +$5 on a winning settlement, and HOLD TO SETTLEMENT.

    mode 'taker' : pay the executable ask (UP) / 1-bid (DOWN)   -> path A
    mode 'maker' : rest at bid (UP) / 1-ask (DOWN); fill must be DEFENSIBLE
                   under the corrected rule                     -> path B
    """
    spot_t = [s[0] for s in spot]
    spot_p = [s[1] for s in spot]
    rows = []
    for m, w in W.items():
        if not (w["expiry_t"] and w["resolution"] and w["book"]):
            continue
        exp = w["expiry_t"]
        start = exp - 300.0
        outcome = "UP" if w["resolution"] == "yes" else "DOWN"
        for lo, hi in PHASES:
            t = start + lo
            bkk = book_at(w["book"], t)
            if bkk is None or not bkk.two_sided:
                continue
            mid = bkk.mid
            fav = "UP" if mid > D("0.5") else "DOWN"
            fav_px_mid = mid if fav == "UP" else (ONE - mid)
            band = None
            for blo, bhi in BANDS:
                if D(blo) / 100 <= fav_px_mid < D(bhi) / 100:
                    band = (blo, bhi)
                    break
            if band is None:
                continue
            entry = bkk.price_for(fav, mode)
            if entry is None or entry <= 0 or entry >= 1:
                continue
            win_per_ct = ONE - entry
            if win_per_ct <= 0:
                continue
            qty = (TARGET / win_per_ct).quantize(D("0.01"))
            if qty <= 0:
                continue
            depth = bkk.depth_for(fav, mode)
            # TAKER cannot exceed displayed depth; MAKER needs a defensible fill
            filled = None
            if mode == "taker":
                if depth is None or depth <= 0:
                    continue
                filled = min(qty, depth)
                basis = "taker_depth"
            else:
                q_ahead = depth if depth is not None else D("0")
                traded = D("0")
                got = D("0")
                basis = None
                for (tt, ppx, pq, bim) in w["ptrade"]:
                    if tt <= t or tt > exp:
                        continue
                    passive_up = bim is True
                    if fav == "UP" and not passive_up:
                        continue
                    if fav == "DOWN" and passive_up:
                        continue
                    ref = entry if fav == "UP" else (ONE - entry)
                    through = (ppx < ref) if fav == "UP" else (ppx > ref)
                    at = ppx == ref
                    if through:
                        got = min(qty, pq)
                        basis = "through_price"
                        break
                    if at:
                        traded += pq
                        if traded > q_ahead:
                            got = min(qty, traded - q_ahead)
                            basis = "same_price_fifo"
                            break
                if got <= 0:
                    rows.append({"market": m, "t": t, "phase": (lo, hi),
                                 "band": band, "mode": mode, "side": fav,
                                 "entry": float(entry), "qty_target": float(qty),
                                 "filled": 0.0, "no_fill": True,
                                 "spread_bucket": spread_bucket(bkk.spread),
                                 "spread": float(bkk.spread)})
                    continue
                filled = got
            inv = Inventory()
            inv.add(make_fill(t, fav, mode, entry, filled, basis))
            inv.assert_identities()
            gross = inv.pnl_if(outcome)
            net = inv.net_if(outcome)
            bs = btc_state(spot_t, spot_p, t, w["strike"])
            rows.append({
                "market": m, "t": t, "phase": (lo, hi), "band": band,
                "mode": mode, "side": fav, "outcome": outcome,
                "won": fav == outcome,
                "entry": float(entry), "mid": float(mid),
                "spread": float(bkk.spread),
                "spread_bucket": spread_bucket(bkk.spread),
                "depth": float(depth) if depth is not None else None,
                "qty_target": float(qty), "filled": float(filled),
                "cash_risk": float(inv.cash),
                "gross": float(gross), "fees": float(inv.fees),
                "rebate": float(inv.rebates), "net": float(net),
                "full_loss": float(-inv.cash - inv.fees + inv.rebates),
                "breakeven_p": float(entry),
                "btc": bs, "no_fill": False})
    return rows


def report_simple(rows, label):
    f = [r for r in rows if not r["no_fill"]]
    nf = [r for r in rows if r["no_fill"]]
    print(f"\n=== {label} ===")
    print(f"opportunities: {len(rows)}   filled: {len(f)}   no_fill: {len(nf)}")
    if not f:
        print("  no defensible fills -> nothing to score")
        return
    tot = sum(r["net"] for r in f)
    wins = [r for r in f if r["won"]]
    loss = [r for r in f if not r["won"]]
    print(f"  win rate            : {len(wins)}/{len(f)} = {len(wins)/len(f)*100:.1f}%")
    print(f"  mean breakeven_p    : {sum(r['breakeven_p'] for r in f)/len(f):.4f}")
    print(f"  TOTAL NET           : ${tot:+.2f}")
    print(f"  mean NET / fill     : ${tot/len(f):+.4f}")
    print(f"  avg winning net     : ${(sum(r['net'] for r in wins)/len(wins)) if wins else 0:+.2f}")
    print(f"  avg losing net      : ${(sum(r['net'] for r in loss)/len(loss)) if loss else 0:+.2f}")
    print(f"  worst single        : ${min(r['net'] for r in f):+.2f}")
    print(f"  total fees          : ${sum(r['fees'] for r in f):.2f}")
    print(f"  total rebates       : ${sum(r['rebate'] for r in f):.2f}")
    print(f"  mean cash at risk   : ${sum(r['cash_risk'] for r in f)/len(f):.2f}")
    print()
    print("  BY BAND (favorite mid price):")
    print(f"    {'band':>8s} {'n':>5s} {'win%':>6s} {'be_p':>6s} {'net_tot':>10s} {'net/fill':>9s}")
    for b in BANDS:
        g = [r for r in f if tuple(r["band"]) == b]
        if not g:
            continue
        w = sum(1 for r in g if r["won"])
        print(f"    {b[0]}-{b[1]}c {len(g):5d} {w/len(g)*100:5.1f}% "
              f"{sum(r['breakeven_p'] for r in g)/len(g):6.3f} "
              f"{sum(r['net'] for r in g):+10.2f} {sum(r['net'] for r in g)/len(g):+9.3f}")
    print()
    print("  BY SPREAD BUCKET:")
    print(f"    {'bucket':>8s} {'n':>5s} {'win%':>6s} {'be_p':>6s} {'net_tot':>10s} {'net/fill':>9s}")
    for name, _, _ in SPREAD_BUCKETS:
        g = [r for r in f if r["spread_bucket"] == name]
        if not g:
            continue
        w = sum(1 for r in g if r["won"])
        print(f"    {name:>8s} {len(g):5d} {w/len(g)*100:5.1f}% "
              f"{sum(r['breakeven_p'] for r in g)/len(g):6.3f} "
              f"{sum(r['net'] for r in g):+10.2f} {sum(r['net'] for r in g)/len(g):+9.3f}")
    print()
    print("  BY PHASE:")
    print(f"    {'phase':>10s} {'n':>5s} {'win%':>6s} {'net_tot':>10s} {'net/fill':>9s}")
    for p in PHASES:
        g = [r for r in f if tuple(r["phase"]) == p]
        if not g:
            continue
        w = sum(1 for r in g if r["won"])
        print(f"    {p[0]}-{p[1]}s {len(g):5d} {w/len(g)*100:5.1f}% "
              f"{sum(r['net'] for r in g):+10.2f} {sum(r['net'] for r in g)/len(g):+9.3f}")


def main():
    W, spot = load()
    usable = {m: w for m, w in W.items()
              if w["expiry_t"] and w["resolution"] and w["book"]}
    print("=" * 78)
    print("FI REPLAY -- cohort: data/ref_lag (settlement truth + strike + BTC)")
    print("=" * 78)
    print(f"BTC05M windows seen        : {len(W)}")
    print(f"  with settlement + book   : {len(usable)}")
    print(f"  BTCUSD spot trades       : {len(spot)}")
    res = defaultdict(int)
    for w in usable.values():
        res[w["resolution"]] += 1
    print(f"  resolution split         : {dict(res)}")

    # chronological split; OOS is reserved for the PROSPECTIVE infoedge cohort
    order = sorted(usable.items(), key=lambda kv: kv[1]["expiry_t"])
    n = len(order)
    dev = dict(order[:int(n * 0.6)])
    val = dict(order[int(n * 0.6):])
    print(f"  DEV windows              : {len(dev)}")
    print(f"  VALIDATION windows       : {len(val)}")
    print("  UNTOUCHED OOS            : data/gemini_infoedge (prospective, not read here)")

    for mode, path in (("taker", "PATH A -- directional TAKER"),
                       ("maker", "PATH B -- directional MAKER")):
        for name, cohort in (("DEV", dev), ("VALIDATION", val)):
            rows = simple_baseline(cohort, spot, mode)
            report_simple(rows, f"{path}  [{name}]  simple $5-target, hold to settlement")
            Path(f"analysis/gemini/favorite_inventory/fi_simple_{mode}_{name}.json"
                 ).write_text(json.dumps(rows, indent=1, default=str))


if __name__ == "__main__":
    main()
