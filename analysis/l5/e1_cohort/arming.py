"""E1's OWN authorization namespace. `PMUS_E1_*`, and nothing else.

WHY THIS EXISTS. E1 was reading `analysis.l5.live_exec.ledger.may_submit`,
which reads `PMUS_EXEC1_*` -- XR2W's keys. The service
`l5-live-probe-oneshot` already carries `PMUS_EXEC1_LIVE`,
`PMUS_EXEC1_SESSION_ID`, `PMUS_EXEC1_ARM`, `PMUS_EXEC1_GATE_EVIDENCE` and
`PMUS_EXEC1_AUTHORIZED_FINGERPRINT` for XR2W, so a legacy value left in
that environment could have participated in arming E1. Sharing an
authorization namespace between two experiments means neither one's arming
state is its own.

So E1 reads `PMUS_E1_*` ONLY. It does not read `PMUS_EXEC1_*` at any point,
for any purpose, not even to report on it -- a test sets ALL FIVE legacy
keys to valid-looking values and asserts E1 still refuses.

XR2W IS COMPLETELY UNCHANGED. Nothing here writes to, reads from or
depends on `analysis/l5/live_exec/ledger.py`, `live_exec2` or `live_exec5`.
This module is additive.

NOTHING HERE SETS A VALUE. Every function reads the environment and
reports. Arming is the owner's, and there is no code path in this package
that writes an arming key.
"""
from __future__ import annotations

import os
from typing import Any, Dict

#: E1's four arming keys. Note the prefix: PMUS_E1_, never PMUS_EXEC1_.
ARM_KEYS = ("PMUS_E1_LIVE", "PMUS_E1_SESSION_ID", "PMUS_E1_ARM",
            "PMUS_E1_GATE_EVIDENCE")

#: the fingerprint pin, also in E1's own namespace.
FINGERPRINT_PIN_ENV = "PMUS_E1_AUTHORIZED_FINGERPRINT"

SESSION_ID_ENV = "PMUS_E1_SESSION_ID"

#: XR2W's keys, named here ONLY so a test can assert E1 ignores them. No
#: function in this module reads any of these.
LEGACY_XR2W_KEYS_NEVER_READ = (
    "PMUS_EXEC1_LIVE", "PMUS_EXEC1_SESSION_ID", "PMUS_EXEC1_ARM",
    "PMUS_EXEC1_GATE_EVIDENCE", "PMUS_EXEC1_AUTHORIZED_FINGERPRINT")

#: Q1's ARMING keys. E1's image now contains `src.pmus_q1_executor`,
#: because the PMUS-native book (`src.l5_pmus_auth_book`) imports the
#: evidence recorder, which imports it. That module carries
#: `place_limit_buy`, `cancel_all_deadman` and `run_campaign`, and it arms
#: from ITS OWN namespace -- so a Q1 authorization present in E1's
#: container would mean two independently armed execution stacks sharing
#: one process. Importing it is provably inert (no module-level network or
#: order call; only Decimal constants and an `if __name__` guard), but
#: "inert on import" is not "safe to leave armed".
#:
#: `PMUS_Q1_STATE_DIR` is deliberately NOT here: it names where Q1's
#: historical state lives and authorizes nothing.
Q1_AUTHORIZATION_KEYS = (
    "PMUS_Q1_LIVE", "PMUS_Q1_ARM", "PMUS_Q1_SESSION_ID",
    "PMUS_Q1_GATE_EVIDENCE", "PMUS_Q1_MARKET_SLUG")


def q1_authorization_present() -> dict:
    """Which Q1 arming values are set in this container, by NAME only."""
    present = sorted(k for k in Q1_AUTHORIZATION_KEYS
                     if (os.getenv(k) or "").strip())
    return {"keys_checked": list(Q1_AUTHORIZATION_KEYS),
            "present": present, "any_present": bool(present),
            "values_never_read": True,
            "state_dir_is_not_authorization": "PMUS_Q1_STATE_DIR"}


def assert_no_foreign_executor_authorization() -> dict:
    """FAIL CLOSED AT BOOT if another executor is armed in this container.

    E1 cannot police what else is installed beside it, but it can refuse
    to run next to a second armed order path. This is asserted at every
    boot, before the arming gate and before any network call, and it
    raises SystemExit rather than returning a flag so no caller can
    continue past it by ignoring a return value.
    """
    q1 = q1_authorization_present()
    if q1["any_present"]:
        raise SystemExit(
            "REFUSED: a Q1 executor authorization is present in E1's "
            "container (%s). `src.pmus_q1_executor` ships in this image "
            "as a dependency of the PMUS-native book and can place and "
            "cancel orders from its own namespace. E1 will not run "
            "beside a second armed execution stack. Blank these values "
            "and redeploy." % ", ".join(q1["present"]))
    return {"foreign_executor_authorization": "NONE",
            "q1": q1,
            "why": "two independently armed order paths must never share "
                   "one process"}


def session_id() -> str:
    return os.getenv(SESSION_ID_ENV, "").strip()


def armed() -> bool:
    """E1's own four keys, and only E1's. Isolated: no other experiment's
    arming can arm this one, and arming this one arms nothing else."""
    sid = session_id()
    return (os.getenv("PMUS_E1_LIVE", "").strip().upper() == "YES"
            and bool(sid)
            and os.getenv("PMUS_E1_ARM", "").strip() == sid)


def may_submit(slug: str) -> Dict[str, Any]:
    gated = bool(slug) and os.getenv("PMUS_E1_GATE_EVIDENCE",
                                     "").strip() == slug
    is_armed = armed()
    return {"armed": is_armed, "gate_asserted_for_slug": gated,
            "may_submit": bool(is_armed and gated),
            "namespace": "PMUS_E1_*",
            "reads_pmus_exec1": False,
            "refusal": (None if (is_armed and gated) else
                        "DISARMED: the owner has not set the four "
                        "PMUS_E1_* keys for this slug.")}


def fingerprint_pinned(required: str) -> Dict[str, Any]:
    got = os.getenv(FINGERPRINT_PIN_ENV, "").strip()
    return {"env": FINGERPRINT_PIN_ENV, "set": bool(got),
            "matches_this_build": got == required,
            "named": (got[:12] + "…") if got else None,
            "required": required,
            "namespace": "PMUS_E1_*"}


def armed_env_summary() -> Dict[str, Any]:
    """Shaped like `orders.armed_env_summary()` so the runner's reporting
    is unchanged, but reading E1's namespace. BOOLEANS ONLY -- no value of
    any key is ever returned or logged."""
    return {"keys_present": {k: bool(os.getenv(k, "").strip())
                             for k in ARM_KEYS},
            "fingerprint_pin_present": bool(
                os.getenv(FINGERPRINT_PIN_ENV, "").strip()),
            "armed": armed(),
            "namespace": "PMUS_E1_*",
            "reads_pmus_exec1_namespace": False,
            "VALUES_NEVER_REPORTED": True}
