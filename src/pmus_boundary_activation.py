"""The eight-step boundary activation chain, and what counts as proof.

OWNER: "A deployment request, SUCCESS build, or pushed commit alone is not
activation proof."

That sentence is the whole design. Three things that look like activation and
are not:

  * a deploy REQUEST        -- the API accepted a call
  * a SUCCESS build         -- an image was produced
  * a pushed commit         -- a SHA exists on a branch

None of them says the running process is executing that code under the
reviewed envelope. Tonight produced each failure in turn: 9912e9a8 read
SKIPPED and left broken code serving; d2624218 built SUCCESS and collected
nothing; a railway.json watch list that looked authoritative was not. So the
chain below ends at the SERVING PROCESS and nowhere earlier.

    1 flat, from the probe's own authoritative status / venue reconciliation
    2 all seven preflight assertions
    3 any UNKNOWN or FAIL -> idle
    4 explicit deploy of the approved revision
    5 deployment actually SUCCESS
    6 running probe reports the expected commit SHA
      6b it also reports carrying all four approved components
    7 critical envelope assertions re-run FROM THE SERVING PROCESS
    8 only then may acquisition begin

Step 6b exists because a SHA is an identity, not an inventory. "The right
commit is serving" and "the serving code contains the residual quarantine"
are different claims, and only the second is what step 4 approved. The
serving process names its own components; a revision that cannot name them
is UNKNOWN, and one that names a short list is MISSING.

UNKNOWN IS NOT FAIL, even though both stop the chain. "The venue did not
answer" and "the venue said no" are different facts, and collapsing them is
how a transport outage comes to look like a safety refusal -- or worse, how a
refusal comes to look like a blip worth retrying. Both idle; both are named.

NOTHING HERE HOLDS CREDENTIALS OR CALLS A VENUE. Deployment and status reads
are injected by the caller, so this module can be exercised in full without
secrets and cannot itself move money, deploy anything, or read a key.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional, Sequence

# Terminal-idle states: the chain stopped and acquisition is NOT permitted.
IDLE_NOT_FLAT = "IDLE_NOT_FLAT"
IDLE_FLATNESS_UNKNOWN = "IDLE_FLATNESS_UNKNOWN"
IDLE_PREFLIGHT_FAILED = "IDLE_PREFLIGHT_FAILED"
IDLE_PREFLIGHT_UNKNOWN = "IDLE_PREFLIGHT_UNKNOWN"
DEPLOY_REQUEST_FAILED = "DEPLOY_REQUEST_FAILED"
DEPLOY_NOT_SUCCESS = "DEPLOY_NOT_SUCCESS"
SERVING_SHA_MISMATCH = "SERVING_SHA_MISMATCH"
SERVING_SHA_UNKNOWN = "SERVING_SHA_UNKNOWN"
SERVING_COMPONENTS_MISSING = "SERVING_COMPONENTS_MISSING"
SERVING_COMPONENTS_UNKNOWN = "SERVING_COMPONENTS_UNKNOWN"
SERVING_ENVELOPE_FAILED = "SERVING_ENVELOPE_FAILED"
SERVING_ENVELOPE_UNKNOWN = "SERVING_ENVELOPE_UNKNOWN"

# The ONLY state that permits acquisition.
ACTIVATION_PROVEN = "ACTIVATION_PROVEN"

# What the approved probe revision must contain. Named so the record says what
# was activated, and so a revision missing a component cannot pass as one.
REQUIRED_COMPONENTS = ("ranked_selector", "settlement_horizon_protection",
                       "residual_quarantine", "boundary_reader")

# OWNER step 2: "all seven preflight assertions". A preflight that reports
# ok=True over three checks has not run the seven, and a preflight that
# reports ok=True over NONE has run nothing at all -- which is the shape a
# crashed or stubbed checker returns. Too few checks is UNKNOWN, not pass.
EXPECTED_PREFLIGHT_CHECKS = 7


def may_acquire(state: Optional[str]) -> bool:
    """Exactly one state opens acquisition. Everything else is closed."""
    return state == ACTIVATION_PROVEN


class _Step:
    __slots__ = ("name", "ok", "unknown", "detail")

    def __init__(self, name, ok, unknown=False, detail=None):
        self.name, self.ok, self.unknown, self.detail = name, ok, unknown, detail

    def row(self) -> dict:
        return {"step": self.name, "ok": bool(self.ok),
                "unknown": bool(self.unknown), "detail": self.detail}


def _call(fn, *a, **kw):
    """Run an injected probe. An exception is UNKNOWN, never False.

    A transport error is not evidence that a condition is false; treating it
    as such is how "could not ask" turns into "answered no".
    """
    try:
        return fn(*a, **kw), None
    except Exception as exc:                                    # noqa: BLE001
        return None, repr(exc)


def activate(read_flat: Callable[[], Mapping[str, Any]],
             run_preflight: Callable[[], Mapping[str, Any]],
             deploy: Callable[[], Mapping[str, Any]],
             read_deployment: Callable[[str], Mapping[str, Any]],
             read_serving: Callable[[], Mapping[str, Any]],
             check_envelope: Callable[[Mapping[str, Any]], Mapping[str, Any]],
             expected_sha: str,
             components: Sequence[str] = REQUIRED_COMPONENTS,
             now: Callable[[], float] = time.time) -> dict:
    """Run the chain once. Never raises; never returns a half-activated state.

    Each injected callable returns a mapping; anything that raises, returns
    None, or omits the field the step needs is UNKNOWN.
    """
    steps: list = []

    def _stop(state, **kw):
        return _row(state, steps, expected_sha, components, now, **kw)

    # 1-3. FLAT, from the probe's own authority.
    flat, err = _call(read_flat)
    if err is not None or not isinstance(flat, Mapping):
        steps.append(_Step("flat", False, True, err or "no status row").row())
        return _stop(IDLE_FLATNESS_UNKNOWN)
    is_flat = flat.get("flat")
    if is_flat is not True and is_flat is not False:
        # Not merely absent: a non-boolean is not a statement of flatness.
        steps.append(_Step("flat", False, True,
                           f"status row did not state flatness: {is_flat!r}").row())
        return _stop(IDLE_FLATNESS_UNKNOWN)
    steps.append(_Step("flat", bool(is_flat), False,
                       {k: flat.get(k) for k in
                        ("held", "status", "flat_verified")}).row())
    if not is_flat:
        return _stop(IDLE_NOT_FLAT)

    pre, err = _call(run_preflight)
    if err is not None or not isinstance(pre, Mapping):
        steps.append(_Step("preflight", False, True, err or "no result").row())
        return _stop(IDLE_PREFLIGHT_UNKNOWN)
    checks = [c for c in (pre.get("checks") or []) if isinstance(c, Mapping)]
    pre_ok = pre.get("ok")
    if pre_ok is not True and pre_ok is not False:
        steps.append(_Step("preflight", False, True,
                           f"no verdict: {pre_ok!r}").row())
        return _stop(IDLE_PREFLIGHT_UNKNOWN)
    if len(checks) < EXPECTED_PREFLIGHT_CHECKS:
        steps.append(_Step("preflight", False, True,
                           {"n_checks": len(checks),
                            "want": EXPECTED_PREFLIGHT_CHECKS,
                            "note": "fewer assertions than the seven"}).row())
        return _stop(IDLE_PREFLIGHT_UNKNOWN)
    unknown = [c for c in checks if c.get("unknown")]
    failed = [c.get("check") for c in checks
              if not c.get("ok") and not c.get("unknown")]
    steps.append(_Step("preflight", pre_ok, bool(unknown),
                       {"failed": failed,
                        "unknown": [c.get("check") for c in unknown],
                        "n_checks": len(checks)}).row())
    if unknown:
        return _stop(IDLE_PREFLIGHT_UNKNOWN, failed=failed,
                     unknown=[c.get("check") for c in unknown])
    if not pre_ok:
        return _stop(IDLE_PREFLIGHT_FAILED, failed=failed)

    # 4. EXPLICIT deploy of the approved revision.
    dep, err = _call(deploy)
    dep_id = (dep or {}).get("id") if isinstance(dep, Mapping) else None
    if err is not None or not dep_id:
        steps.append(_Step("deploy_requested", False, True,
                           err or "no deployment id returned").row())
        return _stop(DEPLOY_REQUEST_FAILED)
    steps.append(_Step("deploy_requested", True, False,
                       {"deployment_id": dep_id}).row())

    # 5. The deployment actually SUCCEEDED -- a request is not a build.
    got, err = _call(read_deployment, dep_id)
    status = (got or {}).get("status") if isinstance(got, Mapping) else None
    steps.append(_Step("deploy_success", status == "SUCCESS",
                       err is not None or status is None,
                       {"status": status, "error": err}).row())
    if status != "SUCCESS":
        return _stop(DEPLOY_NOT_SUCCESS, deployment_id=dep_id)

    # 6. The RUNNING process reports the expected SHA -- a build is not a
    #    running process.
    serving, err = _call(read_serving)
    sha = (serving or {}).get("commit") if isinstance(serving, Mapping) else None
    if err is not None or not sha:
        steps.append(_Step("serving_sha", False, True,
                           err or "serving process did not report a commit").row())
        return _stop(SERVING_SHA_UNKNOWN, deployment_id=dep_id)
    match = str(sha).startswith(str(expected_sha)[:len(str(sha))]) or \
        str(expected_sha).startswith(str(sha))
    steps.append(_Step("serving_sha", match, False,
                       {"serving": sha, "expected": expected_sha}).row())
    if not match:
        return _stop(SERVING_SHA_MISMATCH, deployment_id=dep_id,
                     serving_sha=sha)

    # 6b. The serving process carries every approved component. The SHA says
    #     WHICH code; this says WHAT is in it.
    got = serving.get("components")
    if got is None:
        steps.append(_Step("serving_components", False, True,
                           "serving process did not name its components").row())
        return _stop(SERVING_COMPONENTS_UNKNOWN, deployment_id=dep_id,
                     serving_sha=sha)
    have = set(got)
    missing = [c for c in components if c not in have]
    steps.append(_Step("serving_components", not missing, False,
                       {"missing": missing, "serving": sorted(have)}).row())
    if missing:
        return _stop(SERVING_COMPONENTS_MISSING, deployment_id=dep_id,
                     serving_sha=sha, missing=missing)

    # 7. The envelope, re-asserted FROM THE SERVING PROCESS. The preflight in
    #    step 2 ran against the OLD process; this one is the new one.
    env, err = _call(check_envelope, serving)
    verdict = env.get("ok") if isinstance(env, Mapping) else None
    if err is not None or (verdict is not True and verdict is not False):
        steps.append(_Step("serving_envelope", False, True,
                           err or f"envelope check returned no verdict: "
                                  f"{verdict!r}").row())
        return _stop(SERVING_ENVELOPE_UNKNOWN, deployment_id=dep_id,
                     serving_sha=sha)
    steps.append(_Step("serving_envelope", verdict, False,
                       {k: env.get(k) for k in ("ok", "mismatches", "rung")}).row())
    if not verdict:
        return _stop(SERVING_ENVELOPE_FAILED, deployment_id=dep_id,
                     serving_sha=sha, mismatches=env.get("mismatches"))

    # 8. Proven.
    return _stop(ACTIVATION_PROVEN, deployment_id=dep_id, serving_sha=sha)


def _row(state, steps, expected_sha, components, now, **kw) -> dict:
    row = {
        "kind": "boundary_activation",
        "state": state,
        "may_acquire": may_acquire(state),
        "steps": steps,
        "expected_sha": expected_sha,
        "required_components": list(components),
        "proven_at": now() if state == ACTIVATION_PROVEN else None,
        "labels": ("acquisition opens on ACTIVATION_PROVEN and no other "
                   "state. A deploy request, a SUCCESS build and a pushed "
                   "commit are each necessary and none is sufficient: the "
                   "proof is the SERVING process reporting the expected SHA "
                   "and re-passing the envelope. UNKNOWN and FAIL both idle "
                   "and are never merged."),
    }
    row.update(kw)
    return row
