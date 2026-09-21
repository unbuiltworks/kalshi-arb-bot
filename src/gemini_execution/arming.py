"""Dual-key arming. Inert by default, and unreachable when either key is absent.

Two independent facts must both hold before a mutation is even constructible:

  GEMINI_CANARY_LIVE  set to YES -- the deliberate execution arm
  GEMINI_CANARY_AUTH  the EXACT one-canary authorization identifier

Neither alone does anything. A mismatched or expired identifier is the same as
no arm at all, so an authorization cannot be reused by leaving a flag set.

WHY AN IDENTIFIER AND NOT JUST A FLAG. A flag says "execution is allowed". An
identifier says "THIS authorization, once". The difference is what stops a
later run, a retried workflow or a copied environment from spending an
authorization that was granted for one order in one window.

READS STAY OPEN WHILE DISARMED. The preflight, the settlement probe and every
reconciliation read work with no arm at all, because refusing to look at the
account while disarmed would make the safe path harder than the unsafe one.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

LIVE_ENV = "GEMINI_CANARY_LIVE"
AUTH_ENV = "GEMINI_CANARY_AUTH"
EXPIRY_ENV = "GEMINI_CANARY_AUTH_EXPIRES"

#: the identifier this build will honour. A different one is refused, so an
#: authorization for a different probe cannot be spent here.
#:
#: MOVED 2026-09-18 from OWNER-2026-09-17-GEMINI-CANARY-1, which is SPENT (its
#: latch is on disk in state REJECTED). Exactly one authorization is current at
#: a time and this is it: the one-window continuous probe. Retiring the canary
#: identifier is itself fail-closed -- the canary path can no longer arm at
#: all, so a stale workflow input naming it is now inert rather than dangerous.
#: MOVED AGAIN 2026-09-18. CONTINUOUS-1 is SPENT: its latch is on disk in
#: state INTENT_PERSISTED with venue_order_id null, consumed at 13:33Z by a run
#: that then failed closed at preflight with zero orders placed. A spent
#: authorization never arms again, so the identifier moves rather than the
#: latch being cleared.
#: MOVED AGAIN 2026-09-18. CONTINUOUS-2 is SPENT: it placed two real orders
#: (venue orderIds 145828836242288478 and 145828836242288483) at 14:31:00Z,
#: both acknowledged 200, NEITHER FILLED, realized $0.00. Its latch is on disk.
#: A spent authorization never arms again, so the identifier moves rather than
#: the latch being cleared. CONTINUOUS-3 is granted against executable_sha
#: 842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4, which
#: carries the cancel-seam fix (order.cancel now sends the venue orderId, not
#: the clientOrderId that the venue rejected -1013 during CONTINUOUS-2).
#: MOVED AGAIN 2026-09-18. CONTINUOUS-3 is SPENT: its latch is on disk in
#: state INTENT_PERSISTED, consumed at 16:15:40Z by a run that then refused
#: inside run_window on a stale ledger row and sent NOTHING. No
#: authorization is in force, and this sentinel matches no environment
#: value, so the transport cannot arm at all until a human installs one.
EXPECTED_AUTH_ID = "NO-AUTHORIZATION-IN-FORCE"


class NotArmed(Exception):
    """Raised when a mutation is attempted without both keys. Structural."""


def _env(name: str) -> str:
    return str(os.getenv(name, "")).strip()


def live_flag() -> bool:
    return _env(LIVE_ENV).upper() == "YES"


def auth_id() -> str:
    return _env(AUTH_ENV)


def expired(now: Optional[float] = None) -> Optional[bool]:
    """True/False, or None when no expiry was supplied.

    An absent expiry is NOT treated as 'never expires': `armed()` requires one,
    because an authorization with no end is not a one-canary authorization.
    """
    raw = _env(EXPIRY_ENV)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True                      # unparseable == expired, fail closed
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    t = now if now is not None else datetime.now(timezone.utc).timestamp()
    return t >= dt.timestamp()


def armed(now: Optional[float] = None) -> bool:
    """Both keys, the exact identifier, and an expiry that has not passed."""
    return bool(live_flag()
                and auth_id() == EXPECTED_AUTH_ID
                and expired(now) is False)


def state(now: Optional[float] = None) -> Dict[str, Any]:
    """Why it is or is not armed. Never reveals a credential; these are not
    credentials, they are an arm flag and an authorization label."""
    exp = expired(now)
    got = auth_id()
    return {
        "armed": armed(now),
        "live_flag": live_flag(),
        "auth_id_present": bool(got),
        "auth_id_matches": got == EXPECTED_AUTH_ID,
        "expiry_supplied": exp is not None,
        "expired": exp,
        "expected_auth_id": EXPECTED_AUTH_ID,
        "why_not": _why_not(now),
    }


def _why_not(now: Optional[float]) -> Optional[str]:
    if armed(now):
        return None
    if not live_flag():
        return "%s is not YES" % LIVE_ENV
    got = auth_id()
    if not got:
        return "%s is absent" % AUTH_ENV
    if got != EXPECTED_AUTH_ID:
        return "authorization identifier does not match this build"
    exp = expired(now)
    if exp is None:
        return ("%s is absent; an authorization with no end is not a "
                "one-canary authorization" % EXPIRY_ENV)
    if exp:
        return "the authorization has expired"
    return "unknown"


def require(now: Optional[float] = None) -> None:
    """Raise unless fully armed. Every mutation calls this first."""
    if not armed(now):
        raise NotArmed(_why_not(now) or "not armed")
