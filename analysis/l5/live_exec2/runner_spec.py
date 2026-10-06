"""LIVE-L5-EXEC-1-XR — the EXEC-1-X components plus the runner that uses them.

EXEC-1-X was an order-capable library whose only entrypoint was a boot
preflight, so Attempt 2 was an IMPLEMENTATION SKIP: zero observations, not a
no-fill. This spec adds the three things that were missing and nothing else:

  (1) a frozen deterministic SIDE RULE, fixed before any window is observed;
  (2) TIMING written out so "TTE 120s" has exactly one reading; and
  (3) the ARMING INSTALL mechanism -- a fifth owner-set key that binds the
      four authorized values to THIS fingerprint.

Derived, not retyped: EXEC-1-X's SPEC is deep-copied and a literal DIFF
applied, and EXEC-1-X in turn derives from EXEC-1, so the $0.0099 envelope
is inherited through two derivations and re-asserted at every boot.

THE QUESTION IS UNCHANGED: does one minimum-size near-touch passive order
rest and fill? No predictive model was added. The side rule is a hash
precisely so that no forecast enters.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List

from analysis.l5.live_exec.spec import (EXEC1_FINGERPRINT,
                                        MAX_POSSIBLE_LOSS_USD,
                                        VENUE_MAX_PRICE, VENUE_MIN_QTY)
from analysis.l5.live_exec2 import sides as S
from analysis.l5.live_exec2 import timing as T
from analysis.l5.live_exec2.spec import EXEC1X_FINGERPRINT, INVARIANTS
from analysis.l5.live_exec2.spec import SPEC as EXEC1X_SPEC
from analysis.l5.live_exec2.spec import _get, assert_frozen as assert_x_frozen

PROBE = "LIVE-L5-EXEC-1-XR"

#: the fifth key. It is NOT a second arming switch: the four PMUS_EXEC1_*
#: values still do the arming. This one makes them INERT unless the owner
#: also names the fingerprint they are authorizing, so an arming set left
#: over from an earlier authorization cannot silently arm a later build.
FINGERPRINT_PIN_ENV = "PMUS_EXEC1_AUTHORIZED_FINGERPRINT"

DIFF: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 3,
    "derived_from_probe": "LIVE-L5-EXEC-1-X",
    "derived_from_fingerprint": EXEC1X_FINGERPRINT,
    "root_probe": "LIVE-L5-EXEC-1",
    "root_fingerprint": EXEC1_FINGERPRINT,
    "experiment_unchanged": True,
    "loss_envelope_unchanged": True,
    "question_unchanged": True,
    "predictive_model_added": False,

    "entrypoint": {
        "module": "scripts.l5_exec1xr_run",
        "executes_the_real_runner": True,
        "is_preflight_only": False,
        "disarmed_behaviour": ("runs the WHOLE runner to the final pre-POST "
                               "refusal and reports where it stopped"),
        "call_graph_to_submit": [
            "scripts.l5_exec1xr_run:main",
            "analysis.l5.live_exec2.runner:run_experiment",
            "analysis.l5.live_exec2.runner:_place",
            "analysis.l5.live_exec2.orders:submit_one",
            "analysis.l5.live_exec2.latch:consume",
            "analysis.l5.live_exec2.orders:BoundedOrderTransport.request"
            " -> POST /v1/orders",
        ],
    },

    "side_rule": {
        "kind": "FROZEN_DETERMINISTIC_HASH",
        "rule": S.RULE,
        "inputs": ["market slug", "PMUS_EXEC1_SESSION_ID"],
        "depends_on_price": False,
        "decided_before_the_window_is_observed": True,
        "is_a_forecast": False,
        "why": ("a runner that picks its side after watching the book turns "
                "one observation into an unfalsifiable one"),
        "up_token": S.UP, "down_token": S.DOWN,
    },

    "placement_rule": {
        "offset_ticks_from_touch": 0,
        "resting_at": "the current SAME-SIDE best bid",
        "never_crosses": True,
        "crossing_refused_locally": "WOULD_CROSS before any network call",
        "quantity_shares": str(VENUE_MIN_QTY),
        "price_ceiling": str(VENUE_MAX_PRICE),
        "provenance": ("the single most strongly evidenced L5 behaviour: "
                       "median offset 0 ticks from the touch"),
    },

    "timing_explicit": {
        "window_len_s": T.WINDOW_LEN_S,
        "tte_shutdown_s": T.TTE_SHUTDOWN_S,
        "placement_allowed_while_tte_gt_s": T.PLACEMENT_CUTOFF_TTE_S,
        "placement_after_cutoff": "REFUSED, never a later worse order",
        "withdraw_required_at_tte_le_s": T.WITHDRAW_AT_TTE_S,
        "open_at_boundary": "CANCELLED, never left to ride to settlement",
        "unknown_tte": "treated as past the boundary: withdraw, never place",
    },

    "arming_install": {
        "claude_may_not_set_any_of_them": True,
        "owner_sets": ["PMUS_EXEC1_LIVE", "PMUS_EXEC1_SESSION_ID",
                       "PMUS_EXEC1_ARM", "PMUS_EXEC1_GATE_EVIDENCE",
                       FINGERPRINT_PIN_ENV],
        "fingerprint_pin_env": FINGERPRINT_PIN_ENV,
        "mechanism": ("the four values arm nothing unless %s equals THIS "
                      "fingerprint exactly. An arming set authorized for an "
                      "earlier build is therefore inert here."
                      % FINGERPRINT_PIN_ENV),
        "resolves_contradiction": (
            "EXEC-1 asserted claude_may_not_set_these=True and a later "
            "instruction asked Claude to set them. That is not overridden: "
            "Claude still sets none of the five. What changes is that the "
            "owner's values are now bound to a named fingerprint, so "
            "installing them is an act that names what it authorizes."),
    },

    "cancellation": {
        "available_after_latch_consumption": True,
        "requires_live_arming": False,
        "why_not": ("refusing to cancel because a flag is unset is the "
                    "dangerous direction"),
        "may_submit_a_replacement": False,
    },

    "markouts": {
        "horizons_s": [1, 5, 10, 30, 60],
        "source": "authenticated AuthBook only",
        "missing_stays_missing": True,
        "interpolation": False,
        "settlement_markout": "reported only when settlement is observed",
    },

    "one_submission": {
        "bound_by": "the EXEC-1-X durable volume latch",
        "consumed_immediately_before_the_real_post": True,
        "never_a_second_submission_after": [
            "timeout", "exception", "unknown response", "rejection",
            "partial fill", "cancellation", "process restart"],
    },
}


def _apply(spec: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = spec
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def build() -> Dict[str, Any]:
    spec = copy.deepcopy(EXEC1X_SPEC)
    for dotted, value in DIFF.items():
        _apply(spec, dotted, value)
    return spec


SPEC: Dict[str, Any] = build()

#: everything EXEC-1-X already guaranteed, plus the runner's own bounds
XR_INVARIANTS = INVARIANTS + (
    ("placement_rule.offset_ticks_from_touch", 0),
    ("placement_rule.never_crosses", True),
    ("placement_rule.quantity_shares", str(VENUE_MIN_QTY)),
    ("side_rule.depends_on_price", False),
    ("side_rule.is_a_forecast", False),
    ("timing_explicit.tte_shutdown_s", 120.0),
    ("timing_explicit.placement_after_cutoff",
     "REFUSED, never a later worse order"),
    ("arming_install.claude_may_not_set_any_of_them", True),
    ("cancellation.may_submit_a_replacement", False),
    ("markouts.interpolation", False),
    ("markouts.missing_stays_missing", True),
    ("entrypoint.is_preflight_only", False),
    ("predictive_model_added", False),
)


def assert_envelope_preserved(spec: Dict[str, Any] = SPEC) -> None:
    bad: List[str] = []
    for dotted, want in XR_INVARIANTS:
        got = _get(spec, dotted)
        if got != want:
            bad.append("%s: want %r, got %r" % (dotted, want, got))
    if bad:
        raise SystemExit("LIVE-L5-EXEC-1-XR changed the experiment or its "
                         "loss envelope: " + "; ".join(bad))


def diff_report() -> Dict[str, Any]:
    added, changed = {}, {}
    for dotted, value in DIFF.items():
        try:
            before = _get(EXEC1X_SPEC, dotted)
        except KeyError:
            added[dotted] = value
            continue
        if before != value:
            changed[dotted] = {"exec1x": before, "exec1xr": value}
    return {"from_probe": "LIVE-L5-EXEC-1-X",
            "from_fingerprint": EXEC1X_FINGERPRINT,
            "to_probe": PROBE, "to_fingerprint": XR_FINGERPRINT,
            "root_fingerprint": EXEC1_FINGERPRINT,
            "keys_added": added, "keys_changed": changed, "keys_removed": [],
            "max_possible_loss_usd_unchanged": str(MAX_POSSIBLE_LOSS_USD)}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous.
XR_FINGERPRINT = \
    "f3e54d8225db2d9bc626ddbc285e347186f2ca9511543a8ff9b86f87359bc5e9"


def fingerprint_pinned() -> Dict[str, Any]:
    """The owner's fifth key must name THIS fingerprint."""
    import os
    got = os.getenv(FINGERPRINT_PIN_ENV, "").strip()
    return {"env": FINGERPRINT_PIN_ENV, "set": bool(got),
            "matches_this_build": got == XR_FINGERPRINT,
            "named": (got[:12] + "…") if got else None,
            "required": XR_FINGERPRINT}


def assert_frozen() -> None:
    assert_x_frozen()                     # and, through it, EXEC-1
    assert_envelope_preserved()
    got = fingerprint()
    if got != XR_FINGERPRINT:
        raise SystemExit(
            "LIVE-L5-EXEC-1-XR spec moved: expected %s, got %s. Re-authorize "
            "rather than running." % (XR_FINGERPRINT, got))


assert_envelope_preserved()


if __name__ == "__main__":
    print(fingerprint())
