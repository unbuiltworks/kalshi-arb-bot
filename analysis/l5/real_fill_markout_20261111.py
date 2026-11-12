"""What the market did after OUR real maker fills, beside L5's.

Our fills: every venue-confirmed fill in the settled windows
(`lifecycle.json`), with how long the order had rested. The price afterwards
is read from the recorded full-window book of the same market
(`books_380.json`), so only windows that book covers are used; nothing is
conditioned on whether the leg later completed.

Markout: mid price of the side we bought, h seconds after the fill, minus the
price we paid. Negative means the market moved against the fill. Live fills,
venue-confirmed; the book is the service's own recorded feed (a sample about
every 6.6 s), so a fill between samples is read at the next one.
"""
from __future__ import annotations

import bisect
import collections
import json
import pathlib
import statistics as st
import sys
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "l5" / "recent" / "wide_20261111"
HORIZONS = (0, 30, 60, 120, 300)


def fills(rows):
    by = collections.OrderedDict()
    out = []
    for r in rows:
        o = by.setdefault(r["coid"], {"t0": r["t"], "side": None, "cum": D(0)})
        side = r.get("side_tracked") or r.get("side")
        if side:
            o["side"] = side
        try:
            cum = D(str(r.get("cum_qty") or r.get("cum") or 0))
        except Exception:                                    # noqa: BLE001
            cum = D(0)
        if cum > o["cum"] and o["side"] in ("UP", "DOWN"):
            wire = float(r.get("avg_px") or 0)
            own = wire if o["side"] == "UP" else 1 - wire
            out.append({"t": r["t"], "side": o["side"], "px": own,
                        "qty": float(cum - o["cum"]),
                        "rested": r["t"] - o["t0"]})
            o["cum"] = cum
    return out


def main():
    life = json.loads((DATA / "lifecycle.json").read_text())
    books = json.loads((DATA / "books_380.json").read_text())
    recs = []
    for slug, rows in life.items():
        if slug not in books:
            continue
        b = books[slug]["rows"]
        ts = [x[0] for x in b]
        net = 0.0
        for f in sorted(fills(rows), key=lambda x: x["t"]):
            sgn = f["qty"] if f["side"] == "UP" else -f["qty"]
            first = net == 0 or (net > 0) == (sgn > 0)
            net += sgn
            mo = {}
            for h in HORIZONS:
                i = bisect.bisect_left(ts, f["t"] + h)
                if i >= len(b) or b[i][0] - (f["t"] + h) > 20:
                    continue
                mid = (float(b[i][1]) + float(b[i][2])) / 2
                held = mid if f["side"] == "UP" else 1 - mid
                mo[h] = held - f["px"]
            recs.append(dict(f, slug=slug, first=first, mo=mo))
    print(f"settled windows with a recorded full-window book: "
          f"{len(set(r['slug'] for r in recs))}; venue-confirmed fills "
          f"{len(recs)}; contracts {sum(r['qty'] for r in recs):.2f}")

    def line(tag, g):
        if not g:
            return
        s = f"  {tag:36s} fills {len(g):3d}"
        for h in HORIZONS:
            xs = [(r["mo"][h], r["qty"]) for r in g if h in r["mo"]]
            if not xs:
                continue
            w = sum(q for _, q in xs)
            mean = sum(m * q for m, q in xs) / w
            s += f"  +{h:>3d}s {100 * mean:+5.1f}c"
        s += (f"  | rested median "
              f"{st.median(r['rested'] for r in g):5.1f}s")
        print(s)

    print("price of what we bought, after the fill, minus what we paid "
          "(size-weighted):")
    line("all fills", recs)
    line("first legs", [r for r in recs if r["first"]])
    line("completing legs", [r for r in recs if not r["first"]])
    print("first legs, by how long the order had rested when it filled:")
    for a, b_, tag in ((0, 10, "under 10 s"), (10, 30, "10-30 s"),
                       (30, 60, "30-60 s"), (60, 1e9, "over 60 s")):
        line("  " + tag, [r for r in recs if r["first"]
                          and a <= r["rested"] < b_])
    print("first legs, by seconds into the window:")
    for a, b_, tag in ((0, 60, "first minute"), (60, 300, "1-5 min"),
                       (300, 1e9, "after 5 min")):
        line("  " + tag, [r for r in recs if r["first"] and a <= r["t"] < b_])
    slugs = sorted(set(r["slug"] for r in recs))
    half = set(slugs[: len(slugs) // 2])
    print("first legs, by half (time order):")
    line("  earlier windows", [r for r in recs if r["first"]
                               and r["slug"] in half])
    line("  later windows", [r for r in recs if r["first"]
                             and r["slug"] not in half])
    print("L5, same measure on its own fills (public activity, 6,357 "
          "fills): +30s -0.03c, +120s +0.84c, +300s -0.06c")
    return 0


def first_legs_all_windows():
    """Every settled window, from the ladder the service recorded while the
    first leg was unmatched. Horizons stop at 30 s: a leg that completed
    sooner leaves the sample, which leans these figures slightly negative."""
    life = json.loads((DATA / "lifecycle.json").read_text())
    lad = json.loads((DATA / "residual.json").read_text())
    recs = []
    for slug, rows in life.items():
        ladder = [x for x in lad.get(slug, []) if x.get("bids") and x.get("asks")]
        if not ladder:
            continue
        ts = [x["t"] for x in ladder]
        net = 0.0
        for f in sorted(fills(rows), key=lambda x: x["t"]):
            sgn = f["qty"] if f["side"] == "UP" else -f["qty"]
            first = net == 0 or (net > 0) == (sgn > 0)
            net += sgn
            if not first:
                continue
            mo = {}
            for h in (0, 15, 30):
                i = bisect.bisect_left(ts, f["t"] + h)
                if i >= len(ladder) or ts[i] - (f["t"] + h) > 12:
                    continue
                mid = (float(ladder[i]["bids"][0][0])
                       + float(ladder[i]["asks"][0][0])) / 2
                mo[h] = (mid if f["side"] == "UP" else 1 - mid) - f["px"]
            recs.append(dict(f, slug=slug, mo=mo))
    slugs = sorted(set(r["slug"] for r in recs))
    half = set(slugs[: len(slugs) // 2])
    print(f"\nALL SETTLED WINDOWS: first-leg fills {len(recs)} in "
          f"{len(slugs)} windows (ladder recorded while unmatched)")

    def line(tag, g):
        s = f"  {tag:30s} fills {len(g):3d}"
        for h in (0, 15, 30):
            xs = [r["mo"][h] for r in g if h in r["mo"]]
            if len(xs) < 3:
                continue
            se = st.stdev(xs) / len(xs) ** 0.5
            s += (f"  +{h:>2d}s {100 * st.mean(xs):+5.1f}c "
                  f"+/-{196 * se:4.1f} (n={len(xs)})")
        print(s)

    line("all first legs", recs)
    for a, b, tag in ((0, 5, "rested under 5 s"), (5, 10, "5-10 s"),
                      (10, 30, "10-30 s"), (30, 1e9, "over 30 s")):
        line(tag, [r for r in recs if a <= r["rested"] < b])
    for tag, keep in (("earlier windows", lambda r: r["slug"] in half),
                      ("later windows", lambda r: r["slug"] not in half)):
        g = [r for r in recs if keep(r)]
        line(tag, g)
        line("  rested 30 s or less", [r for r in g if r["rested"] <= 30])
        line("  rested over 30 s", [r for r in g if r["rested"] > 30])


if __name__ == "__main__":
    main()
    first_legs_all_windows()
    sys.exit(0)
