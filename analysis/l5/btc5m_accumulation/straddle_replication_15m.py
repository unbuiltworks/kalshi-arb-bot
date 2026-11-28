"""Does the straddle appear on a DIFFERENT venue and a DIFFERENT series?

READ-ONLY, committed data only.

WHY THIS IS NOT ANOTHER ARM ON THE SAME TAPE. Every straddle number so far
comes from one PMUS BTC5m tape of 189 windows, and 80+ arms have been
searched on it. The edge sits at t +1.58, which is exactly where
tape-specific noise lives. What that tape cannot tell us is whether the
MECHANISM is real.

The mechanism, if real, is venue-independent and series-independent:

  a binary resolves, so the losing side's price collapses through any deep
  resting bid and we end up holding it (cheap, and usually worthless);
  occasionally the market swings deep BOTH ways inside one window, both
  sides fill, and the matched pair redeems 1.00 for well under 1.00.

Nothing in that argument mentions PMUS or five minutes. So it should appear
on Gemini BTC15m as well. If it does not, the PMUS 5m result is most likely
noise. If it does, we have an independent replication that no amount of
re-searching the 5m tape could have bought.

TWO REASONS 15m MIGHT BE BETTER, both a priori. Breakeven is just the price
paid, so it does not change with window length -- but the time available to
swing deep both ways triples. If the pair rate rises at all with window
length, the margin improves.

DATA. data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz -- 23,318 BBO rows,
136 distinct BTC15m windows, 2026-08-30 to 2026-09-02, with sizes at the
touch. Symbols are UP only, so DOWN is priced 1 - UP ask, the same own-price
convention as the PMUS work.

WHAT THIS DOES NOT CLAIM. Gemini's fee and rebate structure is not PMUS's;
these are GROSS settlement figures. A different venue in a different month
is also a different regime. It is a replication of the MECHANISM, not a
forecast of our economics.

Usage: python3 -I analysis/l5/btc5m_accumulation/straddle_replication_15m.py
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import os
import statistics
import sys
from decimal import Decimal as D

TAPE = os.path.join("data", "transfer_backtest", "gemini_bbo_btc15m.jsonl.gz")
WINDOW_S = 900.0
CANCEL_LEAD_S = 30.0
RESOLVE_AFTER_S = WINDOW_S - CANCEL_LEAD_S
MIN_ROWS = 30
UP, DOWN = "UP", "DOWN"
RUNGS = tuple(D(c) / 100 for c in (5, 6, 7, 8, 9, 10))


def dec(v):
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def window_start(symbol):
    """GEMI-BTC15M2608310030-UP -> the window START, 00:15Z.

    THE SYMBOL NAMES THE WINDOW END, not its start. Measured, not assumed:
    read as a start, every row landed at a NEGATIVE time-in-window, topping
    out at -34s -- i.e. no quote existed at or after the nominal time, which
    cannot happen for a window that is still open. Read as an end, the rows
    top out at +866s, just inside a 900s window. Hence the -WINDOW_S.

    Rows also begin long BEFORE the window opens (down to about -3800s): the
    tape quotes the whole live slate, so a window is priced for over an hour
    before it starts. `walk` discards t < 0 for that reason.
    """
    core = symbol.split("-")[1]                 # BTC15M2608310030
    digits = core[len("BTC15M"):]
    if len(digits) != 10 or not digits.isdigit():
        return None
    yy, mm, dd, hh, mi = (int(digits[0:2]), int(digits[2:4]),
                          int(digits[4:6]), int(digits[6:8]), int(digits[8:10]))
    try:
        end = dt.datetime(2000 + yy, mm, dd, hh, mi,
                          tzinfo=dt.timezone.utc).timestamp()
        return end - WINDOW_S
    except ValueError:
        return None


def load():
    by = {}
    opener = gzip.open if TAPE.endswith(".gz") else open
    with opener(TAPE, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            sym = r.get("symbol")
            ws = window_start(sym) if sym else None
            if ws is None:
                continue
            t = float(r["t"]) - ws
            by.setdefault(sym, []).append(
                {"t": t, "bid": dec(r.get("bid")), "ask": dec(r.get("ask"))})
    for sym in by:
        by[sym].sort(key=lambda q: q["t"])
    return by


def own_bid(q, side):
    """UP is priced at the YES bid; DOWN at 1 - the YES ask."""
    if side == UP:
        return q["bid"]
    return None if q["ask"] is None else D(1) - q["ask"]


def outcome(rows):
    for q in reversed(rows):
        if q["t"] < RESOLVE_AFTER_S:
            break
        if q["bid"] is not None and q["bid"] >= D("0.90"):
            return UP
        if q["ask"] is not None and q["ask"] <= D("0.10"):
            return DOWN
    return None


def walk(rows, rungs, cancel_lead):
    won = outcome(rows)
    if won is None:
        return None
    stop = WINDOW_S - cancel_lead
    filled = {UP: set(), DOWN: set()}
    for q in rows:
        if q["t"] < 0 or q["t"] >= stop:
            continue
        for s in (UP, DOWN):
            b = own_bid(q, s)
            if b is None:
                continue
            for r in rungs:
                if r not in filled[s] and b <= r:
                    filled[s].add(r)
    #: settlement P&L is linear in legs; grouping is presentation only
    pnl = D(0)
    for s in (UP, DOWN):
        for r in filled[s]:
            pnl += (D(1) if s == won else D(0)) - r
    pairs = len(filled[UP] & filled[DOWN])
    return {"pnl": float(pnl), "pairs": pairs,
            "legs": len(filled[UP]) + len(filled[DOWN]), "won": won}


def stats(v):
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0, 0.0
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, (m / se if se else 0.0)


def main():
    if not os.path.exists(TAPE):
        print("tape missing: %s" % TAPE)
        return 1
    by = load()
    #: SANITY FIRST. If the symbol does not decode to the window start, every
    #: t is wrong and nothing below means anything.
    spans = [(min(q["t"] for q in rs), max(q["t"] for q in rs))
             for rs in by.values() if rs]
    lo = min(s[0] for s in spans)
    hi = max(s[1] for s in spans)
    print("symbols %d   t-in-window range %.0f .. %.0f s  (expect ~0..900)"
          % (len(by), lo, hi))
    #: a very negative floor is EXPECTED -- the slate is quoted ahead. What
    #: must hold is that rows reach the END of the window, or the mapping is
    #: wrong and every fill time is meaningless.
    if not (WINDOW_S * 0.8 <= hi <= WINDOW_S + 300.0):
        print("ABORT: rows do not reach the end of a 900s window, so the")
        print("       symbol-to-window mapping is wrong. Not reporting")
        print("       numbers on this.")
        return 2

    usable = {s: rs for s, rs in by.items() if len(rs) >= MIN_ROWS}
    print("windows with >= %d rows: %d of %d" % (MIN_ROWS, len(usable), len(by)))
    resolved = {s: rs for s, rs in usable.items() if outcome(rs) is not None}
    print("of those, with a determinable outcome: %d" % len(resolved))

    #: NO OUTCOME, NO TEST. Measured on this tape 2026-11-13: only 62 of 136
    #: windows carry any in-window rows at all, the last one lands at a
    #: MEDIAN t of 546s of 900, and just 8 finals are decisive. This is a
    #: SLATE SNAPSHOT tape -- it prices every live window, some of them over
    #: an hour ahead, but it does not follow any single window to
    #: resolution. A straddle's P&L is settlement arithmetic, so without the
    #: outcome there is nothing to compute, and guessing one from a quote at
    #: t=546 would be inventing the answer.
    if len(resolved) < 40:
        print()
        print("STOP: %d windows with a known outcome is not a test." % len(resolved))
        print()
        print("  This tape prices the whole live slate rather than following")
        print("  one window to its close, so most windows simply stop")
        print("  mid-flight. A straddle's P&L IS the settlement, so a window")
        print("  whose outcome is unknown contributes nothing, and inferring")
        print("  one from a mid-window quote would be inventing it.")
        print()
        print("  Worth recording from what IS here: Gemini BTC15m books are")
        print("  10-14 cents wide (e.g. bid 0.05 / ask 0.15) against PMUS")
        print("  BTC5m's 1 cent. Whatever else differs, these are not the")
        print("  same microstructure, so a replication on this venue was")
        print("  never going to be a clean read across.")
        print()
        print("  This script is correct and ready. It needs a 15m tape that")
        print("  follows windows to resolution -- the pmus-btc15m-tape")
        print("  service produces that shape, it just has not accumulated")
        print("  enough yet (363 lines, ~17 minutes as committed).")
        print()
        print("VERDICT  BLOCKED ON DATA, not negative. The 15m mechanism")
        print("         remains untested either way.")
        return 3
    print()

    slugs = sorted(resolved, key=lambda s: window_start(s))
    cut = int(len(slugs) * 0.67)
    print("GEMINI BTC15m, one share a side a rung, GROSS of fees")
    print("%-18s %7s %8s %9s %8s %6s %10s %10s" % (
        "config", "legs/w", "pairs/w", "NET c/w", "se", "t", "in-samp", "OOS"))
    print("-" * 82)
    cfgs = [("0.08 single", (D("0.08"),), CANCEL_LEAD_S),
            ("0.05-0.10 ladder", RUNGS, CANCEL_LEAD_S),
            ("ladder, cancel 90s", RUNGS, 90.0)]
    for name, rungs, lead in cfgs:
        rs = [walk(resolved[s], rungs, lead) for s in slugs]
        rs = [r for r in rs if r is not None]
        if not rs:
            continue
        m, se, t = stats([r["pnl"] for r in rs])
        mi, _, _ = stats([r["pnl"] for r in rs[:cut]])
        mo, _, _ = stats([r["pnl"] for r in rs[cut:]])
        print("%-18s %7.2f %8.2f %+9.2f %8.2f %+6.2f %+10.2f %+10.2f" % (
            name,
            statistics.fmean([r["legs"] for r in rs]),
            statistics.fmean([r["pairs"] for r in rs]),
            100 * m, 100 * se, t, 100 * mi, 100 * mo))

    rs = [walk(resolved[s], RUNGS, CANCEL_LEAD_S) for s in slugs]
    rs = [r for r in rs if r is not None]
    n = len(rs)
    anyp = sum(1 for r in rs if r["pairs"] > 0)
    m, se, t = stats([r["pnl"] for r in rs])
    print()
    print("windows with >=1 pair: %d of %d = %.1f%%   (PMUS 5m: 10.7%%)"
          % (anyp, n, 100 * anyp / n))
    print("net %+.2f c/w   se %.2f   t %+.2f   n %d" % (100 * m, 100 * se, t, n))
    print()
    print("THE QUESTION THIS ANSWERS: does the mechanism survive a change of")
    print("venue AND series? It is one pre-specified configuration on data")
    print("never used for this, so there is no search bar to clear.")
    print()
    if t >= 2.0:
        print("VERDICT  REPLICATED. The straddle is positive on a different")
        print("         venue and a different series at |t| >= 2. That is")
        print("         much stronger than anything the 5m tape could give.")
    elif t <= -2.0:
        print("VERDICT  CONTRADICTED. Negative at |t| >= 2 on independent")
        print("         data. The PMUS 5m result should be treated as noise")
        print("         until something explains the difference.")
    else:
        print("VERDICT  INCONCLUSIVE (|t| < 2). Consistent with the 5m")
        print("         result and consistent with zero. The sign and the")
        print("         pair rate are still worth reading as weak evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
