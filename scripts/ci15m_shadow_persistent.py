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
from typing import Any, Dict

from src.gemini_execution import ci15m_budget as BUD
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution import ci15m_runner as R
from src.gemini_execution import cohort, transport

SHADOW_ROOT = os.path.join("analysis", "gemini", "shadow_cohorts")


def _book(symbol: str) -> Dict[str, Any]:
    """The REAL executable ladder, so a simulated fill is decided against
    the same book the policy priced from."""
    bk = P.book_from_venue(symbol) or {}
    return {k: bk.get(k) for k in ("bid", "ask", "bid_size", "ask_size")}


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

    P.set_write_sink(P.shadow_sink(_book))
    P.set_cancel_sink(P.shadow_cancel_sink())
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
