"""LIVE-L5-EXEC-1-XR2 — XR with exactly one behavioural/safety correction.

THE CORRECTION. XR evaluated the frozen TTE placement gate once, at the start
of the run. Time then passed: the book wait, the metadata read, the preview
round trip. A run that was eligible when it began could therefore have POSTed
below the cutoff. Attempt 3 never reached that gap -- it refused at the
initial check with 71.9 s left -- but the gap was real and is now closed.

THE INVARIANT, stated once:

    NO REAL ORDER SUBMISSION MAY OCCUR WHEN TTE <= 120.0 SECONDS.

It is enforced THREE times, as defence in depth and not as a replacement:

  1. at run start, exactly as XR did (unchanged);
  2. immediately BEFORE the durable latch is consumed -- the check that
     protects the latch, since a refusal there costs nothing;
  3. immediately BEFORE POST /v1/orders -- which closes the gap between the
     latch and the wire.

Unknown or unreadable TTE counts as past the boundary at every one of them.

THE OPERATIONAL FIX. XR had to be deployed INTO an already-open window, which
made the experiment depend on a chat worker waking at the right second and on
a redeploy finishing in time. XR2 can be started BEFORE the frozen window and
waits for it. It waits for THAT SLUG AND NO OTHER: there is no discovery, no
substitution and no roll-forward to the next window. A missed window is a
SKIP.

Because the slug must be frozen in advance, XR2 also REFUSES to run with an
empty `PMUS_EXEC1_GATE_EVIDENCE`. XR fell back to "whatever window is current
now", and that fallback is itself a substitution -- the thing this build is
required not to do.

NOTHING ELSE CHANGES. Quantity, the $0.0099 bound, the sha256(slug|session_id)
side rule, offset-0 at the same-side best bid, post-only, the 2.0 s AuthBook
bound, the venue-derived tick, preview/echo, the durable one-submission latch,
no retry or replacement or complement, markouts, lifecycle and the deadman are
all inherited unchanged and re-asserted at every boot.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List

from analysis.l5.live_exec.spec import MAX_POSSIBLE_LOSS_USD
from analysis.l5.live_exec2.runner_spec import (FINGERPRINT_PIN_ENV,
                                                XR_FINGERPRINT, XR_INVARIANTS)
from analysis.l5.live_exec2.runner_spec import SPEC as XR_SPEC
from analysis.l5.live_exec2.runner_spec import assert_frozen as assert_xr_frozen
from analysis.l5.live_exec2.spec import _get
from analysis.l5.live_exec2 import timing as T

PROBE = "LIVE-L5-EXEC-1-XR2"

#: how long XR2 may wait for its frozen window to open, in seconds. A bound,
#: not a schedule: it exists so a container started against a slug that is
#: days away exits instead of idling forever.
MAX_PREOPEN_WAIT_S = 3600.0

DIFF: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 4,
    "derived_from_probe": "LIVE-L5-EXEC-1-XR",
    "derived_from_fingerprint": XR_FINGERPRINT,
    "correction_count": 1,
    "correction": ("the frozen TTE placement gate is re-evaluated "
                   "immediately before durable latch consumption AND "
                   "immediately before POST /v1/orders"),

    "tte_invariant": {
        "statement": ("no real order submission may occur when "
                      "TTE <= 120.0 seconds"),
        "checked_at_run_start": True,
        "checked_before_latch_consumption": True,
        "checked_before_post": True,
        "defence_in_depth_not_a_replacement": True,
        "unknown_tte_counts_as_past_the_boundary": True,
        "stale_tte_counts_as_past_the_boundary": True,
        "refusal_before_latch_does_not_consume_it": True,
        "refusal_after_latch_spends_it_and_sends_nothing": (
            "the safe direction: a spent latch loses a run, a late POST "
            "breaks the envelope"),
        "cutoff_s": T.PLACEMENT_CUTOFF_TTE_S,
    },

    "launch": {
        "may_start_before_the_frozen_window_opens": True,
        "waits_for": "the single frozen slug in PMUS_EXEC1_GATE_EVIDENCE",
        "max_preopen_wait_s": MAX_PREOPEN_WAIT_S,
        "discovery": False,
        "substitution": False,
        "roll_forward_to_next_window": False,
        "empty_gate_evidence": ("REFUSED. XR's fallback to the current "
                                "window was itself a substitution."),
        "missed_window": "SKIP, never the next one",
        "depends_on_a_chat_worker_waking_on_time": False,
        "depends_on_a_redeploy_after_market_open": False,
    },

    "entrypoint": {
        "module": "scripts.l5_exec1xr2_run",
        "executes_the_real_runner": True,
        "is_preflight_only": False,
        "disarmed_behaviour": ("boots early, recognises the frozen future "
                               "slug, waits for it to open, then runs the "
                               "whole path to the DISARMED final refusal"),
        "call_graph_to_submit": [
            "scripts.l5_exec1xr2_run:main",
            "analysis.l5.live_exec3.runner:run_experiment",
            "analysis.l5.live_exec3.runner:_await_window   (pre-open wait)",
            "analysis.l5.live_exec3.runner:_tte_guard      (recheck 2 and 3)",
            "analysis.l5.live_exec2.orders:submit_one(guard=...)",
            "  guard() -> analysis.l5.live_exec2.latch:consume -> guard()",
            "analysis.l5.live_exec2.orders:BoundedOrderTransport.request"
            " -> POST /v1/orders",
        ],
    },
}


def _apply(spec: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = spec
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def build() -> Dict[str, Any]:
    spec = copy.deepcopy(XR_SPEC)
    for dotted, value in DIFF.items():
        _apply(spec, dotted, value)
    return spec


SPEC: Dict[str, Any] = build()

XR2_INVARIANTS = XR_INVARIANTS + (
    ("tte_invariant.checked_at_run_start", True),
    ("tte_invariant.checked_before_latch_consumption", True),
    ("tte_invariant.checked_before_post", True),
    ("tte_invariant.unknown_tte_counts_as_past_the_boundary", True),
    ("tte_invariant.cutoff_s", 120.0),
    ("launch.discovery", False),
    ("launch.substitution", False),
    ("launch.roll_forward_to_next_window", False),
    ("correction_count", 1),
)


def assert_envelope_preserved(spec: Dict[str, Any] = SPEC) -> None:
    bad: List[str] = []
    for dotted, want in XR2_INVARIANTS:
        got = _get(spec, dotted)
        if got != want:
            bad.append("%s: want %r, got %r" % (dotted, want, got))
    if bad:
        raise SystemExit("LIVE-L5-EXEC-1-XR2 changed the experiment or its "
                         "loss envelope: " + "; ".join(bad))


def diff_report() -> Dict[str, Any]:
    added, changed = {}, {}
    for dotted, value in DIFF.items():
        try:
            before = _get(XR_SPEC, dotted)
        except KeyError:
            added[dotted] = value
            continue
        if before != value:
            changed[dotted] = {"xr": before, "xr2": value}
    return {"from_probe": "LIVE-L5-EXEC-1-XR",
            "from_fingerprint": XR_FINGERPRINT,
            "to_probe": PROBE, "to_fingerprint": XR2_FINGERPRINT,
            "keys_added": added, "keys_changed": changed, "keys_removed": [],
            "max_possible_loss_usd_unchanged": str(MAX_POSSIBLE_LOSS_USD)}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous.
XR2_FINGERPRINT = \
    "0f3340195d68a5faf42dca97450e88eef3e7964ce4ca794c6e00baed54c9042f"


def fingerprint_pinned() -> Dict[str, Any]:
    import os
    got = os.getenv(FINGERPRINT_PIN_ENV, "").strip()
    return {"env": FINGERPRINT_PIN_ENV, "set": bool(got),
            "matches_this_build": got == XR2_FINGERPRINT,
            "named": (got[:12] + "…") if got else None,
            "required": XR2_FINGERPRINT}


def assert_frozen() -> None:
    assert_xr_frozen()                  # and through it XR-X and EXEC-1
    assert_envelope_preserved()
    got = fingerprint()
    if got != XR2_FINGERPRINT:
        raise SystemExit(
            "LIVE-L5-EXEC-1-XR2 spec moved: expected %s, got %s. "
            "Re-authorize rather than running." % (XR2_FINGERPRINT, got))


assert_envelope_preserved()


if __name__ == "__main__":
    print(fingerprint())
