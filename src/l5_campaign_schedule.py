"""A SHORT RUN OF OWNER-APPROVED WINDOWS, ONE DECISION AT A TIME. Default off.

WHY. Each live window so far was prepared by hand: pick a window, dry-run
the gates, wait, arm, watch, restore, wait for settlement. Hours pass
between fifteen-minute measurements. This module is the part of that loop
that can be decided mechanically -- WHICH step is due now for WHICH exactly
approved window -- so that two to four approved windows can be worked
through without re-deciding the procedure each time.

WHAT IT IS. A pure function over three things the caller supplies: the
owner's plan, the record of what has happened so far, and the clock. It
returns one step. It reads no network, holds no credentials, writes no
variable, arms nothing and submits nothing. Arming is still done by the
existing path (`l5_launch`, which carries the owner's approval document and
refuses unless its digest matches), with every gate that path already has.

WHAT IT WILL NOT DO.

  * choose a window. The plan lists exact window starts; a window that is
    missed or refused is never replaced by another one.
  * start without the owner. The plan carries a digest; `next_step` refuses
    unless the caller presents the digest the owner approved, and the plan
    must state windows, per-window and aggregate limits, budgets, switches,
    maker-only, and stop-on-failure. Nothing is defaulted.
  * widen anything. `arming_variables` refuses a figure looser than the
    one in force, and the only switches it will write are in `SWITCHES`.
  * carry on after trouble. Any refused gate, a missed window, a restore
    that cannot be confirmed, unresolved accounting, or a window that
    settles at a loss stops the run. A stopped run never resumes itself.
  * repeat a losing configuration. A plan whose configuration is recorded
    as having lost is refused until the owner approves it again by name.
  * promote itself. When the last window settles the answer is
    COMPLETE_OWNER_REVIEW_REQUIRED, not another window.

OFF UNLESS `L5_CAMPAIGN_SCHEDULE` IS EXACTLY "ON".
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

from src import l5_campaign_approval as CA
from src import l5_pmus_auth_book as AB

SCHEDULE_ENV = "L5_CAMPAIGN_SCHEDULE"

#: a run is a handful of windows, not a session
MAX_PLAN_WINDOWS = 4
#: window starts at least this far apart, so the previous window has ended,
#: been restored, settled and checked before the next one can be armed
MIN_GAP_S = 1800
#: the last window starts no later than this after the owner's approval
MAX_HORIZON_S = 6 * 3600
#: arming happens inside this band before the window start -- the same band
#: the hand-run procedure used
ARM_LEAD_MIN_S = 420
ARM_LEAD_MAX_S = 1500
WINDOW_S = 900
#: every order is cancelled by this many seconds into the window
CANCEL_BY_S = 600
#: locks must be confirmed back within this long after the window ends
RESTORE_GRACE_S = 600

#: the only switches a plan may turn on, and the values it may give them
SWITCHES: Dict[str, tuple] = {
    "L5_INVENTORY_RECOVERY": ("ON",),
    "L5_TS_COMPLETION_MAX_PAIR": ("0.96", "0.97", "0.98", "0.99", "1.00"),
    "L5_TS_REQUOTE_KEEPS_ROUND": ("ON",),
    "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE": ("ON",),
    "L5_TS_COMPLETION_RESTS_AT_CEILING": ("ON",),
    "L5_PARTIAL_COMPLETION_BASIS": ("PER_CONTRACT",),
    "L5_CASH_POLICY": ("NO_RECYCLING", "RECYCLE_ON_COMPLETE"),
}
#: the figures a plan states for each window, and the variable each bounds
TIGHTENED = {
    "max_live_orders": "L5_MAX_LIVE_ORDERS",
    "concurrent_rounds": "L5_TS_CONCURRENT_ROUNDS",
    "additional_loss_usd": "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD",
}
BUDGETS = {"OPEN": "L5_LIVE_AUTH_MAX_OPEN",
           "COMPLETE": "L5_LIVE_AUTH_MAX_COMPLETE",
           "REPLACE": "L5_LIVE_AUTH_MAX_REPLACE",
           "TOTAL": "L5_LIVE_AUTH_MAX_SUBMISSIONS"}

# ---- steps
DISABLED = "DISABLED"
PLAN_REFUSED = "PLAN_REFUSED"
WAIT = "WAIT"
PREFLIGHT_AND_ARM = "PREFLIGHT_AND_ARM"
MONITOR = "MONITOR"
RESTORE = "RESTORE"
AWAIT_SETTLEMENT = "AWAIT_SETTLEMENT"
STOPPED = "STOPPED"
COMPLETE = "COMPLETE_OWNER_REVIEW_REQUIRED"


def enabled() -> bool:
    return (os.getenv(SCHEDULE_ENV) or "") == "ON"


def _utc(text: Any) -> Optional[dt.datetime]:
    try:
        d = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        return None
    return d.astimezone(dt.timezone.utc)


def _iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dec(v: Any) -> Optional[D]:
    try:
        d = D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def _int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    try:
        i = int(str(v))
    except (TypeError, ValueError):
        return None
    return i if str(i) == str(v).strip() else None


def configuration_of(plan: Mapping[str, Any]) -> Dict[str, Any]:
    """What is being run, apart from when: the part a loss is charged to."""
    return {"sha": plan.get("sha"), "execution": plan.get("execution"),
            "switches": dict(plan.get("switches") or {}),
            "per_window": plan.get("per_window")}


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"),
        default=str).encode()).hexdigest()


def configuration_digest(plan: Mapping[str, Any]) -> str:
    return _digest(configuration_of(plan))


def plan_digest(plan: Mapping[str, Any]) -> str:
    """The figure the owner approves. Any change to the plan changes it."""
    return _digest({k: v for k, v in plan.items()})


def market_for(start: dt.datetime) -> Optional[str]:
    try:
        return AB.slug_for(int(start.timestamp()))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def plan_refusals(plan: Any, *,
                  losing_configurations: Any = ()) -> List[str]:
    """Every reason this plan may not run. Empty means it is well formed.

    Nothing is defaulted: a field the owner did not state is a refusal.
    """
    if not isinstance(plan, Mapping):
        return ["PLAN_NOT_A_MAPPING"]
    bad: List[str] = []
    sha = str(plan.get("sha") or "")
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        bad.append("SHA_NOT_A_FULL_COMMIT")
    if plan.get("execution") != "MAKER_ONLY":
        bad.append("EXECUTION_NOT_MAKER_ONLY")
    if plan.get("stop_on_failure") is not True:
        bad.append("STOP_ON_FAILURE_NOT_STATED")
    approved = _utc(plan.get("approved_utc"))
    if approved is None:
        bad.append("APPROVAL_TIME_NOT_STATED")

    # ---- windows: exact, aligned, ordered, spaced, few, soon
    raw = plan.get("windows")
    starts: List[dt.datetime] = []
    if not isinstance(raw, (list, tuple)) or not raw:
        bad.append("NO_WINDOWS_STATED")
    else:
        if len(raw) > MAX_PLAN_WINDOWS:
            bad.append("TOO_MANY_WINDOWS")
        for w in raw:
            s = _utc(w)
            if s is None or _iso(s) != str(w):
                bad.append("WINDOW_NOT_AN_EXACT_UTC_START")
                continue
            if s.minute % 15 or s.second or s.microsecond:
                bad.append("WINDOW_NOT_ON_A_QUARTER_HOUR")
                continue
            if market_for(s) is None:
                bad.append("WINDOW_HAS_NO_MARKET")
                continue
            starts.append(s)
        if len(starts) == len(raw):
            for a, b in zip(starts, starts[1:]):
                if (b - a).total_seconds() < MIN_GAP_S:
                    bad.append("WINDOWS_TOO_CLOSE_OR_OUT_OF_ORDER")
                    break
            if approved is not None:
                if (starts[0] - approved).total_seconds() < ARM_LEAD_MIN_S:
                    bad.append("FIRST_WINDOW_TOO_SOON_AFTER_APPROVAL")
                if (starts[-1] - approved).total_seconds() > MAX_HORIZON_S:
                    bad.append("LAST_WINDOW_BEYOND_HORIZON")
    markets = plan.get("markets")
    if markets is not None and starts and len(starts) == len(raw or ()):
        if list(markets) != [market_for(s) for s in starts]:
            bad.append("MARKETS_DO_NOT_MATCH_WINDOWS")

    # ---- per-window figures
    pw = plan.get("per_window")
    if not isinstance(pw, Mapping):
        bad.append("PER_WINDOW_LIMITS_NOT_STATED")
        pw = {}
    else:
        exp = _dec(pw.get("max_total_exposure_usd"))
        if exp is None or exp <= 0:
            bad.append("EXPOSURE_NOT_STATED")
        elif exp > CA.MAX_TOTAL_EXPOSURE_USD:
            bad.append("EXPOSURE_ABOVE_CODE_CEILING")
        loss = _dec(pw.get("additional_loss_usd"))
        if loss is None or loss <= 0:
            bad.append("WINDOW_LOSS_LIMIT_NOT_STATED")
        elif exp is not None and loss > exp:
            bad.append("WINDOW_LOSS_LIMIT_ABOVE_EXPOSURE")
        for k in ("max_live_orders", "concurrent_rounds"):
            n = _int(pw.get(k))
            if n is None or n <= 0:
                bad.append("%s_NOT_STATED" % k.upper())
        bud = pw.get("budgets")
        if not isinstance(bud, Mapping) or set(bud) != set(BUDGETS):
            bad.append("SUBMISSION_BUDGETS_NOT_STATED")
        else:
            vals = {k: _int(bud[k]) for k in BUDGETS}
            if any(v is None or v <= 0 for v in vals.values()):
                bad.append("SUBMISSION_BUDGET_NOT_A_POSITIVE_COUNT")
            elif vals["TOTAL"] > (vals["OPEN"] + vals["COMPLETE"]
                                  + vals["REPLACE"]):
                bad.append("TOTAL_BUDGET_ABOVE_ITS_PARTS")

    # ---- aggregate
    agg = plan.get("aggregate")
    agg_loss = (_dec(agg.get("additional_loss_usd"))
                if isinstance(agg, Mapping) else None)
    if agg_loss is None or agg_loss <= 0:
        bad.append("AGGREGATE_LOSS_LIMIT_NOT_STATED")
    else:
        loss = _dec(pw.get("additional_loss_usd"))
        n = len(raw) if isinstance(raw, (list, tuple)) else 0
        if loss is not None and n and agg_loss > loss * n:
            bad.append("AGGREGATE_LOSS_ABOVE_SUM_OF_WINDOWS")

    # ---- switches: an allowlist, and stated even when empty
    sw = plan.get("switches")
    if not isinstance(sw, Mapping):
        bad.append("SWITCHES_NOT_STATED")
    else:
        for name, value in sw.items():
            if name not in SWITCHES:
                bad.append("SWITCH_NOT_ALLOWED:%s" % str(name)[:60])
            elif str(value) not in SWITCHES[name]:
                bad.append("SWITCH_VALUE_NOT_ALLOWED:%s" % str(name)[:60])
        if "L5_CASH_POLICY" not in sw:
            bad.append("CASH_RECYCLING_NOT_STATED")

    # ---- a configuration that lost is not run again by default
    try:
        lost = set(str(x) for x in (losing_configurations or ()))
    except TypeError:
        lost = set()
        bad.append("LOSING_CONFIGURATIONS_UNREADABLE")
    if not bad and configuration_digest(plan) in lost \
            and plan.get("repeats_a_losing_configuration") is not True:
        bad.append("REPEATS_A_LOSING_CONFIGURATION")
    return bad


def arming_variables(plan: Mapping[str, Any],
                     standing: Mapping[str, Any]) -> Dict[str, str]:
    """The variables one window's arming sets BESIDES the approval document
    and the two locks, which `l5_launch` owns. Raises ValueError rather than
    return anything looser than `standing`, the values in force now."""
    out: Dict[str, str] = {}
    pw = plan["per_window"]
    for field, name in TIGHTENED.items():
        want, have = _dec(pw[field]), _dec(standing.get(name))
        if have is None:
            raise ValueError("standing %s is unreadable" % name)
        if want > have:
            raise ValueError("%s %s is looser than the %s in force"
                             % (name, want, have))
        out[name] = str(pw[field])
    for klass, name in BUDGETS.items():
        want, have = _int(pw["budgets"][klass]), _int(standing.get(name))
        if have is None:
            raise ValueError("standing %s is unreadable" % name)
        if want > have:
            raise ValueError("%s %s is looser than the %s in force"
                             % (name, want, have))
        out[name] = str(want)
    exp, have = (_dec(pw["max_total_exposure_usd"]),
                 _dec(standing.get("L5_MAX_TOTAL_EXPOSURE_USD")))
    if have is None or exp != have:
        raise ValueError("the plan's exposure %s is not the exposure in "
                         "force %s; this never writes that limit"
                         % (exp, have))
    for name, value in (plan.get("switches") or {}).items():
        if name not in SWITCHES or str(value) not in SWITCHES[name]:
            raise ValueError("switch %s is not allowed" % name)
        out[name] = str(value)
    return out


def restore_variables(arming: Mapping[str, str],
                      standing: Mapping[str, Any]) -> Dict[str, str]:
    """Exactly what was in force before, for every name arming touched. A
    name that did not exist goes back to empty, which every accessor here
    reads as off."""
    return {name: str(standing.get(name) or "") for name in arming}


def _step(action: str, why: str, **more: Any) -> Dict[str, Any]:
    return dict({"action": action, "why": why}, **more)


def next_step(plan: Any, state: Any, now: dt.datetime, *,
              approved_digest: Any = None,
              losing_configurations: Any = ()) -> Dict[str, Any]:
    """The one step that is due. Pure: same inputs, same answer.

    `state["windows"][<start>]` is the caller's record for a window:
      armed_utc, arm_refused (list of gate names), restored_utc,
      settled (bool), settled_pnl_usd, reconciled (bool),
      accounting_unresolved (count).
    `state["owner_halt"]` true stops everything.
    """
    if not enabled():
        return _step(DISABLED, "%s is not ON" % SCHEDULE_ENV)
    bad = plan_refusals(plan, losing_configurations=losing_configurations)
    if bad:
        return _step(PLAN_REFUSED, bad[0], refusals=bad)
    if not approved_digest or str(approved_digest) != plan_digest(plan):
        return _step(PLAN_REFUSED, "DIGEST_NOT_THE_ONE_THE_OWNER_APPROVED",
                     plan_digest=plan_digest(plan))
    if not isinstance(state, Mapping):
        return _step(STOPPED, "STATE_UNREADABLE")
    if state.get("owner_halt"):
        return _step(STOPPED, "OWNER_HALT")
    if now.tzinfo is None:
        return _step(STOPPED, "CLOCK_HAS_NO_TIMEZONE")
    recs = state.get("windows") or {}
    if not isinstance(recs, Mapping):
        return _step(STOPPED, "STATE_UNREADABLE")
    unknown = sorted(set(recs) - set(plan["windows"]))
    if unknown:
        return _step(STOPPED, "STATE_NAMES_A_WINDOW_NOT_IN_THE_PLAN",
                     windows=unknown)

    pw = plan["per_window"]
    win_loss = _dec(pw["additional_loss_usd"])
    agg_loss = _dec(plan["aggregate"]["additional_loss_usd"])
    total = D(0)
    for i, w in enumerate(plan["windows"]):
        start = _utc(w)
        rec = recs.get(w) or {}
        if not isinstance(rec, Mapping):
            return _step(STOPPED, "STATE_UNREADABLE", window=w)
        here = {"window": w, "market": market_for(start), "index": i}
        lead = (start - now).total_seconds()

        if rec.get("arm_refused"):
            return _step(STOPPED, "ARM_GATE_FAILED",
                         gates=list(rec["arm_refused"]), **here)
        if not rec.get("armed_utc"):
            if lead > ARM_LEAD_MAX_S:
                return _step(WAIT, "NEXT_WINDOW_NOT_YET_IN_THE_ARMING_BAND",
                             arm_from=_iso(start - dt.timedelta(
                                 seconds=ARM_LEAD_MAX_S)),
                             arm_until=_iso(start - dt.timedelta(
                                 seconds=ARM_LEAD_MIN_S)), **here)
            if lead < ARM_LEAD_MIN_S:
                # never replaced by a later window
                return _step(STOPPED, "WINDOW_MISSED", lead_s=round(lead),
                             **here)
            return _step(PREFLIGHT_AND_ARM, "IN_THE_ARMING_BAND",
                         lead_s=round(lead), **here)

        # armed
        if not rec.get("restored_utc"):
            if now < start + dt.timedelta(seconds=CANCEL_BY_S):
                return _step(MONITOR, "WINDOW_IN_PROGRESS", **here)
            if now > start + dt.timedelta(seconds=WINDOW_S
                                          + RESTORE_GRACE_S):
                return _step(STOPPED, "RESTORE_NOT_CONFIRMED",
                             must_restore=True, **here)
            return _step(RESTORE, "ORDERS_ARE_PAST_THEIR_CANCEL_TIME",
                         **here)

        # restored
        if rec.get("settled") is not True:
            nxt = (plan["windows"][i + 1]
                   if i + 1 < len(plan["windows"]) else None)
            if nxt is not None and \
                    (_utc(nxt) - now).total_seconds() < ARM_LEAD_MIN_S:
                return _step(STOPPED, "PREVIOUS_WINDOW_NOT_SETTLED_IN_TIME",
                             missed=nxt, **here)
            return _step(AWAIT_SETTLEMENT, "VENUE_HAS_NOT_SETTLED_IT", **here)

        # settled: the review every later window depends on
        if rec.get("reconciled") is not True:
            return _step(STOPPED, "RECONCILIATION_NOT_CONFIRMED", **here)
        unresolved = _int(rec.get("accounting_unresolved"))
        if unresolved is None or unresolved != 0:
            return _step(STOPPED, "ACCOUNTING_UNRESOLVED", **here)
        pnl = _dec(rec.get("settled_pnl_usd"))
        if pnl is None:
            return _step(STOPPED, "SETTLED_PNL_UNREADABLE", **here)
        total += pnl
        if pnl <= -win_loss:
            return _step(STOPPED, "WINDOW_LOSS_LIMIT_REACHED",
                         settled_pnl_usd=str(pnl), **here)
        if total <= -agg_loss:
            return _step(STOPPED, "AGGREGATE_LOSS_LIMIT_REACHED",
                         settled_total_usd=str(total), **here)
        if pnl < 0:
            return _step(STOPPED, "WINDOW_SETTLED_AT_A_LOSS",
                         settled_pnl_usd=str(pnl),
                         configuration=configuration_digest(plan), **here)
    return _step(COMPLETE, "EVERY_WINDOW_SETTLED_AND_CHECKED",
                 settled_total_usd=str(total),
                 configuration=configuration_digest(plan),
                 windows=len(plan["windows"]))
