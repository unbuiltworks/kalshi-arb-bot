"""What a 15 s or 30 s quote lifetime would have REMOVED from our real fills.

Every venue-confirmed first-leg fill in the settled windows, with how long
its order had rested when it filled, and what that leg went on to earn:
the part later completed is paid (1 - pair cost), first in first out; the
part never completed is paid its settlement value minus cost.

A fill on an order older than the lifetime would not have happened at that
price. This counts what those fills earned or lost. It is an upper bound on
the flow given up: a re-priced order may well have filled later at another
price, and that fill is not modelled here. Live fills and venue settlement;
no replay.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys
from decimal import Decimal as D

DATA = (pathlib.Path(__file__).resolve().parents[2] / "data" / "l5"
        / "recent" / "wide_20261111")


def legs(rows, up_won):
    by = collections.OrderedDict()
    fills = []
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
            wire = D(str(r.get("avg_px") or 0))
            own = wire if o["side"] == "UP" else D(1) - wire
            fills.append({"t": r["t"], "side": o["side"], "px": own,
                          "qty": cum - o["cum"], "rested": r["t"] - o["t0"]})
            o["cum"] = cum
    fills.sort(key=lambda f: f["t"])
    net, out, open_ = D(0), [], []
    for f in fills:
        sgn = f["qty"] if f["side"] == "UP" else -f["qty"]
        if net == 0 or (net > 0) == (sgn > 0):
            leg = dict(f, left=f["qty"], pnl=D(0), done=D(0))
            out.append(leg)
            open_.append(leg)
        else:
            left = f["qty"]
            for leg in open_:
                if left <= 0:
                    break
                k = min(left, leg["left"])
                if k <= 0:
                    continue
                leg["left"] -= k
                leg["done"] += k
                leg["pnl"] += k * (D(1) - leg["px"] - f["px"])
                left -= k
            if left > 0:
                leg = dict(f, qty=left, left=left, pnl=D(0), done=D(0))
                out.append(leg)
                open_.append(leg)
        net += sgn
    for leg in out:
        won = up_won == (leg["side"] == "UP")
        leg["pnl"] += leg["left"] * ((D(1) if won else D(0)) - leg["px"])
    return out


def main():
    life = json.loads((DATA / "lifecycle.json").read_text())
    settled = json.loads((DATA / "settled.json").read_text())
    slugs = sorted(s for s in life if s in settled
                   and settled[s].get("yes_value") is not None)
    half = set(slugs[: len(slugs) // 2])
    recs = []
    for s in slugs:
        for leg in legs(life[s], D(str(settled[s]["yes_value"])) == 1):
            recs.append(dict(leg, slug=s))
    total = sum(r["pnl"] for r in recs)
    print(f"first-leg fills {len(recs)} in {len(slugs)} settled windows; "
          f"what they went on to earn, settled: {total:+.4f}")

    def line(tag, g):
        if not g:
            print(f"  {tag:34s} none")
            return
        q = sum(r["qty"] for r in g)
        done = sum(r["done"] for r in g)
        p = sum(r["pnl"] for r in g)
        print(f"  {tag:34s} fills {len(g):3d}  contracts {q:7.2f}  "
              f"completed {100 * float(done / q):3.0f}%  earned {p:+8.4f}  "
              f"per contract {100 * float(p / q):+6.1f}c")

    for scope, keep in (("ALL WINDOWS", lambda r: True),
                        ("EARLIER HALF", lambda r: r["slug"] in half),
                        ("LATER HALF", lambda r: r["slug"] not in half)):
        g = [r for r in recs if keep(r)]
        print(scope)
        for a, b, tag in ((0, 15, "order rested under 15 s"),
                          (15, 30, "15-30 s"), (30, 1e9, "over 30 s")):
            line(tag, [r for r in g if a <= r["rested"] < b])
        for cut in (15, 30):
            gone = [r for r in g if r["rested"] >= cut]
            kept = [r for r in g if r["rested"] < cut]
            pg, pk = sum(r["pnl"] for r in gone), sum(r["pnl"] for r in kept)
            wins = sum(r["pnl"] for r in gone if r["pnl"] > 0)
            print(f"    a {cut:2d} s lifetime removes {len(gone):3d} fills "
                  f"that earned {pg:+.4f} in all (their winners alone "
                  f"{wins:+.4f}); the fills kept earned {pk:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
