"""Reconcile the reported BTC5m figures against the underlying window table.

WHY THIS IS POSSIBLE. A third-party summary quoted 304 BTC5m windows, a
derived pre-fee P&L of -$9,727, a median matched-pair cost of $1.020 and 41%
of windows below $1, plus an internal gap (components -$9,959 against a
reported -$9,727) and a "missing 304th window". We hold the underlying table:
`analysis/traders/bonereaper/l5_windows_summary.csv`, 1,560 windows, of which
EXACTLY 304 are BTC 5-minute. So the arithmetic can be checked rather than
argued about.

WHAT THIS IS NOT. It is not a check of a different wallet. The 0x8dxd figures
measured in `analysis/l5/btc15m/edge_decomposition.py` -- BTC5m median pair
cost 0.9508, 66.5% below $1.00, +$185,754 REDEEM-anchored -- are a DIFFERENT
TRADER and cannot be compared to these. 2,000 random 304-window subsamples of
the 0x8dxd BTC5m data put the 99% range of the median pair cost at
[0.9279, 0.9742] and of net P&L at [+$137, +$31,673]: the reported 1.020 and
-$9,727 lie outside both, which is the proof that the two samples are not the
same trader rather than a disagreement about method.
"""
from __future__ import annotations

import collections
import csv
import statistics as st
import sys
from decimal import Decimal as D


def dec(x):
    s = (x or "").strip()
    if not s:
        return None
    try:
        return D(s)
    except Exception:
        return None


def main(path, asset="BTC", minutes="5"):
    rows = [r for r in csv.DictReader(open(path))
            if r.get("asset") == asset and r.get("window_minutes") == minutes]
    print("=" * 78)
    print("bonereaper %s %sm -- %d windows" % (asset, minutes, len(rows)))
    print("=" * 78)

    pcs = [dec(r["matched_pair_unit_cost"]) for r in rows]
    have_pc = [x for x in pcs if x is not None]
    below = sum(1 for x in have_pc if x < 1)
    print("MATCHED-PAIR COST")
    print("  windows with a matched pair     %d of %d" % (len(have_pc), len(rows)))
    print("  median unit cost                %.4f      reported 1.020"
          % st.median(have_pc))
    print("  below 1.00                      %d/%d = %.1f%%   reported 41%%"
          % (below, len(have_pc), 100.0 * below / len(have_pc)))

    print("\nP&L, BY THE TABLE'S OWN COLUMNS")
    for col in ("pnl_derived_resolution", "pnl_polymarket_positions",
                "realized_trading_pnl_within_window"):
        vals = [dec(r[col]) for r in rows]
        got = [v for v in vals if v is not None]
        print("  %-38s n=%3d  total %+12.2f"
              % (col, len(got), sum(got)))
        if len(got) != len(rows):
            miss = [r["market_slug"] for r, v in zip(rows, vals) if v is None]
            print("      MISSING in %d window(s): %s"
                  % (len(miss), ", ".join(miss[:4])))

    #: THE COMPONENTS, matched + residual, by the table's own allocation
    print("\nCOMPONENTS (matched vs residual), as the table allocates them")
    mp = rp = D(0)
    mn = rn = 0
    for r in rows:
        mq, muc = dec(r["matched_qty"]), dec(r["matched_pair_unit_cost"])
        if mq is not None and muc is not None:
            mp += mq * (D(1) - muc)
            mn += 1
        rq, rc, side = (dec(r["residual_qty_abs"]), dec(r["residual_cost"]),
                        (r.get("residual_side") or "").strip())
        won = (r.get("winning_outcome") or "").strip()
        if rq is not None and rc is not None and rq > 0:
            payoff = rq if (side and won and side.upper() == won.upper()) else D(0)
            rp += payoff - rc
            rn += 1
    print("  matched-pair P&L   %+12.2f  over %d windows" % (mp, mn))
    print("  residual P&L       %+12.2f  over %d windows" % (rp, rn))
    print("  components total   %+12.2f      reported -9959" % (mp + rp))

    tot = sum(v for v in (dec(r["pnl_derived_resolution"]) for r in rows)
              if v is not None)
    print("  table total        %+12.2f      reported -9727" % tot)
    print("  GAP (total - components) %+8.2f  reported gap +232"
          % (tot - (mp + rp)))

    print("\nANOMALIES AND RESOLUTION SOURCES")
    an = collections.Counter((r.get("anomalies") or "").strip() for r in rows)
    for k, n in an.most_common(6):
        print("  anomalies=%-28s %d" % (k or "(none)", n))
    rs = collections.Counter((r.get("resolution_source") or "").strip()
                             for r in rows)
    for k, n in rs.most_common(6):
        print("  resolution_source=%-21s %d" % (k or "(none)", n))

    print("\nHOW THIS TRADER WAS PAID -- the part a pair-cost table cannot show")
    tk = sum(1 for r in rows if (r.get("first_action") or "").upper() == "TAKER")
    print("  windows whose first action was TAKER   %d of %d" % (tk, len(rows)))
    ge90 = sum(1 for r in rows
               if (r.get("any_buy_ge_90c") or "").strip().lower()
               in ("true", "1", "yes"))
    print("  windows with a buy at 90c or above     %d (%.0f%%)"
          % (ge90, 100.0 * ge90 / len(rows)))
    print("  NOTE: workup.json for this wallet records, across ALL assets,")
    print("  derived_trading_pnl -14562.84 against incentives of")
    print("  TAKER_REBATE 6466.66 + MAKER_REBATE 1087.80 + REWARD 143.83")
    print("  and merge_usdc 39613.33 over 35 merge events. The trading leg")
    print("  LOST money; the account was made whole by incentives and merges.")
    print("  This account is maker Tier 1, where the maker rebate is ZERO and")
    print("  Tier 2 needs $10,000,000 of 30-day maker volume against roughly")
    print("  $38,880 a month (src/pmus_fees.py). That component is therefore")
    print("  not available to us at any size.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1
                          else "analysis/traders/bonereaper/l5_windows_summary.csv"))
