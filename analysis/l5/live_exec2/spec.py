"""LIVE-L5-EXEC-1-X — the EXECUTION-CAPABLE build of LIVE-L5-EXEC-1. DISARMED.

Same experiment. Same one order. Same 0.01 shares. Same $0.0099 maximum
possible loss. What changes is only HOW the single order is bounded and
transmitted:

  (1) the one-submission latch becomes DURABLE, on the mounted volume,
      consumed at the FIRST ACTUAL SUBMISSION ATTEMPT and fail-closed
      thereafter -- so a process restart can no longer hand the experiment
      a second order the way the in-memory latch could; and
  (2) the minimum order mutation path exists: submit and cancel for exactly
      one post-only 0.01-share order, and nothing else.

The spec is NOT retyped. It is DERIVED from EXEC-1's frozen SPEC by applying
`DIFF` to a deep copy, so no economic or behavioural field can drift by
transcription error, and `diff_report()` prints the literal change set. Every
other key is EXEC-1's, byte for byte.

WHY A NEW FINGERPRINT. The content changed, so the hash must change. EXEC-1's
`b1dbe7f1...2e06` stays authorized for the read-only preflight artifact and is
re-verified here; this build carries its own fingerprint and needs its own
owner authorization. Reusing EXEC-1's hash for different code would make the
hash meaningless.

ZERO ARMING BY DEFAULT. The order path exists but cannot be reached: it
requires the owner's four PMUS_EXEC1_* keys AND a durable latch it can
consume. Claude may set neither.
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal as D
from typing import Any, Dict, List

from analysis.l5.live_exec.spec import (EXEC1_FINGERPRINT, MAX_POSSIBLE_LOSS_USD,
                                        VENUE_MAX_PRICE, VENUE_MIN_QTY)
from analysis.l5.live_exec.spec import SPEC as EXEC1_SPEC
from analysis.l5.live_exec.spec import assert_frozen as assert_exec1_frozen

PROBE = "LIVE-L5-EXEC-1-X"

#: where the durable latch lives. The volume `l5-live-probe-state` is mounted
#: here on the credentialed service, so the file survives a restart.
LATCH_DIR = "/state/pmus-ws/exec1x"

#: the ONLY venue mutations this build may make. Anything else is refused by
#: the transport itself, not by a caller's discipline.
ALLOWED_MUTATIONS = (
    ("POST", "/v1/order/preview"),     # read-shaped, but a POST
    ("POST", "/v1/orders"),            # the one submission
    ("POST", "/v1/order/{id}/cancel"),  # cancel our own order
    ("POST", "/v1/orders/open/cancel"),  # market-scoped deadman
)

#: the literal, auditable change set from EXEC-1. Keys are dotted paths into
#: SPEC; each value is the new value. Nothing else differs.
DIFF: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 2,
    "derived_from_probe": "LIVE-L5-EXEC-1",
    "derived_from_fingerprint": EXEC1_FINGERPRINT,
    "experiment_unchanged": True,
    "loss_envelope_unchanged": True,

    "behaviour.no_second_order": "ENFORCED_BY_DURABLE_LATCH",
    "size.may_place_a_second_order_to_repair": False,

    "latch": {
        "kind": "DURABLE_SESSION_SCOPED_ONE_SUBMISSION",
        "dir": LATCH_DIR,
        "scope_key": "PMUS_EXEC1_SESSION_ID",
        "consumed_at": "the first ACTUAL submission ATTEMPT, before the POST",
        "consumed_even_if_the_post_fails": True,
        "atomicity": "O_CREAT|O_EXCL on the volume; the winner is the writer",
        "survives_process_restart": True,
        "survives_container_restart": True,
        "fail_closed_after_consumption": True,
        "claude_may_not_clear_it": True,
        "supersedes": ("the EXEC-1 in-memory OneOrderOnly latch, which was "
                       "PROCESS-SCOPED and so leaned on restartPolicy=NEVER"),
    },

    "mutation_path": {
        "exists": True,
        "allowed": [m[0] + " " + m[1] for m in ALLOWED_MUTATIONS],
        "everything_else_refused_by_transport": True,
        "refusal_is_structural_not_a_flag": True,
        "requires_durable_latch_consumption": True,
        "requires_owner_arming_keys": True,
        "preview_echo_gate": "l5_preview_gate.require_echo",
        "post_mutations_never_retried": True,
        "quantity_hard_equal": str(VENUE_MIN_QTY),
        "price_hard_ceiling": str(VENUE_MAX_PRICE),
        "post_only_flag_always_true": True,
        "taker_refused": True,
    },

    "deadman.mechanism": ("market-scoped cancel-all in a finally block, via "
                          "POST /v1/orders/open/cancel"),

    "does_not_contaminate.exec1_preflight_artifact": (
        "SHA 882aa5647d4d67a4f873a92b455c74f9bd15d807, deployment 163cf6a9, "
        "preserved as the read-only mutation-incapable preflight"),
}


def _apply(spec: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = spec
    for p in parts[:-1]:
        node = node[p]
    node[parts[-1]] = value


def build() -> Dict[str, Any]:
    """EXEC-1's spec with DIFF applied. Never retyped."""
    spec = copy.deepcopy(EXEC1_SPEC)
    for dotted, value in DIFF.items():
        _apply(spec, dotted, value)
    return spec


SPEC: Dict[str, Any] = build()

#: economics and experiment identity that MUST survive the derivation. Checked
#: at import by assert_envelope_preserved(), so a future edit to DIFF that
#: touched the money would fail loudly instead of quietly.
INVARIANTS = (
    ("size.quantity_shares", str(VENUE_MIN_QTY)),
    ("size.MAX_POSSIBLE_LOSS_USD", str(MAX_POSSIBLE_LOSS_USD)),
    ("size.max_entry_price", str(VENUE_MAX_PRICE)),
    ("size.may_place_a_second_order_to_repair", False),
    ("orders.markets_concurrent", 1),
    ("orders.series", "btc-updown-15m"),
    ("orders.max_simultaneous_resting_orders", 1),
    ("orders.max_orders_this_experiment", 1),
    ("orders.maker_intended_post_only", True),
    ("orders.taker_permitted", False),
    ("book.staleness_bound_s", 2.0),
    ("book.bbo_source", "l5_pmus_auth_book.AuthBook"),
    ("behaviour.pair_cost_reservation", None),
    ("accounting.matched_pair_economics", "N/A_SINGLE_LEG_EXPERIMENT"),
    ("experiment_class", "EXECUTION_MEASUREMENT"),
    ("armed_by_default", False),
    ("withdrawal.tte_shutdown_s", 120),
    ("daily_limit.daily_realized_loss_stop_usd", "-1.00"),
)


def _get(spec: Dict[str, Any], dotted: str) -> Any:
    node: Any = spec
    for p in dotted.split("."):
        node = node[p]
    return node


def assert_envelope_preserved(spec: Dict[str, Any] = SPEC) -> None:
    bad: List[str] = []
    for dotted, want in INVARIANTS:
        got = _get(spec, dotted)
        if got != want:
            bad.append("%s: want %r, got %r" % (dotted, want, got))
    if bad:
        raise SystemExit("LIVE-L5-EXEC-1-X changed the EXEC-1 experiment or "
                         "its loss envelope: " + "; ".join(bad))


def diff_report() -> Dict[str, Any]:
    """The exact change set against EXEC-1, computed rather than narrated."""
    added, changed = {}, {}
    for dotted, value in DIFF.items():
        try:
            before = _get(EXEC1_SPEC, dotted)
        except KeyError:
            added[dotted] = value
            continue
        if before != value:
            changed[dotted] = {"exec1": before, "exec1x": value}
    return {"from_probe": "LIVE-L5-EXEC-1",
            "from_fingerprint": EXEC1_FINGERPRINT,
            "to_probe": PROBE, "to_fingerprint": EXEC1X_FINGERPRINT,
            "keys_added": added, "keys_changed": changed,
            "keys_removed": [],
            "max_possible_loss_usd_unchanged": str(MAX_POSSIBLE_LOSS_USD)}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous.
EXEC1X_FINGERPRINT = \
    "d2f322760699f22e50a16975179a484b48c1523e3a667a77579ed6c5dd3a6c79"


def assert_frozen() -> None:
    """Both hashes. EXEC-1 must still be intact for this build to be what it
    claims to be: EXEC-1 plus exactly two changes."""
    assert_exec1_frozen()
    assert_envelope_preserved()
    got = fingerprint()
    if got != EXEC1X_FINGERPRINT:
        raise SystemExit(
            "LIVE-L5-EXEC-1-X spec moved: expected %s, got %s. Re-authorize "
            "rather than running." % (EXEC1X_FINGERPRINT, got))


assert_envelope_preserved()


if __name__ == "__main__":
    print(fingerprint())
