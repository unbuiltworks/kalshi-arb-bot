"""P9-D companion: the quoted spread in every Gemini family, right now.

    python3 analysis/gemini/p9d_spreads.py [out.json]

WHY THIS IS THE OTHER HALF OF THE RANKING. P9 established that on a mirrored
one-ladder venue a matched pair's repeatable margin is the spread we quote --
everything above that is the price having moved between our two fills, which
is a coin flip we selected on rather than an edge. So a family's economics are
bounded by two numbers: how wide its book is (the margin per pair) and how
often both sides get hit (the frequency). This measures the first.

One pass over the events endpoint; no per-contract call. Public, read-only.
"""
import json
import sys
import time
import urllib.request
from collections import defaultdict
from fractions import Fraction as F

BASE = "https://api.gemini.com"
CATS = ("Crypto", "Sports", "Weather", "Commodities", "Politics", "Economics")


def get(path, tries=3):
    for i in range(tries):
        try:
            time.sleep(0.2)
            with urllib.request.urlopen(BASE + path, timeout=25) as r:
                return json.load(r)
        except Exception as e:                                  # noqa: BLE001
            if i == tries - 1:
                return {"__error__": str(e)}
    return {}


def main(out_path=None):
    agg = defaultdict(lambda: {"n": 0, "sp": [], "mid": []})
    for c in CATS:
        off = 0
        while off < 400:
            d = get(f"/v1/prediction-markets/events?limit=100&offset={off}"
                    f"&category={c}")
            rows = (d or {}).get("data") or []
            if not rows:
                break
            for e in rows:
                sub = e.get("subcategory") or {}
                key = (c, (sub.get("name") if isinstance(sub, dict) else "?"))
                for k in (e.get("contracts") or []):
                    pr = k.get("prices") or {}
                    bb, ba = pr.get("bestBid"), pr.get("bestAsk")
                    if bb in (None, "") or ba in (None, ""):
                        continue
                    bb, ba = F(str(bb)), F(str(ba))
                    if not (0 < bb <= ba < 1):
                        continue
                    a = agg[key]
                    a["n"] += 1
                    a["sp"].append(float(ba - bb))
                    a["mid"].append(float((ba + bb) / 2))
            tot = (d.get("pagination") or {}).get("total") or 0
            off += 100
            if off >= tot:
                break

    print(f"{'category':>13} {'subcategory':>22} {'quoted':>7} {'med spread':>11} "
          f"{'p25':>7} {'p75':>7} {'maker pair cost':>16} {'gross per pair':>15}")
    out = []
    for (c, s), a in agg.items():
        if a["n"] < 10:
            continue
        sp = sorted(a["sp"])
        out.append((sp[len(sp) // 2], c, s, a, sp))
    rows = []
    for m, c, s, a, sp in sorted(out, reverse=True):
        def q(p):
            return sp[int(p * (len(sp) - 1))]
        print(f"{c:>13} {str(s)[:22]:>22} {a['n']:>7} {m:>11.3f} {q(.25):>7.3f} "
              f"{q(.75):>7.3f} {1 - m:>16.3f} {m:>14.1%}")
        rows.append({"category": c, "subcategory": s, "quoted": a["n"],
                     "median_spread": m, "p25": q(.25), "p75": q(.75)})
    if out_path:
        json.dump(rows, open(out_path, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
