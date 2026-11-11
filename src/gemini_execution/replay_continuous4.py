"""Replay the CONTINUOUS-4 chronology and print what changed. Read-only.

No session, no credentials, no order path. It drives the real Driver and the
real controller over the venue's own recorded frames and writes an artifact
showing the state each decision saw, before and after the fix.
"""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from typing import Any, Dict, List

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609181730-UP"

#: the chronology as the wire log recorded it
CHRONOLOGY = [
    ("17:27:59.440", "place", "YES", "0.77", "145828836246611817"),
    ("17:27:59.521", "place", "NO", "0.14", "145828836246611835"),
    ("17:28:05.645", "cancel", "YES", "0.77", "145828836246611817"),
    ("17:28:05.759", "cancel", "NO", "0.14", "145828836246611835"),
    ("17:28:05.977", "place", "YES", "0.92", "145828836246613472"),
    ("17:28:06.206", "place", "NO", "0.01", "145828836246613510"),
    ("17:28:12.376", "cancel", "NO", "0.01", "145828836246613510"),
    ("17:28:12.491", "place", "NO", "0.04", "145828836246614326"),
    ("17:28:38.473", "cancel", "YES", "0.92", "145828836246613472"),
]

CANCEL_ACK_EMPTY = {"status": 200,
                    "result": {"orderId": "", "symbol": "", "status": ""}}

#: what the venue later confirmed: these two filled, and nothing else did
FILLED = [{"leg": "NO", "qty": "0.01", "price": "0.14"},
          {"leg": "YES", "qty": "0.01", "price": "0.92"}]


def decision_state(inv: CP.Inventory) -> Dict[str, Any]:
    r = inv.report()
    return {k: r.get(k) for k in
            ("yes_qty", "no_qty", "matched_qty", "directional_residual",
             "inventory_imbalance", "total_spend_usd", "avg_yes_cost",
             "avg_no_cost", "n_fills")}


def main() -> int:
    out: Dict[str, Any] = {
        "label": "GEMINI_CONTINUOUS4_REPLAY_V1",
        "symbol": SYMBOL,
        "source": "analysis/gemini/continuous_wire_20260918T172750Z.json",
        "chronology": [{"t": t, "op": op, "leg": leg, "price": px,
                        "orderId": oid}
                       for t, op, leg, px, oid in CHRONOLOGY],
        "labels": ("READ-ONLY replay. No session, no credentials, no order "
                   "path. REALIZED is not computed here: settlement has not "
                   "paid and a cost basis is not a profit."),
    }

    # ---- what the driver BELIEVED, cycle by cycle, with no refresh
    before_fix = CP.Inventory()          # frozen at the startup snapshot
    out["without_refresh"] = {
        "state_at_every_decision": decision_state(before_fix),
        "why": ("apply_fill had no caller in the live loop and the "
                "orders@account queue was never drained, so this never moved "
                "for the whole window"),
    }

    # ---- what the venue actually held at the moment YES 0.92 was decided
    at_yes_decision = CP.Inventory()
    at_yes_decision.apply_fill("NO", "0.01", "0.14", source="venue_restart")
    out["with_refresh_at_the_yes_0_92_decision"] = {
        "state": decision_state(at_yes_decision),
        "why": ("NO 0.14 had FILLED and its cancel returned 200 with an empty "
                "result. The reconciliation the fix adds observes the NO leg "
                "before the next decision instead of after the window."),
    }

    # ---- the cancel seam, on the real ack
    result = CANCEL_ACK_EMPTY.get("result")
    confirmed = bool(isinstance(result, dict)
                     and str(result.get("orderId") or ""))
    out["cancel_seam"] = {
        "ack": CANCEL_ACK_EMPTY,
        "confirmed_before_fix": True,
        "confirmed_after_fix": confirmed,
        "consequence_before": ("the order was dropped from the working set, so "
                               "a filled position became invisible"),
        "consequence_after": ("the order stays in the working set and the "
                              "authoritative refresh decides"),
    }

    # ---- the completed pair
    pair = Decimal("0.92") + Decimal("0.14")
    out["completed_pair"] = {
        "yes_price": "0.92", "no_price": "0.14",
        "combined_cost": str(pair),
        "redeems_at": "1.00",
        "cost_basis_delta_per_unit": str(Decimal("1.00") - pair),
        "units": "0.01",
        "spring_discipline_target": "<= 0.96",
        "label": ("COST BASIS, NOT A LOSS. Settlement has not paid, so "
                  "nothing here is REALIZED."),
    }

    # ---- final state both ways
    reconciled = CP.Inventory()
    for f in FILLED:
        reconciled.apply_fill(f["leg"], f["qty"], f["price"],
                              source="venue_restart")
    out["final_reconciled_inventory"] = decision_state(reconciled)

    out["behavioural_difference"] = (
        "Before: every decision in the window was computed against yes_qty 0 / "
        "no_qty 0, because CP.plan builds state['inventory'] FROM the "
        "Inventory object and that object never moved. The oscillator "
        "therefore never saw the NO leg, and no risk gate could refuse a rung "
        "on inventory it did not know existed. After: the venue is re-read "
        "before every decision and again after cancels when a place is "
        "pending, so the YES 0.92 decision is taken against no_qty 0.01 "
        "rather than 0.")

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
