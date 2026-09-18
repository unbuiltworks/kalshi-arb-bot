"""A one-shot authorization latch that survives crashes, retries and restarts.

THE FAILURE THIS EXISTS FOR. A workflow retries. A process is killed between
sending a request and reading its response. A duplicate invocation starts. In
every one of those, an authorization that was granted for ONE order must not
buy a second one. Intent is not enough; the code has to make it impossible.

CONSUMED BEFORE THE MUTATION, NEVER AFTER. The latch is taken atomically
BEFORE the request is sent, using O_CREAT|O_EXCL -- a file that the filesystem
will let exactly one caller create. If the process dies one instruction later,
the authorization is already spent and a restart cannot take it again.

That ordering deliberately prefers a LOST order to a DOUBLE order: if the
request never left, the authorization is still consumed and the operator must
re-authorize. Spending an authorization on nothing is recoverable by asking;
placing two orders on one authorization is not recoverable at all.

AMBIGUITY IS A TERMINAL STATE, NOT A RETRY. A send whose outcome cannot be
established becomes SUBMISSION_UNKNOWN_RECONCILE_REQUIRED and stays there. The
answer is an authoritative READ -- active orders, trades -- never another
POST /order.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Optional

#: lifecycle. Nothing returns to an earlier state.
INTENT_PERSISTED = "INTENT_PERSISTED"
SUBMITTED = "SUBMITTED"
SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN_RECONCILE_REQUIRED"
REJECTED = "REJECTED"
UNFILLED = "UNFILLED"
FILLED = "FILLED"
EXPIRED_OR_SETTLED = "EXPIRED_OR_SETTLED"
RECONCILED = "RECONCILED"
REALIZED = "REALIZED"

STATES = (INTENT_PERSISTED, SUBMITTED, SUBMISSION_UNKNOWN, REJECTED, UNFILLED,
          FILLED, EXPIRED_OR_SETTLED, RECONCILED, REALIZED)

#: states from which nothing may be submitted again, i.e. all of them
DEFAULT_DIR = os.getenv("GEMINI_CANARY_STATE_DIR", "/state/gemini-canary")


class AlreadyConsumed(Exception):
    """The authorization has been spent. There is no retry, by design."""


def _path(directory: str, auth_id: str) -> str:
    safe = "".join(c for c in auth_id if c.isalnum() or c in "-_")
    return os.path.join(directory, "latch_%s.json" % safe)


def consume(auth_id: str, intent: Dict[str, Any],
            directory: str = DEFAULT_DIR,
            durable_check=None) -> Dict[str, Any]:
    """Atomically spend the authorization and persist the intent.

    Raises AlreadyConsumed if it was ever spent before -- including by a run
    that then crashed. Returns the persisted record.
    """
    # A LOCAL latch is only as durable as the filesystem under it. On an
    # ephemeral runner a retry gets a clean disk, the local latch is gone and
    # the one-shot guarantee evaporates exactly when it is needed. Callers on
    # such hosts pass a durable_check that consults storage which OUTLIVES the
    # process, and it is asked FIRST.
    if durable_check is not None and durable_check(auth_id):
        raise AlreadyConsumed(
            "authorization %s is recorded consumed in DURABLE storage; a "
            "fresh filesystem does not make it unspent" % auth_id)
    os.makedirs(directory, exist_ok=True)
    path = _path(directory, auth_id)
    rec = {
        "authorization_id": auth_id,
        "intent_id": intent.get("intent_id"),
        "symbol": intent.get("symbol"),
        "side": intent.get("side"),
        "outcome": intent.get("outcome"),
        "price": intent.get("price"),
        "amount": intent.get("amount"),
        "created_at": time.time(),
        "state": INTENT_PERSISTED,
        "venue_order_id": None,
        "history": [{"state": INTENT_PERSISTED, "t": time.time()}],
        # WHAT WAS AUTHORIZED, not only what was sent. Without this the record
        # shows one order's fields and nothing about the bound they sit
        # inside, so an audit after the fact cannot tell a one-order envelope
        # from an unbounded one that happened to send once.
        "envelope": {k: intent[k] for k in
                     ("max_orders", "max_residual", "maker_only", "transport")
                     if k in intent},
        "labels": ("consumed BEFORE the mutation. A lost order is preferred "
                   "to a double order, because only one of them is "
                   "recoverable."),
    }
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise AlreadyConsumed(
            "authorization %s was already consumed; read %s and reconcile "
            "rather than resubmitting" % (auth_id, path))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    return rec


def read(auth_id: str, directory: str = DEFAULT_DIR) -> Optional[Dict[str, Any]]:
    path = _path(directory, auth_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def consumed(auth_id: str, directory: str = DEFAULT_DIR) -> bool:
    return os.path.exists(_path(directory, auth_id))


def advance(auth_id: str, state: str, *, venue_order_id: Any = None,
            detail: Optional[Dict[str, Any]] = None,
            directory: str = DEFAULT_DIR) -> Dict[str, Any]:
    """Record a lifecycle transition durably. Never un-consumes the latch.

    Written to a temp file and renamed, so a crash mid-write cannot leave a
    record that parses as something else.
    """
    if state not in STATES:
        raise ValueError("unknown state %r" % (state,))
    rec = read(auth_id, directory)
    if rec is None:
        raise AlreadyConsumed("no latch for %s; refusing to invent one"
                              % auth_id)
    rec["state"] = state
    if venue_order_id is not None:
        rec["venue_order_id"] = venue_order_id
    rec.setdefault("history", []).append(
        {"state": state, "t": time.time(),
         **({"detail": detail} if detail else {})})
    path = _path(directory, auth_id)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".latch", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return rec


def needs_reconciliation(auth_id: str,
                         directory: str = DEFAULT_DIR) -> bool:
    """A latch stuck before a terminal read is reconciliation work, not a
    reason to submit anything."""
    rec = read(auth_id, directory)
    return bool(rec and rec.get("state") in (INTENT_PERSISTED, SUBMITTED,
                                             SUBMISSION_UNKNOWN))
