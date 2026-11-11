#!/usr/bin/env python3
"""Can the data we HAVE support joint-fill and conditional-settlement
calibration? Read-only; answers yes or no with the arithmetic.

THE TWO THINGS THE PAIRED-ENTRY MODEL REQUIRES.

  JOINT FILL  P(both), P(up only), P(down only), P(neither) -- and it must
              be a JOINT distribution, because independent maker fills
              would conceal the trend-driven adverse selection that is the
              whole effect.
  CONDITIONAL SETTLEMENT
              P(UP settles | UP filled alone) and
              P(UP settles | DOWN filled alone). This is the adverse
              selection, and it is what the 15:30Z loss turned on.

WHAT EACH NEEDS FROM THE RECORDINGS. The joint distribution needs, per
round, whether each leg filled -- which the recorded books give through the
PROVEN/POSSIBLE arms without any order being placed. The conditional
settlement needs, per window, WHICH SIDE SETTLED, and that is the field the
recordings do not carry: a 15-minute up/down market settles at t=900 and
the tapes stop earlier.

A terminal book can stand in for the settled side only when it is both LATE
and EXTREME. Both thresholds are stated here rather than chosen quietly,
and the residual error is reported as the implied probability of the other
side rather than ignored.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal as D

sys.path.insert(0, ".")
from analysis.l5.issue208 import queue_scenarios as QS   # noqa: E402
from src import l5_pmus_auth_book as AB                   # noqa: E402

WINDOW_S = 900.0
#: a terminal book this far into the window is treated as informative.
LATE_S = 700.0
#: and this close to a boundary. 0.02 implies ~2% for the other side, which
#: is the error the proxy carries and is reported, not hidden.
EXTREME = D("0.03")


def wilson(k: int, n: int, z: float = 1.96):
    """95% interval for a proportion. Small-n honest, unlike k/n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, c - h), min(1.0, c + h))


def assess():
    rows = []
    for src in list(QS.OUT_OF_SAMPLE) + list(QS.IN_SAMPLE):
        try:
            books = QS.load(src[0], src[1])
        except Exception as exc:                           # noqa: BLE001
            rows.append({"source": src[0], "error": str(exc)[:60]})
            continue
        for slug, frames in sorted(books.items()):
            start = AB.slug_start(slug)
            if start is None:
                # a decision-keyed record, not a market slug
                continue
            last = frames[-1]
            t_last = last["t"] - start
            tt = QS.touches(last)
            bid, ask = (tt[0], tt[1]) if tt else (None, None)
            late = t_last >= LATE_S
            up_settled = bid is not None and bid >= (D(1) - EXTREME)
            dn_settled = ask is not None and ask <= EXTREME
            rows.append({
                "slug": slug, "frames": len(frames),
                "first_s": round(frames[0]["t"] - start, 1),
                "last_s": round(t_last, 1),
                "coverage": round(t_last / WINDOW_S, 3),
                "last_bid": None if bid is None else str(bid),
                "last_ask": None if ask is None else str(ask),
                "late": late,
                "settled_side": ("UP" if late and up_settled else
                                 "DOWN" if late and dn_settled else None),
                "why_not": (None if (late and (up_settled or dn_settled))
                            else ("TAPE_ENDS_EARLY" if not late
                                  else "TERMINAL_BOOK_NOT_EXTREME")),
                "implied_other_side": (
                    None if not (late and (up_settled or dn_settled))
                    else str(D(1) - bid if up_settled else ask)),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = [r for r in assess() if "slug" in r]
    determined = [r for r in rows if r["settled_side"]]
    n_win = len(rows)

    out = {"windows": n_win, "settled_side_determined": len(determined),
           "late_threshold_s": LATE_S, "extreme_threshold": str(EXTREME),
           "rows": rows}

    if a.json:
        print(json.dumps(out, indent=1))
        return 0

    print("MARKET WINDOWS IN THE REPLAY SOURCES: %d" % n_win)
    print("  %-30s %6s %8s %9s %10s %s" % ("slug", "frames", "last_s",
                                           "coverage", "settled", "why not"))
    for r in rows:
        print("  %-30s %6d %8.0f %9.3f %10s %s"
              % (r["slug"][-28:], r["frames"], r["last_s"], r["coverage"],
                 r["settled_side"] or "-", r["why_not"] or ""))
    print()
    print("SETTLED SIDE DETERMINABLE: %d of %d windows (late >= %.0fs AND "
          "terminal book within %s of a boundary)"
          % (len(determined), n_win, LATE_S, EXTREME))
    early = sum(1 for r in rows if r["why_not"] == "TAPE_ENDS_EARLY")
    mid = sum(1 for r in rows if r["why_not"] == "TERMINAL_BOOK_NOT_EXTREME")
    print("  tape ends early:            %d" % early)
    print("  terminal book not extreme:  %d" % mid)
    print()
    print("WHAT THAT SUPPORTS, with 95%% Wilson intervals rather than k/n:")
    for label, n in (("conditional settlement, per cell", len(determined)),
                     ("joint fill, venue-confirmed rounds", 11)):
        lo, hi = wilson(max(1, n // 2), n)
        print("  %-36s n=%-3d  a 50%% estimate spans %.2f..%.2f (width %.2f)"
              % (label, n, lo, hi, hi - lo))
    print()
    print("A conditional-settlement probability drives the ENTRY decision")
    print("through a break-even skew of 0.0871, so an interval wider than")
    print("+/-0.09 cannot distinguish 'enter' from 'do not enter'.")
    for n in (len(determined), 11, 30, 100, 400):
        lo, hi = wilson(n // 2, n)
        half = (hi - lo) / 2
        print("   n=%-4d half-width %.3f  %s" % (
            n, half, "SUFFICIENT" if half <= 0.09 else "insufficient"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
