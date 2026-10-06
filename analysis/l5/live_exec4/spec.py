"""LIVE-L5-EXEC-1-XR3 — XR2's experiment, reached in seconds instead of
90 minutes. BUILT AND TESTED, NOT DEPLOYED, NOT AUTHORIZED.

ONE CHANGE, AND IT IS NOT A TRADING CHANGE: how an authorized experiment
reaches an already-running process.

XR2 proved its own scheduler -- the pre-open wait held 2450 s over 490 polls
and entered eligibility exactly at window open. What it could not fix is
getting the container started: Railway applies environment variables at
deployment, so every authorization cost a redeploy, observed at 13, 25 and
54 minutes against a 13-minute placement window. Attempt 4 died of exactly
that, 39.6 minutes after its window had closed.

So XR3 stops using the deploy pipeline as a scheduler. The service stays up,
DISARMED, with AuthBook connected, and polls for an owner-signed
authorization document. Authorization to eligibility becomes one poll
interval.

WHAT DOES NOT CHANGE, and is re-asserted at every boot and before every
experiment: 0.01 shares, $0.0099, the sha256(slug|session_id) side rule,
offset 0 at the same-side best bid, post-only with taker refused, AuthBook
only at <= 2.0 s, venue-derived tick and minimumTradeQty, preview/echo, the
durable one-submission latch, all three TTE checks with the 120 s cutoff, one
order, no retry, no replacement, no complement, no next-market selection,
and the deadman.

THE HONEST COST. The order path now exists continuously rather than for ~70
seconds per attempt. That is the real trade in this design and it is stated
here rather than buried: see THREAT_MODEL in EXEC_SUPERVISOR_PROPOSAL.md.
The mitigations are structural -- the idle loop constructs no order
transport, every submission needs a fresh signed, unexpired, unconsumed
document, and the authorization ledger and submission latch are separate
durable files on the volume.

Claude may not author an authorization: the service holds only the owner's
PUBLIC key, and that key is inside this spec and therefore inside the
fingerprint.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List

from analysis.l5.live_exec.spec import MAX_POSSIBLE_LOSS_USD, VENUE_MIN_QTY
from analysis.l5.live_exec2.spec import _get
from analysis.l5.live_exec3.spec import SPEC as XR2_SPEC
from analysis.l5.live_exec3.spec import (XR2_FINGERPRINT, XR2_INVARIANTS,
                                         assert_frozen as assert_xr2_frozen)

PROBE = "LIVE-L5-EXEC-1-XR3"

#: where the authorization document is fetched from. Outbound only: no
#: inbound port is opened on a credentialed, order-capable service.
AUTHZ_URL_ENV = "PMUS_EXEC1_AUTHZ_URL"

#: the owner's Ed25519 PUBLIC key, base64. Placeholder until the owner
#: supplies one; the supervisor refuses to arm while it is the placeholder,
#: and supplying the real key CHANGES THIS FINGERPRINT -- which is the
#: point: the key is part of what gets authorized.
OWNER_PUBLIC_KEY_B64 = "OWNER_PUBLIC_KEY_NOT_YET_SUPPLIED"

#: idle poll cadence, seconds
POLL_INTERVAL_S = 5.0

#: durable ledgers on the mounted volume. DELIBERATELY two directories:
#: an authorization is not a submission.
AUTHZ_LEDGER_DIR = "/state/pmus-ws/exec1xr3_authz"
SUBMISSION_LATCH_DIR = "/state/pmus-ws/exec1x"     # unchanged, live_exec2

#: bounds on a long-lived process, so "continuously order-capable" is not
#: also "unboundedly order-capable"
MAX_EXPERIMENTS_PER_PROCESS = 4
MAX_EXPERIMENTS_PER_UTC_DAY = 4

DIFF: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 5,
    "derived_from_probe": "LIVE-L5-EXEC-1-XR2",
    "derived_from_fingerprint": XR2_FINGERPRINT,
    # correction_count stays 1: XR2's TTE recheck is still in force and is
    # still the only behavioural correction in this lineage. XR3 adds no
    # second one -- its change is delivery, not behaviour.
    "trading_behaviour_changed": False,
    "risk_limits_changed": False,
    "execution_rules_changed": False,
    "change": ("delivery of owner authorization to an already-running "
               "DISARMED process; nothing about what is traded"),

    "supervisor": {
        "process_lifetime": "CONTINUOUS",
        "idle_state": "DISARMED",
        "idle_holds_an_order_transport": False,
        "authbook_connected_while_idle": True,
        "poll_interval_s": POLL_INTERVAL_S,
        "authorization_source": "outbound HTTPS GET of a signed document",
        "authorization_url_env": AUTHZ_URL_ENV,
        "inbound_port": None,
        "inbound_listener": False,
        "max_experiments_per_process": MAX_EXPERIMENTS_PER_PROCESS,
        "max_experiments_per_utc_day": MAX_EXPERIMENTS_PER_UTC_DAY,
        "redeploy_required_per_authorization": False,
        "env_vars_as_runtime_scheduler": False,
    },

    "authorization": {
        "mechanism": "detached Ed25519 over canonical JSON",
        "owner_public_key_b64": OWNER_PUBLIC_KEY_B64,
        "private_key_location": "the owner only; never the service, repo "
                                "or any Claude session",
        "claude_may_author_one": False,
        "claude_may_set_the_url": True,
        "claude_may_sign": False,
        "signed_fields": ["probe", "spec_fingerprint", "authorization_id",
                          "session_id", "market_slug", "quantity_shares",
                          "max_possible_loss_usd", "not_before_unix",
                          "not_after_unix", "max_orders"],
        "forbidden_fields": ["side", "outcome_side", "price",
                             "offset_ticks", "pair_cost_reservation",
                             "tte_cutoff_s", "staleness_bound_s"],
        "side_is_derived_not_carried": True,
        "economics_must_equal_the_frozen_spec": True,
        "economics_mismatch": "REFUSED, never obeyed",
        "validity_window_required": True,
        "replay": "REFUSED via a durable single-use nonce ledger",
    },

    "two_ledgers": {
        "authorization_ledger_dir": AUTHZ_LEDGER_DIR,
        "submission_latch_dir": SUBMISSION_LATCH_DIR,
        "they_are_separate_files": True,
        "why": ("an authorization can be consumed with no submission ever "
                "attempted (gates refuse, window missed), and in that case "
                "the submission latch must stay unspent so the evidence "
                "still says no order was attempted. Neither fact may be "
                "inferred from the other."),
        "authorization_consumed_when": "before the experiment begins",
        "submission_latch_consumed_when": ("immediately before the real "
                                           "POST, unchanged from XR2"),
        "a_spent_latch_does_not_free_its_authorization": True,
        "a_consumed_authorization_does_not_spend_a_latch": True,
    },

    "entrypoint": {
        "module": "scripts.l5_exec1xr3_supervise",
        "executes_the_real_runner": True,
        "is_preflight_only": False,
        "disarmed_behaviour": ("stays up DISARMED with AuthBook connected, "
                               "polling for a signed authorization; with no "
                               "valid document it never constructs an order "
                               "transport"),
        "call_graph_to_submit": [
            "scripts.l5_exec1xr3_supervise:main",
            "analysis.l5.live_exec4.supervisor:serve_forever",
            "analysis.l5.live_exec4.authz:verify            (signature, "
            "fingerprint, economics, validity, nonce)",
            "analysis.l5.live_exec4.authz:consume_authorization",
            "analysis.l5.live_exec3.runner:run_experiment   (XR2, unchanged)",
            "  _await_window -> _tte_guard -> orders.submit_one(guard=...)",
            "  guard() -> live_exec2.latch:consume -> guard()",
            "analysis.l5.live_exec2.orders -> POST /v1/orders",
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
    spec = copy.deepcopy(XR2_SPEC)
    for dotted, value in DIFF.items():
        _apply(spec, dotted, value)
    return spec


SPEC: Dict[str, Any] = build()

XR3_INVARIANTS = XR2_INVARIANTS + (
    ("trading_behaviour_changed", False),
    ("risk_limits_changed", False),
    ("execution_rules_changed", False),
    ("supervisor.idle_state", "DISARMED"),
    ("supervisor.idle_holds_an_order_transport", False),
    ("supervisor.inbound_listener", False),
    ("supervisor.inbound_port", None),
    ("authorization.claude_may_author_one", False),
    ("authorization.claude_may_sign", False),
    ("authorization.side_is_derived_not_carried", True),
    ("authorization.economics_mismatch", "REFUSED, never obeyed"),
    ("two_ledgers.they_are_separate_files", True),
    ("two_ledgers.a_spent_latch_does_not_free_its_authorization", True),
    ("two_ledgers.a_consumed_authorization_does_not_spend_a_latch", True),
)


def assert_envelope_preserved(spec: Dict[str, Any] = SPEC) -> None:
    bad: List[str] = []
    for dotted, want in XR3_INVARIANTS:
        got = _get(spec, dotted)
        if got != want:
            bad.append("%s: want %r, got %r" % (dotted, want, got))
    if bad:
        raise SystemExit("LIVE-L5-EXEC-1-XR3 changed the experiment or its "
                         "loss envelope: " + "; ".join(bad))


def owner_key_supplied() -> bool:
    return OWNER_PUBLIC_KEY_B64 != "OWNER_PUBLIC_KEY_NOT_YET_SUPPLIED"


def diff_report() -> Dict[str, Any]:
    added, changed = {}, {}
    for dotted, value in DIFF.items():
        try:
            before = _get(XR2_SPEC, dotted)
        except KeyError:
            added[dotted] = value
            continue
        if before != value:
            changed[dotted] = {"xr2": before, "xr3": value}
    return {"from_probe": "LIVE-L5-EXEC-1-XR2",
            "from_fingerprint": XR2_FINGERPRINT,
            "to_probe": PROBE, "to_fingerprint": XR3_FINGERPRINT,
            "keys_added": added, "keys_changed": changed, "keys_removed": [],
            "max_possible_loss_usd_unchanged": str(MAX_POSSIBLE_LOSS_USD),
            "quantity_unchanged": str(VENUE_MIN_QTY)}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous. This value is
#: for the PLACEHOLDER owner key; supplying the real key changes it, and the
#: owner authorizes the fingerprint that carries their own key.
XR3_FINGERPRINT = \
    "6e7f5aedc5125e3bf4461f61267b16fb81e98397c2d5bb56dc7cde00118199a4"


def assert_frozen() -> None:
    assert_xr2_frozen()                 # and through it XR, XR-X, EXEC-1
    assert_envelope_preserved()
    got = fingerprint()
    if got != XR3_FINGERPRINT:
        raise SystemExit(
            "LIVE-L5-EXEC-1-XR3 spec moved: expected %s, got %s. "
            "Re-authorize rather than running." % (XR3_FINGERPRINT, got))


assert_envelope_preserved()


if __name__ == "__main__":
    print(fingerprint())
