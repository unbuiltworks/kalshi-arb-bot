"""Were there REAL, EXECUTABLE opportunities the old model rejected and the
corrected one would have accepted?  READ-ONLY.

Every input is real: the venue's own 1-second BRTI for 2026-10-25, the
venue's own `priceToBeat` and settlement for each window, and the full
recorded order-book ladders the live engine emitted as
L5_RESIDUAL_BOOK_SAMPLE while inventory was unmatched.

THE BINDING LIMIT, stated first. Of 290 recorded frames only EIGHT fall
in tau < 45, which is the only regime the correction changes. 280 sit at
tau >= 60 where the two estimators are identical by construction. So this
table is an EXISTENCE CHECK on real data, not a measurement: n=8 supports
no rate, no average and no edge claim.

Run: PYTHONPATH=. python3 analysis/l5/btc5m/opportunity_table.py
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.getcwd())
from src import l5_btc_reference as BR          # noqa: E402

FRAMES = os.path.join("data", "l5_strand_replay",
                      "residual_books_20261025.jsonl")
BRTI = os.environ.get("BRTI_1025", "")
THETA = 0.0695                      # published PMUS taker fee coefficient

#: venue records, fetched 2026-11-18 from /v1/market/slug/{slug}
WINDOWS = {
    "0030z": (80765.43, "DOWN"), "0100z": (80777.49, "DOWN"),
    "0115z": (80692.16, "UP"),   "0130z": (80698.33, "UP"),
    "0145z": (80755.41, "UP"),
}


def fee(price: float) -> float:
    return THETA * price * (1.0 - price)


def wend(slug: str) -> float:
    hhmm = slug.rsplit("-", 1)[-1].rstrip("z")
    day = slug.split("-15m-")[1][:10]
    t = dt.datetime.fromisoformat("%sT%s:%s:00+00:00"
                                  % (day, hhmm[:2], hhmm[2:]))
    return (t + dt.timedelta(minutes=15)).timestamp()


def p_up(series, strike, bucket, now, exact):
    if exact:
        os.environ[BR.EXACT_LATE_VAR_ENV] = BR.EXACT_LATE_VAR
    else:
        os.environ.pop(BR.EXACT_LATE_VAR_ENV, None)
    cut = [(t, v) for t, v in series if t <= bucket]
    e = BR.probability_up(series=cut, strike=strike,
                          window_end=wend_cache[0], now=now)
    return e


wend_cache = [0.0]


def main():
    if not BRTI or not os.path.exists(BRTI):
        print("set BRTI_1025 to the venue history response for 2026-10-25")
        return
    candles = json.load(open(BRTI))["series"][0]["candles"]
    series = [(int(c["timestamp"]), float(c["close"])) for c in candles
              if int(c.get("tickCount", 0)) == BR.AVERAGING_S]

    rows = [json.loads(l) for l in open(FRAMES)]
    print("290 recorded frames; showing every one with tau < 60.\n")
    hdr = ("  win     tau   BRTI_avg    strike   p_old   p_new  side  "
           "px_exec  depth    EV_old    EV_new  old→new   settled  right?")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    flips = []
    for r in sorted(rows, key=lambda x: x["t"]):
        w = r["slug"].rsplit("-", 1)[-1]
        if w not in WINDOWS:
            continue
        strike, outcome = WINDOWS[w]
        wend_cache[0] = wend(r["slug"])
        bucket = int(r["t"])
        tau = (wend_cache[0] - 1.0) - bucket
        if not (0 < tau < 60):
            continue
        now = float(r["t"]) + 0.5
        e0 = p_up(series, strike, bucket, now, False)
        e1 = p_up(series, strike, bucket, now, True)
        if not (e0.get("ok") and e1.get("ok")):
            print("  %-5s %7.1f   NO ESTIMATE (%s)"
                  % (w, tau, e0.get("reason") or e1.get("reason")))
            continue
        a_now = [v for t, v in series if t == bucket][0]
        #: A SIDE CAN BE EMPTY, and late in the window it is: buying UP
        #: needs a resting ask, buying DOWN needs a resting bid (its own
        #: ask is 1 - bid). An absent side is not a price of zero -- it is
        #: nothing to trade against, and it is reported rather than
        #: skipped silently, because "the corrected model would have
        #: accepted" means nothing if there was no order to accept.
        asks, bids = (r.get("asks") or []), (r.get("bids") or [])
        legs = []
        if asks:
            legs.append(("UP", float(asks[0][0]), float(asks[0][1])))
        else:
            print("  %-5s %7.1f   no ASK -- UP not executable" % (w, tau))
        if bids:
            legs.append(("DOWN", 1.0 - float(bids[0][0]),
                         float(bids[0][1])))
        else:
            print("  %-5s %7.1f   no BID -- DOWN not executable" % (w, tau))
        for side, px, depth in legs:
            pr0 = e0["p_up"] if side == "UP" else 1.0 - e0["p_up"]
            pr1 = e1["p_up"] if side == "UP" else 1.0 - e1["p_up"]
            ev0 = pr0 - px - fee(px)
            ev1 = pr1 - px - fee(px)
            flip = ev0 <= 0 < ev1
            mark = "FLIP" if flip else ("both+" if ev1 > 0 and ev0 > 0
                                        else "")
            won = (side == outcome)
            print("  %-5s %7.1f  %9.2f  %8.2f  %.4f  %.4f  %-4s  "
                  "%7.4f %7.1f  %+8.4f  %+8.4f  %-7s  %-6s  %s"
                  % (w, tau, a_now, strike, e0["p_up"], e1["p_up"], side,
                     px, depth, ev0, ev1, mark, outcome,
                     "yes" if won else "no"))
            if flip:
                flips.append((w, tau, side, px, ev1, won))
    print()
    print("FLIPS (old rejected, corrected accepts): %d" % len(flips))
    for w, tau, side, px, ev, won in flips:
        print("   %s tau=%.0fs buy %s at %.4f, EV_new %+.4f, settled %s"
              % (w, tau, side, px, ev, "WIN" if won else "LOSS"))
    if not flips:
        print("   none in the eight frames available.")
    print()
    print("n=8 frames in the affected regime. NO rate, average or edge is")
    print("claimed from this; it is an existence check on real data.")


if __name__ == "__main__":
    main()
