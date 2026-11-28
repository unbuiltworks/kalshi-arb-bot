"""ROUTE B against the seven reconstructed windows, decision by decision.

WHAT IS BEING TESTED AND WHY IT IS NOT A NEW SCHEDULER. The one-sided holds
of 10.3-239.0 s measured in `ORDER_BY_ORDER_RECONSTRUCTION.md` come from a
completion that was only priced inside `l5_live_execution.handle(row, ...)`
-- one call per admitted L5 action. `src/l5_book_completion.py` ("ROUTE B")
already closes that: the runner's own tick calls `maybe_book_completion`
every `DECIDE_EVERY_S = 5.0` s regardless of whether L5 acted, and it is
ENABLED in production. The 2026-10-24 tapes predate it. So the question is
not whether to build a scheduler; it is what the deployed one does.

WHAT THIS MEASURES. For each window, the held leg the venue confirms and
the real book from that window's own presubmit record are fed to the real
`BC.decide`. The output is its action, its reason, the leg it would rest,
and the pair that leg implies -- against what actually happened.

COUNTERFACTUAL, AND LABELLED. ROUTE B never ran in these windows. A
`COMPLETE` here means the decision function would have asked for that quote
at that book, NOT that it would have filled. Queue position at the touch is
unmeasured, no fill is credited, and no pair is counted as completed.

THE ECONOMIC COMPARISON IS THE POINT. ROUTE B quotes at
`completion_depth_ticks()`, which is 0 -- the touch -- while the deployed
opening probe rests 2 ticks behind. A touch quote is more likely to fill and
costs more, so the pair it implies is dearer. Whether that trade is worth
making is decided by comparing the pair against carrying the residual at the
measured -0.0236/contract, not by preferring more fills.
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import order_lifecycle_reconstruction as R      # noqa: E402

#: measured adverse-selection constant for an unmatched leg carried to
#: settlement, from the repaired union over 1.24M contracts.
RESIDUAL_PER_CONTRACT = D("-0.0236")

TAPES = ["analysis/l5/residual_exit/preserved/*.ndjson"]


def _legs(window: str, got: Dict[str, Any], pr: Dict[str, Any]):
    opening = completion = None
    for o in got["orders"].values():
        if o.window != window:
            continue
        if (o.terminal_state == R.FILLED and o.side == pr["open_side"]
                and o.own_px == pr["open_px"]):
            opening = o
        if o.side == pr["completion_side"] and o.own_px == pr["completion_px"]:
            completion = o
    return opening, completion


def replay(tapes: Optional[List[str]] = None,
           *, residual_budget_usd: str = "1.00",
           submissions_left: int = 2) -> List[Dict[str, Any]]:
    """One `BC.decide` per window, on that window's own confirmed inputs."""
    os.environ.setdefault("L5_BOOK_COMPLETION", "1")
    from src import l5_book_completion as BC

    got = R.reconstruct(tapes or TAPES)
    prs = {p["window"]: p for p in R.pairs(got["orders"], got["settlements"])}
    out: List[Dict[str, Any]] = []
    for window, pr in sorted(prs.items()):
        opening, completion = _legs(window, got, pr)
        if not (opening and completion and completion.quote):
            continue
        book = dict(completion.quote.get("book") or {})
        qty = opening.final_qty
        held_cost = (D(opening.own_px) * D(qty)).quantize(D("0.000001"))
        rec = BC.decide(
            market=window, held_side=opening.side, held_qty=qty,
            held_cost_usd=str(held_cost),
            deficient_qty="0", deficient_cost_usd="0", book=book,
            now_t=completion.quote_line_at,
            window_start=completion.quote_line_at - 120,
            resting=(), tick="0.01", min_qty="0.01",
            depth_ticks=BC.completion_depth_ticks(),
            residual_budget_usd=residual_budget_usd,
            submissions_left=submissions_left,
            outstanding_qty="0", last_decide_t=None)
        leg = rec.get("leg") or {}
        row = {"window": window, "held_side": opening.side, "qty": qty,
               "held_px": opening.own_px,
               "action": rec.get("action"), "reason": rec.get("reason"),
               "leg_side": leg.get("side") or "",
               "leg_px": str(leg.get("price") or ""),
               "actual_completion_px": pr["completion_px"],
               "actual_state": pr["completion_state"].replace(
                   "ORDER_STATE_", ""),
               "actual_pair_cost": pr["realized_pair_cost"]}
        # the pair each price implies, in own terms
        try:
            row["route_b_pair"] = str((D(opening.own_px)
                                       + D(str(leg["price"]))))
        except BaseException:                                  # noqa: BLE001
            row["route_b_pair"] = ""
        try:
            row["attempted_pair"] = str((D(opening.own_px)
                                         + D(pr["completion_px"])))
        except BaseException:                                  # noqa: BLE001
            row["attempted_pair"] = ""
        # gross matched edge a completed pair earns, per contract, before
        # fees; and what carrying the residual is measured to cost instead
        for k, src in (("route_b_edge", "route_b_pair"),
                       ("attempted_edge", "attempted_pair")):
            try:
                row[k] = str(D(1) - D(row[src]))
            except BaseException:                              # noqa: BLE001
                row[k] = ""
        row["carry_instead"] = str((RESIDUAL_PER_CONTRACT * D(qty))
                                   .quantize(D("0.0001")))
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", default="1.00")
    ap.add_argument("--slots", type=int, default=2)
    a = ap.parse_args()
    rows = replay(residual_budget_usd=a.budget, submissions_left=a.slots)
    hdr = ("window           held  qty   held_px  ROUTE B leg   reason"
           "                        pair   edge/ct | actually      pair")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%-16s %-5s %-5s %-8s %-4s@%-7s %-29s %-6s %-7s | %-8s %-5s %s"
              % (r["window"], r["held_side"], r["qty"], r["held_px"],
                 r["leg_side"], r["leg_px"], str(r["reason"])[:29],
                 r["route_b_pair"], r["route_b_edge"],
                 r["actual_state"], r["actual_completion_px"],
                 r["actual_pair_cost"] or "-"))
    done = [r for r in rows if r["action"] == "COMPLETE"]
    print()
    print("ROUTE B would have asked to complete %d of %d windows."
          % (len(done), len(rows)))
    print("Pair costs it implies: %s"
          % ", ".join(sorted(r["route_b_pair"] for r in done)))
    print("Pairs actually realized: %s"
          % ", ".join(sorted(r["actual_pair_cost"] for r in rows
                             if r["actual_pair_cost"])))
    print()
    print("NO FILL IS CREDITED. A COMPLETE is a decision to rest a quote at "
          "that book, not a fill;\nqueue position at the touch is unmeasured "
          "and no pair here is counted as completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
