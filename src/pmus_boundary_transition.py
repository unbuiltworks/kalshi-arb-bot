"""Automatic clean-boundary transition: flat -> preflight -> activate.

OWNER: "The current cricket position should not require Brett/ChatGPT/Claude
to notice settlement. Build the durable transition mechanism so that when the
existing a88dff63 campaign becomes flat, it automatically runs the
already-approved seven-check preflight and activates ranked selector +
residual quarantine iff all checks pass. Fail closed. No manual owner action."

WHY ACTIVATION IS DURABLE STATE AND NOT A DEPLOY. If enabling the selector
and quarantine required flipping a Railway variable or pushing a commit, the
transition would still need a human awake at the moment of settlement -- which
is the entire thing this module removes. So activation is a record on the
campaign's own durable mount, written by the running process at the boundary
and read on every subsequent cycle. No redeploy, no variable, no owner.

WHAT ACTIVATION DOES AND DOES NOT CHANGE. It turns on the ranked selector and
residual quarantine. It changes NO cap, ceiling, band, or concurrency: the
envelope is asserted by the preflight immediately before activation and
re-asserted here, so a transition that would alter the reviewed risk envelope
cannot complete. Activation buys scheduling behaviour, never risk.

FAIL CLOSED, IN THE ONLY DIRECTION THAT MATTERS. Every uncertainty resolves
to "do not activate":

  * an exception anywhere in detection or preflight -> no activation
  * a campaign that is not flat                     -> no activation
  * an unsettled market                             -> no activation
  * an unattributable order                         -> no activation
  * any failed preflight check                      -> no activation
  * an unreadable or corrupt activation record      -> no activation

"We could not establish that the campaign is flat" is never "the campaign is
flat". The cost of a missed boundary is a later boundary; the cost of a false
one is trading with a position we did not know about.

ONE-SHOT. Activation is a promotion of the engine's configuration, not a
per-market gate. Once recorded it is not re-run, and a residual acquired
afterwards does not un-promote the engine -- that is what quarantine is for.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping, Optional

from src import pmus_boundary_preflight as pf
from src import pmus_residual_quarantine as rq

ACTIVATION_FILE = "boundary_activation.json"

# The two things a transition turns on, named so a reader of the record can
# see exactly what changed without reading this module.
ACTIVATES = ("ranked_selector", "residual_quarantine")

WAITING = "WAITING_FOR_CLEAN_BOUNDARY"
# "The campaign is still holding" and "we could not ask the venue" BOTH
# refuse to activate, but they are not the same fact and must not print the
# same word. Run from a container without venue credentials, every condition
# that needs transport errors, and a single WAITING state would report the
# identical row whether the campaign held a residual or was flat and ready --
# a monitor watching for the boundary would wait forever on a campaign that
# had already reached it. Reported separately so an unattended reader can
# tell "not yet" from "not asked".
UNDETERMINED = "UNDETERMINED_CANNOT_ESTABLISH_BOUNDARY"
REFUSED = "REFUSED_PREFLIGHT"
ACTIVATED = "ACTIVATED"
ALREADY = "ALREADY_ACTIVE"


def activation_path(state_dir=None) -> str:
    if state_dir is None:
        from src import pmus_l5_continuous_session as cs
        state_dir = str(cs.base.q1.base.state_dir())
    return os.path.join(str(state_dir), ACTIVATION_FILE)


def load_activation(path: Optional[str] = None) -> dict:
    """Unreadable or malformed reads as NOT ACTIVE. A corrupt record must
    not be able to enable anything."""
    try:
        with open(path or activation_path()) as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) and doc.get("active") is True else {}
    except Exception:                                       # noqa: BLE001
        return {}


def is_active(path: Optional[str] = None) -> bool:
    return bool(load_activation(path))


def clean_boundary(continuous=None, t=None, sweep=None) -> dict:
    """Flat, settled, and no attributable resting orders.

    Each condition is evaluated independently so a refusal names the reason.
    An exception inside any condition makes that condition FALSE.
    """
    if continuous is None:
        continuous = pf._continuous()
    conds: dict = {}

    def _c(name, fn):
        try:
            conds[name] = bool(fn())
        except Exception as exc:                            # noqa: BLE001
            conds[name] = False
            conds[name + "_error"] = repr(exc)

    # flat: the VENUE's positions, not the campaign's belief about them
    _c("flat_no_held_inventory", lambda: not continuous.held_markets(t))
    _c("settlements_current", lambda: not continuous.unsettled_slugs())
    _c("no_resting_orders",
       lambda: pf._no_orphan_ambiguity(continuous, t, sweep))

    blockers = [k for k, v in conds.items()
                if not k.endswith("_error") and v is False]
    # A condition that RAISED was not evaluated; it is unknown, not false.
    unknown = sorted(k[:-len("_error")] for k in conds if k.endswith("_error"))
    return {"clean": not blockers, "conditions": conds, "blockers": blockers,
            "undetermined": unknown,
            "established": not unknown,
            "refused_because": ("could not establish: " + ", ".join(unknown))
            if unknown else ("not clean: " + ", ".join(blockers))
            if blockers else "clean"}


def attempt_transition(t=None, continuous=None, session=None, sweep=None,
                       ranked_candidate: Optional[Mapping[str, Any]] = None,
                       held_slugs=None, path: Optional[str] = None,
                       run_tests: bool = True, now=None) -> dict:
    """Run the whole transition once. NEVER raises; never partially applies.

    The activation record is written only after the preflight returns ok on
    every check, so there is no state in which the engine is half promoted.
    """
    now = now or time.time
    try:
        path = path or activation_path()
    except Exception as exc:                                # noqa: BLE001
        return _row(WAITING, note=f"no durable state path: {exc!r}")

    if is_active(path):
        return _row(ALREADY, path=path)

    try:
        boundary = clean_boundary(continuous=continuous, t=t, sweep=sweep)
    except Exception as exc:                                # noqa: BLE001
        return _row(WAITING, note=f"boundary check failed closed: {exc!r}")
    if not boundary["clean"]:
        # Unknown outranks not-yet: if anything could not be established, say
        # so, rather than reporting the campaign's state as though we read it.
        state = WAITING if boundary.get("established") else UNDETERMINED
        return _row(state, boundary=boundary)

    try:
        result = pf.preflight(t=t, sweep=sweep,
                              ranked_candidate=ranked_candidate,
                              held_slugs=held_slugs, session=session,
                              continuous=continuous, run_tests=run_tests)
    except Exception as exc:                                # noqa: BLE001
        # preflight() documents that it never raises; if it ever does, that
        # is a failure to activate, not a reason to skip the gate.
        return _row(REFUSED, note=f"preflight raised: {exc!r}",
                    boundary=boundary)

    if not result.get("ok"):
        return _row(REFUSED, boundary=boundary, preflight=result,
                    failed=result.get("failed"))

    record = {
        "active": True,
        "activated_at": now(),
        "activates": list(ACTIVATES),
        "envelope": {k: str(v) for k, v in pf.REQUIRED_ENVELOPE.items()},
        "acquisition_slots": rq.ACTIVE_ACQUISITION_SLOTS,
        "preflight": result,
        "boundary": boundary,
    }
    if not _write(path, record):
        # Could not persist -> do NOT report activation. An activation that
        # does not survive a restart is worse than none: the engine would
        # promote itself and silently demote on the next boot.
        return _row(REFUSED, note="activation record could not be persisted",
                    boundary=boundary, preflight=result)
    return _row(ACTIVATED, path=path, record=record, preflight=result)


def _write(path: str, record: Mapping[str, Any]) -> bool:
    """Atomic: a torn write must not read back as an active record."""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(record, fh, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except Exception:                                       # noqa: BLE001
        return False


def _row(transition: str, **kw) -> dict:
    row = {"kind": "boundary_transition", "transition": transition,
           "activates": list(ACTIVATES),
           "labels": ("fail-closed: anything not established refuses to "
                      "activate; activation changes scheduling only, never "
                      "a cap, ceiling, band, or concurrency")}
    row.update(kw)
    return row


def activation_for_campaign(path: Optional[str] = None) -> dict:
    """What a running campaign should read to know it has been promoted.

    THE CONSUMPTION SIDE OF THE AUTOMATIC PATH. The transition writes a
    record; the campaign asks this on each cycle whether that record exists
    and what it turns on. Kept here rather than inside the campaign so the
    campaign's own module -- which is on the live probe's deploy watch path
    -- does not have to change to gain the ability to read it: the probe
    imports one function at the boundary, when it is flat and a restart
    costs nothing.

    Returns a plain, fully-defaulted answer. A missing or unreadable record
    is NOT an activation, so a campaign that cannot read its own state keeps
    running exactly as it does today.
    """
    doc = load_activation(path)
    return {
        "active": bool(doc),
        "ranked_selector": bool(doc) and "ranked_selector" in (
            doc.get("activates") or []),
        "residual_quarantine": bool(doc) and "residual_quarantine" in (
            doc.get("activates") or []),
        "activated_at": doc.get("activated_at") if doc else None,
        "envelope": doc.get("envelope") if doc else None,
        "acquisition_slots": (doc.get("acquisition_slots") if doc
                              else rq.ACTIVE_ACQUISITION_SLOTS),
        "labels": ("a missing or unreadable record is NOT an activation; the "
                   "campaign keeps running exactly as it does today"),
    }


# ------------------------------------------------------------- the runner
PREFIX = "PMUS_BOUNDARY_TRANSITION "
CYCLE_S = float(os.getenv("PMUS_BOUNDARY_CYCLE_S", "60"))


def run(cycles: Optional[int] = None, sleep=time.sleep) -> int:
    """Poll for the clean boundary until it happens, then transition ONCE.

    WHY THIS IS A SEPARATE PROCESS. The transition has to fire on a campaign
    that is currently HOLDING, and wiring the detector into that campaign
    would mean redeploying it mid-hold -- restarting the very process whose
    position we are trying not to disturb, to install a feature that only
    matters once the position is gone. So detection, the preflight, and the
    activation record live out here, where they cost the live campaign
    nothing and can watch it for as long as it takes.

    It writes ONE durable record and stops transitioning. It does not cancel,
    complete, settle, or reprice anything, and it holds no credentials for
    doing so.
    """
    n = 0
    while cycles is None or n < cycles:
        n += 1
        try:
            row = attempt_transition()
        except Exception as exc:                            # noqa: BLE001
            row = _row(WAITING, note=f"cycle failed closed: {exc!r}")
        row["cycle"] = n
        print(PREFIX + json.dumps(row, separators=(",", ":"), default=str),
              flush=True)
        if row.get("transition") in (ACTIVATED, ALREADY):
            # The decision is made and durable. Keep the process alive so the
            # service does not read as CRASHED, but stop re-deciding.
            while cycles is None or n < cycles:
                n += 1
                sleep(CYCLE_S)
            return 0
        sleep(CYCLE_S)
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(run())
