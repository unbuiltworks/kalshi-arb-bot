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

#: THE PRE-DEPLOYMENT DRAIN SWITCH. Set it to stop admitting new exposure
#: without stopping the process, so a deploy can be staged against a book
#: that is verifiably empty. UNSET MEANS RUNNING -- an absent variable never
#: pauses a live executor -- and any set value other than an explicit
#: "0"/"false"/"no"/"off" pauses, because the safe reading of an
#: unrecognised value in a STOP switch is stop. It gates ARMING only:
#: cancellation, reconciliation and settlement stay live by design.
SUBMIT_PAUSE_ENV = "L5_SUBMIT_PAUSE"

#: the capital envelope the owner approved, recorded INTO the claim. Arming
#: refuses if the running limits are LOOSER than what was approved, so
#: widening a cap after approval invalidates the authorization instead of
#: silently inheriting it.
SCOPED_LIMIT_FIELDS = ("max_usd_per_market", "max_total_exposure_usd",
                       "daily_realized_loss_halt_usd")


#: the campaign worst-case budget, in dollars. UNSET MEANS OFF: no budget is
#: constructed, no behaviour changes, and nothing new can refuse. A gate that
#: switched itself on by default would be a limit nobody approved.
CAMPAIGN_BUDGET_ENV = "L5_CAMPAIGN_BUDGET_USD"


def campaign_budget_ledger(root: Optional[str] = None) -> Any:
    """The configured campaign budget, or None when it is not configured.

    Never raises: a construction failure returns None and leaves the
    pre-existing gates to decide, exactly as they did before this module
    existed. The budget's own read failures are refusals, but that is the
    LEDGER's contract and is evaluated where it is consulted.
    """
    raw = (os.getenv(CAMPAIGN_BUDGET_ENV) or "").strip()
    if not raw:
        return None
    try:
        from src import l5_campaign_budget as CBD
        return CBD.CampaignBudgetLedger(root=root, budget_usd=raw)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


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


#: the running worst-case deficit limit, read for the preflight row only.
#: Named here rather than imported from the runner so this module keeps no
#: dependency on it; the runner's own `DEFICIT_ENV` is the same string and
#: `test_the_deficit_env_name_matches_the_runners` pins that they agree.
DEFICIT_LIMIT_ENV = "L5_MAX_WORST_CASE_DEFICIT_USD"


def window_owner_refusal(market: Any) -> Optional[Dict[str, Any]]:
    """`l5_two_sided_probe.window_owner_refusal`, imported where it is used.

    LOCAL IMPORT, for the reason every other strategy import in this module
    is local: this file ships in images that do not carry the strategy.

    FAIL CLOSED WHEN AN OWNER IS NAMED AND CANNOT BE CHECKED. If
    `L5_WINDOW_STRATEGY` is set and the strategy module will not import or
    evaluate, the window's owner is unverifiable and arming refuses by name.
    With no owner named there is nothing to verify and nothing is refused,
    so an image without the strategy is unaffected.
    """
    named = str(os.getenv("L5_WINDOW_STRATEGY", "")).strip()
    try:
        from src import l5_two_sided_probe as _TSW
        return _TSW.window_owner_refusal(str(market or ""))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        if not named:
            return None
        return {"reason": "WINDOW_OWNER_UNVERIFIABLE", "owner": named[:40],
                "error": type(exc).__name__,
                "note": "an owner is named and could not be checked"}


def authorization_preflight(market: str = "", *,
                            now: Optional[dt.datetime] = None,
                            root: Optional[str] = None) -> Dict[str, Any]:
    """EVERY config-level arming blocker at once, read-only. Nothing is armed.

    WHY THIS EXISTS, and it is an operational scar rather than a nicety.
    `authorize_arming` raises on the FIRST refusal, which is correct for a
    money path and useless for preparing a campaign: on 2026-10-23 the owner
    applied three Railway updates across 40 minutes and three redeploys,
    each revealing the next refusal in sequence -- step 14
    CAMPAIGN_LIMITS_WIDER_THAN_APPROVED, then step 26 AUTHORIZATION_EXPIRED --
    with no way to see whether anything behind them would also refuse. Four
    approved windows went unused, not through risk but through discovery.

    IT CALLS THE SAME ACCESSORS THE MONEY PATH CALLS. `not_after_status`,
    `series_prefix`, `role_refusal`, `max_windows_env`, `session_id` and
    `campaign_approval.campaign_decision` are the real ones, so this cannot
    drift from what actually enforces. A second implementation that computed
    these independently would eventually disagree, and then the preflight
    would be describing a system nobody is running.

    IT RESERVES NOTHING. `campaign_decision` is called with `reserve=False`,
    so asking "would this window be granted?" never spends one -- the
    property added after verification consumed the 1600z window.

    IT NEVER EMITS A CONFIGURED VALUE. Deadlines report OPEN/EXPIRED/UNSET
    and seconds remaining; the series reports set/unset and whether the
    market matches. A preflight that leaked the values it checks would be a
    worse problem than the one it solves.

    WHAT IT DOES NOT COVER, named rather than implied: the sequential claim
    steps -- window bounds, CLAIM_NOT_HELD, MANDATE_SCOPE_CHANGED and the
    ORDER_STATE/reconciliation reads -- depend on live venue state and on
    state created earlier in the same call, so they are not independently
    evaluable here. They are reported as NOT_EVALUATED, never as PASS.
    """
    t = now or dt.datetime.now(dt.timezone.utc)
    rows: List[Dict[str, Any]] = []

    def row(name: str, verdict: str, **d: Any) -> None:
        rows.append(dict({"check": name, "verdict": verdict}, **d))

    # 1. the two locks the owner controls directly
    try:
        from src import l5_pmus_live_runner as _R
        lock = bool(_R.shadow_only())
        row("shadow_lock", "FAIL" if lock else "PASS", engaged=lock,
            blocks="SHADOW_ONLY_LOCK (step 9)")
    except BaseException as exc:                               # noqa: BLE001
        row("shadow_lock", "UNKNOWN", error=type(exc).__name__)

    # 1b. THE LOCAL OPERATOR HALT, which this preflight did not evaluate.
    #
    # WHAT IT COST, on 2026-11-09. `Runner.try_arm` opens with
    #
    #     if t is None or t.armed or self.halted(): return
    #
    # so the operator halt stops arming BEFORE any numbered step below is
    # reached. This preflight checked the shadow lock, the owner token, the
    # deadline, the campaign, the halt GATE and nine other things -- and not
    # that one line. The owner-approved 08:30Z window therefore read
    # `ok true`, `fail []`, campaign `approved`, shadow lock cleared, owner
    # token valid, while all 162 decisions in the window refused
    # `SAFETY_OVERLAY:OPERATOR_HALT`. Zero orders. The window was not
    # consumed and exposure stayed $0 -- the engine failed CLOSED, which is
    # right -- but a readiness verdict that cannot see a blocker is worse
    # than no verdict, because it is believed.
    #
    # IT IS A DIFFERENT THING FROM `halt_gate` BELOW, and both are reported.
    # `halt_gate` is the INDEPENDENT watchdog latch, read over HTTP from a
    # store the executor cannot write. This is the LOCAL env halt that the
    # operator sets on the service itself. Either one alone stops arming, so
    # collapsing them would hide whichever was not chosen.
    #
    # SAME EXPRESSION, NOT A SECOND OPINION. It calls
    # `l5_pmus_live_runner.operator_halted`, which `Runner.halted` now
    # delegates to, so the preflight and `try_arm` cannot disagree about
    # whether the halt is engaged. A duplicated `os.getenv` here would be
    # exactly the divergence this repairs.
    try:
        from src import l5_pmus_live_runner as _R2
        _oh = bool(_R2.operator_halted())
        row("local_operator_halt", "FAIL" if _oh else "PASS", engaged=_oh,
            env=_R2.LIVE_HALT_ENV,
            blocks="Runner.try_arm returns before step 1",
            note=("any non-empty value halts; it is a deadman, so there is "
                  "no 'false' spelling. Distinct from the independent "
                  "watchdog latch reported as halt_gate."))
    except BaseException as exc:                               # noqa: BLE001
        row("local_operator_halt", "UNKNOWN", error=type(exc).__name__)

    # 1c. THE WINDOW'S OWNER, which this preflight did not evaluate either.
    #
    # Reported whether or not it refuses, so its absence can never be read
    # as a pass. WITHOUT A MARKET the binding cannot be judged -- an exact
    # slug either matches a window or does not -- so the startup line says
    # NOT_EVALUATED rather than guessing in either direction; the
    # market-specific verdict on the heartbeat is the one that counts.
    try:
        if not market and str(os.getenv("L5_WINDOW_STRATEGY", "")).strip():
            row("window_owner_active", "NOT_EVALUATED",
                owner=str(os.getenv("L5_WINDOW_STRATEGY", "")).strip()[:40],
                note="needs the market slug; evaluated per window")
        else:
            _wo = window_owner_refusal(market)
            row("window_owner_active", "PASS" if _wo is None else "FAIL",
                owner=(str(os.getenv("L5_WINDOW_STRATEGY", "")).strip()[:40]
                       or None),
                blocks="authorize_arming step 1a",
                detail=_wo)
    except BaseException as exc:                               # noqa: BLE001
        row("window_owner_active", "UNKNOWN", error=type(exc).__name__)

    try:
        row("owner_arm_token", "PASS" if Q1.armed() else "FAIL",
            blocks="OWNER_AUTHORIZATION_ABSENT (step 11)")
    except BaseException as exc:                               # noqa: BLE001
        row("owner_arm_token", "UNKNOWN", error=type(exc).__name__)

    # 2. the absolute deadline -- the step-26 refusal, now visible first.
    #
    # UNSET IS NOT UNCONDITIONALLY FINE, and this is the subtlety that makes
    # a naive preflight wrong. Step 35 refuses MANDATE_HAS_NO_DEADLINE when
    # `mw > 1` AND no valid campaign is present, because a multi-window
    # mandate must get its deadline from somewhere. A VALID CAMPAIGN CARRIES
    # IT, so with one in force the env deadline is genuinely optional --
    # which also means clearing a stale one is a legitimate fix rather than
    # a widening.
    st = not_after_status(now=t)
    _state = str(st.get("state"))
    _camp_valid = False
    try:
        from src import l5_campaign_approval as _CA
        _camp_valid = bool(_CA.campaign_decision(
            market or "", root=root, now=t, reserve=False).get("ok"))
    except BaseException:                                      # noqa: BLE001
        _camp_valid = False
    if _state == "UNSET":
        _mw_now = max_windows_env()
        if _mw_now > 1 and not _camp_valid:
            _v, _n = "FAIL", ("multi-window mandate with no valid campaign "
                              "must have an env deadline")
            _b = "MANDATE_HAS_NO_DEADLINE (step 35)"
        else:
            _v, _n = "PASS", ("optional: the valid campaign carries the "
                              "deadline" if _camp_valid else
                              "single-window mode needs no env deadline")
            _b = None
    else:
        _v = {"OPEN": "PASS", "EXPIRED": "FAIL",
              "UNREADABLE": "FAIL"}.get(_state, "UNKNOWN")
        _n, _b = None, "AUTHORIZATION_EXPIRED (step 26)"
    row("absolute_deadline", _v, state=_state,
        expires_in_s=st.get("expires_in_s"), env=NOT_AFTER_ENV,
        blocks=_b, note=_n)

    # 3. the claim TTL
    raw_ttl = (os.getenv(TTL_ENV) or "").strip()
    try:
        ttl = float(raw_ttl) if raw_ttl else float(DEFAULT_TTL_S)
        row("claim_ttl_s", "PASS" if ttl > 0 else "FAIL", ttl_s=ttl,
            using_default=not raw_ttl, env=TTL_ENV)
    except ValueError:
        row("claim_ttl_s", "FAIL", env=TTL_ENV, note="unparseable")

    # 4. series declaration and whether it covers this market.
    #
    # THE ENV IS NOT THE ONLY SOURCE, and a preflight that assumed it was
    # would report a false FAIL. Step 31 resolves
    # `series or series_prefix() or campaign_scope["series"]`, so a VALID
    # CAMPAIGN DECLARING ITS OWN SERIES satisfies the check with the env
    # unset. Reported with which source supplied it, because "declared by
    # the campaign" and "declared by the environment" fail in different
    # ways later.
    pref = series_prefix()
    _src = "env" if pref else None
    if not pref and _camp_valid:
        try:
            from src import l5_campaign_approval as _CA2
            _d2 = _CA2.campaign_decision(market or "", root=root, now=t,
                                         reserve=False)
            pref = str((_d2.get("scope") or {}).get("series") or "")
            _src = "campaign" if pref else None
        except BaseException:                                  # noqa: BLE001
            pref, _src = "", None
    if not pref:
        row("market_series", "FAIL", env=SERIES_ENV, declared_by=None,
            blocks="MANDATE_SERIES_NOT_DECLARED (step 31)",
            note="neither the environment nor a valid campaign declares it")
    elif market and not str(market).startswith(pref):
        row("market_series", "FAIL", env=SERIES_ENV, declared_by=_src,
            covers_market=False, blocks="AUTHORIZATION_BOUND_TO_MARKET")
    else:
        row("market_series", "PASS", env=SERIES_ENV, declared_by=_src,
            covers_market=bool(market) or None)

    # 5. role and session binding
    rr = role_refusal()
    row("role_binding", "PASS" if rr is None else "FAIL",
        reason=(rr or {}).get("reason"),
        blocks="AUTHORIZATION_SESSION_MISMATCH (step 41)")
    try:
        row("session_id", "PASS" if session_id() else "FAIL")
    except BaseException as exc:                               # noqa: BLE001
        row("session_id", "UNKNOWN", error=type(exc).__name__)

    # 6. the window budget, as the INTERSECTION the code actually applies
    mw = max_windows_env()
    row("window_budget_env", "PASS" if mw >= 1 else "FAIL",
        max_windows_env=mw, env=MAX_WINDOWS_ENV,
        note=("unset defers to the campaign; a SET value can only tighten "
              "it, so a stale 1 silently caps a four-window campaign"))

    # 7. the campaign approval, WITHOUT reserving a window.
    #
    # WITH NO MARKET YET, DO NOT ASSERT MARKET COVERAGE. At startup the
    # transport has no slug, and `campaign_decision("")` reaches
    # authorize_window's series test -- `"".startswith("cpc-btc-...")` is
    # False -- so a perfectly valid approval was reported as
    # CAMPAIGN_SERIES_MISMATCH. That false FAIL was emitted in production on
    # 2026-10-23T19:37 against an approval whose state was "approved" with
    # the right exposure, windows and daily loss, and it is exactly the kind
    # of misleading diagnostic this preflight exists to eliminate.
    #
    # So: with a market, evaluate the full decision. Without one, evaluate
    # the DOCUMENT through the canonical gate -- which needs no market --
    # and report coverage as NOT_EVALUATED rather than as a failure.
    try:
        from src import l5_campaign_approval as CA
        if market:
            d = CA.campaign_decision(market, root=root, now=t,
                                     reserve=False)
            if not d.get("present"):
                row("campaign_approval", "FAIL", present=False,
                    reason=d.get("reason"))
            else:
                sc = dict(d.get("scope") or {})
                row("campaign_approval", "PASS" if d.get("ok") else "FAIL",
                    state=d.get("approval_state"), reason=d.get("reason"),
                    max_windows=sc.get("max_windows"),
                    approved_exposure=sc.get("max_total_exposure_usd"),
                    approved_daily_loss=sc.get("max_daily_loss_usd"),
                    document_sha256=d.get("document_sha256"),
                    covers_market=True, reserved_nothing=True)
        else:
            g = CA.approval_gate()
            st2 = g.state()
            sc = dict(st2.get("scope") or {})
            row("campaign_approval",
                "PASS" if st2.get("approved") is True else "FAIL",
                state=g.last_state, reason=st2.get("why"),
                max_windows=sc.get("max_windows"),
                approved_exposure=sc.get("max_total_exposure_usd"),
                approved_daily_loss=sc.get("max_daily_loss_usd"),
                document_sha256=g.last_sha256,
                covers_market="NOT_EVALUATED: no market slug yet",
                reserved_nothing=True)
    except BaseException as exc:                               # noqa: BLE001
        row("campaign_approval", "UNKNOWN", error=type(exc).__name__)

    # 8. the independent halt
    try:
        from src import l5_pmus_live_runner as _R2
        g = _R2.halt_gate()
        if g is None:
            row("halt_gate", "FAIL", present=False,
                blocks="HALT_GATE_ABSENT (step 16)")
        else:
            stt = g.state()
            row("halt_gate", "PASS" if stt.get("permitted") else "FAIL",
                permitted=bool(stt.get("permitted")), why=stt.get("why"),
                blocks="HALTED (step 15)")
    except BaseException as exc:                               # noqa: BLE001
        row("halt_gate", "UNKNOWN", error=type(exc).__name__)

    # 8b. THE EFFECTIVE RESIDUAL CEILING, reported so an operator can read
    # it without waiting for an order to reach the gate. The owner approved
    # "residual cap 3"; the implemented rule is min(k * clip, absolute), and
    # until 2026-10-23 only the relative half existed -- which at the live
    # 2015z clip of 1.3 gave 2.6 and happened to sit inside the approval.
    # This reports both halves and which one would bind at the LARGEST clip
    # the durable store has recorded, so the answer is about production's
    # own state rather than about a constant. It reads nothing but code
    # constants and the ledger, and decides nothing.
    try:
        from src import l5_live_execution as _LX
        from src import l5_pmus_live_runner as _R3
        from src import l5_two_sided_probe as _TS
        absolute = _LX.ABS_RESIDUAL_CAP_CONTRACTS
        k = _R3.residual_cap_k()
        clip = _R3.largest_recorded_clip(root=root)
        rel = (k * clip) if clip is not None else None
        eff = min(rel, absolute) if rel is not None else None
        row("residual_ceiling", "PASS",
            k=str(k),
            absolute_cap_contracts=str(absolute),
            largest_recorded_clip=(str(clip) if clip is not None
                                   else "UNREADABLE"),
            relative_cap_at_that_clip=(str(rel) if rel is not None
                                       else "NOT_EVALUATED"),
            effective_cap_contracts=(str(eff) if eff is not None
                                     else "NOT_EVALUATED"),
            which_binds=("ABSOLUTE" if rel is not None and absolute < rel
                         else "RELATIVE" if rel is not None
                         else "NOT_EVALUATED"),
            outer_bound_contracts=str(
                (_R3.PROBE_LIMITS or {}).get("max_abs_residual")),
            # WHAT WILL ACTUALLY BIND THE NEXT WINDOW, reported beside the
            # historical worst case because the two differ by 5x at pilot
            # size and an operator was misled by the historical one.
            # `largest_recorded_clip` is account-wide and a BTC15m window
            # that has never filled has max_clip 0, so the gate's
            # `use_clip = max(qty, clip)` falls back to the ORDER's own size.
            # At a 1-contract clip that is min(1 * 1, 5) = 1 contract, which
            # is the approved figure -- not the 5 the row above implies.
            applied_cap_at_opening_clip=(
                "POLICY_OFF" if k is None else str(
                    min(_LX.D(str(k)) * _LX.D(str(
                        _TS.opening_leg_max_contracts())), absolute))),
            applied_cap_basis=("min(k * max(this order's qty, THIS market's "
                               "recorded clip), absolute). A market with no "
                               "fill has clip 0, so the order's own size "
                               "decides and a 1-contract clip gives a "
                               "1-contract cap. The fields above answer a "
                               "different question: which half would bind at "
                               "the largest clip this ACCOUNT has ever "
                               "produced. POLICY_OFF means "
                               "PMUS_RESIDUAL_CAP_K is unset, blank, "
                               "unparseable or non-positive -- which selects "
                               "no policy rather than some default k, so this "
                               "cap does not apply at all and only the "
                               "separate outer bound does."),
            note=("the applied ceiling is min(k * clip, absolute) computed "
                  "PER MARKET inside the gate; the outer bound is the "
                  "separate SafetyLimits max_abs_residual and is not the "
                  "approved figure"))
    except BaseException as exc:                               # noqa: BLE001
        # NOT_EVALUATED, never UNKNOWN. This row REPORTS a ceiling the gate
        # enforces for itself; it is not one of the arming blockers, so a
        # fault reading it must not turn the whole preflight red and send an
        # operator hunting for an authorization problem that does not exist.
        row("residual_ceiling", "NOT_EVALUATED", error=type(exc).__name__,
            note="telemetry only; the gate computes the applied cap itself")

    # 8b. THE WORST-CASE DEFICIT BOUND, when the approval names one.
    #
    # Mirrors the step-14 refusal so an operator sees it BEFORE spending a
    # window rather than after. NOT_EVALUATED when no campaign names a
    # deficit, which is every existing configuration: this row can only turn
    # a preflight red for a campaign that asked for the bound.
    try:
        _dscope = {}
        try:
            from src import l5_campaign_approval as _CA3
            _dd = _CA3.campaign_decision(market or "", root=root, now=t,
                                         reserve=False)
            _dsc = (_dd or {}).get("scope") or {}
            _dscope = _dsc if isinstance(_dsc, dict) else {}
        except BaseException:                                  # noqa: BLE001
            _dscope = {}
        _dappr = _dec_or_none(_dscope.get("max_worst_case_deficit_usd"))
        if _dappr is None:
            # NO ROW AT ALL, deliberately. `not_evaluated` means "this
            # preflight cannot answer it", and this one CAN be answered: no
            # approval asks for a deficit bound, so none is required and
            # nothing is pending. Emitting NOT_EVALUATED would put it in a
            # list that two tests rightly enumerate as the things that need
            # a venue read or live arming, which it is not.
            #
            # The bound is still visible either way: the heartbeat's
            # `effective_cash_gate` always reports
            # `max_worst_case_deficit_usd` and says
            # NOT_CONFIGURED_NO_CHECK_RUNS when it is absent. This row's job
            # is narrower -- whether an APPROVAL that asked for one would be
            # honoured -- so it appears only when an approval asks.
            pass
        else:
            _draw = (os.getenv(DEFICIT_LIMIT_ENV) or "").strip()
            _drun = _dec_or_none(_draw) if _draw else None
            if _drun is None or _drun <= 0:
                row("worst_case_deficit_bound", "FAIL",
                    env=DEFICIT_LIMIT_ENV,
                    set=bool(_draw),
                    note=("the approval names a deficit bound and the "
                          "running limit is unset, unreadable or "
                          "non-positive. An unset bound does not WIDEN the "
                          "deficit check -- it REMOVES it, so arming "
                          "refuses CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT"))
            elif _drun > _dappr:
                row("worst_case_deficit_bound", "FAIL",
                    env=DEFICIT_LIMIT_ENV,
                    note=("running limit is wider than the approval; step "
                          "14 refuses CAMPAIGN_LIMITS_WIDER_THAN_APPROVED"))
            else:
                row("worst_case_deficit_bound", "PASS",
                    env=DEFICIT_LIMIT_ENV,
                    note="set, positive and inside the approval")
    except BaseException as exc:                               # noqa: BLE001
        # UNKNOWN, not PASS: this is an arming blocker, so a fault reading
        # it must hold the preflight red rather than wave it through.
        row("worst_case_deficit_bound", "UNKNOWN",
            error=type(exc).__name__)

    # 9. what cannot be answered here, named rather than assumed PASS
    for n, why in (
            ("window_bounds", "needs live venue window metadata"),
            ("order_state_and_reconciliation",
             "needs an authoritative venue read"),
            ("claim_held_and_scope_digest",
             "created inside authorize_arming itself")):
        row(n, "NOT_EVALUATED", note=why)

    fails = [r["check"] for r in rows if r["verdict"] == "FAIL"]
    unknown = [r["check"] for r in rows if r["verdict"] == "UNKNOWN"]
    return {
        "label": "L5_AUTHORIZATION_PREFLIGHT",
        "market": market or None,
        "utc": t.isoformat(),
        "ok": not fails and not unknown,
        "fail": fails,
        "unknown": unknown,
        "not_evaluated": [r["check"] for r in rows
                          if r["verdict"] == "NOT_EVALUATED"],
        "rows": rows,
        "labels": ("READ-ONLY. Arms nothing, reserves nothing, emits no "
                   "configured value. UNKNOWN and NOT_EVALUATED are never "
                   "PASS. authorize_arming still decides."),
    }


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


#: how many SUBMISSIONS one authorization may make, across every strategy.
#:
#: WHY THIS EXISTS, AND WHY NEITHER EXISTING BOUND COVERED IT. The 1215z
#: window placed TWO orders under a one-window authorization, and both were
#: legitimate: the two-sided engine takes a per-(market, round, leg)
#: accumulation claim, so a round is two claims and `L5_ACC_MAX_ROUNDS`
#: permits eight rounds -- sixteen submissions in one window, bounded only by
#: capital. `submission_sid` bounds the OTHER strategy to one, and
#: `max_live_orders` bounds CONCURRENCY, not the total. So "my authorization
#: may place N orders" was a property of the written mandate and of no gate.
#:
#: Counted from the LOCK FILES for the same reason `windows_spent` is: the
#: lock is created before the ledger row, so a crash between them spends the
#: submission rather than releasing it.
#:
#: UNSET MEANS NO BOUND, which preserves the behaviour that produced the one
#: profitable pair we have. It is the owner's number, per authorization.
MAX_SUBMISSIONS_ENV = "L5_LIVE_AUTH_MAX_SUBMISSIONS"
#: named in refusals so an operator can see the other way to
#: authorize. Declared here to avoid importing the campaign
#: module into the boundary it is consumed by.
CAMPAIGN_APPROVAL_ENV = "L5_CAMPAIGN_APPROVAL"


def max_submissions() -> Optional[int]:
    """The owner's submission budget for this authorization, or None."""
    raw = (os.getenv(MAX_SUBMISSIONS_ENV) or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        return 0        # unreadable is NEVER unlimited: refuse everything
    return max(0, v)


def submissions_spent(path: str, experiment: str) -> List[str]:
    """Every submission claim this experiment holds, across BOTH strategies.

    `sub1.<exp>` is the single-submission claim and `acc1.<exp>...` are the
    per-leg accumulation claims. Both are counted, so the budget binds
    independently of which strategy chose the order.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(experiment))[:120]
    d = pathlib.Path(path).parent
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("l5_auth_claim_*.lock"):
        # `b5m1.` is the 5-minute strategy's per-order claim. Counted here so
        # the SAME total binds it; adding a kind can only count more.
        for kind in ("sub1.", "acc1.", "b5m1."):
            pre = "l5_auth_claim_%s%s" % (
                re.sub(r"[^A-Za-z0-9_.-]", "_", kind), safe)
            if p.name.startswith(pre):
                out.append(p.name)
                break
    return sorted(out)


def submission_budget_refusal(path: str, experiment: str
                              ) -> Optional[Dict[str, Any]]:
    """None if this authorization may make another submission, else why not.

    Applied in BOTH claim paths, so it cannot be sidestepped by the choice of
    strategy. An unreadable budget refuses; an unset one does not bind.
    """
    cap = max_submissions()
    if cap is None:
        return None
    try:
        spent = submissions_spent(path, experiment)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"reason": "SUBMISSION_BUDGET_UNREADABLE",
                "error": type(exc).__name__,
                "note": "a budget we cannot count is not a budget we may "
                        "assume has room"}
    if len(spent) >= cap:
        return {"reason": "SUBMISSION_BUDGET_EXHAUSTED",
                "experiment": str(experiment),
                "max_submissions": cap, "spent": len(spent),
                "claims": spent[:16],
                "env": MAX_SUBMISSIONS_ENV,
                "note": "counted across BOTH strategies from the durable "
                        "lock files, so it binds whichever path chose the "
                        "order"}
    return None


# ======================================= THE THREE DURABLE CLASS BUDGETS
#
# WHY ONE TOTAL WAS THE WRONG SHAPE. A single "8 submissions per
# authorization" bound treats every order as the same kind of act. It is
# not. L5 works inventory SEQUENTIALLY -- open, extend, wait, complete,
# keep working -- so a flat total is spent by the orders that TAKE ON risk
# and then has nothing left for the orders that REDUCE it. The budget that
# runs out first would be the one that completes a pair, which is precisely
# backwards: an exhausted completion budget strands unmatched exposure that
# the exposure ceiling is then powerless to unwind.
#
# So the budget is split by what the order DOES:
#
#   OPEN      an order that increases |prospective unmatched| -- a first
#             leg or a same-side EXTEND. This is the one that must be
#             scarce, because it is the only class that can add risk.
#   COMPLETE  an order that strictly REDUCES |prospective unmatched|. Held
#             deliberately looser than OPEN, and separately gated on
#             actually reducing (see `balancing_refusal` in the executor):
#             a generous budget is safe only because an order in this class
#             cannot add inventory.
#   REPLACE   a re-placement of a leg that has already been placed once
#             (attempt > 0) -- a re-price after the book moved. Bounded by
#             the serialization mechanics: one outstanding order at a time
#             and a 120s per-order TTL.
#
# CANCELLATION IS NOT BUDGETED, EVER. A bound that could refuse a cancel
# would be a bound that traps an order on the venue. Only the REPLACEMENT
# SUBMISSION that follows a cancel is counted; the cancel itself stays
# available unconditionally, exactly as the submission gate's own labels
# promise ("Cancellation is NOT gated by any of these").
#
# DURABLE AND MONOTONIC. Each class's consumption is a sequence of
# single-use ticket locks on the same volume as the ledger, taken with
# O_CREAT|O_EXCL and fsynced before the wire. A redeploy, a crash, a
# cancelled order, a rejected order and an ambiguous acknowledgement all
# leave the ticket spent -- the same rule every other claim here follows.
#
# WHY TICKETS AND NOT A NEW CLAIM KEY. The existing `acc1.` ids were
# written without a class axis, and changing their shape would hand every
# claim already on the volume back as unspent. A ticket is additive: the
# original claim keeps its exact key and the class counter sits beside it.

#: the classes, in the order they are reported.
SUBMISSION_CLASSES = ("OPEN", "COMPLETE", "REPLACE")

CLASS_BUDGET_ENV = {
    "OPEN": "L5_LIVE_AUTH_MAX_OPEN",
    "COMPLETE": "L5_LIVE_AUTH_MAX_COMPLETE",
    "REPLACE": "L5_LIVE_AUTH_MAX_REPLACE",
}

#: a ticket index can never reach this. Purely a loop bound, so a corrupt
#: directory cannot spin here.
_CLASS_TICKET_CEILING = 4096


def normalize_class(klass: Any) -> Optional[str]:
    """One of `SUBMISSION_CLASSES`, or None for 'not stated'.

    An UNRECOGNISED class is not None: it raises. 'I was told something I do
    not understand' and 'I was told nothing' must not collapse, because the
    second is allowed when no class budget is configured and the first is
    always a programming error.
    """
    if klass is None:
        return None
    k = str(klass).strip().upper()
    if not k:
        return None
    if k not in SUBMISSION_CLASSES:
        raise Refused("SUBMISSION_CLASS_UNKNOWN",
                      {"klass": k[:40],
                       "known": list(SUBMISSION_CLASSES)})
    return k


def max_of_class(klass: str) -> Optional[int]:
    """This class's durable budget, or None for 'not configured'.

    Same three-way reading as `max_submissions`: unset does not bind,
    unreadable is ZERO rather than unlimited.
    """
    env = CLASS_BUDGET_ENV.get(str(klass).strip().upper())
    if not env:
        return None
    raw = (os.getenv(env) or "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return 0            # unreadable is NEVER unlimited


def any_class_budget_configured() -> bool:
    """True when the owner has configured at least one class budget."""
    return any(max_of_class(k) is not None for k in SUBMISSION_CLASSES)


#: how many COMPLETE submissions one outstanding obligation can need: the
#: complement itself, plus ONE re-derivation after a partial fill or a book
#: that moved under it. Derived, not guessed: with one live-order slot the
#: complement is always placed later than the leg it matches, against a book
#: that has moved, which is why `complement_order` re-prices from the
#: current book at all.
COMPLETION_SUBMISSIONS_PER_OBLIGATION = 2


def total_backstop_binds(reducing: bool) -> bool:
    """Does the flat total apply to a submission with this EFFECT?

    NO FOR A RISK-REDUCING ORDER, and this is the whole repair. A global
    count that can refuse an order which provably reduces |unmatched| --
    it has already passed `BALANCING_DOES_NOT_REDUCE` and
    `BALANCING_WOULD_OVERSHOOT`, so it cannot add exposure and cannot
    overshoot -- protects nothing and can only STRAND risk. OPEN and
    REPLACE spending the total down to zero would otherwise leave the
    account holding unmatched inventory with no submission left to close
    it, which is the one outcome every limit here exists to prevent.

    The total still binds every submission that is NOT risk-reducing, so it
    remains a finite backstop on the only class of order that can add
    exposure or be placed discretionarily.
    """
    return not bool(reducing)


def completion_capacity_refusal(path: str, experiment: str
                                ) -> Optional[Dict[str, Any]]:
    """None if a NEW exposure-increasing order may be placed, else why not.

    DO NOT OPEN WHAT YOU CANNOT CLOSE. Reserving capacity after the fact is
    impossible: once OPEN and REPLACE have spent the budget, the inventory
    they created is already on the books. So the check is made BEFORE the
    exposure exists, and it is made against BOTH bounds that could run out:

      * the COMPLETE class budget, which must still hold enough for one
        obligation;
      * the flat total, which must still leave room for this order AND for
        that obligation -- even though the total does not itself bind a
        risk-reducing order, because a reserve that only one of the two
        bounds respects is not a reserve.

    Applied to OPEN only. REPLACE is discretionary: a re-price refused here
    leaves the original intent intact and costs nothing.
    """
    cap_c = max_of_class("COMPLETE")
    reserve = COMPLETION_SUBMISSIONS_PER_OBLIGATION
    if cap_c is not None:
        try:
            spent_c = len(class_spent(path, experiment, "COMPLETE"))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"reason": "COMPLETION_CAPACITY_UNREADABLE",
                    "error": type(exc).__name__,
                    "note": "a reserve we cannot count is not a reserve we "
                            "may assume exists"}
        if cap_c - spent_c < reserve:
            return {"reason": "NO_COMPLETION_CAPACITY_RESERVED",
                    "complete_max": cap_c, "complete_spent": spent_c,
                    "complete_remaining": cap_c - spent_c,
                    "reserve_required": reserve,
                    "note": "refusing to open exposure the completion "
                            "budget could no longer close"}
    cap_t = max_submissions()
    if cap_t is not None:
        try:
            spent_t = len(submissions_spent(path, experiment))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"reason": "COMPLETION_CAPACITY_UNREADABLE",
                    "error": type(exc).__name__,
                    "note": "a reserve we cannot count is not a reserve we "
                            "may assume exists"}
        if spent_t + 1 + reserve > cap_t:
            return {"reason": "NO_COMPLETION_CAPACITY_RESERVED",
                    "total_max": cap_t, "total_spent": spent_t,
                    "reserve_required": reserve,
                    "note": "the flat backstop would not leave room for this "
                            "order AND the obligation it creates"}
    return None


def class_sid(experiment: str, klass: str, n: int) -> str:
    """The claim id for TICKET `n` of one class of one authorization."""
    return "cls1.%s.%s.%d" % (experiment, str(klass).upper(), int(n))


def class_spent(path: str, experiment: str, klass: str) -> List[str]:
    """Every ticket this class holds. Durable; counted from the filesystem.

    COUNTS THIS ID'S OWN TICKETS AND ITS CAMPAIGNS'. A ticket is named
    `cls1.<exp>.<CLASS>.<n>`, and under a campaign `<exp>` carries the
    `.camp.<digest>` suffix `experiment_id` appends -- so a campaign's
    tickets are named `cls1.<base>.camp.<digest>.<CLASS>.<n>`. Matching the
    class at the TAIL rather than at a fixed offset makes both forms count
    under the base id, while a campaign id still counts only its own.

    That asymmetry is the whole point, and it is what keeps the namespace
    from becoming a way to replenish a budget:

      * under campaign X the prefix is `<base>.camp.X`, which no other
        campaign's and not the bare id's tickets match -- X gets the fresh
        budget the owner authorized for it;
      * under the bare `<base>` -- which is what an absent, unreadable or
        expired campaign falls back to -- every campaign's tickets match, so
        the fallback can only ever see MORE spent than any campaign did.

    A restart inside one campaign re-derives the same digest and so re-reads
    the same files; it sees its own spend, never a clean budget.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(experiment))[:120]
    d = pathlib.Path(path).parent
    if not d.is_dir():
        return []
    k = str(klass).upper()
    # the trailing dot matters: without it `<exp>` would also match the
    # tickets of any id it is a textual prefix of, which is a DIFFERENT
    # experiment rather than a campaign of this one.
    head = "l5_auth_claim_cls1.%s." % (safe,)
    out = []
    for p in d.glob("l5_auth_claim_cls1.*.lock"):
        n = p.name
        if not n.startswith(head):
            continue
        parts = n[len(head):-len(".lock")].split(".")
        if len(parts) >= 2 and parts[-2] == k:
            out.append(n)
    return sorted(out)


def class_budget_report(path: str, experiment: str) -> Dict[str, Any]:
    """Read-only. What each class has spent and what it may still spend."""
    out: Dict[str, Any] = {}
    for k in SUBMISSION_CLASSES:
        cap = max_of_class(k)
        try:
            spent = len(class_spent(path, experiment, k))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out[k] = {"max": cap, "spent": None,
                      "remaining": None,
                      "reason": "UNREADABLE:%s" % type(exc).__name__}
            continue
        out[k] = {"max": cap, "spent": spent,
                  "remaining": None if cap is None else max(0, cap - spent)}
    return out


def claim_class_ticket(path: str, experiment: str, klass: str
                       ) -> Optional[Dict[str, Any]]:
    """Consume one ticket in `klass`, or raise. None when unconfigured.

    Takes the LOWEST free ticket index, atomically. Two processes cannot
    take the same index -- `claim_once` is O_EXCL -- and a loser simply
    moves to the next, so a race costs an index rather than letting a
    second order through on one ticket.
    """
    k = str(klass).upper()
    cap = max_of_class(k)
    if cap is None:
        return None
    if cap <= 0:
        raise Refused("%s_BUDGET_EXHAUSTED" % k,
                      {"klass": k, "max": cap, "spent": 0,
                       "env": CLASS_BUDGET_ENV[k],
                       "note": "a budget of zero, or one we could not read, "
                               "permits nothing"})
    try:
        spent = len(class_spent(path, experiment, k))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused("%s_BUDGET_UNREADABLE" % k,
                      {"klass": k, "error": type(exc).__name__,
                       "note": "a budget we cannot count is not a budget we "
                               "may assume has room"})
    if spent >= cap:
        raise Refused("%s_BUDGET_EXHAUSTED" % k,
                      {"klass": k, "max": cap, "spent": spent,
                       "env": CLASS_BUDGET_ENV[k],
                       "note": "this class of order is spent for this "
                               "authorization; the other classes are "
                               "unaffected"})
    for n in range(spent, _CLASS_TICKET_CEILING):
        if n >= cap:
            break
        sid = class_sid(experiment, k, n)
        rec = {"kind": "class_ticket", "experiment": str(experiment),
               "klass": k, "index": n, "sid": sid,
               "t": dt.datetime.now(dt.timezone.utc).isoformat()}
        try:
            claim_once(path, sid, rec)
        except Refused:
            continue            # someone else holds this index; try the next
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise Refused("%s_BUDGET_UNAVAILABLE" % k,
                          {"klass": k, "sid": sid,
                           "error": type(exc).__name__,
                           "note": "the ticket could not be persisted, so it "
                                   "is not held. Refusing rather than "
                                   "submitting on an unproven budget."})
        return rec
    raise Refused("%s_BUDGET_EXHAUSTED" % k,
                  {"klass": k, "max": cap, "spent": spent,
                   "env": CLASS_BUDGET_ENV[k],
                   "note": "every index inside the budget is already held"})


#: A risk-reducing re-placement may be charged to the COMPLETE budget once
#: the REPLACE budget is spent. Exactly "ON" or it is off.
REDUCING_REPLACE_USES_COMPLETE_ENV = "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE"


def reducing_replace_uses_complete() -> bool:
    return (os.getenv(REDUCING_REPLACE_USES_COMPLETE_ENV) or "") == "ON"


def class_claim_or_refuse(path: str, experiment: str,
                          klass: Any,
                          reducing: bool = False
                          ) -> Optional[Dict[str, Any]]:
    """The one place a submission's class budget is spent.

    UNSTATED IS A REFUSAL ONCE BUDGETS EXIST. A caller that does not say
    what kind of order this is cannot be charged for it, and an uncharged
    order is an unbudgeted one. While no class budget is configured at all,
    silence is accepted and nothing is spent -- that is the pre-existing
    behaviour, unchanged.
    """
    k = normalize_class(klass)
    if k is None:
        if any_class_budget_configured():
            raise Refused("SUBMISSION_CLASS_NOT_STATED",
                          {"known": list(SUBMISSION_CLASSES),
                           "note": "class budgets are configured, so an "
                                   "order that does not say which class it "
                                   "belongs to cannot be charged for and is "
                                   "refused"})
        return None
    # DO NOT OPEN WHAT YOU CANNOT CLOSE, checked before the ticket is spent.
    if k == "OPEN":
        short = completion_capacity_refusal(path, experiment)
        if short:
            raise Refused(str(short["reason"]), short)
    # A RE-PLACED COMPLETION IS STILL A COMPLETION. Every re-placement is
    # classed REPLACE whatever its direction, so once the REPLACE tickets
    # were gone a leg that only REDUCES unmatched inventory could not be
    # sent again -- while the COMPLETE budget, which exists for exactly
    # that order, sat unused. Nine settled windows show it line by line
    # (2026-10-30 to 2026-11-04: one first leg filled, the completing leg
    # refused REPLACE_BUDGET_EXHAUSTED 1,058 times with a pair of 0.95 or
    # less planned, COMPLETE_BUDGET_EXHAUSTED never logged), and all nine
    # settled unmatched (analysis/l5/completion_decline_20261111.py). With
    # the owner's
    # switch such an order is charged to COMPLETE instead: one ticket, from
    # a budget that is configured and has room, or it is refused exactly as
    # before. No budget is raised and none is shared in the other
    # direction; an order that is not risk-reducing never takes this path.
    if k == "REPLACE" and bool(reducing) and reducing_replace_uses_complete():
        try:
            return claim_class_ticket(path, experiment, "REPLACE")
        except Refused as exc:
            if not (exc.args and exc.args[0] == "REPLACE_BUDGET_EXHAUSTED") \
                    or max_of_class("COMPLETE") is None:
                raise
            ticket = claim_class_ticket(path, experiment, "COMPLETE")
            if ticket is None:
                raise
            return dict(ticket, charged_to="COMPLETE",
                        because="REPLACE_BUDGET_EXHAUSTED_AND_RISK_REDUCING")
    return claim_class_ticket(path, experiment, k)


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


#: THE LONGEST SID MUST FIT IN `_lock_path`'s 120 CHARACTERS, so this caps
#: the experiment id rather than letting a long one be silently cut.
#:
#: The longest id any claim builds is an accumulation claim --
#: `acc1.<exp>.<market>.<rnd>.<leg>.a<attempt>` -- so the budget is:
#:
#:     "acc1." 5 + "." 1 + market 40 + "." 1 + rnd 2 + "." 1
#:           + "DOWN" 4 + "." 1 + "a" 1 + attempt 2            = 58
#:
#: 120 - 58 = 62. A PMUS BTC15m slug is 35 characters, so 40 leaves room for
#: a longer series without this needing revisiting; if a venue ever exceeds
#: it, the sid is still cut, so the figure is checked by a test rather than
#: trusted.
MAX_EXPERIMENT_LEN = 62


def campaign_claim_suffix() -> str:
    """`camp.<digest>` while an approved campaign is in force, else "".

    WHY THE EXPERIMENT ID IS NOT ENOUGH ON ITS OWN. Every durable submission
    claim is a lock file keyed on the experiment string -- `sub1.<exp>`,
    `acc1.<exp>...`, `cls1.<exp>.<CLASS>.<n>`. The owner sets `<exp>` in a
    variable, and nothing ties that variable to the campaign it is spending
    for, so two consecutively authorized campaigns carrying the same value
    share one budget: the second inherits every claim the first spent.

    That is not hypothetical. Window 4 of the four-window campaign of
    2026-10-28 submitted NOTHING, because 27 claims left by earlier
    campaigns under the same `L5_EXPERIMENT_ID` were counted against its
    40-submission backstop -- `total_max: 40, total_spent: 38` -- leaving no
    room for the completion reserve. The window was authorized and funded
    and traded nothing.

    So the namespace is keyed on the campaign ITSELF rather than on a
    variable whose value the campaign's scope does not control. The campaign
    already has a durable identity -- `campaign_claim_sid`, the SHA-256 of
    the owner-held nonce -- and appending it gives every authorized campaign
    its own budget without the owner having to remember to rotate anything.

    WHY A RESTART CANNOT REPLENISH. The suffix is a pure function of the
    nonce, which lives in a variable the executor cannot write. A restart,
    a redeploy or a crash-recovery re-reads the same nonce, derives the same
    digest, and therefore counts the same claim files. Only a NEW document
    carrying a NEW nonce -- which only the owner can issue -- opens a new
    namespace. Budget replenishment is an owner action, by construction.

    FAILS TOWARD FEWER CLAIMS, NEVER MORE. Anything but a readable, approved
    document yields "", which leaves the bare experiment id -- and that id
    counts every campaign-suffixed claim too, because `submissions_spent`
    matches on prefix and `class_spent` matches the class segment at the
    tail. So an unreadable or expired campaign sees MORE spent, never less.
    A budget we cannot attribute is not a budget we may assume has room.
    """
    try:
        from src import l5_campaign_approval as _CA   # late: avoids a cycle
        st = _CA.approval_gate().state() or {}
        if not st.get("approved"):
            return ""
        return campaign_claim_sid(st.get("scope") or {})
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return ""


def experiment_id() -> str:
    """The claim namespace THIS campaign's submissions are counted in.

    `<owner id>.camp.<digest>` under an approved campaign; the bare owner id
    otherwise, which is the behaviour every caller had before campaigns
    existed and is still what applies when none is in force.

    AN UNNAMED EXPERIMENT STAYS UNNAMED. A campaign never supplies the id by
    itself: with `L5_EXPERIMENT_ID` unset this returns "", so the callers
    that refuse on an unnamed experiment -- `SUBMISSION_CLAIM_NO_EXPERIMENT`
    and its siblings -- go on refusing. The suffix narrows a named budget; it
    does not create one.
    """
    base = (os.getenv(EXPERIMENT_ENV) or "").strip()
    if not base:
        return ""
    suf = campaign_claim_suffix()
    if not suf:
        return base
    # THE DIGEST SURVIVES, THE LABEL GIVES WAY. `_lock_path` truncates the
    # WHOLE sid at 120 characters, and the longest sid is an accumulation
    # claim, which carries the market slug as well as the experiment (see
    # MAX_EXPERIMENT_LEN). Appending 30 characters of campaign suffix spends
    # most of the headroom that id had, so a longer experiment label would
    # push `acc1....DOWN.a3` past the cut -- and a truncated sid makes two
    # distinct claims share one lock file, which leaves the single-use
    # ledger ambiguous about which leg it is holding.
    #
    # So the composed id is bounded, and it is the owner's LABEL that is
    # shortened rather than the digest: the digest is the part that carries
    # isolation, and a shortened label can at worst collide with another
    # label inside the SAME campaign, which is cosmetic. Losing digest
    # characters would merge two campaigns' budgets, which is the bug this
    # whole mechanism exists to fix.
    room = MAX_EXPERIMENT_LEN - len(suf) - 1
    if room < 1:
        return suf                      # unreachable at any sane suffix
    return "%s.%s" % (base[:room], suf)


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


def refuse_unauthorized_window(market: str) -> None:
    """Raise unless this market's window is inside the authorized grant.

    THE INCIDENT, 2026-11-04T08:00Z. The owner authorized eight windows
    ending 07:45Z with expiry 08:15Z; an un-enumerated 08:00Z window was
    claimed and traded -- 5 orders, 3 fills, settled -$0.2900 -- because
    `expires_utc` was doing double duty as trading authorization AND
    settlement grace.

    CALLED FROM EVERY MARKET-KEYED PRE-WIRE CLAIM, and that is the point.
    The first fix guarded `claim_submission` alone, and
    `claim_accumulation_leg` -- the branch the accumulation path actually
    takes, which the runner calls three times over -- went straight past
    it. A bound enforced on one of three doors is not a bound. The window
    decision itself lives once, in `l5_campaign_approval.opening_refusal`,
    so no two call sites can disagree about which windows exist.

    ABSENT SAYS NOTHING; PRESENT AND INVALID REFUSES. Only the three
    spellings of absent leave the pre-campaign single-window handshake
    alone. A stale, expired or malformed document refuses, because
    otherwise an exhausted campaign quietly becomes a fresh one-window
    authorization at the last boundary before the venue. An unreadable one
    refuses too: a submission we cannot place inside the grant is refused.
    """
    mkt = str(market or "").strip()
    try:
        from src import l5_campaign_approval as _CAW   # late: avoids a cycle
        _st = _CAW.approval_gate().state()
    except BaseException as _exc:                              # noqa: BLE001
        if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
            raise
        # Only the READ is guarded. The decision below raises `Refused` on
        # its own account and must not be swallowed and relabelled.
        raise Refused("SUBMISSION_WINDOW_UNVERIFIABLE",
                      {"error": type(_exc).__name__,
                       "note": "the authorized-window set could not be "
                               "established; a submission we cannot place "
                               "inside the grant is refused."})
    _why = str(_st.get("why") or "")
    if _st.get("approved"):
        # THE DOCUMENT'S OWN SERIES FIRST. `opening_refusal` judges the
        # window's START, and a 15-minute and a 5-minute window can open at
        # the same instant -- so the start alone cannot tell one strategy's
        # window from the other's. Arming already refuses a series mismatch;
        # this is the same refusal at the last boundary before the wire, so
        # an approval for one series can never be spent on the other.
        _series = str((_st.get("scope") or {}).get("series") or "")
        if not _series or not mkt.startswith(_series):
            raise Refused("SUBMISSION_SERIES_NOT_AUTHORIZED",
                          {"series": _series, "market": mkt[:80],
                           "note": "the approval in force names another "
                                   "series; it authorizes nothing here."})
        _bad = _CAW.opening_refusal(_st.get("scope") or {}, mkt)
        if _bad is not None:
            raise Refused("SUBMISSION_WINDOW_NOT_AUTHORIZED",
                          dict(_bad.get("detail") or {},
                               why=str(_bad.get("reason")),
                               market=mkt[:80]))
    elif _why not in ("approval_absent", "approval_empty",
                      "approval_not_set"):
        raise Refused("SUBMISSION_WINDOW_NOT_AUTHORIZED",
                      {"why": _why or "approval_not_valid",
                       "market": mkt[:80],
                       "note": "an approval document is present and does "
                               "not authorize anything."})


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
#: how many PLACEMENTS one leg of one round may take. A leg cancelled
#: UNFILLED may be re-placed; a leg that FILLED may never be. The attempt
#: axis is what keeps every individual placement single-use and restart-safe
#: while still allowing repricing -- without it a reprice had to consume a
#: whole pair cycle, so a window of active quoting exhausted the rounds.
ACC_MAX_ATTEMPTS_ENV = "L5_ACC_MAX_ATTEMPTS"
ACC_MAX_ATTEMPTS_DEFAULT = 4
ACC_MAX_ATTEMPTS_HARD_CAP = 16


def acc_max_attempts() -> int:
    """Placements permitted per leg per round. Unparseable reads as one."""
    raw = (os.getenv(ACC_MAX_ATTEMPTS_ENV) or "").strip()
    if not raw:
        return ACC_MAX_ATTEMPTS_DEFAULT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1                        # unparseable is the safe reading
    return max(1, min(n, ACC_MAX_ATTEMPTS_HARD_CAP))


def accumulation_sid(experiment: str, market: str, rnd: int, leg: str,
                     attempt: int = 0) -> str:
    """The claim id for ONE PLACEMENT of one leg of one round.

    ATTEMPT ZERO KEEPS ITS OLD KEY, deliberately: claims already on the
    durable volume were written without an attempt axis, and a key change
    would hand every one of them back as unspent. Attempt zero is therefore
    byte-identical to the previous id and later attempts extend it.
    """
    base = "acc1.%s.%s.%d.%s" % (experiment, market, int(rnd), leg)
    a = int(attempt)
    if a < 0:
        raise Refused("ACC_CLAIM_BAD_ATTEMPT", str(attempt))
    return base if a == 0 else "%s.a%d" % (base, a)


def claim_accumulation_leg(market: str, rnd: int, leg: str,
                           payload: Mapping[str, Any],
                           path: Optional[str] = None,
                           attempt: int = 0,
                           klass: Any = None,
                           reducing: bool = False) -> Dict[str, Any]:
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
    # THE SAME WINDOW BOUND AS EVERY OTHER PRE-WIRE CLAIM. This is the
    # branch the accumulation path actually takes, so leaving it unguarded
    # left the 08:00Z hole open on the busiest door.
    refuse_unauthorized_window(mkt)
    side = str(leg or "").upper()
    if side not in PROBE_LEGS:
        raise Refused("ACC_CLAIM_BAD_LEG", side)
    try:
        r = int(rnd)
    except (TypeError, ValueError):
        raise Refused("ACC_CLAIM_BAD_ROUND", str(rnd))
    if r < 0:
        raise Refused("ACC_CLAIM_BAD_ROUND", str(rnd))
    try:
        a = int(attempt)
    except (TypeError, ValueError):
        raise Refused("ACC_CLAIM_BAD_ATTEMPT", str(attempt))
    if a < 0 or a >= acc_max_attempts():
        raise Refused("ACC_CLAIM_ATTEMPTS_EXHAUSTED",
                      {"attempt": a, "max": acc_max_attempts(),
                       "leg": side, "market": mkt, "round": r,
                       "note": "the placement budget for this leg is spent; "
                               "repricing further would need a new round"})
    # THE OWNER'S SUBMISSION BUDGET, enforced here as well as in
    # `claim_submission`, so it binds independently of which strategy chose
    # the order. The 1215z window's two orders were both legitimate per-leg
    # claims; nothing counted them against the authorization as a whole.
    # THE FLAT BACKSTOP -- and it does NOT bind a risk-reducing order. See
    # `total_backstop_binds`: a global count that can refuse an order which
    # provably reduces |unmatched| protects nothing and can only strand the
    # inventory the other classes created.
    if total_backstop_binds(reducing):
        over = submission_budget_refusal(path or ledger_path(), exp)
        if over:
            raise Refused(str(over["reason"]), over)
    # THE CLASS BUDGET, spent BEFORE the leg claim. Order matters: if the
    # class is exhausted, nothing should consume the leg's own slot, because
    # the leg slot is what a later retry under a replenished class budget
    # would need. The reverse order would spend the leg on an order the
    # class budget then refused.
    ticket = class_claim_or_refuse(path or ledger_path(), exp, klass,
                                   reducing=reducing)
    sid = accumulation_sid(exp, mkt, r, side, a)
    rec = {"kind": "accumulation_leg_claim", "experiment": exp,
           "market": mkt, "round": r, "leg": side, "attempt": a, "sid": sid,
           "klass": normalize_class(klass), "reducing": bool(reducing),
           "class_ticket": (None if ticket is None else ticket.get("sid")),
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
                     path: Optional[str] = None,
                     klass: Any = None,
                     reducing: bool = False) -> Dict[str, Any]:
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
    refuse_unauthorized_window(mkt)
    # the same exemption, on the other strategy's path: a risk-reducing
    # order is never refused by the flat total, whichever engine chose it.
    if total_backstop_binds(reducing):
        over = submission_budget_refusal(path or ledger_path(), exp)
        if over:
            raise Refused(str(over["reason"]), over)
    # the same class budget, on the other strategy's path, so a class bound
    # cannot be sidestepped by which engine chose the order.
    ticket = class_claim_or_refuse(path or ledger_path(), exp, klass,
                                   reducing=reducing)
    sid = submission_sid(exp)
    rec = {"kind": "submission_claim", "experiment": exp, "market": mkt,
           "sid": sid, "klass": normalize_class(klass),
           "reducing": bool(reducing),
           "class_ticket": (None if ticket is None else ticket.get("sid")),
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


# ================================================ THE BTC 5-MINUTE CLAIM
#
# OWNER DECISION, 2026-11-11: "Extend the existing single-use authorization
# mechanism to recognize explicitly approved BTC5m markets. Do not create a
# second, weaker authorization path. Each strategy must have its own
# approved market, time window, exposure cap, and order ownership."
#
# SO THIS IS THE SAME MECHANISM, NOT A NEW ONE. Every part below already
# exists and already guards the 15-minute strategy:
#
#   the approval      `L5_CAMPAIGN_APPROVAL`, an owner-set document the
#                     executor cannot write, read through `approval_gate()`
#   the window grant  `opening_refusal` + the durable per-nonce
#                     `WindowLedger`, spent through `reserve_window`
#   the budgets       `submission_budget_refusal` (the flat total) and
#                     `class_claim_or_refuse` (OPEN / COMPLETE / REPLACE)
#   the claim itself  `claim_once`: O_CREAT|O_EXCL, fsynced before the wire,
#                     never returned by a cancel, a fill, a restart, a
#                     rejection or an ambiguous acknowledgement
#   the holder        `role_refusal` and `durability_refusal`
#
# WHERE IT IS STRICTER THAN THE 15-MINUTE CLAIM, and why. The 15-minute
# executor reaches its claim only after `authorize_arming` has returned, so
# its claim may treat an ABSENT approval as "the single-window handshake
# decides". The 5-minute adapter has no such second boundary behind it, so
# here everything is decided at the claim and absent is a refusal:
#
#   * an approved document is REQUIRED, and it must name the 5-minute
#     series -- a 15-minute approval authorizes nothing here, and (see
#     `refuse_unauthorized_window`) a 5-minute one authorizes nothing there;
#   * the windows must be ENUMERATED (`window_starts`). "Explicitly
#     approved markets" is a set of windows, not an interval;
#   * the flat submission total must be CONFIGURED. Unset does not bind the
#     15-minute path for historical reasons; here unset refuses;
#   * the order's class must be STATED;
#   * the caller must state the strategy's worst-case exposure including
#     this order and its settled result for the UTC day, and both are held
#     to the document's own limits (already clamped to the code ceilings);
#   * both shared locks and the deploy drain are read again, here, exactly
#     as the 15-minute service reads them.
#
# WHAT IT DOES NOT DO. It does not arm a transport, hold a credential, or
# decide a price. The venue transport still needs its own preflight and the
# owner's arm token before `submit` can do anything at all, exactly as for
# the 15-minute strategy.

BTC5M_SERIES = "cpc-btc-updown-5m-"
BTC5M_WINDOW_S = 300.0
BTC5M_CLAIM_PREFIX = "b5m1."
BTC5M_COID_RE = re.compile(r"b5m-[0-9a-f]{24}")

#: WHICH 5-MINUTE SERIES THIS LANE MAY TRADE.
#:
#: These three gates were BTC-only literals, and the fleet's ETH, SOL and
#: XRP lanes were refused every order -- OBSERVED 2026-11-16T12:02Z,
#: AUTHORIZATION:BTC5M_MARKET_NOT_IN_SERIES on every submission, with the
#: coid check waiting immediately behind it.
#:
#: THE SAFETY PROPERTY IS UNCHANGED AND SLIGHTLY STRONGER. It was "the
#: market and the approval both equal this one constant". It is now "the
#: market and the approval both equal THIS LANE'S series", so a lane
#: still cannot trade a series its own approval does not name, and in
#: addition cannot trade a sibling lane's series even with a valid
#: approval for that sibling.
#:
#: UNSET IS BTC5M, so a single executor is byte-for-byte unchanged.
#: ANYTHING UNRESOLVABLE REFUSES -- an unknown strategy, a non-5-minute
#: one, or a registry that will not import -- because the permissive
#: answer here is the one that lets a lane submit into a series nobody
#: approved.
LANE_STRATEGY_ENV = "L5_5M_STRATEGY"


def _lane_row():
    """This lane's registry row, or None if it cannot be resolved."""
    want = str(os.environ.get(LANE_STRATEGY_ENV, "")).strip().upper()
    if not want:
        want = "BTC5M"
    try:
        from src import l5_series_registry as _REG   # stdlib-only table
        row = _REG.BY_STRATEGY.get(want)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None
    if row is None or float(getattr(row, "horizon_s", 0)) != BTC5M_WINDOW_S:
        return None
    return row


def lane_series() -> Optional[str]:
    """The series prefix this lane may trade, or None (refuse)."""
    row = _lane_row()
    return None if row is None else str(row.prefix)


def lane_coid_re():
    """The client-order-id shape this lane may claim, or None (refuse)."""
    row = _lane_row()
    if row is None:
        return None
    return re.compile(re.escape(str(row.coid_prefix)) + r"[0-9a-f]{24}")
#: the two shared locks, by the names the 15-minute service reads them
#: under (`l5_pmus_live_runner.LIVE_HALT_ENV` / `SHADOW_ONLY_ENV`). Not
#: imported: this module must not depend on the runner. A test pins the
#: names and the readings to the runner's.
LIVE_HALT_ENV = "L5_LIVE_HALT"
SHADOW_ONLY_ENV = "L5_SHADOW_ONLY"
#: `_lock_path` keeps 120 characters of a claim id. An id longer than that
#: would be TRUNCATED, and two different orders could then share one lock.
_LOCK_ID_MAX = 120


def operator_halt_engaged() -> bool:
    """Any non-empty value halts. Same expression as the runner's."""
    return bool(str(os.getenv(LIVE_HALT_ENV, "")).strip())


def shadow_lock_engaged() -> bool:
    """Released ONLY by exactly "0". Unset is locked."""
    return str(os.getenv(SHADOW_ONLY_ENV, "1")).strip() != "0"


def btc5m_sid(experiment: str, client_order_id: str) -> str:
    """The claim id for ONE 5-minute order. One client order id, one claim."""
    return "%s%s.%s" % (BTC5M_CLAIM_PREFIX, experiment, client_order_id)


def _usd(v: Any) -> Optional[Any]:
    """A finite Decimal, or None. A bool is not a number."""
    from decimal import Decimal as _D, InvalidOperation as _IO
    if v is None or isinstance(v, bool):
        return None
    try:
        d = v if isinstance(v, _D) else _D(str(v).strip())
    except (_IO, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def btc5m_authorization(market: Any, *,
                        now: Optional[dt.datetime] = None,
                        path: Optional[str] = None,
                        root: Optional[str] = None,
                        require_durable: bool = True) -> Dict[str, Any]:
    """May an order be sent into THIS 5-minute market right now? Default NO.

    READ-ONLY: it reserves nothing, spends nothing and writes nothing, so
    it is safe to call on every tick and to publish. `claim_btc5m_submission`
    calls it first and then spends; the verdict here and the refusal there
    are therefore one decision, never two opinions.

    Returns `{"ok": bool, "reason": str|None, "detail": {...}, "scope": ...}`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    path = path or ledger_path()

    def no(reason: str, **detail: Any) -> Dict[str, Any]:
        return {"ok": False, "reason": reason, "detail": detail,
                "scope": None, "strategy": "BTC5M"}

    # 0. the deploy drain, read as `authorize_arming` reads it
    _pause = (os.getenv(SUBMIT_PAUSE_ENV) or "").strip()
    if _pause and _pause.lower() not in ("0", "false", "no", "off"):
        return no("SUBMISSION_PAUSED_FOR_DEPLOY", env=SUBMIT_PAUSE_ENV)
    # 1. the two shared locks
    if operator_halt_engaged():
        return no("OPERATOR_HALT", env=LIVE_HALT_ENV)
    if shadow_lock_engaged():
        return no("SHADOW_ONLY_LOCK", env=SHADOW_ONLY_ENV)
    # 2. this process is the declared holder, on durable storage
    _role = role_refusal()
    if _role is not None:
        return no(str(_role["reason"]), **dict(_role.get("detail") or {}))
    if require_durable:
        _dur = durability_refusal(path)
        if _dur is not None:
            return no(str(_dur["reason"]), **dict(_dur.get("detail") or {}))
    # 3. a 5-minute market, by name, in THIS LANE'S series
    _series = lane_series()
    if _series is None:
        return no("BTC5M_LANE_SERIES_UNRESOLVED",
                  env=LANE_STRATEGY_ENV,
                  strategy=str(os.environ.get(LANE_STRATEGY_ENV, ""))[:40],
                  note="an unresolvable lane may not submit: the "
                       "permissive answer would authorize a series "
                       "nobody approved")
    mkt = str(market or "").strip() if isinstance(market, str) else ""
    if not mkt or not mkt.startswith(_series):
        return no("BTC5M_MARKET_NOT_IN_SERIES", series=_series,
                  market=str(market)[:80])
    # 4. the owner's approval: present, valid, for THIS series, with the
    #    windows enumerated, and this window one of them
    try:
        from src import l5_campaign_approval as _CAW   # late: avoids a cycle
        verdict = _CAW.campaign_decision(mkt, root=root, now=now,
                                         reserve=False)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return no("BTC5M_APPROVAL_UNVERIFIABLE", error=type(exc).__name__)
    if not verdict.get("present"):
        return no("BTC5M_APPROVAL_ABSENT", env=CAMPAIGN_APPROVAL_ENV,
                  note="no approval document: the 5-minute strategy has no "
                       "other way to be authorized")
    if not verdict.get("ok"):
        return no(str(verdict.get("reason") or "BTC5M_APPROVAL_NOT_VALID"),
                  **{"approval": dict(verdict.get("detail") or {})})
    scope = dict(verdict.get("scope") or {})
    if str(scope.get("series") or "") != _series:
        #: the approval must name THIS lane's series. A valid approval for
        #: a sibling series is still a refusal here.
        return no("BTC5M_APPROVAL_SERIES_MISMATCH",
                  series=str(scope.get("series"))[:60], expected=_series)
    if not scope.get("window_starts"):
        return no("BTC5M_WINDOWS_NOT_ENUMERATED",
                  note="the approval must list each window it authorizes; "
                       "an interval is not an explicit approval")
    # 5. the window is OPEN: not before its start, not at or after its end
    ws = _CAW._slug_start_utc(mkt)
    if ws is None:
        return no("WINDOW_START_UNREADABLE", market=mkt[:80])
    age = (now - ws).total_seconds()
    if age < 0:
        return no("BTC5M_WINDOW_NOT_OPEN", opens_in_s=round(-age, 1))
    if age >= BTC5M_WINDOW_S:
        return no("BTC5M_WINDOW_CLOSED", closed_for_s=round(
            age - BTC5M_WINDOW_S, 1))
    # 6. a named experiment and a configured total
    if not experiment_id():
        return no("BTC5M_CLAIM_NO_EXPERIMENT", env=EXPERIMENT_ENV)
    if max_submissions() is None:
        return no("BTC5M_SUBMISSION_BUDGET_NOT_SET", env=MAX_SUBMISSIONS_ENV,
                  note="the flat submission total must be configured for "
                       "the 5-minute strategy; unset is not unlimited here")
    # THE NONCE STAYS WITH THE OWNER. The verdict is published on the
    # heartbeat; the limits are what a reader needs, the credential is not.
    scope = {k: v for k, v in scope.items()
             if k not in ("nonce", "nonce_sid")}
    return {"ok": True, "reason": None, "detail": {}, "scope": scope,
            "strategy": "BTC5M", "window": verdict.get("window"),
            "document_sha256": verdict.get("document_sha256"),
            "window_start_utc": ws.isoformat(),
            "window_end_utc": (ws + dt.timedelta(
                seconds=BTC5M_WINDOW_S)).isoformat()}


def claim_btc5m_submission(market: str, client_order_id: str,
                           payload: Mapping[str, Any], *,
                           worst_case_usd: Any,
                           day_realized_usd: Any,
                           klass: Any,
                           reducing: bool = False,
                           path: Optional[str] = None,
                           root: Optional[str] = None,
                           now: Optional[dt.datetime] = None,
                           require_durable: bool = True) -> Dict[str, Any]:
    """Consume the claim for ONE 5-minute order, or raise. Never auto-resets.

    The caller invokes this BEFORE `transport.submit`. Everything that can
    refuse is evaluated before anything is spent; then, in this order, the
    window is reserved (idempotent per slug), the class ticket is taken, and
    the order's own lock is created and fsynced. A process that dies after
    this returns has spent the claim, which is the conservative direction.

    `worst_case_usd` is the strategy's dollars at risk INCLUDING this order;
    `day_realized_usd` its settled result for the current UTC day (negative
    is a loss). Both are the caller's own authenticated ledger figures, and
    an unreadable one refuses.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    path = path or ledger_path()
    v = btc5m_authorization(market, now=now, path=path, root=root,
                            require_durable=require_durable)
    if not v.get("ok"):
        raise Refused(str(v.get("reason")), dict(v.get("detail") or {}))
    mkt = str(market).strip()
    # THE SHARED WINDOW GUARD, by name, as every market-keyed pre-wire claim
    # calls it. The verdict above already required this window to be in the
    # approved set; this is the same decision made by the one function the
    # other two claims use, so the three can never disagree.
    refuse_unauthorized_window(mkt)
    scope = dict(v.get("scope") or {})
    coid = str(client_order_id or "").strip()
    #: THIS LANE'S id shape. `btc5m_authorization` above already refused
    #: an unresolvable lane, so None here is belt and braces -- and it
    #: refuses rather than falling back to the BTC pattern, which would
    #: let one lane claim for another.
    _coid_re = lane_coid_re()
    if _coid_re is None:
        raise Refused("BTC5M_LANE_SERIES_UNRESOLVED",
                      {"env": LANE_STRATEGY_ENV})
    if not _coid_re.fullmatch(coid):
        raise Refused("BTC5M_CLAIM_BAD_ORDER_ID",
                      {"client_order_id": coid[:40],
                       "note": "a 5-minute order carries the 5-minute "
                               "strategy's own id; anything else is not "
                               "this strategy's order to claim for"})
    # THE STRATEGY'S OWN LIMITS, from the document (already clamped to the
    # code ceilings by `ApprovalGate.state`). Unreadable is a refusal.
    cap, loss_cap = (_usd(scope.get("max_total_exposure_usd")),
                     _usd(scope.get("max_daily_loss_usd")))
    wc, day = _usd(worst_case_usd), _usd(day_realized_usd)
    if cap is None or loss_cap is None or cap <= 0 or loss_cap <= 0:
        raise Refused("BTC5M_APPROVED_LIMITS_UNREADABLE")
    if wc is None or wc < 0:
        raise Refused("BTC5M_EXPOSURE_UNREADABLE",
                      {"note": "the strategy's worst-case exposure "
                               "including this order was not stated"})
    if wc > cap:
        raise Refused("BTC5M_EXPOSURE_ABOVE_APPROVED",
                      {"worst_case_usd": str(wc),
                       "approved_usd": str(cap)})
    if day is None:
        raise Refused("BTC5M_DAY_RESULT_UNREADABLE")
    if day <= -loss_cap:
        raise Refused("BTC5M_DAILY_LOSS_LIMIT",
                      {"day_realized_usd": str(day),
                       "approved_loss_usd": str(loss_cap),
                       "note": "the strategy's settled loss for the UTC day "
                               "has reached the approved limit"})
    try:
        k = normalize_class(klass)
    except Refused:
        raise
    if k is None:
        raise Refused("BTC5M_CLASS_NOT_STATED",
                      {"known": list(SUBMISSION_CLASSES)})
    exp = experiment_id()
    sid = btc5m_sid(exp, coid)
    if len(re.sub(r"[^A-Za-z0-9_.-]", "_", sid)) > _LOCK_ID_MAX:
        raise Refused("BTC5M_CLAIM_ID_TOO_LONG", {"length": len(sid)})
    # A REPLAY OF THE SAME ORDER spends nothing further: no window, no
    # ticket. The lock is the record that it was already sent.
    if claim_held(sid, path):
        raise Refused("AUTHORIZATION_ALREADY_USED",
                      {"session_id": sid,
                       "note": "this order's claim is already spent; one "
                               "client order id is submitted once"})
    # THE FLAT TOTAL. Not applied to a risk-reducing order, for the reason
    # `total_backstop_binds` gives: a count that can refuse the hedge can
    # only strand the inventory the other classes created.
    if total_backstop_binds(reducing):
        over = submission_budget_refusal(path, exp)
        if over:
            raise Refused(str(over["reason"]), over)
    # THE WINDOW, spent last among the things that can still refuse for a
    # reason unrelated to the budget. Idempotent per slug.
    try:
        from src import l5_campaign_approval as _CAW   # late: avoids a cycle
        res = _CAW.reserve_window(mkt, root=root, now=now)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused("BTC5M_WINDOW_RESERVE_FAILED",
                      {"error": type(exc).__name__})
    if not res.get("ok"):
        raise Refused(str(res.get("reason") or "BTC5M_WINDOW_NOT_RESERVED"),
                      dict(res.get("detail") or {}) if isinstance(
                          res.get("detail"), Mapping) else {"window": res})
    ticket = class_claim_or_refuse(path, exp, k, reducing=reducing)
    rec = {"kind": "btc5m_submission_claim", "strategy": "BTC5M",
           "experiment": exp, "market": mkt, "sid": sid,
           "client_order_id": coid, "klass": k, "reducing": bool(reducing),
           "class_ticket": (None if ticket is None else ticket.get("sid")),
           "window_index": res.get("index"),
           "worst_case_usd": str(wc), "approved_exposure_usd": str(cap),
           "document_sha256": v.get("document_sha256"),
           "t": now.isoformat(),
           **dict(payload)}
    try:
        claim_once(path, sid, rec)
    except Refused:
        raise
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise Refused("BTC5M_CLAIM_UNAVAILABLE",
                      {"sid": sid, "market": mkt,
                       "error": "%s: %s" % (type(exc).__name__,
                                            str(exc)[:200]),
                       "note": "the claim could not be persisted, so it is "
                               "not held. Refusing rather than submitting "
                               "on an unproven claim."})
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


def _mandate_pubkeys_present() -> bool:
    """Whether a trusted mandate key is registered. Local import, same
    image-closure reason as every other `l5_mandate_signature` use here."""
    try:
        from src import l5_mandate_signature as MS
        return bool(MS.MANDATE_PUBKEYS)
    except BaseException:                                      # noqa: BLE001
        # A registry we cannot read is treated as PRESENT, so the promotion
        # gate binds rather than silently disappearing.
        return True


def _strategy_config_or_note() -> Dict[str, Any]:
    """The strategy fingerprint for the claim, or a named failure.

    IMPORTED LOCALLY. `l5_two_sided_probe` is the strategy, not the
    authorization layer, and a module-level import here would put it into
    the import closure of the probe and halt-reset images that do not copy
    it -- the same breakage `test_l5_live_probe_image_closure` caught for
    `l5_mandate_signature`.
    """
    try:
        from src import l5_two_sided_probe as TS
        return dict(TS.strategy_config_fingerprint())
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"label": None, "digest": None,
                "unreadable": type(exc).__name__,
                "labels": ("identifier only; an unreadable fingerprint "
                           "never refuses an otherwise-permitted window")}


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
                  max_windows: int = 1,
                  session: Optional[str] = None) -> Dict[str, Any]:
    """The exact envelope being approved, as plain strings.

    `session` NAMES WHO APPROVED IT, and under a campaign that is the
    CAMPAIGN, not `PMUS_Q1_SESSION_ID`. The scope digest is re-proved every
    window, so recording the legacy session id here kept the old failure
    alive even once the claim KEY was campaign-derived: rotating that
    variable mid-campaign changed the digest and refused every remaining
    window with MANDATE_SCOPE_CHANGED. The identity that keys the claim and
    the identity recorded in its scope have to be the same one.

    None falls back to `session_id()`, so the legacy single-window path is
    byte-identical to before.
    """
    d: Dict[str, Any] = {}
    try:
        d = dict(limits.as_dict()) if hasattr(limits, "as_dict") else dict(
            limits or {})
    except BaseException:                                      # noqa: BLE001
        d = {}
    scope = {
        "session_id": (session if session is not None else session_id()),
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


def _dec_or_none(v: Any):
    """A Decimal, or None. Unreadable is never zero and never unlimited."""
    if v is None:
        return None
    try:
        return D(str(v))
    except BaseException:                                      # noqa: BLE001
        return None


def _limit_of(limits: Any, field: str):
    """One field off the running SafetyLimits, or None if it cannot be read."""
    if limits is None:
        return None
    try:
        return _dec_or_none(getattr(limits, field))
    except BaseException:                                      # noqa: BLE001
        return None


def campaign_claim_sid(scope: Mapping[str, Any]) -> str:
    """The durable claim identity of ONE approved campaign.

    WHY THE CAMPAIGN NEEDS AN IDENTITY AT ALL. Every durable claim here is
    keyed on "who approved this" -- the mandate, the per-window claim, the
    holder check. That key used to be the owner's `PMUS_Q1_SESSION_ID`, so a
    campaign whose scope differed from the recorded mandate could only be
    authorized by ROTATING that variable: exactly the per-test ceremony the
    campaign layer exists to abolish.

    A campaign has its own owner-held credential -- the nonce, in a variable
    the executor cannot write -- so it can supply the identity itself. A new
    campaign is a new nonce is a new identity, which takes a fresh mandate
    under the new scope automatically. "A second mandate needs a new session
    id" becomes "a second campaign needs a new document", and the owner gets
    that by issuing one.

    HASHED, NOT THE NONCE. The identity goes into claim payloads on the
    volume and into the scope digest, so it must not carry the nonce itself.
    SHA-256 is one-way and the nonce is 32 bytes of os entropy, so the
    digest identifies the campaign without disclosing it.
    """
    nid = str(scope.get("nonce_sid") or "").strip()
    if not nid:
        return ""
    return "camp.%s" % hashlib.sha256(nid.encode()).hexdigest()[:24]


def authorize_arming(*, market: str, preflight: Any, store: Any,
                     halted: bool, reconciled: bool,
                     halt_gate_present: bool = False,
                     unresolved: Any = (), limits: Any = None,
                     shadow_locked: bool = False,
                     window_bounds: Optional[Mapping[str, Any]] = None,
                     nominal_window_s: Optional[float] = None,
                     series: Optional[str] = None,
                     max_windows: int = 1,
                     campaign: Optional[Mapping[str, Any]] = None,
                     auth: Optional["Authorization"] = None,
                     path: Optional[str] = None,
                     require_durable: bool = True,
                     transport_armable: Optional[bool] = None,
                     promotion_evidence: Optional[Mapping[str, Any]] = None,
                     gates: Any = None,
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

    # 0. THE DEPLOYMENT DRAIN, outermost because it is operational rather
    #    than economic: when the operator is about to replace this image,
    #    new submissions must stop BEFORE the restart so the process is not
    #    killed holding a resting order it has not reconciled.
    #
    #    WHY ARMING AND NOT THE TRANSPORT. Cancelling must stay available
    #    while draining -- a pause that blocked cancels could strand exactly
    #    the order it was protecting -- and `ShadowOnlyTransport` already
    #    establishes that asymmetry for the shadow lock. Arming is the gate
    #    that admits NEW exposure and nothing else, so pausing here stops
    #    submissions while leaving cancellation, reconciliation and
    #    settlement fully live.
    #
    #    UNSET IS RUNNING. An absent variable must not pause a live
    #    executor, so only an explicitly truthy value drains. Anything set
    #    and non-empty other than "0"/"false"/"no" pauses, because the safe
    #    reading of an unrecognised value in a STOP switch is stop.
    # carried out of the signed-mandate block for step 9b below. None means
    # "no signed mandate in force", which is the shipped state.
    _mandate_promotion: Optional[Mapping[str, Any]] = None
    _mandate_enforcing = bool(_mandate_pubkeys_present())

    _pause = (os.getenv(SUBMIT_PAUSE_ENV) or "").strip()
    if _pause and _pause.lower() not in ("0", "false", "no", "off"):
        raise ArmingRefusal(
            "SUBMISSION_PAUSED_FOR_DEPLOY",
            {"env": SUBMIT_PAUSE_ENV, "value": _pause[:40],
             "note": "new submissions are paused; cancellation, "
                     "reconciliation and settlement are unaffected. Clear "
                     "the variable to resume."})

    # 1. the structural lock, before anything else. Arming a transport whose
    #    submit() raises is meaningless, and saying so by name keeps the
    #    runner's logs honest about WHY it is not live.
    if shadow_locked:
        raise ArmingRefusal("SHADOW_ONLY_LOCK",
                            {"note": "submit() is structurally blocked; "
                                     "arming would change nothing"})
    # 1a. THE STRATEGY THAT OWNS THIS WINDOW MUST BE ABLE TO ACT IN IT.
    #
    #     HERE, before the signature, the campaign and above all before the
    #     claim: this is a statement about configuration, it is cheap, and a
    #     refusal must cost the owner NOTHING. On 2026-11-09 20:00Z arming
    #     passed every check below, claimed the single authorized window,
    #     and then placed no order, because the window was exclusive to a
    #     two-sided engine still bound to the 15:30Z market. See
    #     `l5_two_sided_probe.window_owner_refusal`.
    _wor = window_owner_refusal(market)
    if _wor is not None:
        raise ArmingRefusal(str(_wor.get("reason")), _wor)
    # 1b. THE OWNER'S SIGNATURE, and it is the control the campaign document
    #     is not. `l5_campaign_approval` says Claude cannot write its
    #     document; `l5_mandate_signature` records that this is false in
    #     practice, because anyone holding a Railway token can set the
    #     variable. A signature fixes that by making writing the bytes
    #     INSUFFICIENT: the payload must carry an Ed25519 signature from a
    #     key whose private half the owner holds offline and which this
    #     process has never seen and cannot produce.
    #
    #     IT IS A NECESSARY ADDITIONAL CONDITION, NOT A REPLACEMENT, and
    #     it is checked FIRST so that an unsigned mandate is named as such
    #     rather than reported as some incidental inner failure. The campaign
    #     document still does its own job below -- bounding windows and
    #     reserving one durably -- but writing that variable alone no longer
    #     authorizes anything, which is the entire fix.
    #
    #     SELF-ACTIVATING, AND THAT IS DELIBERATE. The gate binds exactly
    #     when `MANDATE_PUBKEYS` is non-empty. With no trusted key -- the
    #     shipped state -- behaviour is unchanged, which keeps every existing
    #     one-window probe and test authorized as before. The moment the
    #     owner's public half is committed in a reviewed change, a signature
    #     becomes REQUIRED for live arming and no variable edit can satisfy
    #     it. Turning the gate off therefore takes a repo commit and a
    #     deploy, not an environment change.
    # IMPORTED HERE, NOT AT MODULE LEVEL, and the reason is concrete: a
    # top-level import put `l5_mandate_signature` into the import closure of
    # every image that carries this module, and `l5_live_probe` and
    # `l5_halt_reset` do not COPY it -- so both images stopped being able to
    # import their own entrypoint. `test_l5_live_probe_image_closure` caught
    # it. The gate runs once per arming attempt, so a local import costs
    # nothing, and this keeps each image's surface to what it actually runs.
    from src import l5_mandate_signature as MS
    if MS.MANDATE_PUBKEYS:
        # THE HALT LATCH IS STILL REQUIRED, as the FAST revocation route.
        # The signing procedure names three ways to withdraw a mandate and
        # the latch is the first: no redeploy, and this process cannot
        # clear it. The revocation LIST below handles a named mandate; the
        # latch stops everything immediately. A signed mandate with no
        # latch has no immediate withdrawal path and is refused.
        if not halt_gate_present:
            raise ArmingRefusal(
                "MANDATE_REVOCATION_UNAVAILABLE",
                {"needs": ["L5_HALT_URL"],
                 "note": "the halt latch is the immediate withdrawal path "
                         "for a signed mandate; with no latch there is no "
                         "way to stop it quickly and it is refused"})
        _mraw = os.getenv(MS.MANDATE_ENV)
        _mkey = ""
        try:
            _mkey = str((json.loads(_mraw or "{}") or {}).get("key_id") or "")
        except BaseException:                                  # noqa: BLE001
            _mkey = ""
        # THE REPLAY FLOOR AND THE REVOCATION LIST, BOTH FROM THE WATCHDOG,
        # over the GET-only clients that already exist for them. They are
        # read from the watchdog and not from our own disk on purpose: a
        # floor this process can write is a floor this process can roll
        # back, and rolling it back would let an older, looser owner-signed
        # mandate be replayed. Neither is defaulted -- absence, staleness, a
        # malformed document and an unreachable host all refuse, which is
        # the rule `HaltGate` already applies to the latch.
        _store_url = os.getenv(MS.STORE_URL_ENV, "").strip()
        if not _store_url:
            raise ArmingRefusal(
                "MANDATE_STORE_UNCONFIGURED",
                {"needs": [MS.STORE_URL_ENV],
                 "serves": [MS.FLOOR_KEY, MS.REVOCATION_KEY],
                 "note": "the watchdog must publish the replay floor and "
                         "the revocation list; without them this process "
                         "cannot establish that its mandate is current and "
                         "unrevoked, and an unestablished answer is a "
                         "refusal"})
        try:
            _floor = MS.HttpSeqFloorStore(_store_url).floor(_mkey)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ArmingRefusal("MANDATE_SEQ_FLOOR_UNAVAILABLE",
                                {"error": type(exc).__name__,
                                 "detail": ("%s" % exc)[:160]})
        try:
            _revoked = MS.HttpRevocationStore(_store_url).revoked()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ArmingRefusal("MANDATE_REVOCATION_UNAVAILABLE",
                                {"error": type(exc).__name__,
                                 "detail": ("%s" % exc)[:160]})
        _v = MS.verify(
            _mraw,
            environment=os.getenv("RAILWAY_ENVIRONMENT_NAME", ""),
            service_role=os.getenv(SERVICE_ROLE_ENV, ""),
            series=(series or os.getenv(SERIES_ENV, "")),
            now=now,
            seq_floor=_floor, revoked=_revoked)
        if not _v.get("ok"):
            raise ArmingRefusal(
                str(_v.get("reason") or "MANDATE_REFUSED"),
                {"detail": _v.get("detail"),
                 "trusted_key_ids": sorted(MS.MANDATE_PUBKEYS),
                 "note": "a trusted key is registered, so a valid "
                         "owner-SIGNED mandate is required and no variable "
                         "edit can substitute for one"})
        # THE SIGNED SCOPE MUST BOUND THE RUNNING LIMITS, by the same
        # intersection discipline the campaign approval uses: a limit
        # widened after signing invalidates the signature's scope rather
        # than inheriting it.
        # the verdict carries the payload under `scope`, not `payload`
        _mp = dict(_v.get("scope") or {})
        _mcap = dict(_mp.get("capital") or {})
        _mloss = dict(_mp.get("loss_limits") or {})
        _sig_exp = _dec_or_none(_mcap.get("max_total_exposure_usd"))
        _sig_loss = _dec_or_none(_mloss.get("daily_realized_loss_halt_usd"))
        _run_exp = _limit_of(limits, "max_total_exposure_usd")
        _run_loss = _limit_of(limits, "daily_realized_loss_halt_usd")
        if _sig_exp is None or _sig_loss is None:
            raise ArmingRefusal("MANDATE_SCOPE_UNREADABLE", {})
        if _run_exp is None or _run_loss is None:
            raise ArmingRefusal(
                "MANDATE_RUNNING_LIMITS_UNREADABLE",
                {"note": "limits that cannot be read cannot be shown to be "
                         "inside the signed scope"})
        if _run_exp > _sig_exp or _run_loss > _sig_loss:
            raise ArmingRefusal(
                "MANDATE_LIMITS_WIDER_THAN_SIGNED",
                {"signed_exposure": str(_sig_exp),
                 "running_exposure": str(_run_exp),
                 "signed_daily_loss": str(_sig_loss),
                 "running_daily_loss": str(_run_loss)})
        # MAKER ONLY IS A SIGNED TERM, re-asserted here so a signed mandate
        # cannot be read as authorizing a crossing order even if some other
        # layer were to permit one.
        if str(_mp.get("execution_mode") or "").upper() != "MAKER_ONLY":
            raise ArmingRefusal("MANDATE_EXECUTION_MODE_NOT_MAKER_ONLY",
                                {"mode": str(_mp.get("execution_mode"))[:40]})

        # THE SIGNED PROMOTION TERMS, CARRIED FORWARD. The promotion gate
        # itself runs LATE (step 9b): it depends on measured evidence, which
        # makes it the least structural check in this function, and this
        # function's contract is that a refusal names the OUTERMOST reason.
        # Refusing here would report "the strategy is unproven" to an
        # operator whose actual problem is a missing authorization.
        _mandate_promotion = _mp.get("promotion")

    # 2. EXPLICIT OWNER AUTHORIZATION, from EITHER of two owner-held
    #    objects. Not both, and neither is weaker than the other.
    #
    #    THE CAMPAIGN DOCUMENT IS AN AUTHORIZATION, NOT MERELY A BOUND ON
    #    ONE. Its security property is the same one the Q1 handshake has and
    #    the same one `HttpHaltStore` states for the halt latch: the owner
    #    holds an object the executor cannot write. `EnvApprovalStore.put`
    #    refuses by design, so Claude cannot approve a campaign. Requiring
    #    the Q1 handshake ON TOP of a valid campaign bought nothing -- both
    #    are the same kind of evidence from the same owner -- and cost
    #    exactly what this layer exists to abolish: a Railway edit per
    #    campaign, and, because the mandate is keyed on the session id, a
    #    session-id rotation whenever the scope changed.
    #
    #    THE LEGACY PATH IS UNTOUCHED. With no campaign document, the Q1
    #    handshake is required exactly as before, so one-window probes and
    #    every existing test keep their authorization.
    #
    #    FAIL CLOSED ON BOTH: with neither a valid campaign nor the
    #    handshake, this refuses and names both ways to satisfy it.
    _camp_present = bool(campaign is not None and campaign.get("present"))
    _camp_ok = bool(_camp_present and campaign.get("ok"))
    _camp_scope = dict((campaign or {}).get("scope") or {})
    # PRESENT AND NOT OK REFUSES BY ITS OWN NAME, FIRST. Falling through to
    # OWNER_AUTHORIZATION_ABSENT would report an exhausted or expired
    # campaign as "no authorization configured", which sends an operator
    # looking for a missing variable instead of at the budget that ran out.
    if _camp_present and not _camp_ok:
        raise ArmingRefusal(
            str(campaign.get("reason") or "CAMPAIGN_NOT_APPROVED"),
            {"detail": campaign.get("detail"),
             "note": "a campaign document exists and does not permit this; "
                     "the legacy single-window handshake is NOT a fallback"})
    if _camp_ok:
        sid = campaign_claim_sid(_camp_scope)
        if not sid:
            raise ArmingRefusal("CAMPAIGN_IDENTITY_UNKNOWN",
                                {"note": "an approved campaign with no "
                                         "identity cannot key a durable "
                                         "claim, and an unkeyed claim is "
                                         "not a claim"})
    elif Q1.armed():
        sid = session_id()
        if not sid:
            raise ArmingRefusal("OWNER_AUTHORIZATION_ABSENT",
                                {"needs": [Q1.SID_ENV]})
    else:
        raise ArmingRefusal(
            "OWNER_AUTHORIZATION_ABSENT",
            {"needs_either": {
                "campaign": [CAMPAIGN_APPROVAL_ENV],
                "legacy_handshake": [Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV]},
             "campaign_present": _camp_present,
             "note": "a valid campaign approval authorizes campaign windows "
                     "on its own; without one the legacy one-window "
                     "handshake is required"})
    # 2b. THE CAMPAIGN APPROVAL, when one exists.
    #
    #     Passed in as a FACT by the caller, exactly as `reconciled` and
    #     `halted` are, so a caller that cannot establish it has to say so
    #     and be refused rather than omit it.
    #
    #     THREE OUTCOMES, and the middle one is the whole point:
    #       absent          -> this says nothing; the single-window
    #                          handshake above already authorized us, and a
    #                          campaign layer that REPLACED it would be a
    #                          widening disguised as a feature.
    #       present, not ok -> REFUSE. Never fall back to the single-window
    #                          path: an expired, exhausted or unreadable
    #                          campaign would otherwise quietly become a
    #                          fresh one-window authorization, which is the
    #                          opposite of what spending a window means.
    #       present, ok     -> proceed, with the window already reserved
    #                          durably by the caller.
    if _camp_ok:
        # present-and-not-ok already refused in step 2, by its own name.
        _scope = _camp_scope
        # the running caps must sit INSIDE what was approved. A cap widened
        # after approval invalidates the approval rather than inheriting it.
        _appr_exp = _dec_or_none(_scope.get("max_total_exposure_usd"))
        _appr_loss = _dec_or_none(_scope.get("max_daily_loss_usd"))
        _run_exp = _limit_of(limits, "max_total_exposure_usd")
        _run_loss = _limit_of(limits, "daily_realized_loss_halt_usd")
        if _appr_exp is None or _appr_loss is None:
            raise ArmingRefusal("CAMPAIGN_SCOPE_UNREADABLE", {"scope": _scope})
        if _run_exp is None or _run_loss is None:
            raise ArmingRefusal("CAMPAIGN_RUNNING_LIMITS_UNREADABLE",
                                {"note": "limits that cannot be read cannot "
                                         "be shown to be inside the approval"})
        if _run_exp > _appr_exp or _run_loss > _appr_loss:
            raise ArmingRefusal("CAMPAIGN_LIMITS_WIDER_THAN_APPROVED",
                                {"approved_exposure": str(_appr_exp),
                                 "running_exposure": str(_run_exp),
                                 "approved_daily_loss": str(_appr_loss),
                                 "running_daily_loss": str(_run_loss)})

        # THE WORST-CASE DEFICIT BOUND, REQUIRED ONLY WHEN THE APPROVAL
        # NAMES IT -- and then absolutely.
        #
        # WHY THIS IS NEEDED. The deficit gate is written
        # `if self.max_worst_case_deficit_usd is not None:`, so an UNSET
        # limit does not loosen the bound -- it removes the check entirely.
        # The Stage 1 preflight found exactly that in production: the
        # heartbeat's `effective_cash_gate` enumerated the gates in force
        # and the deficit bound was not among them, so a risk profile
        # specified as "$1 maximum worst-case deficit" would have armed with
        # no deficit bound at all and a `max_usd_per_market` of 20.
        #
        # WHY IT IS OPT-IN, AND WHY THAT IS NOT A LOOPHOLE. The requirement
        # is declared in the CAMPAIGN SCOPE, which is the owner-signed
        # document. A campaign that does not name a deficit says nothing
        # here and every existing configuration is untouched -- so this
        # introduces no new live limit by itself. A campaign that DOES name
        # one cannot then be armed without it, which is the property the
        # Stage 1 profile needs.
        #
        # THREE WAYS TO FAIL, all refusals, because an absent bound is not a
        # wide bound -- it is no bound:
        #   absent / unreadable -> CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT
        #   non-positive        -> CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT
        #   wider than approved -> CAMPAIGN_LIMITS_WIDER_THAN_APPROVED
        _appr_def = _dec_or_none(_scope.get("max_worst_case_deficit_usd"))
        if _appr_def is not None:
            _run_def = None
            try:
                _run_def = _dec_or_none(
                    getattr(gates, "max_worst_case_deficit_usd", None))
            except BaseException:                              # noqa: BLE001
                _run_def = None
            if _run_def is None or _run_def <= 0:
                raise ArmingRefusal(
                    "CAMPAIGN_REQUIRES_WORST_CASE_DEFICIT",
                    {"approved_worst_case_deficit": str(_appr_def),
                     "running_worst_case_deficit": (
                         None if _run_def is None else str(_run_def)),
                     "note": ("the approval names a worst-case deficit "
                              "bound; an unset or non-positive running "
                              "limit REMOVES the deficit check rather than "
                              "widening it, so arming is refused")})
            if _run_def > _appr_def:
                raise ArmingRefusal(
                    "CAMPAIGN_LIMITS_WIDER_THAN_APPROVED",
                    {"approved_worst_case_deficit": str(_appr_def),
                     "running_worst_case_deficit": str(_run_def)})
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
    # A CAMPAIGN CARRIES ITS OWN ABSOLUTE DEADLINE, clamped by the code to
    # four hours from issue, so the owner does not have to set a second one
    # by hand and the two cannot disagree. It is applied IN ADDITION to the
    # env deadline, never instead: whichever is sooner binds.
    if _camp_ok:
        _cexp = _camp_scope.get("expires_utc")
        try:
            _cdl = dt.datetime.fromisoformat(str(_cexp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ArmingRefusal("CAMPAIGN_EXPIRY_UNREADABLE",
                                {"expires_utc": str(_cexp)[:40]})
        if _cdl.tzinfo is None:
            raise ArmingRefusal("CAMPAIGN_EXPIRY_NAIVE",
                                {"expires_utc": str(_cexp)[:40]})
        if now > _cdl:
            raise ArmingRefusal("CAMPAIGN_EXPIRED",
                                {"expires_utc": _cdl.isoformat(),
                                 "now": now.isoformat()})
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
    # 9a. THE CAMPAIGN WORST-CASE BUDGET, at arming.
    #
    #     WHY HERE AND NOT ONLY PER ORDER. The per-order gate refuses the
    #     order that would breach the budget, but arming happens once per
    #     window and `WindowLedger.reserve` is deliberately NOT refundable:
    #     arming an exhausted campaign would spend a window that can only
    #     ever be refused. This also enforces the campaign LOCK at the
    #     earliest point, so a campaign that does not own the budget never
    #     reaches the order path at all.
    #
    #     ABSENT IS OFF. No budget configured leaves every existing
    #     authorization path byte-for-byte unchanged.
    # THE RELEASE OUTCOME IS EVIDENCE, SO IT HAS TO LEAVE THIS FUNCTION.
    # It was computed and dropped on the floor, which made the one ledger
    # transition that matters -- a finished campaign's budget handed to the
    # next -- invisible to the heartbeat. It now rides the window claim, the
    # record the owner already reads in L5_LIVE_ARMED.
    _budget_release = None
    if _camp_ok:
        _budget = campaign_budget_ledger()
        if _budget is not None:
            _cid = str(_camp_scope.get("nonce_sid") or "").strip()
            # RELEASE A FINISHED CAMPAIGN'S BUDGET BEFORE CLAIMING.
            #
            # `close` was the right gate and nothing called it, so a budget
            # stayed OPEN for ever once its windows were spent and every
            # later campaign was refused CAMPAIGN_BUDGET_LOCKED. Seen in
            # production at 2026-10-23T02:08:08Z, with the venue reporting
            # positions {}, openOrders 0 and unsettledFunds 0.
            #
            # EVIDENCE, NOT ASSERTION. `release_stale` refuses unless the
            # store shows nothing outstanding and nothing unsettled, refuses
            # a campaign releasing ITSELF (which would reset peak_usd
            # mid-run), and erases no loss: realized figures live in the
            # store and re-enter the gate on every evaluation. An unreadable
            # store yields a SENTINEL outstanding entry, so the release
            # fails closed rather than freeing a budget we cannot vouch for.
            try:
                _live = store.live_orders() if store is not None else {}
                _out = [o for o in (_live or {}).values()
                        if _dec_or_none(o.get("remaining")) is None
                        or (_dec_or_none(o.get("remaining")) or D(0)) > 0]
                _ret = (getattr(store, "state", {}) or {}).get(
                    "retired_markets") or {}
                _legs = [{"market": m, "settled": m in _ret}
                         for m in ((store.committed() or {})
                                   if store is not None else {})]
            except BaseException as _exc:                      # noqa: BLE001
                if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                    raise
                _out = [{"UNREADABLE": type(_exc).__name__}]
                _legs = [{"market": "UNREADABLE", "settled": False}]
            _rel = _budget.release_stale(_cid, legs=_legs, outstanding=_out,
                                         now_iso=str(now or ""))
            # the release is reported through the refusal/telemetry channel
            # the caller already reads; this module does not own a logger.
            _budget_release = dict(_rel)
            _budget_release["labels"] = (
                "CANNOT_RELEASE_OWN_OPEN_CAMPAIGN is the NORMAL reading once "
                "this campaign owns the budget: a campaign may not close its "
                "own live ledger, because that would reset its own peak. "
                "released=true appears exactly once, on the arm that takes "
                "the budget over from a campaign proved finished.")
            _why = _budget.refuse_arming(campaign_id=_cid)
            if _why:
                raise ArmingRefusal(
                    "CAMPAIGN_BUDGET_REFUSED",
                    {"why": _why, "campaign_id": _cid,
                     "note": "the campaign's worst-case budget is spent or "
                             "owned by another campaign; a new nonce does "
                             "not mint a new budget"})
            # CLAIM THE BUDGET, and this is not bookkeeping.
            #
            # Without this call the ledger is never OPENED, and the whole
            # anti-reset property is inert: `read()` returns the closed
            # placeholder, so `open_id` is None, so the LOCKED branch can
            # never fire and the durable peak -- guarded by
            # `open_id and not closed` -- is never written. The per-order
            # AMOUNT ceiling still binds, because that check runs before the
            # ownership logic, which is exactly why the gap was invisible in
            # telemetry: the number looked enforced while the lock was not.
            #
            # Claiming at ARMING is the right moment: it is once per window,
            # it is after refuse_arming has already rejected a foreign
            # campaign, and it is before any order can exist.
            _claim = _budget.open_or_resume(
                campaign_id=_cid,
                now_iso=dt.datetime.now(dt.timezone.utc).isoformat())
            if not _claim.get("ok"):
                raise ArmingRefusal(
                    "CAMPAIGN_BUDGET_REFUSED",
                    {"why": _claim.get("reason"), "campaign_id": _cid,
                     "note": "the budget is owned by another campaign"})
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
    # A CAMPAIGN NAMES ITS OWN SERIES, from the code's allowlist, so the
    # owner need not set `L5_LIVE_AUTH_MARKET_SERIES` as well. Both are
    # applied: the campaign layer has already refused a market outside its
    # series (CAMPAIGN_SERIES_MISMATCH) before this is reached.
    pre = series or series_prefix() or (
        str(_camp_scope.get("series") or "") if _camp_ok else "")
    if _camp_ok and not want:
        _cser = str(_camp_scope.get("series") or "")
        if not _cser or not str(market).startswith(_cser):
            raise ArmingRefusal("AUTHORIZATION_BOUND_TO_MARKET",
                                {"authorized_series": _cser,
                                 "attempted": str(market),
                                 "note": "the approved campaign's series"})
    if (mandate_mode() or _camp_ok) and not want:
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
    # THE INTERSECTION, NEVER THE WIDER OF THE TWO. A campaign states its own
    # window budget, already clamped to the code ceiling, so the owner need
    # not also set `L5_LIVE_AUTH_MAX_WINDOWS` -- and if both are set, the
    # tighter one binds. Unreadable on the campaign side is a refusal, not a
    # fallback to the env: a budget we cannot read is not a budget.
    if _camp_ok:
        try:
            _cmw = int(_camp_scope.get("max_windows"))
        except (TypeError, ValueError):
            raise ArmingRefusal("CAMPAIGN_WINDOW_BUDGET_UNREADABLE",
                                {"max_windows":
                                     str(_camp_scope.get("max_windows"))[:20]})
        if _cmw < 1:
            raise ArmingRefusal("CAMPAIGN_WINDOW_BUDGET_BELOW_ONE",
                                {"max_windows": _cmw})
        # the env default is 1 when unset, which would silently cap a
        # four-window campaign at one window; an UNSET env therefore defers
        # to the campaign, and a SET one can only tighten it.
        mw = _cmw if not (os.getenv(MAX_WINDOWS_ENV) or "").strip() \
            else min(mw, _cmw)
    scope = mandate_scope(market=("" if mw > 1 else market), limits=limits,
                          series=pre, max_windows=mw,
                          # the identity that KEYS the claim, so a legacy
                          # session rotation cannot change the digest a
                          # campaign's later windows must re-prove
                          session=sid)
    digest = scope_digest(scope)
    holder = holder_identity()
    # 9b. THE PROMOTION GATE -- the last economic condition before a claim
    #     is written, and deliberately the LAST of all of them.
    #
    #     A standing mandate is only safe to leave installed if it will not
    #     trade a strategy nobody has shown to make money. The mandate
    #     states the bar itself -- minimum settled sample, minimum
    #     expectancy, a NAMED out-of-sample evidence id, and a requirement
    #     for bounded live validation -- and this refuses until the measured
    #     evidence clears it. The bar is in the SIGNED bytes, so no variable
    #     edit can lower it and nothing in this process can tune it.
    #
    #     WHY HERE AND NOT IN THE RUNNER. Arming is the only place arming is
    #     decided. A promotion check in the runner is a check some other call
    #     path can forget; one here cannot be routed around.
    #
    #     WHY LAST. Every structural and authorization refusal above is
    #     cheaper and more informative. Ordering this after them means an
    #     operator missing an authorization is told that, and only a request
    #     that is otherwise fully permitted is told the strategy is unproven.
    #
    #     IT BINDS ONLY WITH A SIGNED MANDATE, exactly like the signature
    #     gate, so the shipped no-trusted-key state is unchanged.
    #
    #     FAIL CLOSED, TWICE OVER. `evaluate` defaults to blocking and
    #     returns every reason; and only an EXPLICIT False is accepted, so a
    #     malformed verdict or a missing key refuses. `blocks_live: False`
    #     means only that this gate adds no refusal -- never permission.
    #
    #     THIS GATE IS EXPECTED TO REFUSE TODAY, and that refusal is the
    #     acceptance test for requirement 9, not a defect to tune away:
    #     measured realized expectancy is -$0.0108 per settled window over
    #     our seven settled windows, against a best validated ex-ante
    #     improvement of +$0.0176/window.
    #     ON THE SECOND CLAUSE. `MS.verify` already refuses a mandate whose
    #     `promotion` block is missing or incomplete, so a verified mandate
    #     always carries one and the first clause is what fires in practice.
    #     `_mandate_enforcing` is a deliberate backstop against a future
    #     schema change that made the block optional: with a trusted key
    #     registered, this gate binds whether or not the document asked it
    #     to. It is exercised by
    #     `test_the_gate_binds_on_a_trusted_key_even_with_no_promotion_terms`.
    if _mandate_promotion is not None or _mandate_enforcing:
        from src import l5_promotion_gate as PG
        _pev = dict(promotion_evidence or {})
        try:
            _pv = PG.evaluate(promotion=_mandate_promotion,
                              shadow=_pev.get("shadow"),
                              oos_evidence=_pev.get("oos_evidence"),
                              live_validation=_pev.get("live_validation"),
                              now=now)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ArmingRefusal(
                "PROMOTION_GATE_UNEVALUABLE",
                {"error": type(exc).__name__,
                 "note": "a promotion gate that cannot be evaluated is a "
                         "promotion gate that has not been satisfied"}
            ) from exc
        if _pv.get("blocks_live") is not False:
            raise ArmingRefusal(
                "PROMOTION_GATE_BLOCKS_LIVE",
                {"reasons": list(_pv.get("reasons") or ())[:12],
                 "settled_windows": _pv.get("settled_windows"),
                 "net_per_settled_window": _pv.get("net_per_settled_window"),
                 "note": "the signed mandate states a promotion bar and the "
                         "measured evidence does not clear it; no variable "
                         "edit can satisfy this and nothing here tunes it"})

    claim = {"event": "CLAIMED", "session_id": sid, "market": market,
             "utc": now.isoformat(), "expires_utc": window["expires_utc"],
             "ttl_s": ttl, "approved_limits": approved,
             "window": window, "scope": scope, "scope_digest": digest,
             "holder": holder,
             "boundary": "authorize_arming",
             # THE STRATEGY CONFIGURATION THAT PRODUCED THIS WINDOW.
             # Recorded so a settled result can be attributed to the
             # configuration in force when it was armed, which is what makes
             # a before/after comparison across a deployment possible at
             # all. It is an IDENTIFIER: nothing reads it to decide
             # anything, and an unreadable fingerprint is recorded as such
             # rather than refusing -- a label that cannot be computed is a
             # reporting defect, not a reason to decline a window that every
             # risk gate has already permitted.
             "strategy_config": _strategy_config_or_note(),
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
    # 10c-bis. THE TRANSPORT'S OWN ARM CHECK, BEFORE ANY CLAIM IS WRITTEN.
    #
    # A WINDOW IS NOT SPENT ON AN ARM THAT CANNOT HAPPEN. Step 2 accepts
    # EITHER a valid campaign approval OR the Q1 handshake, deliberately --
    # the campaign document is an owner-held authorization in its own right
    # and requiring the handshake on top of it "bought nothing". But
    # `PMUSTransport.arm()` calls `_require_armed()`, which requires
    # `Q1.armed()` ALWAYS. So a process holding a valid campaign and no
    # handshake passes this boundary, SPENDS a window at the claim below,
    # and is then refused by the transport.
    #
    # 0145z went that way: the claim was written at 01:45:01.572 and
    # `t.arm()` raised NotArmed at 01:45:18 for the absent handshake. One of
    # three authorized windows, consumed with no order and no possibility of
    # one.
    #
    # WHY IT MOVED, AND THIS IS THE DEFECT. The check was written inside the
    # `if mw > 1:` mandate branch, so it was evaluated for a MULTI-window
    # mandate and skipped entirely for a ONE-window campaign -- which is the
    # exact shape of every bounded single-window probe this programme runs,
    # and the shape of the campaign that lost 0145z. Found by
    # `analysis/l5/issue206/verify_arming_gates.py` while verifying the gates
    # for a one-window approval: with `max_windows` 1, no handshake and
    # `transport_armable` False, the boundary GRANTED the claim and wrote the
    # lock; with `max_windows` 2 or 4 it correctly refused
    # TRANSPORT_CANNOT_ARM. So the repair protected every case except the
    # only one in use.
    #
    # It now sits before BOTH claim paths -- the mandate's two-level claim
    # and the single-window claim at step 11 -- because the property is about
    # the transport and not about how many windows were approved.
    #
    # PASSED IN AS A FACT, exactly as `halted` and `reconciled` are, so a
    # caller that cannot establish it says so rather than omitting it. None
    # means "not stated" and behaves exactly as before, which keeps every
    # existing caller and test unchanged; only False refuses.
    #
    # THIS WIDENS NOTHING. A campaign-only process could never arm -- the
    # transport refused it one call later -- so refusing it here changes no
    # reachable outcome except that the window survives. The authorization
    # scope is untouched: what may arm is still decided by step 2 and by the
    # transport, not here.
    if transport_armable is False:
        raise ArmingRefusal(
            "TRANSPORT_CANNOT_ARM",
            {"note": "the transport's own arm check would refuse this "
                     "process, so no window is spent on it",
             "needs": [Q1.LIVE_ENV, Q1.SID_ENV, Q1.ARM_ENV],
             "window_preserved": True})

    if mw > 1:
        # a campaign's own expiry, verified at step 8 above, IS the absolute
        # deadline this requires; the env one is then optional.
        if not _camp_ok and not os.getenv(NOT_AFTER_ENV, "").strip():
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
            # THE HOLDER, AND WHY A CAMPAIGN BINDS IT PER WINDOW INSTEAD.
            #
            # `holder_refusal` refuses a restart on purpose: "a restart must
            # never silently resume trading". That is right for a mandate
            # the owner armed as one trading session -- and fatal for a
            # four-window campaign, because this service redeploys whenever
            # a variable changes and the new process has a new deployment
            # id, boot id and pid. Every remaining window would refuse
            # MANDATE_HOLDER_CHANGED, so the campaign could never finish
            # without the owner re-arming by hand: exactly what the campaign
            # exists to abolish.
            #
            # WHAT IT PROTECTS AGAINST, AND WHAT STILL DOES. The hazard is
            # two overlapping executors trading the SAME window. That is
            # already prevented per window by `claim_once` on the window
            # key -- one O_EXCL winner, durably, forever -- and by the
            # per-nonce ledger, which no restart can hand back. The
            # mandate-level holder EQUALITY adds nothing against overlap; it
            # only forbids a later process from using windows nobody has
            # spent.
            #
            # SO THE RELAXATION IS NARROW AND NAMED: under a campaign the
            # recorded holder is kept for audit and not required to match,
            # while an executor that cannot identify its own deployment is
            # still refused -- that is about self-knowledge, not continuity,
            # and an actor we cannot name has no business taking a claim.
            # The legacy single-window path keeps the original rule intact.
            if _camp_ok:
                _me = dict(holder)
                if not _me.get("deployment_id") or not _me.get("boot_id"):
                    raise ArmingRefusal(
                        "DEPLOYMENT_IDENTITY_UNKNOWN",
                        {"have": sorted(k for k, v in _me.items() if v),
                         "note": "an executor that cannot identify its own "
                                 "deployment cannot be named in a claim"})
            else:
                bad = holder_refusal(recorded.get("holder") or {}, holder)
                if bad:
                    raise ArmingRefusal(bad["reason"], bad["detail"])
        # A WINDOW IS NOT SPENT ON AN ARM THAT CANNOT HAPPEN.
        #
        # WHAT THIS FIXES, measured live on 2026-10-30. Step 2 accepts
        # EITHER a valid campaign approval OR the Q1 handshake, deliberately
        # -- the campaign document is an owner-held authorization in its own
        # right and requiring the handshake on top of it "bought nothing".
        # But `PMUSTransport.arm()` calls `_require_armed()`, which requires
        # `Q1.armed()` ALWAYS. So a process holding a valid campaign and no
        # handshake passes this boundary, SPENDS a window at the claim
        # below, and is then refused by the transport.
        #
        # 0145z went that way: the claim was written at 01:45:01.572 and
        # `t.arm()` raised NotArmed at 01:45:18 for the absent handshake.
        # One of three authorized windows, consumed with no order and no
        # possibility of one. It is the same shape as the defect already
        # fixed for `reserve_window` on 2026-10-20, one layer down.
        #
        # PASSED IN AS A FACT, exactly as `halted` and `reconciled` are, so
        # a caller that cannot establish it says so rather than omitting it.
        # None means "not stated" and behaves exactly as before, which keeps
        # every existing caller and test unchanged; only False refuses.
        #
        # THIS WIDENS NOTHING. A campaign-only process could never arm --
        # the transport refused it one call later -- so refusing it here
        # changes no reachable outcome except that the window survives. The
        # authorization scope is untouched: what may arm is still decided by
        # step 2 and by the transport, not here.
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
                      budget_release=_budget_release,
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
