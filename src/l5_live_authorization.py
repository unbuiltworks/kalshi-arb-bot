"""One authorization, one market, spent on use.

The 24/7 observer cannot be armed: it holds no object that can POST, and
nothing in it calls `PmusTransport.arm()`. That is deliberate and stays
true. This module is what a SEPARATE, bounded, one-shot live probe uses to
establish that it may trade exactly once.

The owner's three environment variables (`pmus_auth.armed()`) say WHO
authorized and WHETHER. They cannot say HOW MANY TIMES, because an
environment variable is a standing fact: a service that restarts reads the
same YES again, and a 15-minute market rolls over four times an hour. An
env-only arm is therefore a standing licence to trade every future window,
which is not what "one bounded probe" means.

So authorization is also a LEDGER ENTRY. Claiming writes the session id and
the market to an append-only file on the volume BEFORE any order exists,
and a session id that appears in that file is spent forever. The write
happens first on purpose: if the process dies immediately afterwards, the
authorization is already gone, and the failure mode is a probe that did not
run rather than one that ran twice.

Consequences, each of which is a test:

  - no owner token           -> REFUSED, and no POST object is ever built
  - token, first market      -> CLAIMED, once
  - same token, next window  -> REFUSED: AUTHORIZATION_ALREADY_USED
  - crash after claiming     -> REFUSED on restart, same reason
  - a different market       -> REFUSED: AUTHORIZATION_BOUND_TO_MARKET

The ledger is evidence as well as a lock: it records what was authorized,
for which market, when it was claimed, and how it ended.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import hashlib
import pathlib
from typing import Any, Dict, List, Mapping, Optional

import re
from decimal import Decimal as D

from src import pmus_auth as Q1

#: where the spent-authorization ledger lives. On the probe's volume, so it
#: survives the process that wrote it -- which is the entire point.
LEDGER_ENV = "L5_LIVE_AUTH_LEDGER"
DEFAULT_LEDGER = "/state/pmus-ws/l5_live_authorizations.jsonl"

#: the market the owner authorized. Optional: absent, the probe binds to the
#: window that is open when it claims, and records which one that was.
MARKET_ENV = "L5_LIVE_PROBE_MARKET"


class Refused(RuntimeError):
    """Authorization denied. Carries the reason and the evidence."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def ledger_path() -> str:
    return os.getenv(LEDGER_ENV, "").strip() or DEFAULT_LEDGER


def _read(path: str) -> List[Dict[str, Any]]:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            # an unreadable ledger is NOT an empty one: a line we cannot
            # parse might be the very claim that would refuse us, so it is
            # carried through as an opaque record rather than dropped.
            out.append({"session_id": None, "unparsed": line[:200]})
    return out


def _append(path: str, rec: Mapping[str, Any]) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(rec), separators=(",", ":"),
                            default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def session_id() -> str:
    return os.getenv(Q1.SID_ENV, "").strip()


def spent(sid: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The claim that already used this session id, if there is one."""
    if not sid:
        return None
    for rec in _read(path or ledger_path()):
        if rec.get("session_id") == sid and rec.get("event") == "CLAIMED":
            return rec
        if rec.get("unparsed") and sid in str(rec.get("unparsed")):
            return rec
    return None


class Authorization:
    """One claim, one market. Not reusable, by construction."""

    def __init__(self, path: Optional[str] = None, now_fn=None) -> None:
        self.path = path or ledger_path()
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
        self.sid: Optional[str] = None
        self.market: Optional[str] = None
        self.claimed = False
        self.consumed = False

    # ------------------------------------------------------------ claiming

    def claim(self, market: str, *, limits: Any = None,
              series: Optional[str] = None,
              max_windows: int = 1) -> Dict[str, Any]:
        """Spend the authorization on THIS market, or refuse.

        Written before the caller is allowed to arm anything, so the record
        of the attempt cannot be lost by whatever happens next.
        """
        if not Q1.armed():
            raise Refused("OWNER_AUTHORIZATION_ABSENT",
                          {"needs": [Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV],
                           "note": "no order path is built without all three"})
        sid = session_id()
        if not sid:
            raise Refused("OWNER_AUTHORIZATION_ABSENT", {"needs": [Q1.SID_ENV]})
        if not market:
            raise Refused("NO_MARKET", {"note": "a claim names one market"})

        want = os.getenv(MARKET_ENV, "").strip()
        if want and want != market:
            raise Refused("AUTHORIZATION_BOUND_TO_MARKET",
                          {"authorized": want, "attempted": market})

        prior = spent(sid, self.path)
        if prior is not None:
            raise Refused("AUTHORIZATION_ALREADY_USED",
                          {"session_id": sid,
                           "claimed_market": prior.get("market"),
                           "claimed_at": prior.get("utc"),
                           "note": "one authorization trades one market; "
                                   "a new probe needs a new session id"})

        # THE APPROVED ENVELOPE AND THE ACTOR GO IN THE ORIGINAL PAYLOAD.
        # This is the call that creates the exclusive file, and nothing
        # rewrites it afterwards, so this is the only moment at which either
        # can be recorded immutably. `authorize_arming` later verifies both
        # against the running configuration and refuses any difference, which
        # is why the caller must pass the SAME limits object here that it will
        # pass there.
        scope = mandate_scope(market=market, limits=limits, series=series,
                              max_windows=max_windows)
        rec = {"event": "CLAIMED", "session_id": sid, "market": market,
               "utc": self._now().isoformat(),
               "scope": scope, "scope_digest": scope_digest(scope),
               "holder": holder_identity(),
               "note": "written BEFORE arming, so a crash spends it too"}
        # ATOMIC, not just logged. The `spent()` read above cannot exclude a
        # concurrent peer: between its answer and this write a second process
        # reads the same absence and claims too -- two live probes, one
        # authorization. `claim_once` takes the claim with
        # O_CREAT | O_EXCL, so the filesystem picks exactly one winner. Added
        # here and not only at the arming boundary because this is the OTHER
        # entry point, and an entry point that is not atomic is the one that
        # will be used.
        claim_once(self.path, sid, rec)
        _append(self.path, rec)
        self.sid, self.market, self.claimed = sid, market, True
        return dict(rec)

    def check_market(self, market: str) -> None:
        """Refuse any market but the claimed one. Called before every order."""
        if not self.claimed:
            raise Refused("NOT_CLAIMED", {"attempted": market})
        if market != self.market:
            raise Refused("AUTHORIZATION_BOUND_TO_MARKET",
                          {"claimed": self.market, "attempted": market})

    # ----------------------------------------------------------- consuming

    def consume(self, outcome: Mapping[str, Any]) -> Dict[str, Any]:
        """Close the authorization out with what actually happened."""
        rec = {"event": "CONSUMED", "session_id": self.sid,
               "market": self.market, "utc": self._now().isoformat(),
               "outcome": dict(outcome)}
        _append(self.path, rec)
        self.consumed = True
        return dict(rec)

    def as_dict(self) -> Dict[str, Any]:
        return {"claimed": self.claimed, "consumed": self.consumed,
                "market": self.market, "ledger": self.path,
                "session_id_present": bool(self.sid)}


# ===================================================================== the
# SINGLE SHARED ARMING BOUNDARY
# =====================================================================
#
# WHY THIS EXISTS. Three `.arm()` call sites existed and two of them were one
# chain; `src/l5_pmus_live_runner.py` called `transport.arm()` directly. Their
# preconditions were ASYMMETRIC, and the path the test blessed was the WEAKER
# one -- the runner checked the halt latch, unresolved accounting, venue
# reconciliation and uncertain orders, and the one-shot probe checked none of
# those. Worse, the runner spent no authorization at all, and this module
# exists precisely because "an env-only arm is a standing licence to trade
# every future window": a continuous service with restartPolicyType ALWAYS,
# on a market that rolls over four times an hour, would re-arm forever.
#
# So the decision moves here, once, as the UNION of both sets. Neither caller
# can be weaker than the other because neither caller makes the decision.

#: how long a claim stays valid once written. One BTC15m window by default:
#: an authorization that outlives the window it was approved for is a
#: different authorization.
TTL_ENV = "L5_LIVE_AUTH_TTL_S"
DEFAULT_TTL_S = 900.0

#: THE MANDATE. One owner approval covering CONSECUTIVE windows of one
#: series, instead of one window. Default 1, which is Stage A exactly: with a
#: budget of one window nothing below changes behaviour at all.
#:
#: WHY A SECOND LEVEL RATHER THAN A LOOSER CLAIM. `claim_once` writes one lock
#: per key and never clears it, so a single key spends on the first window and
#: a mandate is impossible without either resetting the lock (which defeats
#: single-use) or minting a token per window (a standing licence in all but
#: name). So the key gains a window component and claim_once is untouched: the
#: MANDATE claim stays keyed on the session id and the WINDOW claim is keyed on
#: mandate-plus-market, decided by the same O_EXCL call.
MAX_WINDOWS_ENV = "L5_LIVE_AUTH_MAX_WINDOWS"

#: the series a mandate covers, as an exact ticker prefix. Unset means no
#: mandate may span markets, so a budget above one still trades one ticker.
#: BTC15m only for now: `src/pmus_event_progress.py` measured PMUS sports
#: serving `endDate` as gameStartTime + 14 days, so `endDate` is a window
#: close only where that has been confirmed from venue data, and
#: WINDOW_BOUNDS_IMPLAUSIBLE is what enforces it rather than trust.
SERIES_ENV = "L5_LIVE_AUTH_MARKET_SERIES"

#: an absolute owner deadline, optional. ISO-8601. Belt and braces with the
#: TTL: the TTL bounds how long a claim lives, this bounds when one may be
#: made at all.
NOT_AFTER_ENV = "L5_LIVE_AUTH_NOT_AFTER"

#: the capital envelope the owner approved, recorded INTO the claim. Arming
#: refuses if the running limits are LOOSER than what was approved, so
#: widening a cap after approval invalidates the authorization instead of
#: silently inheriting it.
SCOPED_LIMIT_FIELDS = ("max_usd_per_market", "max_total_exposure_usd",
                       "daily_realized_loss_halt_usd")


def not_after_status(now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Is the owner's absolute deadline still open? WITHOUT emitting it.

    WHY THIS EXISTS. `authorize_arming` evaluates NOT_AFTER at step 8, which
    is unreachable while either trading lock is engaged -- SHADOW_ONLY_LOCK is
    step 1 -- so whether the authorization window had already closed was
    unobservable from outside the container without lifting a lock. That is
    the same blind spot the `auth_ledger` durability diagnostic was added for,
    and it is answered the same way: report the STATUS at boot.

    THE VALUE IS NEVER RETURNED. Only the state and the seconds remaining,
    rounded, so a log line cannot leak a deadline that an operator considers
    sensitive and cannot be mistaken for the variable itself. The refusal at
    step 8 still decides; nothing here gates anything.
    """
    raw = (os.getenv(NOT_AFTER_ENV) or "").strip()
    out: Dict[str, Any] = {"env": NOT_AFTER_ENV, "set": bool(raw),
                           "state": None, "expires_in_s": None,
                           "labels": ("STATUS ONLY -- the configured value is "
                                      "never emitted. Diagnostic: "
                                      "authorize_arming step 8 still decides "
                                      "and still refuses AUTHORIZATION_EXPIRED")}
    if not raw:
        out["state"] = "UNSET"
        return out
    try:
        deadline = dt.datetime.fromisoformat(raw)
    except ValueError:
        out["state"] = "UNREADABLE"
        return out
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=dt.timezone.utc)
    t = now or dt.datetime.now(dt.timezone.utc)
    remaining = (deadline - t).total_seconds()
    out["expires_in_s"] = round(remaining, 1)
    out["state"] = "OPEN" if remaining > 0 else "EXPIRED"
    return out


def max_windows_env() -> int:
    """The approved window budget. Unreadable is the tightest value."""
    raw = (os.getenv(MAX_WINDOWS_ENV) or "").strip()
    try:
        n = int(raw) if raw else 1
    except ValueError:
        n = 1                      # an unreadable budget is one window
    return max(1, n)               # zero or negative would mean "no windows"


def series_prefix() -> str:
    return (os.getenv(SERIES_ENV) or "").strip()


def mandate_mode() -> bool:
    """True when one approval covers more than one window."""
    return max_windows_env() > 1


#: the key separator for a window claim. A dot, because `_lock_path` keeps
#: `[A-Za-z0-9_.-]` and rewrites everything else to an underscore -- a
#: separator that got rewritten could not be told apart from a session id that
#: happened to contain the replacement.
WINDOW_KEY_SEP = ".w."

#: `_lock_path` truncates its key, and a truncated window key could merge two
#: DIFFERENT windows into one lock -- which would hand a spent window back. So
#: the market is hashed to a fixed width and an over-long composite is
#: refused rather than truncated.
WINDOW_KEY_MAX = 110


def window_key(sid: str, market: str) -> str:
    """The claim key for ONE window of a mandate."""
    h = hashlib.sha256(str(market).encode("utf-8")).hexdigest()[:16]
    key = "%s%s%s" % (sid, WINDOW_KEY_SEP, h)
    if len(key) > WINDOW_KEY_MAX:
        raise ArmingRefusal("WINDOW_KEY_TOO_LONG",
                            {"len": len(key), "max": WINDOW_KEY_MAX,
                             "note": "the claim key would be truncated, and "
                                     "a truncated key could merge two "
                                     "windows into one claim"})
    return key


def windows_spent(path: str, sid: str) -> List[str]:
    """The window claims this mandate has already taken, from the volume.

    COUNTED FROM THE LOCK FILES, not from the ledger. The lock is created
    before the ledger row is appended -- deliberately, so a crash in between
    spends the window rather than releasing it -- so the ledger can be short
    by one and the locks cannot. Counting the ledger would let a crash at that
    exact point buy an extra window.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[:120]
    d = pathlib.Path(path).parent
    if not d.is_dir():
        return []
    pre = "l5_auth_claim_%s%s" % (safe, WINDOW_KEY_SEP)
    return sorted(p.name for p in d.glob("l5_auth_claim_*.lock")
                  if p.name.startswith(pre))


def _lock_path(path: str, sid: str) -> pathlib.Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[:120]
    return pathlib.Path(path).parent / ("l5_auth_claim_%s.lock" % safe)


def claim_once(path: str, sid: str, payload: Mapping[str, Any]) -> None:
    """Atomically take the one claim for `sid`, or raise.

    CONCURRENCY. `spent()` reads the ledger and `_append` writes it, and
    between those two a second process can read the same absence and append
    its own claim -- two live probes, one authorization. An append-only log
    cannot exclude that by itself.

    `os.open(..., O_CREAT | O_EXCL)` can: the filesystem decides exactly one
    winner, atomically, and the loser gets FileExistsError. The lock file
    lives beside the ledger on the same durable volume, so it survives a
    restart as well as a concurrent peer. The ledger is still written -- it is
    the audit record -- but the lock, not the read, is what makes the claim
    single-use.
    """
    lock = _lock_path(path, sid)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        prior = ""
        try:
            prior = lock.read_text(errors="replace")[:400]
        except OSError:                                        # pragma: no cover
            prior = "<unreadable>"
        raise Refused("AUTHORIZATION_ALREADY_USED",
                      {"session_id": sid, "lock": str(lock),
                       "prior_claim": prior,
                       "note": "the claim lock already exists; one "
                               "authorization trades once. A new probe "
                               "needs a new session id."})
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(payload), separators=(",", ":"),
                                default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:                                      # pragma: no cover
        # a lock we could not finish writing is still a spent claim: it is
        # NOT removed, because removing it would hand the authorization to
        # the next caller after a partial failure.
        raise


#: THE EXPERIMENT. One authorized measurement, named explicitly by the owner.
#: Unset is not a default -- an armed submission with no named experiment is
#: refused, because a claim that is not tied to an authorization cannot be
#: single-use with respect to it.
EXPERIMENT_ENV = "L5_EXPERIMENT_ID"


def experiment_id() -> str:
    return (os.getenv(EXPERIMENT_ENV) or "").strip()


def submission_sid(experiment: str) -> str:
    """The claim id for THE one submission of one experiment. ONE, total.

    THE MARKET IS DELIBERATELY NOT IN THE ID. An earlier version keyed it on
    experiment AND market, which gave one authorization one submission *per
    market* -- so a single $1 experiment could legitimately have placed an
    order in every 15-minute window of the day, each one passing its own
    fresh claim. A single-market authorization has to be single across the
    WHOLE experiment, and the only way the lock can enforce that is to be
    keyed on the experiment alone: the first submission in any market
    consumes it, and a different market then has nothing left to claim.

    The market is still recorded in the claim payload, so the audit record
    says which market spent it; it just cannot widen the authorization.

    The `sub1.` prefix keeps this in its own namespace, distinct from the
    ARMING claim that `claim_once` takes on the session id.
    """
    return "sub1.%s" % (experiment,)


#: the two-sided probe needs EXACTLY TWO submissions under one
#: authorization, which `submission_sid` deliberately forbids. Two named
#: slots give each leg its own one-shot claim instead of loosening the
#: single-submission rule for everything else.
PROBE_LEGS = ("UP", "DOWN")


def probe_leg_sid(experiment: str, leg: str) -> str:
    return "ts1.%s.%s" % (experiment, leg)


def claim_probe_leg(leg: str, payload: Mapping[str, Any],
                    path: Optional[str] = None) -> Dict[str, Any]:
    """Consume ONE two-sided probe leg's claim, or raise. Never auto-resets.

    Each leg is claimed once, durably, on the same volume as the ledger. A
    cancel, partial fill, reconnect, restart or ambiguous acknowledgment
    never returns a leg's claim, so the probe can place at most one UP and
    one DOWN order for the whole authorization -- and `max_live_orders`
    still bounds how many rest at once.
    """
    exp = experiment_id()
    if not exp:
        raise Refused("PROBE_CLAIM_NO_EXPERIMENT")
    side = str(leg or "").upper()
    if side not in PROBE_LEGS:
        raise Refused("PROBE_CLAIM_BAD_LEG", side)
    sid = probe_leg_sid(exp, side)
    rec = {"kind": "probe_leg_claim", "experiment": exp, "leg": side,
           "sid": sid, "t": dt.datetime.now(dt.timezone.utc).isoformat(),
           **dict(payload)}
    try:
        claim_once(path or ledger_path(), sid, rec)
    except Refused:
        raise
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused("PROBE_CLAIM_UNAVAILABLE",
                      {"sid": sid, "leg": side,
                       "error": "%s: %s" % (type(exc).__name__,
                                            str(exc)[:200]),
                       "note": "the claim could not be persisted, so it is "
                               "not held. Refusing rather than submitting "
                               "on an unproven claim."})
    return rec


#: CONTINUOUS ACCUMULATION. `PROBE_LEGS` above gives the whole
#: authorization exactly two slots, which is what made the two-sided probe a
#: ONE-SHOT experiment: once `ts1.<exp>.UP` and `ts1.<exp>.DOWN` were spent,
#: no further pair could ever be worked, in that window or any later one.
#:
#: Accumulation needs MANY independent intents while keeping the property
#: that actually protects money: the SAME intent can never be submitted
#: twice. So the slot is namespaced by the thing that identifies the intent
#: -- its market, its accumulation round and its leg -- instead of by the
#: leg alone. A replay of the same decision lands on the same sid and is
#: refused exactly as before; a genuinely new intent (next round, next
#: window, next market) gets its own slot.
#:
#: This LOOSENS NOTHING about duplicate protection. `client_order_id` is
#: still derived, `already_submitted(coid)` still suppresses, and
#: `max_live_orders` still bounds concurrency. What is removed is only the
#: artificial cap of two orders per authorization.
def accumulation_sid(experiment: str, market: str, rnd: int, leg: str) -> str:
    return "acc1.%s.%s.%d.%s" % (experiment, market, int(rnd), leg)


def claim_accumulation_leg(market: str, rnd: int, leg: str,
                           payload: Mapping[str, Any],
                           path: Optional[str] = None) -> Dict[str, Any]:
    """Consume ONE accumulation leg's claim, or raise. Never auto-resets.

    Durable on the same volume as the ledger, taken BEFORE the wire, and
    never returned by a cancel, a partial fill, a reconnect, a restart or an
    ambiguous acknowledgement -- identical semantics to `claim_probe_leg`,
    with the slot keyed on (market, round, leg) rather than on leg alone.
    """
    exp = experiment_id()
    if not exp:
        raise Refused("ACC_CLAIM_NO_EXPERIMENT")
    mkt = str(market or "").strip()
    if not mkt:
        raise Refused("ACC_CLAIM_NO_MARKET")
    side = str(leg or "").upper()
    if side not in PROBE_LEGS:
        raise Refused("ACC_CLAIM_BAD_LEG", side)
    try:
        r = int(rnd)
    except (TypeError, ValueError):
        raise Refused("ACC_CLAIM_BAD_ROUND", str(rnd))
    if r < 0:
        raise Refused("ACC_CLAIM_BAD_ROUND", str(rnd))
    sid = accumulation_sid(exp, mkt, r, side)
    rec = {"kind": "accumulation_leg_claim", "experiment": exp,
           "market": mkt, "round": r, "leg": side, "sid": sid,
           "t": dt.datetime.now(dt.timezone.utc).isoformat(),
           **dict(payload)}
    try:
        claim_once(path or ledger_path(), sid, rec)
    except Refused:
        raise
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused("ACC_CLAIM_UNAVAILABLE",
                      {"sid": sid, "leg": side, "market": mkt, "round": r,
                       "error": "%s: %s" % (type(exc).__name__,
                                            str(exc)[:200]),
                       "note": "the claim could not be persisted, so it is "
                               "not held. Refusing rather than submitting "
                               "on an unproven claim."})
    return rec


def claim_held(sid: str, path: Optional[str] = None) -> bool:
    """Is this claim already spent? Read from DISK, failing closed.

    Used for restart recovery: the lock's presence is the durable record
    that a leg was submitted. An unreadable answer is reported as HELD by
    the caller, because re-placing a leg we cannot rule out is the only
    truly unsafe reading.
    """
    return _lock_path(path or ledger_path(), str(sid)).exists()


def submission_claim_state(experiment: Optional[str] = None, *,
                           path: Optional[str] = None) -> Dict[str, Any]:
    """Whether this authorization's ONE submission has already been taken.

    READ-ONLY, and it must stay that way: it stats the lock and never
    creates, truncates or removes one. `claim_submission` remains the only
    writer and its O_CREAT|O_EXCL open remains the only guarantee. This
    answers the question EARLY so that a spent authorization is visible in
    preflight instead of being discovered at the wire -- which is exactly how
    the 2026-10-19 0330z attempt failed: all sixteen gates passed, the plan
    was fully formed, and then the claim refused
    AUTHORIZATION_ALREADY_USED naming no experiment at all, because the
    report's `submission_claim` is only populated AFTER a successful claim.

    UNKNOWN IS NOT UNUSED. A directory we cannot read is reported as UNKNOWN
    with its reason, never as an absence: an absence we cannot see is not an
    absence. `lock_dir` is reported so the directory actually inspected can
    be checked against the one the executor says it is using, because a
    misconfigured ledger path would otherwise look like a clean experiment.
    """
    exp = (experiment if experiment is not None else experiment_id())
    exp = str(exp or "").strip()
    out: Dict[str, Any] = {
        "experiment": exp or None, "sid": None, "lock_dir": None,
        "state": "UNKNOWN", "why": None,
        "labels": ("read-only. UNUSED means the one-submission lock is "
                   "absent from the directory named here; UNKNOWN means we "
                   "could not establish it and is NEVER to be read as an "
                   "absence. The atomic O_EXCL claim, not this, is what "
                   "makes the submission single-use."),
    }
    if not exp:
        out["why"] = "NO_EXPERIMENT_NAMED"
        return out
    sid = submission_sid(exp)
    out["sid"] = sid
    try:
        lock = _lock_path(path or ledger_path(), sid)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        out["why"] = "LOCK_PATH_UNRESOLVABLE:%s" % type(exc).__name__
        return out
    out["lock_dir"] = str(lock.parent)
    try:
        if lock.exists():
            out["state"] = "SPENT"
            out["why"] = "THE_ONE_SUBMISSION_LOCK_ALREADY_EXISTS"
            try:
                out["lock_mtime_utc"] = dt.datetime.fromtimestamp(
                    lock.stat().st_mtime, dt.timezone.utc).isoformat()
            except OSError:
                pass
            return out
        # THE PARENT IS CHECKED ONLY AFTER a negative existence read, and it
        # is what distinguishes the two negatives. An absent directory CANNOT
        # contain the lock, so that is a real UNUSED; a present directory
        # with no lock is the same answer by the same reasoning.
        if not lock.parent.is_dir():
            out["state"] = "UNUSED"
            out["why"] = "LEDGER_DIRECTORY_DOES_NOT_EXIST_YET"
            return out
        out["state"] = "UNUSED"
        out["why"] = "NO_SUCH_LOCK_IN_THE_LEDGER_DIRECTORY"
        return out
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        out["state"] = "UNKNOWN"
        out["why"] = "LEDGER_UNREADABLE:%s" % type(exc).__name__
        return out


def claim_submission(market: str, payload: Mapping[str, Any],
                     path: Optional[str] = None) -> Dict[str, Any]:
    """Consume THE one submission claim, or raise. Never auto-resets.

    WHY THIS EXISTS, AND WHY max_live_orders DID NOT COVER IT.
    `max_live_orders` is checked as `live_order_count >= max_live_orders`,
    which bounds CONCURRENCY. After a cancellation the count returns to zero
    and the check passes again; after a partial fill the cash gate hands back
    the unfilled headroom; and the ARMING claim is reset per window, so a
    restart can re-arm the same window. One order per *authorization* was
    therefore a property of the written specification and not of the engine.
    This makes it a property of the filesystem.

    ATOMIC, AND BEFORE THE WIRE. `claim_once` takes the lock with
    `os.open(O_CREAT|O_EXCL)` and fsyncs it, so exactly one caller wins and
    the record is durable before this returns. The caller must invoke this
    BEFORE the submit, so a process that dies between the two has already
    spent the claim.

    NEVER RESET AUTOMATICALLY. Nothing here or anywhere else removes the
    lock -- not a cancellation, not a partial or zero fill, not a reconnect,
    not a restart, and not a rejected or ambiguous submission. An attempt
    that the venue refused, or whose acknowledgement we could not read, has
    still spent the authorization: the alternative is retrying into a venue
    that may already hold the order. Clearing it is an owner action with the
    credentials directly, exactly like the halt latch.

    FAIL CLOSED. An unnamed experiment, an unwritable directory, or any other
    persistence failure raises `Refused` rather than returning. A claim we
    cannot prove we hold is not a claim.
    """
    exp = experiment_id()
    if not exp:
        raise Refused("SUBMISSION_CLAIM_NO_EXPERIMENT",
                      {"env": EXPERIMENT_ENV,
                       "note": "no named experiment, so a single-use claim "
                               "cannot be bound to an authorization. Unset "
                               "is never a default."})
    mkt = str(market or "").strip()
    if not mkt:
        raise Refused("SUBMISSION_CLAIM_NO_MARKET",
                      {"note": "the claim is per experiment AND market."})
    sid = submission_sid(exp)
    rec = {"kind": "submission_claim", "experiment": exp, "market": mkt,
           "sid": sid,
           "t": dt.datetime.now(dt.timezone.utc).isoformat(),
           **dict(payload)}
    try:
        claim_once(path or ledger_path(), sid, rec)
    except Refused:
        raise
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused("SUBMISSION_CLAIM_UNAVAILABLE",
                      {"sid": sid, "experiment": exp, "market": mkt,
                       "error": "%s: %s" % (type(exc).__name__,
                                            str(exc)[:200]),
                       "note": "the claim could not be persisted, so it is "
                               "not held. Refusing rather than submitting on "
                               "an unproven claim."})
    return rec


def scoped_limits(limits: Any) -> Dict[str, str]:
    """The capital envelope being approved, as strings for the record."""
    out: Dict[str, str] = {}
    d = {}
    try:
        d = dict(limits.as_dict()) if hasattr(limits, "as_dict") else dict(
            limits or {})
    except BaseException:                                      # noqa: BLE001
        d = {}
    for k in SCOPED_LIMIT_FIELDS:
        if k in d:
            out[k] = str(d[k])
    return out


def _looser(now_s: Mapping[str, str], approved: Mapping[str, str]) -> List[str]:
    """Limit fields that are now LOOSER than what was approved."""
    bad: List[str] = []
    for k, was in approved.items():
        if k not in now_s:
            bad.append("%s:missing_now" % k)
            continue
        try:
            if D(str(now_s[k])) > D(str(was)):
                bad.append("%s:%s>%s" % (k, now_s[k], was))
        except (TypeError, ValueError, ArithmeticError):
            bad.append("%s:unreadable" % k)
    return bad


#: the pre-close margin. An authorization may not reach the venue's own
#: close: a resting maker order at the close is exactly the orphan risk the
#: owner has left open, so the claim expires before it.
CLOSE_MARGIN_ENV = "L5_LIVE_AUTH_CLOSE_MARGIN_S"
DEFAULT_CLOSE_MARGIN_S = 60.0

#: bounds are only accepted when the CALLER states they came from the venue.
#: Our own slug convention derives a window from a 900-second clock grid, and
#: a clock-derived window is not a venue-confirmed one -- the two agree until
#: the venue reschedules, and that is the case the margin exists for.
WINDOW_SOURCE_VENUE = "VENUE"

#: the series' nominal window length, checked against the bounds so a
#: fourteen-day contract expiry cannot be mistaken for a fifteen-minute
#: window. `src/pmus_event_progress.py` measured exactly that on PMUS sports.
NOMINAL_WINDOW_TOLERANCE_S = 1.0


def close_margin_s() -> float:
    """The effective pre-close margin. It can be widened, never narrowed."""
    raw = (os.getenv(CLOSE_MARGIN_ENV) or "").strip()
    try:
        v = float(raw) if raw else DEFAULT_CLOSE_MARGIN_S
    except ValueError:
        v = DEFAULT_CLOSE_MARGIN_S          # unreadable is not permission
    return max(DEFAULT_CLOSE_MARGIN_S, v)


def _ts(value: Any) -> Optional[dt.datetime]:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def window_expiry(bounds: Optional[Mapping[str, Any]], *,
                  now: dt.datetime, ttl_s: float,
                  nominal_s: Optional[float] = None) -> Dict[str, Any]:
    """The venue-bound expiry for one window, or a refusal.

    A TTL measured from arming time is not a window bound: arm forty seconds
    before the close and a 900-second TTL authorizes fourteen minutes of
    trading into a market that no longer exists. So the expiry is the EARLIER
    of the TTL and the venue's own close less the margin.
    """
    if not bounds:
        raise ArmingRefusal("WINDOW_BOUNDS_UNKNOWN",
                            {"note": "no venue window bounds were supplied, "
                                     "so the close is unknown and the claim "
                                     "cannot be bounded by it"})
    src = str(bounds.get("source") or "").strip().upper()
    if src != WINDOW_SOURCE_VENUE:
        raise ArmingRefusal("WINDOW_BOUNDS_UNCONFIRMED",
                            {"source": src or None,
                             "needs": WINDOW_SOURCE_VENUE,
                             "note": "a window derived from our own clock "
                                     "grid is not a venue-confirmed window"})
    start, end = _ts(bounds.get("start")), _ts(bounds.get("end"))
    if start is None or end is None:
        raise ArmingRefusal("WINDOW_BOUNDS_UNREADABLE",
                            {"start": str(bounds.get("start"))[:40],
                             "end": str(bounds.get("end"))[:40]})
    if end <= start:
        raise ArmingRefusal("WINDOW_BOUNDS_IMPLAUSIBLE",
                            {"start": start.isoformat(),
                             "end": end.isoformat(),
                             "note": "the close is not after the open"})
    span = (end - start).total_seconds()
    if nominal_s is not None and abs(span - float(nominal_s)) > (
            NOMINAL_WINDOW_TOLERANCE_S):
        raise ArmingRefusal("WINDOW_BOUNDS_IMPLAUSIBLE",
                            {"span_s": span, "nominal_s": float(nominal_s),
                             "note": "the bounds are not this series' "
                                     "window; a contract expiry is not a "
                                     "window close"})
    margin = close_margin_s()
    last = end - dt.timedelta(seconds=margin)
    if now < start:
        raise ArmingRefusal("WINDOW_NOT_OPEN",
                            {"start": start.isoformat(), "now": now.isoformat()})
    if now >= last:
        raise ArmingRefusal("WINDOW_CLOSING",
                            {"end": end.isoformat(), "margin_s": margin,
                             "now": now.isoformat(),
                             "note": "inside the pre-close margin; no new "
                                     "authorization is issued here"})
    ttl_expiry = now + dt.timedelta(seconds=float(ttl_s))
    expires = min(ttl_expiry, last)
    return {"expires_utc": expires.isoformat(),
            "window_start_utc": start.isoformat(),
            "window_end_utc": end.isoformat(),
            "close_margin_s": margin,
            "bounded_by": ("TTL" if ttl_expiry <= last else "VENUE_CLOSE"),
            "source": WINDOW_SOURCE_VENUE}


# ===================================================================== the
# MANDATE SCOPE AND ITS HOLDER
# =====================================================================
#
# Both are written INSIDE the O_EXCL claim payload and the claim file is never
# modified afterwards. That matters: the ledger is append-only, so a later row
# cannot erase an earlier one but can be added to; the claim file is created
# once by the filesystem and no code path here rewrites or removes it. So the
# approved scope and the identity that may act on it are immutable by
# construction rather than by convention.

#: every field of the approved envelope, in a fixed order so the digest is
#: stable. Adding a field here changes the digest, which is correct: a
#: mandate approved before the field existed did not approve it.
SCOPE_FIELDS = ("session_id", "market", "series", "max_windows", "not_after",
                "execution_mode", "close_margin_s", "max_usd_per_market",
                "max_total_exposure_usd", "daily_realized_loss_halt_usd",
                "max_live_orders", "max_cash_deployed_usd")

MAX_CASH_ENV = "L5_MAX_CASH_DEPLOYED_USD"
EXECUTION_MODE = "MAKER_ONLY"


def mandate_scope(*, market: str, limits: Any = None,
                  series: Optional[str] = None,
                  max_windows: int = 1) -> Dict[str, Any]:
    """The exact envelope being approved, as plain strings."""
    d: Dict[str, Any] = {}
    try:
        d = dict(limits.as_dict()) if hasattr(limits, "as_dict") else dict(
            limits or {})
    except BaseException:                                      # noqa: BLE001
        d = {}
    scope = {
        "session_id": session_id(),
        "market": market or "",
        "series": series or "",
        "max_windows": str(int(max_windows)),
        "not_after": (os.getenv(NOT_AFTER_ENV) or "").strip(),
        "execution_mode": EXECUTION_MODE,
        "close_margin_s": str(close_margin_s()),
        "max_live_orders": str(d.get("max_live_orders", "")),
        "max_cash_deployed_usd": (os.getenv(MAX_CASH_ENV) or "").strip(),
    }
    for k in SCOPED_LIMIT_FIELDS:
        scope[k] = str(d.get(k, ""))
    return {k: scope.get(k, "") for k in SCOPE_FIELDS}


def scope_digest(scope: Mapping[str, Any]) -> str:
    canon = json.dumps({k: str(scope.get(k, "")) for k in SCOPE_FIELDS},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _boot_id() -> Optional[str]:
    try:
        return pathlib.Path(
            "/proc/sys/kernel/random/boot_id").read_text().strip() or None
    except OSError:
        return None


def holder_identity() -> Dict[str, Any]:
    """Who is acting. Every field that distinguishes one actor from another."""
    return {"deployment_id": (os.getenv("RAILWAY_DEPLOYMENT_ID") or "").strip()
            or None,
            "replica_id": (os.getenv("RAILWAY_REPLICA_ID") or "").strip()
            or None,
            "service_role": (os.getenv(SERVICE_ROLE_ENV) or "").strip() or None,
            "boot_id": _boot_id(), "pid": os.getpid()}


def holder_refusal(claimed: Mapping[str, Any],
                   mine: Optional[Mapping[str, Any]] = None
                   ) -> Optional[Dict[str, Any]]:
    """None when I am the recorded holder of this claim.

    RAILWAY'S SINGLE WRITER IS AN ASSUMPTION, NOT A FENCE. A volume attaches
    to one service and a service cannot have two deployments active and
    mounted, so in practice one process tree writes this volume -- but that
    is the platform's behaviour, not a guarantee this code can check, and an
    overlap during a deployment would not announce itself. So the assumption
    is VERIFIED here instead of relied upon: the claim records who took it,
    and anybody else is refused.

    A RESTART IS REFUSED TOO, deliberately. A restarted executor has a new
    pid, boot id and deployment id, so it cannot continue on the old claim.
    That is the point: a restart must never silently resume trading.
    """
    mine = dict(mine or holder_identity())
    if not claimed:
        return {"reason": "MANDATE_HOLDER_UNREADABLE",
                "detail": {"note": "the claim records no holder, so the "
                                   "actor cannot be verified"}}
    # an actor that cannot identify its own deployment cannot be distinguished
    # from an overlapping one, which is the uncertain case and so refuses.
    if not mine.get("deployment_id") or not mine.get("boot_id"):
        return {"reason": "DEPLOYMENT_IDENTITY_UNKNOWN",
                "detail": {"have": sorted(k for k, v in mine.items() if v),
                           "note": "RAILWAY_DEPLOYMENT_ID and the boot id "
                                   "are what distinguish one executor from "
                                   "an overlapping one"}}
    differs = [k for k in ("deployment_id", "replica_id", "service_role",
                           "boot_id", "pid")
               if str(claimed.get(k)) != str(mine.get(k))]
    if differs:
        return {"reason": "MANDATE_HOLDER_MISMATCH",
                "detail": {"differs": differs,
                           "note": "this process is not the one the claim "
                                   "was taken by. A restart lands here too, "
                                   "and ends the mandate rather than "
                                   "resuming it."}}
    return None


def read_claim_payload(path: str, sid: str) -> Optional[Dict[str, Any]]:
    """What the exclusive claim file actually records, or None if absent."""
    lock = _lock_path(path, sid)
    if not lock.exists():
        return None
    try:
        return json.loads(lock.read_text(errors="replace") or "{}")
    except (OSError, ValueError):
        return {"unreadable": True}


class ArmingRefusal(Refused):
    """Refused at the shared arming boundary."""


def authorize_arming(*, market: str, preflight: Any, store: Any,
                     halted: bool, reconciled: bool,
                     halt_gate_present: bool = False,
                     unresolved: Any = (), limits: Any = None,
                     shadow_locked: bool = False,
                     window_bounds: Optional[Mapping[str, Any]] = None,
                     nominal_window_s: Optional[float] = None,
                     series: Optional[str] = None,
                     max_windows: int = 1,
                     auth: Optional["Authorization"] = None,
                     path: Optional[str] = None,
                     require_durable: bool = True,
                     now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """THE ONLY PLACE ARMING IS DECIDED. Returns the claim, or raises.

    Checks are ordered cheapest-and-most-structural first, so a refusal names
    the outermost reason rather than an incidental inner one, and so nothing
    is written to the ledger for a request that was never eligible.

    Every check is a REFUSAL, never a warning, and a state this cannot read
    is a refusal too: `reconciled` and `halted` are passed in as facts by the
    caller precisely so that a caller which cannot establish them has to say
    so rather than omit them.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    path = path or ledger_path()

    # 1. the structural lock, before anything else. Arming a transport whose
    #    submit() raises is meaningless, and saying so by name keeps the
    #    runner's logs honest about WHY it is not live.
    if shadow_locked:
        raise ArmingRefusal("SHADOW_ONLY_LOCK",
                            {"note": "submit() is structurally blocked; "
                                     "arming would change nothing"})
    # 2. explicit owner authorization
    if not Q1.armed():
        raise ArmingRefusal("OWNER_AUTHORIZATION_ABSENT",
                            {"needs": [Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV]})
    sid = session_id()
    if not sid:
        raise ArmingRefusal("OWNER_AUTHORIZATION_ABSENT",
                            {"needs": [Q1.SID_ENV]})
    # 3. the halt latch
    if halted:
        raise ArmingRefusal("HALTED", {"note": "halt latch is set"})
    # 3b. THE LATCH MUST EXIST, not merely read "no halt". `halt_gate()`
    #     returns None when L5_HALT_URL is unconfigured, and the submit path
    #     then skips its halt block ENTIRELY -- so an absent latch reads as
    #     PERMISSION. Configured-but-unreachable already refuses correctly
    #     (HttpHaltStore maps every failure to unavailable and HaltGate turns
    #     unavailable into not-permitted); unconfigured was the hole. An
    #     independent halt that can be switched off by forgetting a variable
    #     is not an independent halt.
    if not halt_gate_present:
        raise ArmingRefusal("HALT_GATE_ABSENT",
                            {"needs": ["L5_HALT_URL"],
                             "note": "no independent halt is configured, so "
                                     "nothing outside this process could "
                                     "stop it"})
    # 4. a CURRENT, PASSED preflight in this process
    pf = (preflight or {}).get("preflight") if isinstance(
        preflight, Mapping) else preflight
    if str(pf) != "PASSED":
        raise ArmingRefusal("PREFLIGHT_NOT_PASSED",
                            {"preflight": str(pf or "NOT_RUN")})
    # 5. reconciled against the venue
    if not reconciled:
        raise ArmingRefusal("NOT_RECONCILED",
                            {"note": "never arm on a book the venue has not "
                                     "confirmed"})
    # 6. no uncertain orders -- this is what the reconciliation repair sets
    #    when the venue contradicts our gross book
    why = None
    try:
        why = store.orders_uncertain() if store is not None else None
    except BaseException as exc:                               # noqa: BLE001
        raise ArmingRefusal("ORDER_STATE_UNREADABLE",
                            {"error": "%s" % type(exc).__name__})
    if why:
        raise ArmingRefusal("ORDER_STATE_UNCERTAIN", {"why": str(why)[:300]})
    # 7. no unresolved accounting
    stale = list(unresolved or ())
    if stale:
        raise ArmingRefusal("UNRESOLVED_ACCOUNTING", {"records": stale[:8]})
    # 8. the owner's absolute deadline, if set
    not_after = os.getenv(NOT_AFTER_ENV, "").strip()
    if not_after:
        try:
            deadline = dt.datetime.fromisoformat(not_after)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            raise ArmingRefusal("AUTHORIZATION_DEADLINE_UNREADABLE",
                                {NOT_AFTER_ENV: not_after[:60]})
        if now > deadline:
            raise ArmingRefusal("AUTHORIZATION_EXPIRED",
                                {"not_after": deadline.isoformat(),
                                 "now": now.isoformat()})
    # 9. the capital envelope
    approved = scoped_limits(limits)
    if not approved:
        raise ArmingRefusal("CAPITAL_SCOPE_UNKNOWN",
                            {"needs": list(SCOPED_LIMIT_FIELDS),
                             "note": "an authorization with no capital scope "
                                     "is unbounded, which is not an "
                                     "authorization"})
    # 9b. WHICH MARKET, before anything about its window. An unauthorized
    #     market is refused whatever its clock says, and the refusal names
    #     the outermost reason.
    want = os.getenv(MARKET_ENV, "").strip()
    if want and want != market:
        raise ArmingRefusal("AUTHORIZATION_BOUND_TO_MARKET",
                            {"authorized": want, "attempted": market})
    if not market:
        raise ArmingRefusal("NO_MARKET", {"note": "a claim names one market"})
    # a MANDATE names a series, and nothing outside it. The exact-market
    # binding above still wins when both are set, so a single-market mandate
    # stays expressible.
    pre = series or series_prefix()
    if mandate_mode() and not want:
        if not pre:
            raise ArmingRefusal("MANDATE_SERIES_NOT_DECLARED",
                                {"env": SERIES_ENV,
                                 "note": "a budget above one window with no "
                                         "series named is a licence to trade "
                                         "any market; name the series or set "
                                         "the exact market"})
        if not market.startswith(pre):
            raise ArmingRefusal("AUTHORIZATION_BOUND_TO_MARKET",
                                {"authorized_series": pre,
                                 "attempted": market})
    # 10. THE CLAIM MUST BE ABLE TO SURVIVE, and this process must be the
    #     declared holder. Both are properties of the process and its
    #     storage, so they are structurally outside anything about the
    #     window: a claim written where it cannot persist is worse than no
    #     claim, because it reports success and protects nothing.
    if require_durable:
        bad = role_refusal() or durability_refusal(path)
        if bad:
            raise ArmingRefusal(bad["reason"], bad["detail"])
    # 10b. THE VENUE'S OWN WINDOW BOUNDS THE CLAIM. A TTL measured from
    #      arming time does not: arm forty seconds before the close with a
    #      900-second TTL and fourteen minutes of trading are authorized into
    #      a market that has gone. Bounds are passed in as a FACT, like
    #      `reconciled` and `halted`, so a caller that cannot establish them
    #      has to say so and be refused rather than omit them and silently
    #      inherit a clock-derived window.
    try:
        ttl = float(os.getenv(TTL_ENV, "").strip() or DEFAULT_TTL_S)
    except ValueError:
        ttl = DEFAULT_TTL_S
    window = window_expiry(window_bounds, now=now, ttl_s=ttl,
                           nominal_s=nominal_window_s)
    # 10c. the approved envelope and the actor, both of which go INSIDE the
    #      payload claim_once writes with O_EXCL. The claim file is never
    #      modified afterwards, so neither can be edited into something else
    #      later.
    # the scope records the budget and series ACTUALLY IN FORCE, so changing
    # either after approval invalidates the mandate rather than extending it.
    # THE ENVIRONMENT IS THE AUTHORITY on the budget, not the caller: a
    # caller that could widen it by argument would be a second way to
    # authorize windows, and the whole point of this module is that there is
    # one. `max_windows` survives in the signature only for the scope a
    # pre-boundary `Authorization.claim` records.
    mw = max_windows_env()
    scope = mandate_scope(market=("" if mw > 1 else market), limits=limits,
                          series=pre, max_windows=mw)
    digest = scope_digest(scope)
    holder = holder_identity()
    claim = {"event": "CLAIMED", "session_id": sid, "market": market,
             "utc": now.isoformat(), "expires_utc": window["expires_utc"],
             "ttl_s": ttl, "approved_limits": approved,
             "window": window, "scope": scope, "scope_digest": digest,
             "holder": holder,
             "boundary": "authorize_arming",
             "note": "written BEFORE arming, so a crash spends it too"}
    # 10d. A MANDATE: ONE APPROVAL, CONSECUTIVE WINDOWS, TWO LEVELS OF CLAIM.
    #
    #      Level 1, the MANDATE claim, is keyed on the session id exactly as a
    #      Stage A claim is, and carries the scope digest and the holder. It is
    #      taken once and never cleared, so one owner approval stays
    #      single-use: a second mandate needs a new session id.
    #
    #      Level 2, the WINDOW claim, is keyed on mandate-plus-market through
    #      the SAME `claim_once`, so the filesystem decides one winner per
    #      window and a spent window stays spent forever. The market ticker is
    #      the window identity -- BTC15m tickers are unique per window -- so
    #      there is no counter or clock to drift or replay.
    #
    #      Nothing here relaxes a Stage A check: every one above has already
    #      run for THIS window, including venue reconciliation, the halt gate,
    #      uncertain orders and the capital envelope. A mandate only lets the
    #      next window ASK.
    if mw > 1:
        if not os.getenv(NOT_AFTER_ENV, "").strip():
            raise ArmingRefusal("MANDATE_HAS_NO_DEADLINE",
                                {"env": NOT_AFTER_ENV,
                                 "note": "a budget above one window with no "
                                         "absolute deadline expires only when "
                                         "the budget runs out, which is not a "
                                         "bounded session"})
        recorded = read_claim_payload(path, sid)
        if recorded is None:
            # the mandate itself, taken once
            mandate = dict(claim, event="MANDATE", window=None,
                           note=("the mandate, taken once; each window takes "
                                 "its own claim under it"))
            claim_once(path, sid, mandate)
            _append(path, mandate)
        else:
            # EVERY WINDOW RE-PROVES THE MANDATE IT IS ACTING UNDER.
            if recorded.get("unreadable"):
                raise ArmingRefusal("MANDATE_SCOPE_UNREADABLE",
                                    {"note": "the mandate payload cannot be "
                                             "read, so neither its scope nor "
                                             "its holder can be verified"})
            was = str(recorded.get("scope_digest") or "")
            if not was:
                raise ArmingRefusal("MANDATE_SCOPE_UNRECORDED",
                                    {"note": "this claim predates scope "
                                             "recording and cannot be "
                                             "verified"})
            if was != digest:
                changed = [k for k in SCOPE_FIELDS
                           if str((recorded.get("scope") or {}).get(k, ""))
                           != str(scope.get(k, ""))]
                raise ArmingRefusal("MANDATE_SCOPE_CHANGED",
                                    {"fields": changed or ["<digest only>"],
                                     "note": "the running configuration is "
                                             "not the one this mandate was "
                                             "approved for"})
            bad = holder_refusal(recorded.get("holder") or {}, holder)
            if bad:
                raise ArmingRefusal(bad["reason"], bad["detail"])
        # THE BUDGET, counted from the locks on the volume rather than from
        # memory, so a restart cannot buy another window.
        wkey = window_key(sid, market)
        spent = windows_spent(path, sid)
        mine = _lock_path(path, wkey).name
        if mine not in spent and len(spent) >= mw:
            raise ArmingRefusal("MANDATE_WINDOW_BUDGET_SPENT",
                                {"spent": len(spent), "budget": mw,
                                 "note": "every window of this mandate has "
                                         "been used; a further window needs a "
                                         "new owner approval"})
        wclaim = dict(claim, event="WINDOW",
                      mandate_session_id=sid, window_key=wkey,
                      windows_spent=len(spent), window_budget=mw,
                      note=("one window of a mandate; the claim is spent on "
                            "this market and is never cleared"))
        # a window already spent is refused here, by the same O_EXCL call that
        # refuses a second Stage A claim: AUTHORIZATION_ALREADY_USED.
        claim_once(path, wkey, wclaim)
        _append(path, wclaim)
        if auth is not None:
            auth.sid, auth.market, auth.claimed = sid, market, True
        return dict(wclaim)

    # 11. THE CLAIM ITSELF -- taken here unless THIS process already holds it.
    #
    # The one-shot probe claims BEFORE it builds a transport, on purpose:
    # "without it nothing that can POST is built". That ordering is a
    # stronger property than centralising the claim, so it is preserved. The
    # boundary therefore RECOGNISES an existing claim held by the same
    # Authorization object for the same market, and scopes it, rather than
    # taking a second one and refusing itself.
    #
    # This is not a bypass. The claim lock still exists on the volume, so a
    # second PROCESS is still refused; what is skipped is only the duplicate
    # write by the process that already won. A claim held for a DIFFERENT
    # market is refused outright rather than reused.
    already = bool(auth is not None and getattr(auth, "claimed", False))
    if already and getattr(auth, "market", None) != market:
        raise ArmingRefusal("AUTHORIZATION_BOUND_TO_MARKET",
                            {"claimed": getattr(auth, "market", None),
                             "attempted": market})
    if already and getattr(auth, "sid", None) not in (None, sid):
        raise ArmingRefusal("AUTHORIZATION_SESSION_MISMATCH",
                            {"claimed_by": getattr(auth, "sid", None),
                             "now": sid})
    if already:
        # THE SCOPED PATH MUST PROVE THE CLAIM, NOT BE TOLD OF IT. `auth` is
        # an in-process object, and an in-process object can be forged by any
        # process that constructs one and sets three attributes. So the lock
        # is re-checked here: scoping is only permitted when the exclusive
        # claim file actually exists for THIS session id, which only the
        # process that won it can have created.
        #
        # Without this, a peer could present a forged holder and get an audit
        # row. It could still not arm -- the lock owns exclusivity and
        # transport.arm() needs the owner token -- but "it cannot do harm" is
        # a weaker property than "it is refused", and this is cheap.
        lock = _lock_path(path, sid)
        if not lock.exists():
            raise ArmingRefusal("CLAIM_NOT_HELD",
                                {"session_id": sid, "lock": str(lock),
                                 "note": "an in-process claim is asserted but "
                                         "no exclusive claim lock exists for "
                                         "it; the holder cannot be verified"})
        # AND THE RECORDED CLAIM MUST STILL MEAN WHAT IT MEANT. Existence of
        # the lock proved only that somebody won it. Two things are checked
        # against what is INSIDE it:
        #
        #   the scope digest -- an existing claim must never authorize a
        #   MODIFIED scope, so any difference refuses. Equality, not
        #   looseness: a tightened envelope is a different mandate from the
        #   one approved, and inheriting it silently is the same defect as
        #   inheriting a widened one.
        #
        #   the holder -- see holder_refusal. A restart is refused here.
        recorded = read_claim_payload(path, sid) or {}
        if recorded.get("unreadable"):
            raise ArmingRefusal("MANDATE_SCOPE_UNREADABLE",
                                {"lock": str(lock),
                                 "note": "the claim payload cannot be read, "
                                         "so neither the approved scope nor "
                                         "the holder can be verified"})
        was = str(recorded.get("scope_digest") or "")
        if not was:
            raise ArmingRefusal("MANDATE_SCOPE_UNRECORDED",
                                {"lock": str(lock),
                                 "note": "this claim predates scope "
                                         "recording; it cannot be verified "
                                         "and so cannot be scoped"})
        if was != digest:
            changed = [k for k in SCOPE_FIELDS
                       if str((recorded.get("scope") or {}).get(k, ""))
                       != str(scope.get(k, ""))]
            raise ArmingRefusal("MANDATE_SCOPE_CHANGED",
                                {"fields": changed or ["<digest only>"],
                                 "note": "the running configuration is not "
                                         "the one this claim was approved "
                                         "for"})
        bad_holder = holder_refusal(recorded.get("holder") or {}, holder)
        if bad_holder:
            raise ArmingRefusal(bad_holder["reason"], bad_holder["detail"])
        claim["event"] = "SCOPED"
        claim["note"] = ("the claim was taken by this process before any "
                         "POST-capable object existed; the boundary scoped "
                         "it with its expiry and capital envelope")
        _append(path, claim)
        return dict(claim)
    claim_once(path, sid, claim)
    _append(path, claim)
    if auth is not None:
        auth.sid, auth.market, auth.claimed = sid, market, True
    return dict(claim)


def still_valid(claim: Mapping[str, Any], *, limits: Any = None,
                now: Optional[dt.datetime] = None) -> Optional[str]:
    """Re-check a claim at use time. None means still good.

    Called before an order, not just at arming: a claim that was valid when
    the window opened can expire inside it, and a limit that was in scope at
    arming can be widened afterwards.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    exp = str((claim or {}).get("expires_utc") or "")
    if not exp:
        return "AUTHORIZATION_HAS_NO_EXPIRY"
    try:
        deadline = dt.datetime.fromisoformat(exp)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return "AUTHORIZATION_EXPIRY_UNREADABLE"
    if now > deadline:
        return "AUTHORIZATION_EXPIRED"
    approved = dict((claim or {}).get("approved_limits") or {})
    if not approved:
        return "CAPITAL_SCOPE_UNKNOWN"
    loose = _looser(scoped_limits(limits), approved)
    if loose:
        return "CAPITAL_SCOPE_WIDENED:%s" % ",".join(loose)
    return None


# ===================================================================== the
# DURABILITY PRECONDITION
# =====================================================================
#
# THE DEFECT THIS EXISTS FOR, measured on the deployed service 2026-10-14.
# `l5-pmus-live` mounts its volume at `/data` and overrides L5_LIVE_ROOT to
# `/data/l5-live-20261012z`, so its INVENTORY is durable. But
# `L5_LIVE_AUTH_LEDGER` is NOT set on that service, so the authorization
# ledger falls back to DEFAULT_LEDGER -- `/state/pmus-ws/...` -- which is
# container-local, and `_lock_path` puts the exclusive claim lock in the same
# directory. With restartPolicyType ALWAYS, every restart would wipe both and
# hand the authorization back. That is precisely the standing licence this
# module exists to prevent, reintroduced by a path mismatch.
#
# My own tests could not catch it: they pass an explicit `path` under tmp.
# So the check is on the RESOLVED path, at the boundary, in production terms.
#
# NO NEW DATABASE AND NO NEW SERVICE. The question "is this path durable" is
# answered by the filesystem: walk up to the nearest mount point and refuse
# if it is the container root. A Railway volume is a real mount; ephemeral
# container storage is not.

#: single-replica is an ASSUMPTION of the lock, so it is checked rather than
#: assumed. Railway exposes a replica id per replica; a count above one means
#: the lock is per-container and exclusivity is not guaranteed.
REPLICA_COUNT_ENVS = ("RAILWAY_REPLICA_COUNT", "L5_REPLICA_COUNT")


def nearest_mount(path: str) -> str:
    """The mount point the path lives on."""
    p = pathlib.Path(path).resolve()
    if not p.is_dir():
        p = p.parent
    while True:
        if os.path.ismount(str(p)) or p == p.parent:
            return str(p)
        p = p.parent


#: WHICH SERVICE HOLDS AUTHORIZATION AUTHORITY. A file lock excludes peers
#: that can see the same file. Two Railway services are two containers with
#: two filesystems, so a lock in one cannot exclude the other -- and NO deploy
#: config in this repo sets L5_LIVE_AUTH_LEDGER, so every order-capable image
#: would fall back to its own container-local copy.
#:
#: That is not fixable with a lock, and it does not need a database or a new
#: service to fix, because we do not run two order-capable processes. So the
#: supported configuration is DECLARED and anything else is refused: the owner
#: names the one service role that may hold authorization, and a process whose
#: own role differs refuses to arm.
OWNER_ROLE_ENV = "L5_LIVE_AUTH_OWNER_ROLE"
SERVICE_ROLE_ENV = "PMUS_SERVICE_ROLE"


def role_refusal() -> Optional[Dict[str, Any]]:
    """None when this process is the declared holder of authorization."""
    declared = os.getenv(OWNER_ROLE_ENV, "").strip()
    mine = os.getenv(SERVICE_ROLE_ENV, "").strip()
    if not declared:
        return {"reason": "AUTH_OWNER_ROLE_NOT_DECLARED",
                "detail": {"env": OWNER_ROLE_ENV, "this_role": mine or None,
                           "note": "a file lock cannot exclude a peer in "
                                   "another container, so the one service "
                                   "that may hold authorization must be "
                                   "named. Unset means nobody may arm."}}
    if not mine:
        return {"reason": "SERVICE_ROLE_UNKNOWN",
                "detail": {"env": SERVICE_ROLE_ENV,
                           "note": "a process that cannot say which service "
                                   "it is cannot be the declared holder"}}
    if mine != declared:
        return {"reason": "NOT_THE_AUTHORIZATION_HOLDER",
                "detail": {"declared": declared, "this_role": mine,
                           "note": "another service is the declared holder; "
                                   "this one may observe and cancel but not "
                                   "arm"}}
    return None


def durability_refusal(path: str) -> Optional[Dict[str, Any]]:
    """None when the claim can survive a restart, else the reason.

    A claim lock on ephemeral storage is not a claim lock: the process that
    restarts reads the same environment, finds no lock, and arms again.
    """
    mount = nearest_mount(path)
    if mount == "/":
        return {"reason": "AUTHORIZATION_LEDGER_NOT_DURABLE",
                "detail": {"ledger": str(path), "nearest_mount": mount,
                           "env": LEDGER_ENV,
                           "note": "the ledger and its exclusive claim lock "
                                   "are on container-local storage, so a "
                                   "restart would hand the authorization "
                                   "back. Point %s at a path on the "
                                   "service's mounted volume." % LEDGER_ENV}}
    for env in REPLICA_COUNT_ENVS:
        raw = os.getenv(env, "").strip()
        if not raw:
            continue
        try:
            n = int(raw)
        except ValueError:
            return {"reason": "REPLICA_COUNT_UNREADABLE",
                    "detail": {env: raw[:40]}}
        if n > 1:
            return {"reason": "EXCLUSIVITY_NOT_GUARANTEED",
                    "detail": {env: n,
                               "note": "the claim lock is a file on one "
                                       "volume; with more than one replica "
                                       "it cannot exclude a peer. Run one "
                                       "replica, or do not arm."}}
    return None
