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
#: MOVED 2026-10-03 on the owner's authorization of the CI15M-2 MECHANICS
#: probe, granted against SHA 13f83d4c49c5eb060320084be291cd75b04027f7 and
#: frozen policy fingerprint
#: 5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329.
#:
#: CI15M-1 IS PERMANENTLY CLOSED at 12/12 and is not reset, reused or
#: modified. Its latch refs stand, its audit is committed, and a fresh
#: runner reading those refs refuses it.
#:
#: WHAT THIS PROBE IS. A MECHANICS probe, not a profitability test and not
#: an endorsement of taker execution. Its success criterion is one chain:
#: OPEN fill -> terminal fill detection -> authoritative venue inventory
#: reconciliation -> correct next frozen-policy decision. If the frozen
#: policy then naturally calls for an EXTEND or a COMPLEMENT, good; no
#: action is manufactured to use a remaining slot.
#:
#: BOUNDS, from the owner: BTC15m only; ONE market/window; q=1; THREE
#: submitted orders total; one outstanding/unresolved order; one OPEN
#: intent; OPEN + at most 2 subsequent inventory actions; max 2 EXTENDs;
#: acquisition <= 0.90; MAX_PAIR_COST 0.96; unmatched <= 3 shares and
#: <= $2.70; frozen 5c EXTEND threshold; OPEN_SIDE_RULE NATIVE_UP; no
#: progression; no retry or chase after a cancelled or unfilled OPEN.
#:
#: TREATMENT T ONLY, priced from the REAL /v1/book executable ladder.
#: `contract.prices.buy.yes` is telemetry and may never determine an
#: executable price. This is also the ONE authorization under which
#: `transport.place(allow_taker=True)` is reachable, which is why
#: `payload.SEMANTICS_PROBE_AUTH_ID` moves to this same value -- the two
#: must AGREE for a crossing order to be constructible at all. The order is
#: still a LIMIT with an explicit price; there is no unrestricted market
#: order anywhere in this repository.
#: PREPARED 2026-10-03 for CI15M-3, the SAME mechanics experiment after the
#: venue-adapter contract sweep. Authorized SHA f52027bc7c4888d1a7429b8a6db0e065f82a0607, frozen policy
#: fingerprint 5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329
#: -- unchanged, because no strategy changed.
#:
#: CI15M-2 IS CLOSED: 0 of 3 submissions, HALTED UNKNOWN_POSITION before its
#: first order. Its record is preserved and it is never cleared or reused.
#: CI15M-1 IS CLOSED at 12/12. Neither is reset.
#:
#: WHY A THIRD ATTEMPT IS DIFFERENT. CI15M-1 and CI15M-2 both died of the
#: same failure class -- a consumer guessing the shape its producer returns.
#: Every venue surface on this path is now normalized once, at the boundary,
#: into an explicit type, and each contract is asserted against the
#: producer's own source. 68 contract tests plus a 10-case end-to-end chain.
#:
#: BOUNDS, unchanged from CI15M-2: Treatment T; BTC15m only; ONE window;
#: q=1; THREE submitted orders; one outstanding/unresolved order; one OPEN
#: intent; OPEN + at most 2 subsequent inventory actions; max 2 EXTENDs;
#: acquisition <= 0.90; MAX_PAIR_COST 0.96; 5c EXTEND threshold; unmatched
#: <= 3 shares and <= $2.70; OPEN_SIDE_RULE NATIVE_UP; no chase or retry
#: after a failed OPEN.
#:
#: UNKNOWN_POSITION still HALTs. The fix was correct interpretation of
#: SUCCESSFUL reads, not treating absence as flat.
#: MOVED 2026-10-03 to CI15M-4, the continuous-mechanics probe, authorized
#: against SHA dfcee7b78a69db37272bbd2c5452f12385d202b9 and frozen policy
#: fingerprint
#: 5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd810536329.
#:
#: CI15M-1, -2 and -3 are HISTORICAL RECORDS and are never cleared or
#: reused. CI15M-3 proved live OPEN execution: a capped marketable limit
#: filled 1 @ 0.34 at the limit, settled UP, actual venue fee $0.02.
#:
#: WHAT CI15M-4 IS FOR. OPEN is no longer the experiment. The acceptance
#: criterion is a POST-FILL STATE TRANSITION: OPEN -> authenticated and
#: ATTRIBUTED fill -> recovered inventory -> next frozen-policy decision ->
#: keep observing -> if naturally triggered, EXTEND or COMPLEMENT ->
#: attributed fill -> recover again. HOLD is expected and valid, and no
#: activity is manufactured to produce a transition.
#:
#: BOUNDS: BTC15m only; TWO windows; q=1; SIX submitted orders, THREE per
#: window; one outstanding/unresolved order; one OPEN intent per market;
#: max 2 EXTENDs before a complement; acquisition <= 0.90; MAX_PAIR_COST
#: 0.96; EXTEND needs >= 5c average improvement; unmatched <= 3 shares and
#: <= $2.70; NATIVE_UP; no progression; no chasing.
#:
#: STOPS: UNATTRIBUTABLE_FILL, an authenticated-fill/local-state ambiguity
#: that cannot be crash-recovered, unreadable authoritative fills,
#: unresolved-order ambiguity, a risk-cap violation, a fill above limit, or
#: any fingerprint/provenance failure.
#: MOVED 2026-10-05 on the owner's explicit LIVE money-moving authorization:
#: the bounded BTC5m EXECUTION CANARY. CI15M-4 is retired. This is NOT a
#: strategy authorization -- there is no proven BTC5m directional rule -- and
#: its envelope is q=1 fixed, acquisition cap 0.90, 5 attempts, one per
#: window, one outstanding order, hold to settlement, no SELL, no averaging
#: down, no size change, cohort outlay cap $4.5315. See
#: src/gemini_execution/btc5m_canary.py and
#: analysis/gemini/updown/BTC5M_PROBE_SPEC.md.
#:
#: MOVING THIS CLOSES THE BTC15m PATH, which is the point: ci15m_probe.submit()
#: refuses when its own PROBE_AUTH_ID is not the live authorization, so the
#: separate PERSIST4 cohort cannot be spent on this grant.
EXPECTED_AUTH_ID: Final = "OWNER-2026-10-05-GEMINI-BTC5M-UNDERDOG-1"


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
