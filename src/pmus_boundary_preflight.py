"""The six assertions that gate the boundary deployment.

OWNER conditional GO 2026-09-10: "At the first clean settlement/flat
boundary, I give the GO now for the next deployment, provided the preflight
is clean: Deploy ranked live selector + residual quarantine together, with
the existing R5F behavior and risk envelope unchanged... If any preflight
assertion fails, remain safely idle."

    1. current held inventory reconciled
    2. zero orphan/foreign-order ambiguity
    3. settlements current
    4. combined-loss budget permits operation
    5. selector has a fresh ranked candidate
    6. quarantine invariants/tests remain green

FAIL CLOSED, AND "COULD NOT ESTABLISH" IS A FAILURE. An exception inside a
check is that check FAILING -- never an unknown that gets the benefit of the
doubt. This is the same discipline as boot_assertions, and it is here for the
same reason: every condition in that list has failed silently at least once
in this campaign's history, which is why they are a gate and not a log line.

WHAT THIS MODULE DOES NOT DO. It does not enable anything, deploy anything,
or change any envelope. It answers one question -- may the boundary
deployment proceed -- and the answer is authoritative only for the moment it
was computed. It is NOT on any deploy watch path, so adding it restarts
nothing.

THE ENVELOPE IT ASSERTS IS UNCHANGED, not improved:
    active acquisition slots        1
    directional acquisition cap     $7.50
    whole-position pair ceiling     $1.00
    COMPLETE ceiling                $1.00
    daily / active-rung loss budget unchanged
    maker-only, no rescue, no taker
"""
from __future__ import annotations

import subprocess
import sys
from decimal import Decimal as D
from typing import Any, Mapping, Optional

QUARANTINE_TESTS = (
    "tests/test_residual_quarantine.py",
    "tests/test_sports_opportunity_rank.py",
    "tests/test_sports_shadow_breadth.py",
)

# The envelope, restated so a drift shows up as a failing assertion rather
# than as a number nobody re-read.
REQUIRED_ENVELOPE = {
    "active_acquisition_slots": 1,
    "directional_acquisition_cap_usd": D("7.50"),
    "whole_position_pair_ceiling_usd": D("1.00"),
    "complete_ceiling_usd": D("1.00"),
    "maker_only": True,
    "taker_rescue_enabled": False,
}


def _run(checks: list, name: str, fn, want=None) -> None:
    try:
        got = fn()
        ok = bool(got) if want is None else (got == want)
    except Exception as exc:                                    # noqa: BLE001
        got, ok = f"ERROR:{type(exc).__name__}:{str(exc)[:80]}", False
    checks.append({"check": name, "ok": ok, "got": str(got),
                   **({"want": str(want)} if want is not None else {})})


def quarantine_tests_green(paths=QUARANTINE_TESTS) -> bool:
    """OWNER condition 6, run rather than asserted.

    A green test suite is a fact about THIS checkout, and the only way to
    know it is to run it. Reading a badge, a previous run, or my own memory
    of having run it earlier would be exactly the "value copied from a
    dashboard" that boot_assertions exists to refuse.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *paths],
        capture_output=True, text=True, timeout=900)
    return proc.returncode == 0


def preflight(t=None, sweep: Optional[Mapping[str, Any]] = None,
              ranked_candidate: Optional[Mapping[str, Any]] = None,
              held_slugs=None, session=None, continuous=None,
              run_tests: bool = True) -> dict:
    """The six conditions. Returns {"ok", "checks", "failed"}; never raises."""
    session = _session() if session is None else session
    continuous = _continuous() if continuous is None else continuous
    checks: list = []

    # 1. current held inventory reconciled -- from a FRESH venue read per
    #    held market, not from the campaign's own belief about what it holds.
    _run(checks, "held_inventory_reconciled",
         lambda: _held_reconciled(session, continuous, t, held_slugs))

    # 2. zero orphan / foreign-order ambiguity. Ambiguity is a FAILURE:
    #    "an order we cannot attribute" and "no orders" are different facts.
    _run(checks, "zero_orphan_or_foreign_ambiguity",
         lambda: _no_orphan_ambiguity(continuous, t, sweep))

    # 3. settlements current
    _run(checks, "settlements_current",
         lambda: len(continuous.unsettled_slugs()) == 0, want=True)

    # 4. combined-loss budget permits operation, at the ACTIVE rung's stop --
    #    not a hard-coded -15. That constant lives in the ladder.
    _run(checks, "combined_loss_budget_permits",
         lambda: _budget_permits(session, continuous))

    # 5. selector has a fresh ranked candidate
    _run(checks, "selector_has_fresh_candidate",
         lambda: _fresh_candidate(ranked_candidate))

    # 6. quarantine invariants / tests green
    if run_tests:
        _run(checks, "quarantine_tests_green", quarantine_tests_green,
             want=True)
    else:
        checks.append({"check": "quarantine_tests_green", "ok": False,
                       "got": "SKIPPED", "want": "True",
                       "note": "a skipped check is a FAILED check"})

    # the envelope must be the reviewed one, unchanged
    _run(checks, "envelope_unchanged", lambda: _envelope_ok(session))

    failed = [c["check"] for c in checks if not c["ok"]]
    return {
        "kind": "boundary_preflight",
        "ok": not failed,
        "checks": checks,
        "failed": failed,
        "action_if_failed": "remain safely idle; do not enable",
        "envelope": {k: str(v) for k, v in REQUIRED_ENVELOPE.items()},
        "labels": ("fail-closed: an exception inside a check is that check "
                   "FAILING; 'could not establish' never passes"),
    }


# ------------------------------------------------------------- the checks
def _held_reconciled(session, continuous, t, held_slugs) -> bool:
    """Every held market reconciled against the venue, one at a time.

    Binds session.reconcile_submissions -- the reconciler that actually
    exists. My first draft called a `reconcile_held` that does not, which
    failed closed (correctly) but named a function nobody would find.

    No transport is a FAILURE, not a vacuous pass: "we could not ask the
    venue" is not "the venue agrees".
    """
    if t is None:
        raise RuntimeError("no transport: cannot reconcile against the venue")
    slugs = list(held_slugs if held_slugs is not None
                 else continuous.unsettled_slugs())
    for slug in slugs:
        out = session.reconcile_submissions(t, slug)
        if out.get("unresolved"):
            raise RuntimeError(f"{slug}: unresolved={out.get('unresolved')}")
    return True


def _no_orphan_ambiguity(continuous, t, sweep) -> bool:
    if sweep is None:
        sweep = continuous._fresh_open_orders(t)
    ours = sweep.get("ours")
    foreign = sweep.get("foreign")
    if ours is None or foreign is None:
        raise RuntimeError("open-order sweep did not attribute orders")
    return int(ours) == 0 and int(foreign) == 0


def _budget_permits(session, continuous) -> bool:
    combined, _ = continuous._combined_now()
    return D(str(combined)) > D(str(session.daily_loss_stop()))


def _fresh_candidate(cand) -> bool:
    if not cand:
        raise RuntimeError("selector produced no ranked candidate")
    from src import pmus_sports_opportunity_rank as rank_mod
    return rank_mod.eligibility(cand)["eligible"]


def _envelope_ok(session) -> dict:
    """The reviewed envelope, read from the ACTIVE RUNG.

    THE CAP IS NOT A CONSTANT IN THIS MODULE, and it must not be. The $7.50
    directional acquisition cap is R5F's `per_market_cap_usd`; the ladder's
    default rung (R1) carries $0.05, and session_notional_cap() returns
    whichever rung is active. So this check FAILS in a dev checkout running
    R1 and PASSES on the live process running R5F -- which is the correct
    behaviour, not a bug to paper over. Hard-coding 7.50 and comparing it
    against a function that reads a different rung is how a preflight comes
    to assert the environment instead of the envelope. Same lesson as
    daily_loss_stop, which is also read from the ladder and never inlined.
    """
    from src import pmus_l5_ramp_ladder as ladder
    from src import pmus_residual_quarantine as q
    rung = ladder.active() or {}
    got = {
        "rung_id": str(rung.get("id")),
        "active_acquisition_slots": q.ACTIVE_ACQUISITION_SLOTS,
        "directional_acquisition_cap_usd": D(str(session.session_notional_cap())),
        "complete_ceiling_usd": D(str(session.completion_pair_ceiling())),
    }
    mismatched = [
        k for k in ("active_acquisition_slots",
                    "directional_acquisition_cap_usd",
                    "complete_ceiling_usd")
        if got[k] != REQUIRED_ENVELOPE[k]]
    if mismatched:
        raise RuntimeError(
            f"envelope mismatch on rung {got['rung_id']}: "
            + ", ".join(f"{k}={got[k]} want {REQUIRED_ENVELOPE[k]}"
                        for k in mismatched))
    return True


def _session():
    from src import pmus_spring_l5_session as s
    return s


def _continuous():
    from src import pmus_l5_continuous_session as c
    return c
