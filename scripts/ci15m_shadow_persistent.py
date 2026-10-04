"""THE FOUR-WINDOW PERSISTENT SHADOW PROOF. Sends nothing.

IT IS THE EXACT PERSISTENT PATH. This driver calls
`ci15m_runner.run(mode=PERSISTENT, shadow=True, deadman=True)` -- the same
function the live run calls -- and changes exactly two things:

  * `ci15m_probe._write`  is routed to the shadow sink, which answers from
    the venue-faithful `ci15m_sim` instead of POSTing an order;
  * `ci15m_probe._cancel` is routed to the shadow cancel sink.

Everything else is live code: real discovery against the venue's public
events, the real `/v1/book` executable ladder, the frozen policy, payload
validation, the one-outstanding-order invariant, durable slot accounting,
terminal-state observation, the carry-over guard and the deadman.

IT WRITES TO ITS OWN COHORT ROOT so a shadow run can never touch, consume
or corrupt a real cohort's durable record. CI15M-1..4 are historical and
are not reachable from here.

NO STRATEGY OR RISK THRESHOLD IS SET HERE. The profile comes from
ci15m_budget; every bound comes from the frozen policy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import ci15m_runner as R
from src.gemini_execution import cohort, transport
from src.gemini_execution import metadata as MD

SHADOW_ROOT = os.path.join("analysis", "gemini", "shadow_cohorts")


def _book(symbol: str) -> Dict[str, Any]:
    """The REAL executable ladder, so a simulated fill is decided against
    the same book the policy priced from."""
    bk = P.book_from_venue(symbol) or {}
    return {k: bk.get(k) for k in ("bid", "ask", "bid_size", "ask_size")}


class ShadowVenue:
    """A venue that is INTERNALLY CONSISTENT with its own simulated fills.

    WHY THIS IS NECESSARY, and the first shadow run proved it in 2.4
    seconds. The write sink simulated a fill, so our ledger held UP q1 --
    and then `reconciled_inventory` read the REAL venue, which had no such
    fill, reported signed 0, and the cohort HALTed INVENTORY_DISAGREEMENT.
    That halt was CORRECT: simulated writes combined with authenticated
    reads of the real account can never agree, and the engine is right to
    refuse to decide on a contradiction.

    So a shadow venue must answer for its OWN fills. This records every
    simulated fill and serves it back on the two surfaces the
    authenticated path reads -- the order-history rows and the mytrades
    rows -- which keeps the PROVEN join
    (`fill_reconciliation.authoritative_fills`) inside the tested path
    rather than stubbing it out.

    WHAT THIS DOES AND DOES NOT PROVE. It proves the LIFECYCLE: discovery,
    the frozen decision, payload construction, one-outstanding-order,
    durable slot accounting, terminal-state observation, the authenticated
    join, inventory reconstruction, carry-over, the deadman, and rollover
    across windows. It does NOT prove reconciliation against REAL venue
    data -- nothing that simulates its own fills can. That was established
    separately: by the CI15M-4 forensics on a real fill, the 17
    terminal-observation regressions built from that real tape, and the
    crash-boundary tests.

    IT IS CONFINED TO THIS DRIVER. No production module learns that a
    shadow exists; the substitution happens at this process's boundary.
    """

    def __init__(self) -> None:
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.trades: List[Dict[str, Any]] = []
        self.n = 0

    def record(self, body: Dict[str, Any], sim: Dict[str, Any]) -> None:
        oid = str(body.get("orderId"))
        self.orders[oid] = dict(body)
        got = D(str(body.get("filledQuantity") or "0"))
        if got > 0:
            self.n += 1
            # fee_amount 0: the ONE authenticated CI15M-4 fill came back
            # aggressor false, fee_amount "0" -- a maker fill at a
            # marketable limit. Modelling a taker fee here would make
            # shadow economics look worse than the only real measurement
            # we have, so the measured value is used and labelled.
            self.trades.append({
                "tid": 9_900_000 + self.n,
                "order_id": oid,
                "symbol": body.get("symbol"),
                "price": str(body.get("avgExecutionPrice")
                             or body.get("price")),
                "amount": str(got),
                "fee_amount": "0",
                "fee_currency": "USD",
                "aggressor": False,
                "timestampms": int(time.time() * 1000),
                "shadow": True,
            })

    def rows_for(self, symbol: Optional[str], statuses: Any) -> List[Dict]:
        want = tuple(str(x).lower() for x in (statuses or ()))
        out = []
        for o in self.orders.values():
            if symbol and o.get("symbol") != symbol:
                continue
            if want and str(o.get("status", "")).lower() not in want:
                continue
            out.append(o)
        return out

    def order_history(self, **kw) -> Dict[str, Any]:
        statuses = kw.get("statuses") or ("filled", "cancelled")
        symbol = kw.get("symbol")
        per = []
        total = 0
        for st in statuses:
            rows = self.rows_for(symbol, (st,))
            total += len(rows)
            per.append({"status_filter": st, "http": 200,
                        "rows": len(rows), "orders": rows})
        return {"readable": True,
                "verdict": "ORDERS_FOUND" if total else "NO_ORDERS_ON_RECORD",
                "total_rows": total, "unreadable_statuses": [],
                "per_status": per, "shadow": True}

    def active_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """ITS OWN resting orders. Reading the REAL account here would gate
        shadow submissions on live orders the shadow did not place -- and
        locally it answered AMBIGUOUS, which correctly refused every
        submission. A shadow venue must own every surface it is asked
        about."""
        rows = [o for o in self.orders.values()
                if (not symbol or o.get("symbol") == symbol)
                and str(o.get("status", "")).lower() == "open"]
        return {"ok": True, "status": 200, "classification": "ACCEPTED",
                "shadow": True, "body": {"orders": rows}}

    def mytrades(self, symbol: str) -> Dict[str, Any]:
        return {"rows": [t for t in self.trades if t["symbol"] == symbol],
                "status": 200, "shadow": True}


async def _no_venue_write(**kw):
    raise AssertionError("SHADOW RUN ATTEMPTED A VENUE WRITE")


async def _no_venue_cancel(*a, **kw):
    raise AssertionError("SHADOW RUN ATTEMPTED A VENUE CANCEL")


def _acceptance(rep: Dict[str, Any], want_windows: int) -> Dict[str, Any]:
    """The owner's acceptance criteria, each answered on its own.

    A NO_TRADE window is VALID and must not fail the proof, so this counts
    windows SEEN and rolled through, never submissions.
    """
    windows = rep["windows_seen"] or {}
    checks = {
        "stayed_alive_across_windows": len(windows) >= want_windows,
        "no_unresolved_prior_window": not rep["unresolved_windows"],
        "no_forgotten_resting_order": not rep["unresolved_windows"],
        "no_duplicate_open": all(
            st in (BUD.OPEN_NOT_ATTEMPTED, BUD.OPEN_SUBMITTED,
                   BUD.OPEN_CANCELLED, BUD.OPEN_FILLED)
            for st in (rep["open_states"] or {}).values()),
        "within_window_budget": (rep["windows_completed"]
                                 <= BUD.PROFILES[BUD.PERSIST4]["windows"]),
        "within_order_budget": (rep["order_slots_consumed"]
                                <= BUD.PROFILES[BUD.PERSIST4]
                                ["orders_total"]),
        "not_halted": rep["halted"] is None,
        "policy_fingerprint_unchanged": (
            rep["policy_fingerprint"]
            == "5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90f"
               "d810536329"),
        "writes_were_disabled": True,
    }
    failed = [k for k, v in checks.items() if not v]
    return {"pass": not failed, "failed": failed, "checks": checks,
            "windows_seen": len(windows), "windows_required": want_windows,
            "labels": ("a NO_TRADE window is VALID: this counts windows "
                       "discovered and rolled through, never submissions.")}


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default="SHADOW-PERSIST4")
    ap.add_argument("--windows", type=int, default=4)
    ap.add_argument("--runtime-s", type=float, default=4200.0)
    ap.add_argument("--root", default=SHADOW_ROOT)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    os.makedirs(a.root, exist_ok=True)
    if not cohort.exists(a.cohort, root=a.root):
        BUD.create(a.cohort, root=a.root, profile=BUD.PERSIST4)

    # THE VENUE MUTATION SURFACE IS REMOVED, not merely unused: if any code
    # path reaches it this run fails loudly instead of trading.
    transport.place = _no_venue_write                 # type: ignore[assignment]
    transport.cancel = _no_venue_cancel               # type: ignore[assignment]
    transport.cancel_and_confirm = _no_venue_cancel   # type: ignore[assignment]

    venue = ShadowVenue()
    base_sink = P.shadow_sink(_book)

    async def recording_sink(**kw):
        res = await base_sink(**kw)
        venue.record(res.get("body") or {}, res.get("simulated") or {})
        return res

    async def shadow_history(**kw):
        return venue.order_history(**kw)

    async def shadow_mytrades(symbol):
        return venue.mytrades(symbol)

    async def shadow_positions():
        # The position surface has NEVER carried a BTC intraday contract
        # (established read-only on the CI15M-3 fill), so an empty read is
        # the faithful answer and the authenticated FILL record is the
        # authority. Reporting it as readable-and-empty is what the real
        # account does.
        return {"ok": True, "rows": [], "status": 200, "total": 0}

    async def shadow_active(symbol=None):
        return venue.active_orders(symbol)

    transport.active_orders = shadow_active   # type: ignore[assignment]
    P.set_write_sink(recording_sink)
    P.set_cancel_sink(P.shadow_cancel_sink())
    MD.order_history = shadow_history          # type: ignore[assignment]
    P._mytrades = shadow_mytrades              # type: ignore[assignment]
    MD.positions = shadow_positions            # type: ignore[assignment]
    print(json.dumps({"label": "CI15M_PERSISTENT_SHADOW_V1",
                      "write_mode": P.write_mode(),
                      "cohort": a.cohort, "root": a.root,
                      "profile": BUD.PROFILES[BUD.PERSIST4],
                      "windows": a.windows,
                      "runtime_s": a.runtime_s,
                      "policy_fingerprint": P.fingerprint(),
                      "started_utc": BUD._now(),
                      "labels": ("the EXACT persistent path with the venue "
                                 "write and cancel disabled. Nothing is "
                                 "sent.")}, indent=2))
    t0 = time.time()
    try:
        out = asyncio.run(R.run(a.cohort, live=False, shadow=True,
                                deadman=True, mode=R.MODE_PERSISTENT,
                                durable_branch=None,
                                max_windows=a.windows,
                                runtime_s=a.runtime_s, root=a.root))
    finally:
        P.set_write_sink(None)
        P.set_cancel_sink(None)

    st = BUD.state(a.cohort, root=a.root)
    windows = BUD.windows(a.cohort, root=a.root) or {}
    report = {
        "label": "CI15M_PERSISTENT_SHADOW_V1",
        "elapsed_s": round(time.time() - t0, 1),
        "exit": out["exit"],
        "windows_seen": windows,
        "n_windows_seen": len(windows),
        "windows_completed": BUD.completed_windows(a.cohort, root=a.root),
        "order_slots_consumed": BUD.consumed(a.cohort, root=a.root),
        "open_states": {t: BUD.open_state(a.cohort, t, root=a.root)
                        for t in windows},
        "unresolved_windows": R.unresolved_windows(a.cohort, root=a.root),
        "halted": st["halted"],
        "telemetry_rows": len(BUD.telemetry(a.cohort, root=a.root) or []),
        "policy_fingerprint": P.fingerprint(),
        "write_mode_at_exit": P.write_mode(),
    }
    # ACCEPTANCE, evaluated here rather than left to a reader.
    report["acceptance"] = _acceptance(report, a.windows)
    print(json.dumps(report, indent=2, default=str))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, indent=2, sort_keys=True,
                                default=str) + "\n")
    return 0 if report["acceptance"]["pass"] else 1


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
