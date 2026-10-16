"""Maker-only matched pairs, zero rebate: is completing a residual better than
holding it?

The economic test the account owner asked for, under his constraints:
  - NO rebate is assumed. This account has never been credited one
    (rebates_ever_credited: false), so every figure here is rebate-free.
  - Residual settlement losses are the ACTUAL ones, from real REDEEM rows.
  - No scaled L5 trade is treated as an executable PMUS order; the two
    caveats that bound the result are printed with it.

Reads only files under data/. Submits nothing and writes no state.

  python3 -I scripts/l5_residual_completion_test.py
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal as D, ROUND_UP
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.l5_settlement_decomposition_audit import (  # noqa: E402
    realized_windows,
    window_records,
)

TAKER_THETA = D("0.0695")
PAIR_COST_BANDS = (0.94, 0.96, 0.98, 1.00, 1.02)
RESIDUAL_RATIOS = (0.00, 0.02, 0.05, 0.10, 0.1825, 0.30)
SLIPPAGES = (0.00, 0.01, 0.02, 0.027, 0.03, 0.05, 0.10, 0.156, 0.20)


def taker_fee(qty: D, price: D) -> D:
    """PMUS taker fee, per ORDER, rounded away from zero to the cent."""
    raw = TAKER_THETA * qty * price * (D(1) - price)
    q = raw.quantize(D("0.01"), rounding=ROUND_UP)
    return q if q != 0 else D("0.01")


def usable(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        r
        for r in recs
        if r["pair_cost"] is not None
        and r["winner"]
        and r["bigger"] != "tie"
        and r["avg_up"] is not None
        and r["avg_dn"] is not None
        and r["residual_sh"] > 0
    ]


def _complement(r: Dict[str, Any]) -> float:
    """The price of the side we are SHORT of -- what completion would buy."""
    return r["avg_dn"] if r["bigger"] == "Up" else r["avg_up"]


def completion_pnl(rows: List[Dict[str, Any]], slippage: float = 0.0) -> Dict[str, float]:
    """Cross the complement to flatten the residual, then redeem the pair at $1."""
    gross = fees = 0.0
    for r in rows:
        qty = r["residual_sh"]
        price = min(_complement(r) + slippage, 0.99)
        fee = float(taker_fee(D(str(round(qty, 2))), D(str(round(price, 2)))))
        gross += qty * (1.0 - r["avg_big"] - price)
        fees += fee
    return {"gross": gross, "fees": fees, "net": gross - fees}


def main() -> None:
    windows, _ = realized_windows()
    rows = usable(window_records(windows))
    if not rows:
        print("NO USABLE WINDOWS: data/l5/raw is absent or carries no settlements.")
        return

    S = lambda k: sum((r[k] or 0.0) for r in rows)
    matched_pnl = S("matched_pnl")
    held = S("residual_pnl")
    matched_sh = S("matched_sh")
    residual_sh = S("residual_sh")
    spend = sum(r["matched_sh"] * r["pair_cost"] + r["residual_sh"] * r["avg_big"] for r in rows)

    print("=" * 74)
    print("MAKER-ONLY MATCHED PAIRS, ZERO REBATE  (%d windows)" % len(rows))
    print("=" * 74)
    print("  matched  %10.0f sh   PnL $%+12.2f   %+0.6f / matched share" % (matched_sh, matched_pnl, matched_pnl / matched_sh))
    print("  residual %10.0f sh   PnL $%+12.2f   %+0.6f / residual share" % (residual_sh, held, held / residual_sh))
    print("  realized ratio k = residual/matched = %.4f" % (residual_sh / matched_sh))

    print()
    print("THE RESIDUAL RATIO IS THE LEVER (zero rebate)")
    per_matched = matched_pnl / matched_sh
    per_residual = held / residual_sh
    cash_matched = sum(r["matched_sh"] * r["pair_cost"] for r in rows) / matched_sh
    cash_residual = sum(r["residual_sh"] * r["avg_big"] for r in rows) / residual_sh
    for k in RESIDUAL_RATIOS:
        net = per_matched + k * per_residual
        cash = cash_matched + k * cash_residual
        print("  k=%.4f   net %+0.6f / matched share   %+8.4f%% of spend" % (k, net, 100 * net / cash))
    print("  BREAK-EVEN k = %.4f" % (per_matched / -per_residual))
    print("  BREAK-EVEN both-leg fill rate = %.2f%%" % (100 * -per_residual / (per_matched - per_residual)))

    print()
    print("HOLD THE RESIDUAL, OR COMPLETE IT BY CROSSING?")
    comp = completion_pnl(rows)
    better = sum(
        1
        for r in rows
        if (
            r["residual_sh"] * (1.0 - r["avg_big"] - _complement(r))
            - float(taker_fee(D(str(round(r["residual_sh"], 2))), D(str(round(_complement(r), 2)))))
        )
        > r["residual_pnl"]
    )
    print("  hold to settlement (what L5 did)   $%+12.2f   %+0.5f / share" % (held, held / residual_sh))
    print("  complete by crossing               $%+12.2f   %+0.5f / share" % (comp["net"], comp["net"] / residual_sh))
    print("    of which PMUS taker fees         $%12.2f   %0.5f / share" % (comp["fees"], comp["fees"] / residual_sh))
    print("  COMPLETING IS BETTER BY $%.2f (%.1f%% of windows individually)" % (comp["net"] - held, 100 * better / len(rows)))
    print("  total: held %+0.4f%%   completed %+0.4f%% of $%.2f spend"
          % (100 * (matched_pnl + held) / spend, 100 * (matched_pnl + comp["net"]) / spend, spend))

    print()
    print("SLIPPAGE SENSITIVITY -- the completion price above is the complement's")
    print("AVERAGE TRADED price, not the ask a crossing order actually pays:")
    print("  %-8s %14s %14s %13s" % ("slip", "completion", "vs hold", "total %spend"))
    for s in SLIPPAGES:
        c = completion_pnl(rows, s)
        print("  %-8.3f %14.2f %14.2f %+12.4f%%"
              % (s, c["net"], c["net"] - held, 100 * (matched_pnl + c["net"]) / spend))

    print()
    print("PAIR-COST SELECTION -- AN UPPER BOUND, NOT A PLAN. The realized pair")
    print("cost is knowable only AFTER both legs filled, so it cannot be")
    print("selected on ex ante:")
    print("  %-10s %8s %13s %13s %13s %10s" % ("pair<=", "windows", "matched", "residual", "net", "%spend"))
    for t in PAIR_COST_BANDS + (9.9,):
        sel = [r for r in rows if r["pair_cost"] <= t]
        if not sel:
            continue
        mp = sum(r["matched_pnl"] for r in sel)
        rp = sum(r["residual_pnl"] for r in sel)
        cash = sum(r["matched_sh"] * r["pair_cost"] + r["residual_sh"] * r["avg_big"] for r in sel)
        label = ("<=%.2f" % t) if t < 9 else "ALL"
        print("  %-10s %8d %13.2f %13.2f %13.2f %+9.4f%%" % (label, len(sel), mp, rp, mp + rp, 100 * (mp + rp) / cash))

    print()
    print("THE BINDING UNKNOWN: no measured PMUS maker fill rate exists. The depth")
    print("comparison reports NOT_COMPARABLE on every run because nothing has ever")
    print("been placed. The break-even both-leg fill rate above is an unmet")
    print("REQUIREMENT, not a satisfied condition.")


if __name__ == "__main__":
    main()
