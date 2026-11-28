"""L5's own BTC 15m windows, 2026-11-10 18:30Z to 2026-11-11 14:45Z.

Input: L5's public Polymarket activity (Data API, read-only), the trade,
redeem and rebate rows as published. Per window it rebuilds the inventory
fill by fill and takes the settled cashflow as REDEEM dollars minus dollars
paid. That is L5's result on POLYMARKET, at L5's size, under Polymarket's
fee and rebate programme. None of it is our P&L, and none of it shows that
the same orders would fill or pay the same on PMUS.

Maker or taker is not published per fill, and fees are not itemised, so the
cashflow below is before fees and before the rebate rows, which are listed
separately and are NOT assumed for our account.
"""
from __future__ import annotations

import collections
import json
import pathlib
import statistics as st
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SRC = (HERE.parent.parent / "data" / "l5" / "recent"
       / "l5_activity_20261110_1822z_20261111_1516z.json")


def windows(rows):
    tmin = min(r["timestamp"] for r in rows)
    tmax = max(r["timestamp"] for r in rows)
    by = collections.defaultdict(lambda: {"tr": [], "red": 0.0, "nred": 0})
    for r in rows:
        slug = str(r.get("slug"))
        if not slug.startswith("btc-updown-15m-"):
            continue
        w0 = int(slug.rsplit("-", 1)[1])
        if w0 < tmin + 60 or w0 + 1500 > tmax:      # whole windows only
            continue
        if r["type"] == "TRADE":
            by[w0]["tr"].append(r)
        elif r["type"] == "REDEEM":
            by[w0]["red"] += float(r.get("usdc_size") or 0)
            by[w0]["nred"] += 1
    out = []
    for w0, v in sorted(by.items()):
        pos = {"Up": 0.0, "Down": 0.0}
        cost = {"Up": 0.0, "Down": 0.0}
        late = sells = 0
        ts = []
        for r in v["tr"]:
            sg = 1 if r["side"] == "BUY" else -1
            q, p = float(r["size"]), float(r["price"])
            pos[r["outcome"]] += sg * q
            cost[r["outcome"]] += sg * q * p
            t = r["timestamp"] - w0
            ts.append(t)
            late += t >= 600
            sells += sg < 0
        if not v["tr"] or not v["nred"] or max(pos.values()) <= 0:
            continue
        both = pos["Up"] > 0 and pos["Down"] > 0
        out.append({
            "w0": w0, "fills": len(v["tr"]), "up": pos["Up"],
            "down": pos["Down"], "matched": min(pos.values()),
            "matched_share": min(pos.values()) / max(pos.values()),
            "pair": (cost["Up"] / pos["Up"] + cost["Down"] / pos["Down"])
            if both else None,
            "paid": cost["Up"] + cost["Down"], "redeemed": v["red"],
            "pnl": v["red"] - cost["Up"] - cost["Down"],
            "first": min(ts), "last": max(ts), "late": late, "sells": sells})
    return out


def main():
    rows = json.loads(SRC.read_text())["rows"]
    ws = windows(rows)
    n = len(ws)
    pnl = [w["pnl"] for w in ws]
    mean, sd = st.mean(pnl), st.stdev(pnl)
    se = sd / n ** 0.5
    stamp = lambda t: time.strftime("%m-%d %H:%MZ", time.gmtime(t))  # noqa
    print(f"whole BTC 15m windows: {n}  ({stamp(ws[0]['w0'])} .. "
          f"{stamp(ws[-1]['w0'])})")
    print(f"  fills per window, median {st.median(w['fills'] for w in ws)}  "
          f"dollars paid per window, median "
          f"{st.median(w['paid'] for w in ws):.0f}  sells {sum(w['sells'] for w in ws)}")
    print(f"  fills at or after T+600: "
          f"{sum(w['late'] for w in ws) / sum(w['fills'] for w in ws):.0%}  "
          f"last fill, median T+{st.median(w['last'] for w in ws):.0f} s")
    print(f"  matched share of the larger side, median "
          f"{st.median(w['matched_share'] for w in ws):.2f}  average pair "
          f"cost, median {st.median(w['pair'] for w in ws if w['pair']):.3f}"
          f"  windows at 0.96 or less "
          f"{sum(1 for w in ws if w['pair'] and w['pair'] <= 0.96) / n:.0%}"
          f"  above 1.00 "
          f"{sum(1 for w in ws if w['pair'] and w['pair'] > 1) / n:.0%}")
    print(f"  settled cashflow (redeemed - paid, before fees and rebates): "
          f"total {sum(pnl):+.2f}  per window {mean:+.2f}  "
          f"95% interval {mean - 1.96 * se:+.2f} .. {mean + 1.96 * se:+.2f}  "
          f"windows ahead {sum(1 for p in pnl if p > 0) / n:.0%}  "
          f"worst {min(pnl):+.2f}  best {max(pnl):+.2f}")
    ws.sort(key=lambda w: w["matched_share"])
    k = n // 3
    for name, g in (("least matched third", ws[:k]),
                    ("middle third", ws[k:2 * k]),
                    ("most matched third", ws[2 * k:])):
        print(f"    {name:19s} windows {len(g)}  matched share "
              f"{st.mean(w['matched_share'] for w in g):.2f}  average pair "
              f"cost {st.mean(w['pair'] for w in g if w['pair']):.3f}  "
              f"settled cashflow {sum(w['pnl'] for w in g):+.2f}")
    print("  rebate rows in the same pull (Polymarket's programme; not "
          "assumed for PMUS):")
    for r in rows:
        if "REBATE" in str(r["type"]):
            print(f"    {r['type']:13s} {stamp(r['timestamp'])}  "
                  f"{float(r.get('usdc_size') or 0):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
