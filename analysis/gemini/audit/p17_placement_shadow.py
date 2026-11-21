"""P17 -- prove the post-placement capture on the REAL path, sending nothing.

WHY A NEW HARNESS. Nothing that already exists exercises the placement code
with a real book. `shadow_soak` never constructs a ws_continuous.Driver, and
the unarmed production path refuses at the authorization gate BEFORE the
window loop reaches `_decide`. So the one defect Probe B could not diagnose --
whether the placement rule transformed a price or no-oped -- had no read-only
path on which it could ever have been caught.

WHAT THIS RUNS. The REAL public book (metadata.outcome_book), the REAL frozen
controller (continuous_probe.state_from_venue -> plan), the REAL placement
rule (ab_arms.apply_A), the REAL crossing re-check, and the REAL capture
(ws_continuous.Driver._record_placement).

WHAT IT CANNOT DO. It constructs no session, holds no credential and calls
neither place() nor cancel(), so no mutation is reachable from here. It
decides nothing: every price it reports was produced by code that already
exists.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal as D
from typing import Any, Dict, List

from src.gemini_execution import ab_arms as AB
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import metadata as MD
from src.gemini_execution import ws_continuous as WC


class _Shadow(WC.Driver):
    """The recorder and nothing else -- no session, no ledger, no transport."""

    def __init__(self, placement: str = "maker_improve"):
        self._placement = placement
        self._placement_log: List[Dict[str, Any]] = []
        self.budget_consumed = 0

    def budget_left(self) -> int:
        return 12


def observe(symbol: str, tte_s: float, *, placement: str = "maker_improve"
            ) -> Dict[str, Any]:
    """One cycle: real book -> real surface -> real placement -> capture."""
    book = MD.outcome_book(symbol)
    inv = CP.Inventory()
    st = CP.state_from_venue(symbol, tte_s, inv, book=book)
    out: Dict[str, Any] = {"symbol": symbol, "tte_s": tte_s,
                           "book_readable": bool((st or {}).get("readable",
                                                                True))}
    try:
        planned = CP.plan(st, inv, sp=CP.probe_params(action_size="1"),
                          actions_taken=0, action_size="1")
    except Exception as exc:                                  # noqa: BLE001
        out["plan_error"] = "%s: %s" % (type(exc).__name__, exc)
        return out
    acts = list(planned.get("actions") or [])
    out["n_actions"] = len(acts)
    if not acts:
        out["why"] = "the frozen surface desired no action at this state"
        return out

    imp_book = WC._improve_book(st)
    out["improve_book"] = imp_book
    sh = _Shadow(placement)
    native = [dict(a) for a in acts]
    placed = AB.apply_A(acts, imp_book) if placement == "maker_improve" else acts
    sh._record_placement(native, placed, imp_book, st)
    out["rows"] = sh._placement_log
    bad = WC._crossing(placed, imp_book)
    out["crossing_refusal"] = bad
    # the capture must not move a price
    out["prices_unchanged_by_capture"] = (
        [str(a.get("price")) for a in placed]
        == [r["submitted_price"] for r in sh._placement_log])
    return out


def reconstructable(row: Dict[str, Any]) -> Dict[str, Any]:
    """Can native -> transformed -> submitted be reconstructed from ONE row?"""
    need = ("native_price", "submitted_price", "improved", "bid", "ask",
            "from_bid", "from_ask", "leg", "action", "inventory",
            "budget_consumed_before", "placement")
    missing = [k for k in need if k not in row]
    ok = not missing
    if ok:
        n, s = D(row["native_price"]), D(row["submitted_price"])
        moved = (s != n)
        consistent = (moved == bool(row["improved"]))
        ok = consistent
    return {"complete": not missing, "missing": missing,
            "native_to_submitted_consistent": ok,
            "delta": (None if missing
                      else str(D(row["submitted_price"])
                               - D(row["native_price"])))}
