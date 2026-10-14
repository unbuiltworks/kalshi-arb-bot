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

    def claim(self, market: str) -> Dict[str, Any]:
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

        rec = {"event": "CLAIMED", "session_id": sid, "market": market,
               "utc": self._now().isoformat(),
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


class ArmingRefusal(Refused):
    """Refused at the shared arming boundary."""


def authorize_arming(*, market: str, preflight: Any, store: Any,
                     halted: bool, reconciled: bool,
                     unresolved: Any = (), limits: Any = None,
                     shadow_locked: bool = False,
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
    # 10. and only now, the atomic single-use claim
    try:
        ttl = float(os.getenv(TTL_ENV, "").strip() or DEFAULT_TTL_S)
    except ValueError:
        ttl = DEFAULT_TTL_S
    expires = now + dt.timedelta(seconds=ttl)
    claim = {"event": "CLAIMED", "session_id": sid, "market": market,
             "utc": now.isoformat(), "expires_utc": expires.isoformat(),
             "ttl_s": ttl, "approved_limits": approved,
             "boundary": "authorize_arming",
             "note": "written BEFORE arming, so a crash spends it too"}
    want = os.getenv(MARKET_ENV, "").strip()
    if want and want != market:
        raise ArmingRefusal("AUTHORIZATION_BOUND_TO_MARKET",
                            {"authorized": want, "attempted": market})
    if not market:
        raise ArmingRefusal("NO_MARKET", {"note": "a claim names one market"})
    # 10b. THE CLAIM MUST BE ABLE TO SURVIVE. Checked before the claim is
    #      taken, because a claim written where it cannot persist is worse
    #      than no claim: it reports success and protects nothing.
    if require_durable:
        bad = role_refusal() or durability_refusal(path)
        if bad:
            raise ArmingRefusal(bad["reason"], bad["detail"])

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
