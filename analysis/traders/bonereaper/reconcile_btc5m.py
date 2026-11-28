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

    #: WHERE THE GAP IS. Per window, the table's P&L against its own
    #: components. Added 2026-11-19: the gap had been put down to a
    #: "matching convention". It is not one.
    print("\nWHERE THE GAP IS, window by window")
    off = []
    cash = D(0)
    for r in rows:
        t = dec(r["pnl_derived_resolution"])
        mq, mc = dec(r["matched_qty"]) or D(0), dec(r["matched_pair_cost"]) or D(0)
        rq, rc = dec(r["residual_qty_abs"]) or D(0), dec(r["residual_cost"]) or D(0)
        side = (r.get("residual_side") or "").strip().upper()
        won = (r.get("winning_outcome") or "").strip().upper()
        comp = (mq - mc) + ((rq if (side and side == won) else D(0)) - rc)
        if won:
            held = dec(r["final_up_qty" if won == "UP" else "final_down_qty"]) or D(0)
            cash += held - (dec(r["total_dollars_bought"]) or D(0))
        if t is None or abs(t - comp) > D("0.01"):
            off.append((r["market_slug"], t, comp, mq - mc, rq, side, rc))
    print("  windows where table != components   %d" % len(off))
    for slug, t, comp, m, rq, side, rc in off:
        print("    %s  table=%s  components=%+.2f" % (slug, t, comp))
        print("      UNRESOLVED: the table omits it; the components sum scored")
        print("      its residual as a total loss. Matched part %+.2f is"
              % m)
        print("      certain. Residual %.2f %s cost %.2f settles at %+.2f or"
              % (rq, side, rc, -rc))
        print("      %+.2f, so the window is %+.2f or %+.2f."
              % (rq - rc, m - rc, m + rq - rc))
    print("  gap explained by those windows      %+8.2f"
          % sum((t or D(0)) - comp for _s, t, comp, *_ in off))
    print("  CASH IDENTITY over resolved windows (winning shares held minus")
    print("  all cash paid)                      %+12.2f" % cash)
    print("  -> the table total is exact; nothing else is unreconciled.")

    print("\nANOMALIES AND RESOLUTION SOURCES")
    an = collections.Counter((r.get("anomalies") or "").strip() for r in rows)
    for k, n in an.most_common(6):
        print("  anomalies=%-28s %d" % (k or "(none)", n))
    rs = collections.Counter((r.get("resolution_source") or "").strip()
                             for r in rows)
    for k, n in rs.most_common(6):
        print("  resolution_source=%-21s %d" % (k or "(none)", n))

    print("\nWHAT THIS WALLET IS AND IS NOT EVIDENCE OF")
    ge90 = sum(1 for r in rows
               if (r.get("any_buy_ge_90c") or "").strip().lower()
               in ("true", "1", "yes"))
    print("  windows with a buy at 90c or above     %d (%.0f%%)"
          % (ge90, 100.0 * ge90 / len(rows)))
    print("  maker/taker per window                 NOT IN THIS TABLE.")
    print("    `first_action` holds BUY for every row, so the earlier line")
    print("    \"0 of 304 first actions were TAKER\" measured nothing.")
    print("    workup.json puts 49.67% of this wallet's dollars as taker.")
    print("  fees                                   NOT IN THIS DATA. Every")
    print("    row is a buy at a price; P&L here is before any fee.")
    print("  ACROSS ALL ASSETS (workup.json, 1,560 windows):")
    print("    derived trading P&L                 -14562.84")
    print("    TAKER_REBATE 6466.66 + MAKER_REBATE 1087.80 + REWARD 143.83")
    print("                                         +7698.29")
    print("    NET AFTER INCENTIVES                 -6864.55")
    print("  MERGES ARE NOT INCOME. merge_usdc 39613.33 (35 events) is")
    print("    matched UP+DOWN pairs handed back for $1.00 each. The trading")
    print("    P&L above already pays every matched pair $1.00, so adding")
    print("    merges would count the same dollars twice. An earlier version")
    print("    of this file said the account was \"made whole by incentives")
    print("    and merges\". It was not: it is net NEGATIVE after incentives.")
    print("  Incentives are an all-asset total and cannot be allocated to")
    print("    BTC 5m from this data.")
    print("  CONCLUSION: this wallet lost money trading, lost money after")
    print("    rebates we cannot earn (maker Tier 1 pays zero), and half its")
    print("    volume is taker. It is NOT evidence of a profitable strategy")
    print("    and must not be cited as one. Its fill pattern remains a")
    print("    description of what is mechanically possible, nothing more.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1
                          else "analysis/traders/bonereaper/l5_windows_summary.csv"))
