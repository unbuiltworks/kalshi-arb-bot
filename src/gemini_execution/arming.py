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
#: INSTALLED 2026-10-03 at the owner's explicit instruction: the bounded
#: EXECUTION-SEMANTICS probe. This is NOT a strategy authorization. It exists
#: to answer one question -- when our parser says the favored side is or is
#: not executable, does Gemini agree? -- and it is the only authorization
#: under which `transport.place(allow_taker=True)` can build a crossing
#: order. Every strategy stays maker-only; see `payload.SEMANTICS_PROBE_AUTH_ID`,
#: which must equal this value for taker behaviour to be reachable at all.
#:
#: BOUNDS, from the owner: one market, ONE order per probe, at most 5 probes,
#: $5 maximum cash per market, $15 aggregate realized loss, no EXTEND, no
#: averaging, no chasing, no FLIP, no complement, no automatic price
#: selection. Enforced in `semantics_probe` and proved in
#: tests/test_gemini_semantics_probe_gates.py.
#:
#: Spending this id does not re-arm it: the latch is one-shot and git-bound,
#: and any semantic or reconciliation anomaly writes a HALT file that blocks
#: every later submission until a human clears it.
#: MOVED 2026-10-03. SEMANTICS-1 is SPENT: it placed one order
#: (145828836703726947), filled 1 @ 0.50, settled NO, realized principal
#: -$0.50, and its latch is on disk. A spent authorization never arms again,
#: so the identifier moves rather than the latch being cleared.
#:
#: R2-EXEC-1 is the bounded R2 EXECUTION-VALIDATION cohort, authorized by the
#: owner against the protocol at 49d7fc58 and immutable R2 at 6bc5c1f7
#: (fingerprint 64b178378806b5c257b58b29d0ba49da). It is an execution-model
#: audit, NOT a profitability estimate: ten q=1 fills cannot distinguish
#: +0.04/contract from zero.
#:
#: BOUNDS, enforced in src/gemini_execution/r2_exec_budget.py and proved in
#: tests/test_r2_exec_budget.py: 10 submitted probes total, 1 per distinct
#: market, q=1, 1 outstanding order, no revisit after SUBMITTED or MISSED, no
#: chasing/repricing/substitution, no EXTEND/FLIP/complement/averaging, $8.00
#: cumulative CONFIRMED realized-principal-loss stop, $10.20 structural
#: maximum, expiry at the 10th submission or the 100-independent-window
#: statistical gate, whichever comes first. An ambiguous send consumes its
#: slot and is never retried. Any semantic or reconciliation contradiction
#: HALTs the cohort.
#: MOVED 2026-10-03 on the owner's FINAL AUTHORIZATION -- CI15M-1, the
#: bounded BTC15m CONTINUOUS-INVENTORY execution-validation probe, granted
#: against SHA bf14f29523f02a72187cea5d39102aa09c457122 and frozen policy
#: fingerprint 5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329.
#:
#: WHAT MOVING IT COSTS, stated rather than discovered later: R2-EXEC-1 is
#: RETIRED by this line with 0 of its 10 slots consumed. The R2 q=1
#: execution-validation runner can no longer arm. Exactly one authorization
#: is current at a time, by design; re-opening R2 needs a new grant from the
#: owner. Nothing about frozen R2 itself changes -- the statistical gate is
#: untouched and still blind.
#:
#: IT ALSO SHUTS THE TAKER PATH. `payload.SEMANTICS_PROBE_AUTH_ID` is
#: deliberately LEFT on R2-EXEC-1, so it no longer equals this value, and
#: `transport.place(allow_taker=True)` is therefore unreachable under this
#: authorization. CI15M is maker-or-cancel only and needs nothing else.
#:
#: BOUNDS, from the owner: BTC15m only; 3 completed live windows; q=1; OPEN
#: -> max 2 EXTENDs -> partial/full COMPLEMENT -> LOCK; max acquisition
#: price 0.90; MAX_PAIR_COST 0.96; EXTEND needs >=5c average improvement;
#: OPEN_SIDE_RULE NATIVE_UP; maker-or-cancel only; one outstanding order;
#: authoritative reconciliation after every fill; max 3 unmatched shares /
#: $2.70 unmatched principal; $12 per-market spend cap; 6 orders per window;
#: 12 orders total; no envelope increase; no martingale or quantity
#: progression; no chasing outside the frozen policy; LOCKED never reopened.
#: Stop at $2.00 CONFIRMED realized principal loss or any cohort limit.
#: Enforced in src/gemini_execution/ci15m_budget.py and ci15m_probe.py,
#: proved in tests/test_ci15m_probe.py.
#:
#: EXECUTION VALIDATION ONLY. Not scaling, and not a declaration of positive
#: expectancy.
EXPECTED_AUTH_ID = "OWNER-2026-10-03-GEMINI-CI15M-1"


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
