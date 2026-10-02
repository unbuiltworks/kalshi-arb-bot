#!/usr/bin/env python3
"""Observed per-window traded volume on Gemini BTC15m/BTC05m -- the capacity
denominator for the two-sided maker question.

Public /v1/trades/{symbol}. Symbols are constructed from the venue's naming
convention and probed backwards from now, so EXPIRED windows are measured.
Live windows read zero because flow concentrates near expiry, which is itself
the finding that made a short live shadow look dead.
"""
import json, sys, urllib.request, datetime as dt, statistics, os
from decimal import Decimal as D

OUT = "analysis/gemini/BTC_TURNOVER.json"


def g(u):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(
            u, headers={"Accept": "application/json"}), timeout=15))
    except Exception as e:
        return {"__err": str(e)[:80]}


def main(pref="BTC15M", step=15, n=96):
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    now -= dt.timedelta(minutes=now.minute % step)
    rows = []
    for k in range(1, n + 1):
        t = now - dt.timedelta(minutes=step * k)
        s = f"GEMI-{pref}{t.strftime('%y%m%d%H%M')}-UP"
        tr = g(f"https://api.gemini.com/v1/trades/{s}?limit_trades=500")
        if isinstance(tr, dict):
            continue
        vol = sum(D(str(x.get("amount") or x.get("quantity") or 0)) for x in tr)
        notl = sum(D(str(x.get("amount") or x.get("quantity") or 0)) *
                   D(str(x.get("price", 0))) for x in tr)
        rows.append({"symbol": s, "window_utc": t.isoformat(),
                     "trades": len(tr), "volume": str(vol), "notional": str(notl)})
    vols = [float(r["volume"]) for r in rows]
    nots = [float(r["notional"]) for r in rows]
    vols_s = sorted(vols)
    pct = lambda v, p: v[min(len(v) - 1, int(len(v) * p))] if v else 0
    summ = {
        "prefix": pref, "windows": len(rows),
        "windows_with_any_trade": sum(1 for v in vols if v > 0),
        "volume_mean": statistics.mean(vols) if vols else 0,
        "volume_median": statistics.median(vols) if vols else 0,
        "volume_p10": pct(vols_s, .10), "volume_p90": pct(vols_s, .90),
        "volume_max": max(vols) if vols else 0,
        "notional_mean": statistics.mean(nots) if nots else 0,
        "notional_median": statistics.median(nots) if nots else 0,
        "trades_median": statistics.median([r["trades"] for r in rows]) if rows else 0,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    prev = json.load(open(OUT)) if os.path.exists(OUT) else {}
    prev[pref] = {"summary": summ, "rows": rows}
    json.dump(prev, open(OUT, "w"), indent=2)
    print(f"{pref}: windows={summ['windows']} traded={summ['windows_with_any_trade']}")
    print(f"  volume/window  mean {summ['volume_mean']:.1f}  median "
          f"{summ['volume_median']:.1f}  p10 {summ['volume_p10']:.1f}  "
          f"p90 {summ['volume_p90']:.1f}  max {summ['volume_max']:.1f}")
    print(f"  notional/window mean ${summ['notional_mean']:.2f}  median "
          f"${summ['notional_median']:.2f}   median trades {summ['trades_median']}")
    return summ


if __name__ == "__main__":
    s1 = main("BTC15M", 15, 96)
    s2 = main("BTC05M", 5, 96)
    print("\n--- capacity: matched shares needed for $3 and $5 per window ---")
    for cents in (0.01, 0.02):
        print(f"  at {cents*100:.0f} cent net per matched share: "
              f"$3 needs {3/cents:.0f} matched, $5 needs {5/cents:.0f} matched")
    print(f"\n  BTC15M median window turnover: {s1['volume_median']:.0f} contracts")
    print(f"  a maker's matched qty <= min(buy flow, sell flow) <= turnover/2 "
          f"= {s1['volume_median']/2:.0f} even at 100% queue capture")
