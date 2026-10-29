#!/usr/bin/env python3
"""Does the Nth window of a campaign earn what the 1st does?

THE DECISION THIS INFORMS. Per-window order throughput has no safe headroom
-- order-slot coverage is 12% of clock, and the idle time is the pair-cost
ceiling refusing guaranteed losses (C7 measured 17 refusals in one 297s gap,
every one of them a 1-19c certain loss). So the only lever left that adds
completed matched volume WITHOUT touching a gate, a price, exposure,
concurrency or capital is WINDOW COUNT.

That lever is only worth pulling if the economics are stationary in the
window index. If later windows earn less -- because the book is thinner at
some hours, or because inventory carried forward crowds the slot -- then
adding windows dilutes rather than multiplies, and the measured per-window
mean is not the rate a longer campaign would earn.

MATCHED SURPLUS ONLY. Residual settlement is excluded deliberately: it
carries t=0.225 over 17 windows and would swamp any index effect with
noise. Matched is +0.1933/window at t=3.320, and it is the series the
decision rests on.

Matched surplus per window = sum over matched contracts of (1 - pair_cost),
pairing UP against DOWN fills in time order at their own-side prices. This
is the same identity the settled ledger reconciles to: a matched pair
redeems exactly $1.00.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]


def legs_from_tapes(camp: str):
    """(window, side, qty, own_px) for every venue-confirmed fill."""
    base = ROOT / "analysis" / "l5" / camp
    subs = {}
    for line in (base / "tape_submits.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            subs[r["coid"]] = r
    out = []
    for line in (base / "tape_fills.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        f = json.loads(line)
        s = subs.get(f["coid"])
        if s is None:
            continue
        out.append((f["utc"], s["window"], s["side"],
                    D(str(f["qty"])), D(str(s["own_px"]))))
    out.sort()
    return out


def legs_from_c8():
    rows = json.loads((ROOT / "analysis/l5/campaign8/c8_joined.json").read_text())
    out = []
    for r in sorted(rows, key=lambda r: r["tsub"]):
        if r["state"] != "FILLED":
            continue
        out.append((r["tsub"], r["win"], r["side"],
                    D(str(r["qty"])), D(str(r["own"]))))
    return out


def matched_by_window(legs):
    """FIFO-pair opposing legs inside each window; return matched surplus."""
    books = {}
    out = {}
    for _, win, side, qty, px in legs:
        b = books.setdefault(win, {"UP": [], "DOWN": []})
        out.setdefault(win, D("0"))
        other = "DOWN" if side.upper() == "UP" else "UP"
        q = qty
        while q > 0 and b[other]:
            oq, opx = b[other][0]
            take = min(q, oq)
            # a matched pair redeems $1.00; surplus is what it did not cost
            out[win] += take * (D("1") - (px + opx))
            q -= take
            if oq - take > 0:
                b[other][0] = (oq - take, opx)
            else:
                b[other].pop(0)
        if q > 0:
            b[side.upper()].append((q, px))
    return out


def main() -> int:
    series = []
    for camp, legs in (("campaign6", legs_from_tapes("campaign6")),
                       ("campaign7", legs_from_tapes("campaign7")),
                       ("campaign8", legs_from_c8())):
        m = matched_by_window(legs)
        for i, win in enumerate(sorted(m), start=1):
            series.append({"campaign": camp, "window": win,
                           "index_in_campaign": i,
                           "matched_surplus": str(round(m[win], 4))})

    # the index effect, by halves rather than a regression: n is small and a
    # slope with 16 points invites more precision than the data carries.
    early = [D(r["matched_surplus"]) for r in series
             if r["index_in_campaign"] <= 3]
    late = [D(r["matched_surplus"]) for r in series
            if r["index_in_campaign"] > 3]

    def mean(xs):
        return (sum(xs) / len(xs)) if xs else None

    print(json.dumps({
        "windows": series,
        "index_effect": {
            "first_three_of_each_campaign": {
                "n": len(early), "mean": str(round(mean(early), 4)),
                "total": str(round(sum(early), 4))},
            "fourth_onward": {
                "n": len(late),
                "mean": (str(round(mean(late), 4)) if late else None),
                "total": str(round(sum(late), 4))},
            "reading": ("if the later mean is not materially below the "
                        "earlier one, window count multiplies rather than "
                        "dilutes, and the per-window mean is the rate a "
                        "longer campaign would earn"),
        },
        "caveat": ("16 windows across 3 campaigns on 2 calendar days. This "
                   "can rule out a LARGE dilution effect; it cannot "
                   "establish stationarity, and it says nothing about hours "
                   "of day these campaigns never traded."),
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
