"""LIVE-L5-EXEC-1-XR2W — XR2 with exactly one wire-serialization correction.

THE CORRECTION. Attempt 5 passed every frozen gate on live in-window data
and was then rejected by `POST /v1/order/preview` with HTTP 400, because
EXEC-1-X hand-rolled its own order body instead of using
`src.l5_order_body` -- the serializer that has actually traded on this
venue, and which every other PMUS order path in this repository already
delegates to. Three fields drifted:

  1. `price` carried no `currency`           (proven sends `"USD"`);
  2. `quantity` was a JSON STRING `"0.01"`   (the venue documents
     number<double>, and fractional quantity depends on it);
  3. the preview request was the BARE BODY, not the `{"request": ...}`
     envelope that `POST /v1/order/preview` takes.

`src.l5_order_body`'s own docstring records that this exact class of bug
already happened once, when `l5_pmus_transport` wrote its own body and
"came to send outcome/side/LIMIT and a bare-string price, none of which
the venue accepts". The fix is therefore not to repair the copy but to
DELETE it: `analysis.l5.live_exec2.orders.order_body` now delegates.

THE 1-X MAPPING WAS NEVER WRONG. A5 sent wire price 0.51 for a semantic
DOWN order at 0.49, which is exactly what the proven serializer sends.
The complement ambiguity is not the defect and is now pinned by fixture.

TWO DIAGNOSTIC CORRECTIONS COME WITH IT, neither of which changes what is
traded:

  * the venue's HTTP error is captured in full -- status, response body
    and an ALLOWLIST of safe response headers. A5's record held only
    "HTTP Error 400: Bad Request" because `urllib` raises before the
    caller reads the body. Request-header values are never recorded.
  * the lifecycle classifier distinguishes PREVIEW_REJECTED from
    AMBIGUOUS_SUBMISSION by EXCEPTION TYPE rather than by reading the
    latch. A preview is a what-if that creates nothing and runs strictly
    before latch consumption; only a failure at or after
    `POST /v1/orders` is genuinely ambiguous. An exception that names
    neither phase is reported as UNCLASSIFIED, never resolved by guess.

NOTHING ELSE CHANGES. Quantity 0.01, the $0.0099 bound, the
sha256(slug|session_id) side rule, offset-0 at the same-side best bid,
post-only, the 2.0 s AuthBook bound, the venue-derived tick, the
preview/echo requirement, the durable one-submission latch, the three TTE
checks, the pre-open wait, no retry/replacement/complement, markouts,
lifecycle and the deadman are inherited unchanged and re-asserted at
every boot.

SUPERSESSION. Because the defect was in SHARED code
(`analysis/l5/live_exec2/orders.py`), fixing it means the earlier
fingerprints no longer describe the code that would run. EXEC-1-X
`d2f32276…6c79`, XR `f3e54d82…c5e9` and XR2 `0f334019…042f` are
SUPERSEDED and must not be re-armed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List

from analysis.l5.live_exec.spec import MAX_POSSIBLE_LOSS_USD
from analysis.l5.live_exec2.runner_spec import FINGERPRINT_PIN_ENV
from analysis.l5.live_exec2.spec import _get
from analysis.l5.live_exec3.spec import (XR2_FINGERPRINT, XR2_INVARIANTS,
                                         MAX_PREOPEN_WAIT_S)
from analysis.l5.live_exec3.spec import SPEC as XR2_SPEC
from analysis.l5.live_exec3.spec import assert_frozen as assert_xr2_frozen

PROBE = "LIVE-L5-EXEC-1-XR2W"

DIFF: Dict[str, Any] = {
    "probe": PROBE,
    "spec_version": 5,
    "derived_from_probe": "LIVE-L5-EXEC-1-XR2",
    "derived_from_fingerprint": XR2_FINGERPRINT,
    "correction_count": 2,
    "correction": ("the order body and the preview envelope are DELEGATED "
                   "to src.l5_order_body, the proven venue serializer, "
                   "instead of being hand-rolled"),

    "wire_serialization": {
        "serializer": "src.l5_order_body",
        "hand_rolled_body_deleted": True,
        "delegates_build": True,
        "delegates_preview_envelope": True,
        "price_carries_currency_usd": True,
        "quantity_is_a_json_number": True,
        "preview_takes_the_request_envelope": True,
        "no_side_price_field_is_invented_locally": True,
        "complement_mapping_unchanged": (
            "price.value is ALWAYS the long (YES) side's price. A NO order "
            "at NO price X goes on the wire as 1 - X. A5 already did this "
            "correctly; it is pinned by fixture, not re-derived."),
        "a5_root_cause": [
            "price.currency absent",
            "quantity sent as a JSON string",
            "preview request sent as the bare body, not {'request': body}",
        ],
        "a5_root_cause_class": (
            "a duplicate serializer. EXEC-1-X was the only PMUS order path "
            "in the repository that did not delegate to the proven one."),
    },

    "venue_error_capture": {
        "captures_status": True,
        "captures_response_body": True,
        "captures_response_headers": "ALLOWLIST_ONLY",
        "request_header_values_recorded": False,
        "request_header_names_recorded": True,
        "secrets_or_authentication_material_recorded": False,
        "why": ("A5's record held only 'HTTP Error 400: Bad Request' "
                "because urllib raises before the caller reads the body, "
                "so the venue's own account of what it rejected was lost."),
    },

    "lifecycle_classification": {
        "preview_failure_is": "PREVIEW_REJECTED",
        "submission_failure_is": "AMBIGUOUS_SUBMISSION",
        "unknown_phase_is": "UNCLASSIFIED_SUBMISSION_FAILURE",
        "determined_by": "EXCEPTION_TYPE",
        "determined_by_reading_the_latch": False,
        "preview_creates_nothing_at_the_venue": True,
        "preview_runs_strictly_before_latch_consumption": True,
        "true_ambiguity_requires_crossing_the_order_boundary": True,
        "unknown_phase_is_never_resolved_by_guess": True,
    },

    # Inherited verbatim from XR2 except for the two names that moved, so
    # the spec cannot claim an entrypoint that no longer exists.
    "entrypoint.module": "scripts.l5_exec1xr2w_run",
    "entrypoint.call_graph_to_submit": [
        "scripts.l5_exec1xr2w_run:main",
        "analysis.l5.live_exec3.runner:run_experiment",
        "analysis.l5.live_exec3.runner:_await_window   (pre-open wait)",
        "analysis.l5.live_exec3.runner:_tte_guard      (recheck 2 and 3)",
        "analysis.l5.live_exec2.orders:submit_one(guard=...)",
        "  orders:order_body -> src.l5_order_body:build",
        "  orders:preview_request -> src.l5_order_body:preview_envelope",
        "  POST /v1/order/preview {'request': body}  (creates nothing)",
        "  guard() -> analysis.l5.live_exec2.latch:consume -> guard()",
        "analysis.l5.live_exec2.orders:BoundedOrderTransport.request"
        " -> POST /v1/orders",
    ],

    "fingerprint_pin": {
        "env": "PMUS_EXEC1_AUTHORIZED_FINGERPRINT",
        "runner_verifies": "ITS_OWN_FINAL_XR2W_FINGERPRINT",
        "runner_verifies_a_superseded_parent": False,
        "xr2_fingerprint_is_armable": False,
        "identity_source_module": "analysis.l5.live_exec5.spec",
        "why": ("the runner is shared code, so the identity it asserts and "
                "pins must be the identity of the build that is actually "
                "authorizable. Pinning a superseded parent would let XR2's "
                "fingerprint arm XR2W's code, which is the whole thing the "
                "pin exists to prevent."),
        "a_pin_naming_a_superseded_fingerprint_leaves_the_build_inert": True,
    },

    "supersedes": {
        "reason": ("the defect was in shared code, so earlier fingerprints "
                   "no longer describe the code that would run"),
        "must_not_be_rearmed": [
            "d2f322760699f22e50a16975179a484b48c1523e3a667a77579ed6c5dd3a6c79",
            "f3e54d8225db2d9bc626ddbc285e347186f2ca9511543a8ff9b86f87359bc5e9",
            "0f3340195d68a5faf42dca97450e88eef3e7964ce4ca794c6e00baed54c9042f",
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

#: XR2's invariants, minus the one key this build deliberately moves.
XR2W_INVARIANTS = tuple(
    (k, v) for k, v in XR2_INVARIANTS if k != "correction_count"
) + (
    ("correction_count", 2),
    ("wire_serialization.serializer", "src.l5_order_body"),
    ("wire_serialization.hand_rolled_body_deleted", True),
    ("wire_serialization.quantity_is_a_json_number", True),
    ("wire_serialization.price_carries_currency_usd", True),
    ("wire_serialization.preview_takes_the_request_envelope", True),
    ("lifecycle_classification.determined_by", "EXCEPTION_TYPE"),
    ("lifecycle_classification.determined_by_reading_the_latch", False),
    ("lifecycle_classification.unknown_phase_is_never_resolved_by_guess",
     True),
    ("entrypoint.module", "scripts.l5_exec1xr2w_run"),
    ("fingerprint_pin.runner_verifies",
     "ITS_OWN_FINAL_XR2W_FINGERPRINT"),
    ("fingerprint_pin.runner_verifies_a_superseded_parent", False),
    ("fingerprint_pin.xr2_fingerprint_is_armable", False),
    ("venue_error_capture.request_header_values_recorded", False),
    ("venue_error_capture.secrets_or_authentication_material_recorded",
     False),
)


def assert_envelope_preserved(spec: Dict[str, Any] = SPEC) -> None:
    bad: List[str] = []
    for dotted, want in XR2W_INVARIANTS:
        got = _get(spec, dotted)
        if got != want:
            bad.append("%s: want %r, got %r" % (dotted, want, got))
    if bad:
        raise SystemExit("LIVE-L5-EXEC-1-XR2W changed the experiment or its "
                         "loss envelope: " + "; ".join(bad))


def diff_report() -> Dict[str, Any]:
    added, changed = {}, {}
    for dotted, value in DIFF.items():
        try:
            before = _get(XR2_SPEC, dotted)
        except KeyError:
            added[dotted] = value
            continue
        if before != value:
            changed[dotted] = {"xr2": before, "xr2w": value}
    return {"from_probe": "LIVE-L5-EXEC-1-XR2",
            "from_fingerprint": XR2_FINGERPRINT,
            "to_probe": PROBE, "to_fingerprint": XR2W_FINGERPRINT,
            "keys_added": added, "keys_changed": changed, "keys_removed": [],
            "max_possible_loss_usd_unchanged": str(MAX_POSSIBLE_LOSS_USD),
            "max_preopen_wait_s_unchanged": MAX_PREOPEN_WAIT_S}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous.
XR2W_FINGERPRINT = \
    "ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562"


def assert_frozen(spec: Dict[str, Any] = SPEC) -> None:
    assert_xr2_frozen()
    assert_envelope_preserved(spec)
    got = fingerprint(spec)
    if got != XR2W_FINGERPRINT:
        raise SystemExit("LIVE-L5-EXEC-1-XR2W is not the frozen spec: "
                         "%s != %s" % (got, XR2W_FINGERPRINT))


def fingerprint_pinned() -> Dict[str, Any]:
    import os
    got = os.getenv(FINGERPRINT_PIN_ENV, "").strip()
    return {"env": FINGERPRINT_PIN_ENV, "set": bool(got),
            "matches_this_build": got == XR2W_FINGERPRINT,
            "named": (got[:12] + "…") if got else None,
            "required": XR2W_FINGERPRINT}
