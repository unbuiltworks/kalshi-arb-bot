#!/usr/bin/env python3
"""The replay's edge, repriced from venue-confirmed settlement.

WHY THE REPLAY AND THE LIVE WINDOW DISAGREED. The out-of-sample replay
reports +0.014833 per entry for variant C on its conservative arm. It
reaches that by charging a completed pair at the price it QUOTED and an
unmatched leg at a flat -0.0236/contract. Neither survives contact with
the only two venue-confirmed settlements we have.

  PAIR COMPOSITION. Window 2015z on 2026-11-06 is the one window whose
  pairs actually completed and settled. Its own reconciliation, penny-exact
  against the venue's `up_cost 1.541400` / `dn_cost 2.4200`:

      UP    4.03 @ avg own 0.38248
      DOWN  4    @ avg own 0.60500
      realized pair cost  0.98748   against a 0.96 quoted ceiling
      gross margin/pair   0.01252
      x 4 matched         0.050074  == settlement matched_pnl exactly

  The 0.02748 of slippage is LEGGING: the legs fill at different moments
  and each fills on an adverse move, so the second is requoted into a
  market that has already moved against the first. The replay never pays
  this, because it prices the pair as quoted.

  STRANDED COST. Window 1530z on 2026-11-09 is the only venue-settled
  stranded leg: 0.7 DOWN at own 0.44, UP settled, the leg paid nothing,
  -$0.3080 settled, reconciled `ours 0.7 / venue 0.7000`. That is
  -0.4400/contract against the replay's -0.0236.

WHAT THIS SCRIPT DOES. It takes the replay's own reported fill structure --
P(pair) and P(residual), which are properties of the recorded books and are
NOT re-estimated here -- and reprices the two cash terms with the
settlement-confirmed figures. Nothing is simulated and no fill is invented.

    net/entry = P(pair) x gross_margin_per_pair + P(residual) x residual_cost

READ-ONLY. No order, no venue call, no deployment.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal as D

# ---- the replay's own fill structure, variant C, pessimistic (PROVEN) arm,
# ---- 13 out-of-sample windows. Properties of the recorded books.
P_PAIR = D("0.3652")
P_RESIDUAL = D("0.1372")
REPLAY_NET = D("0.014833")

# ---- what the replay ASSUMES
QUOTED_PAIR = D("0.95")                 # a 0.96-capped quote at depth 2
ASSUMED_MARGIN = D("1") - QUOTED_PAIR   # 0.05
ASSUMED_RESIDUAL = D("-0.0236")

# ---- what the venue CONFIRMED
REALIZED_PAIR = D("0.98748")            # 2015z, penny-exact to settlement
CONFIRMED_MARGIN = D("1") - REALIZED_PAIR   # 0.01252
CONFIRMED_RESIDUAL = D("-0.4400")       # 1530z, the only settled strand


def net_per_entry(margin: D, residual: D) -> D:
    return P_PAIR * margin + P_RESIDUAL * residual


def breakeven_residual(margin: D) -> D:
    """The residual cost at which the edge is exactly zero."""
    return -(P_PAIR * margin) / P_RESIDUAL


def breakeven_margin(residual: D) -> D:
    return -(P_RESIDUAL * residual) / P_PAIR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = [
        ("replay as published", ASSUMED_MARGIN, ASSUMED_RESIDUAL),
        ("+ venue pair cost", CONFIRMED_MARGIN, ASSUMED_RESIDUAL),
        ("+ venue stranded cost", ASSUMED_MARGIN, CONFIRMED_RESIDUAL),
        ("both venue-confirmed", CONFIRMED_MARGIN, CONFIRMED_RESIDUAL),
    ]
    out = {"p_pair": str(P_PAIR), "p_residual": str(P_RESIDUAL),
           "replay_published_net": str(REPLAY_NET), "rows": []}

    if not a.json:
        print("Variant C, pessimistic (PROVEN) arm, 13 OOS windows.")
        print("P(pair) %s  P(residual) %s  -- from the recorded books, "
              "not re-estimated." % (P_PAIR, P_RESIDUAL))
        print()
        print("  %-24s %10s %12s %14s %9s" % (
            "pricing", "margin", "residual", "net/entry", "verdict"))
    for label, margin, residual in rows:
        net = net_per_entry(margin, residual)
        rec = {"pricing": label, "margin_per_pair": str(margin),
               "residual_per_contract": str(residual),
               "net_per_entry": str(net.quantize(D("0.000001"))),
               "positive": net > 0}
        out["rows"].append(rec)
        if not a.json:
            print("  %-24s %10s %12s %14s %9s" % (
                label, margin, residual,
                "%+.6f" % float(net), "POSITIVE" if net > 0 else "NEGATIVE"))

    be_r = breakeven_residual(CONFIRMED_MARGIN)
    be_m = breakeven_margin(CONFIRMED_RESIDUAL)
    out["breakeven_residual_at_confirmed_margin"] = str(
        be_r.quantize(D("0.000001")))
    out["breakeven_margin_at_confirmed_residual"] = str(
        be_m.quantize(D("0.000001")))
    out["headroom_vs_replay_assumption"] = str(
        (be_r - ASSUMED_RESIDUAL).quantize(D("0.000001")))

    if a.json:
        print(json.dumps(out, indent=1))
        return 0

    print()
    print("Sanity check: the published net implies a margin of %+.5f, which "
          "is a %s pair." % (
              float((REPLAY_NET - P_RESIDUAL * ASSUMED_RESIDUAL) / P_PAIR),
              (D("1") - (REPLAY_NET - P_RESIDUAL * ASSUMED_RESIDUAL) / P_PAIR
               ).quantize(D("0.001"))))
    print()
    print("AT THE VENUE-CONFIRMED PAIR COST:")
    print("  break-even stranded cost      %+.6f /contract" % float(be_r))
    print("  the replay assumes            %+.6f" % float(ASSUMED_RESIDUAL))
    print("  headroom on that assumption   %+.6f" % float(be_r - ASSUMED_RESIDUAL))
    print("  the one settled observation   %+.6f  (%.1fx outside)"
          % (float(CONFIRMED_RESIDUAL),
             float(CONFIRMED_RESIDUAL / be_r)))
    print()
    print("AT THE VENUE-CONFIRMED STRANDED COST:")
    print("  break-even margin/pair        %+.6f  (a %s pair)"
          % (float(be_m), (D("1") - be_m).quantize(D("0.0001"))))
    print("  the venue realized            %+.6f  (a %s pair)"
          % (float(CONFIRMED_MARGIN), REALIZED_PAIR))
    print()
    print("Nothing here is a fill, a pair or a profit. Two settled windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
