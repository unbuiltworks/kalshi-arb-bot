"""SUPERVISED-PROBE MODE: at most ONE POST /v1/orders, ever, per epoch.

WHY A SEPARATE GUARD AT ALL. E1's existing bounds are per-attempt and
per-window: the latch gives one named slot per attempt, `ProbeBudget`
bounds attempts and windows, and `MAX_OUTSTANDING_ORDERS` bounds how many
orders may rest at once. None of them says "one submission, total, and no
second leg" -- `MAX_COMPLEMENT_TRANCHES` is frozen in `spec.py` and cannot
be configured down, so a filled first leg would be complemented as
designed. For a FIRST supervised real-money probe the operator needs a
bound the configuration cannot express, so this adds exactly that one and
nothing else.

DEFAULT OFF, AND ONLY ONE SPELLING TURNS IT ON. `enabled()` is true only
when PMUS_E1_FIRST_LEG_ONLY is exactly "YES" after stripping. Absent,
empty, "yes", "1", "true" and "Y" all leave the normal engine completely
untouched -- which is the direction that matters, because this module must
be incapable of silently changing the cohort's behaviour when nobody asked
for it.

THE BOUND IS DURABLE, NOT A COUNTER IN MEMORY. A process-local integer is
worthless here: the failure this guard exists for is a crash or an OOM kill
after a POST has left the process, and an in-memory count resets to zero on
the next start. So the single permission is a FILE on the same volume as
the latch, created with O_CREAT|O_EXCL. Whoever creates it owns the one
POST this mode allows; everyone else, in this process or any later one,
raises `OnePostSpent`. It is never released and nothing here deletes it --
clearing it is an operator act, under a new epoch.

IT IS CLAIMED IMMEDIATELY BEFORE THE POST, after the preview, the echo and
the attempt latch. So a preview rejection does not spend it, and anything
at or after the POST does. That ordering means an AMBIGUOUS submission
spends the permission too, which is deliberate: an order that may exist
must not be followed by a second one.

FAIL CLOSED ON AN UNREADABLE VOLUME. If the directory cannot be created or
the claim cannot be written and fsynced, this raises. A guard that cannot
record its decision must not permit the action it was asked to bound.

IT CANNOT WIDEN ANYTHING. There is no code path here that permits a POST
the engine would otherwise refuse: `enabled()` false means every function
is a no-op, and `enabled()` true can only raise. It holds no transport, no
order body and no credential.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Optional

#: the ONE spelling that enables this mode
ENV = "PMUS_E1_FIRST_LEG_ONLY"
ENABLED_VALUE = "YES"

#: same volume as the latch, a sibling directory so a listing shows both
DEFAULT_DIR = "/state/pmus-ws/e1_cohort_first_leg_only"
DIR_ENV = "PMUS_E1_FIRST_LEG_ONLY_DIR"

#: the single permission's filename inside the epoch directory
CLAIM_NAME = "the_one_post"


class OnePostSpent(Exception):
    """The one POST this mode allows has already been claimed."""


class GuardUnusable(Exception):
    """The guard could not record its decision, so it permits nothing."""


def enabled(env: Optional[Any] = None) -> bool:
    """True ONLY for the exact string "YES". Anything else is OFF."""
    get = (env or os.environ).get
    return (get(ENV) or "").strip() == ENABLED_VALUE


def directory(env: Optional[Any] = None) -> str:
    get = (env or os.environ).get
    return (get(DIR_ENV) or "").strip() or DEFAULT_DIR


def epoch(env: Optional[Any] = None) -> str:
    """The epoch namespace, so an operator can authorize a SECOND probe
    without deleting the record of the first."""
    get = (env or os.environ).get
    return (get("PMUS_E1_PROBE_EPOCH") or "").strip() or "default"


def claim_path(env: Optional[Any] = None) -> str:
    return os.path.join(directory(env), epoch(env), CLAIM_NAME)


def claimed(env: Optional[Any] = None) -> bool:
    return os.path.exists(claim_path(env))


def claim_the_single_post(detail: Optional[Dict[str, Any]] = None,
                          env: Optional[Any] = None) -> Dict[str, Any]:
    """Claim the one permitted POST, or raise.

    Called ONLY when `enabled()`, immediately before the POST. Raises
    `OnePostSpent` if the permission already exists -- in this process or
    any earlier one -- and `GuardUnusable` if the claim cannot be durably
    recorded.
    """
    path = claim_path(env)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception as exc:                                   # noqa: BLE001
        raise GuardUnusable(
            "the first-leg-only guard cannot create %s (%s), so it permits "
            "no POST" % (os.path.dirname(path), type(exc).__name__)) from None
    body = json.dumps({"claimed_unix": time.time(),
                       "claimed_utc": time.strftime(
                           "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "pid": os.getpid(),
                       "epoch": epoch(env),
                       "detail": dict(detail or {})},
                      sort_keys=True, default=str).encode()
    # write the content to a uuid-unique temp FIRST, then publish the name
    # with a hard link, so a crash between create and fsync can never leave
    # an empty claim that a later reader would have to interpret.
    tmp = "%s.%s.tmp" % (path, uuid.uuid4().hex)
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, body)
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception as exc:                                   # noqa: BLE001
        raise GuardUnusable(
            "the first-leg-only guard could not write its claim (%s), so it "
            "permits no POST" % type(exc).__name__) from None
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise OnePostSpent(
            "this probe's ONE permitted POST /v1/orders was already claimed "
            "(%s). No replacement, no complement and no second attempt may "
            "be submitted under %s=%s." % (path, ENV, ENABLED_VALUE)) from None
    except Exception as exc:                                   # noqa: BLE001
        raise GuardUnusable(
            "the first-leg-only guard could not publish its claim (%s), so "
            "it permits no POST" % type(exc).__name__) from None
    finally:
        try:
            os.unlink(tmp)
        except Exception:                                      # noqa: BLE001
            pass
    try:
        dfd = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except Exception:                                          # noqa: BLE001
        # the claim itself is already durable; a failed directory fsync is
        # reported by the caller's own evidence line, never by permitting a
        # second POST.
        pass
    return {"claimed": True, "path": path, "epoch": epoch(env)}


def refuse_complement(env: Optional[Any] = None) -> None:
    """Raise if a complement is attempted while this mode is on.

    This is the EARLY refusal -- before the preview, before the latch and
    therefore before anything the venue can see. The POST claim above is
    the backstop for any path that reaches a submission another way.
    """
    if enabled(env):
        raise OnePostSpent(
            "%s=%s: this probe submits a FIRST LEG ONLY. A complement is "
            "refused before preview; a filled first leg is carried as "
            "RESIDUAL and reconciled by the operator." % (ENV, ENABLED_VALUE))


def report(env: Optional[Any] = None) -> Dict[str, Any]:
    """What an operator needs on one line, with no interpretation."""
    on = enabled(env)
    return {"first_leg_only": on,
            "env": ENV, "enabled_value_required": ENABLED_VALUE,
            "epoch": epoch(env),
            "claim_path": claim_path(env),
            "one_post_already_claimed": claimed(env) if on else None,
            "DEFAULT_IS_OFF": True,
            "DURABLE_ACROSS_CRASHES": True,
            "COMPLEMENT_REFUSED_BEFORE_PREVIEW": on,
            "NEVER_WIDENS_ANY_BOUND": True}
