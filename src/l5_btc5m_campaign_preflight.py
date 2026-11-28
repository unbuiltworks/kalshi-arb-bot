"""One executable READY/BLOCKED verdict for a BTC5m live campaign.

EVERYTHING HERE IS A READ. The module holds no transport, places nothing,
cancels nothing, clears no gate, writes no variable and changes no limit.
Its only output is a verdict dict. `verdict == "READY"` means "every
precondition we know how to check is satisfied"; it is NOT an
authorization and never becomes one. The owner's approval and the arming
chain in `l5_btc5m_execution` remain the things that actually permit a
submission, exactly as before this file existed.

WHY IT EXISTS. On 2026-11-17 three separately-authorized windows (2225z,
2230z, 2235z) were spent without a single order reaching the venue,
because a shared halt had been latched 28 minutes earlier for a reason
that had nothing to do with the BTC5m lane: a sibling service's 5-share
tennis order rested past its 300s deadline and
`l5_watchdog.Watchdog.sweep` latched `UNMANAGED_RESTING_ORDER`
account-wide at 21:57:04Z. Nothing refused the campaign in advance. Every
individual gate worked; what was missing was one place that reads all of
them BEFORE the window and says BLOCKED, naming the blocker.

The authorization was consumed by a condition that was already true and
already observable. That is the specific failure this module exists to
prevent, and `halt_rows` is the check that would have caught it.

FAIL CLOSED IS THE WHOLE CONTRACT. Three rules, and they are the reason
this is worth having:

  1. A check is a pass only if its `ok` is exactly `True`. `UNKNOWN`,
     `None`, a truthy string and a non-empty dict are all BLOCKED. An
     unevaluated check is never a pass.
  2. A missing observation is BLOCKED, not skipped. Callers cannot widen
     the verdict by omitting an input, which is the one shortcut that
     would make a green preflight meaningless.
  3. `verdict` is READY only when EVERY row passed. There is no
     "mostly ready", no severity ladder and no override argument.

THRESHOLDS ARE IMPORTED, NEVER COPIED. Window counts, daily-loss caps
and the submission-class names come from `l5_campaign_approval` and
`l5_live_authorization`. A copy here would be a second source of truth
that drifts silently, and the whole point of a preflight is that it
agrees with the gates it is predicting.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: a check that could not be evaluated. NEVER read as a pass.
UNKNOWN = "UNKNOWN"

#: the nine groups the owner asked to see before a window is spent. The
#: verdict names them in this order so a BLOCKED report reads top-down
#: from "are we stopped" to "can the executor actually run".
GROUPS = ("halt", "campaign", "deploy", "budgets", "ranker",
          "ev", "exposure", "watchdog", "executor")

#: a managed-lane attestation whose clock sits outside this band is not
#: proof of management -- and an order of that lane's will therefore latch
#: the shared halt on its own age. MIRRORS `l5_managed_lanes.MAX_SKEW_S`
#: and is asserted equal to it by the test suite rather than trusted.
LANE_SKEW_S = 5.0
LANE_MAX_AGE_S = 60.0

#: a watchdog sweep older than this tells us nothing about now.
SWEEP_MAX_AGE_S = 120.0


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _d(v: Any) -> Optional[D]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _ts(v: Any) -> Optional[dt.datetime]:
    try:
        out = dt.datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


def _row(name: str, ok: Any, **detail: Any) -> Dict[str, Any]:
    return dict({"check": name, "ok": ok}, **detail)


def _missing(name: str, what: str) -> Dict[str, Any]:
    """The observation was not supplied. BLOCKED, never skipped."""
    return _row(name, UNKNOWN, why="OBSERVATION_MISSING", observation=what)


def _is_pass(row: Mapping[str, Any]) -> bool:
    """A pass is `ok is True`. Nothing else, deliberately.

    `ok` of UNKNOWN, None, "yes", 1 or {} all land here as False. Python
    would have called most of those truthy, and a preflight that treats
    "UNKNOWN" as a pass is worse than no preflight at all.
    """
    return row.get("ok") is True


# ------------------------------------------------------------------ halt
def halt_rows(halt: Optional[Mapping[str, Any]], *,
              now: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
    """Is the shared halt clear, and if not, could a reset even hold?

    TWO SEPARATE QUESTIONS, and conflating them is how a reset gets
    attempted into a condition that re-latches it. `halt_clear` asks
    whether we are stopped right now. `reset_eligible` asks whether the
    conditions that justify clearing are present -- and the lane-freshness
    term is there because of what actually happened on 2026-11-17:
    clearing the latch while a declared lane's attestation sits outside
    the skew band simply re-arms the same trip, because that lane's next
    resting order crosses `max_order_age_s` unexempted.

    `reset_eligible` is reported even when the halt is clear, as UNKNOWN
    with why NOT_APPLICABLE -- which does NOT block, because a clear halt
    needs no reset. It is the one place a non-True `ok` is not a blocker,
    and it is expressed as a separate non-blocking field rather than a row
    so that rule 1 above stays absolute.
    """
    now = now or _utc()
    if halt is None:
        return [_missing("halt_clear", "halt")]
    rows: List[Dict[str, Any]] = []

    #: ABSENCE IS A HALT. `l5_halt_gate` returns permitted only for a
    #: fresh, parseable, explicit "no halt"; this mirrors that, so the
    #: preflight cannot read "we could not tell" as "we are clear".
    latched = halt.get("halt")
    record = halt.get("record") or {}
    if latched is True:
        rows.append(_row("halt_clear", False, why="HALT_LATCHED",
                         reason=record.get("reason") or halt.get("reason"),
                         who=record.get("who") or halt.get("who"),
                         at=record.get("t") or halt.get("t"),
                         evidence=record.get("evidence")))
    elif latched is False:
        age = None
        t = _ts(halt.get("t") or record.get("t"))
        if t is not None:
            age = (now - t).total_seconds()
        if age is None:
            rows.append(_row("halt_clear", UNKNOWN, why="HALT_AGE_UNREADABLE"))
        elif age > SWEEP_MAX_AGE_S:
            rows.append(_row("halt_clear", False, why="HALT_RECORD_STALE",
                             age_s=age, max_age_s=SWEEP_MAX_AGE_S))
        else:
            rows.append(_row("halt_clear", True, age_s=age))
    else:
        rows.append(_row("halt_clear", UNKNOWN, why="HALT_UNREADABLE",
                         saw=repr(latched)[:80]))

    return rows


def reset_eligibility(halt: Optional[Mapping[str, Any]],
                      watchdog: Optional[Mapping[str, Any]], *,
                      now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Advisory: would clearing the halt actually hold? Never a blocker.

    Reported alongside the verdict so a BLOCKED-on-halt report says what
    the operator would have to fix FIRST, rather than only that they are
    stopped. It authorizes nothing; `scripts/l5_halt_reset` still runs its
    own ceremony and its own venue precondition.
    """
    now = now or _utc()
    if halt is None or halt.get("halt") is not True:
        return {"eligible": UNKNOWN, "why": "NOT_APPLICABLE",
                "note": "no latched halt to reset"}
    blockers: List[Dict[str, Any]] = []
    if watchdog is None:
        blockers.append({"why": "OBSERVATION_MISSING", "observation":
                         "watchdog"})
        return {"eligible": UNKNOWN, "blockers": blockers}

    book = watchdog.get("open_orders")
    if book != 0:
        blockers.append({"why": "VENUE_BOOK_NOT_EMPTY", "open_orders": book})

    #: THE 2026-11-17 TERM. A declared lane outside the skew band earns no
    #: exemption, so its own long-resting order re-latches the shared halt
    #: minutes after any reset. Clearing into that state burns the next
    #: authorization exactly as the last one was burned.
    for note in (watchdog.get("managed_lanes") or []):
        s = str(note)
        if ":STALE:" in s or ":UNREACHABLE:" in s or ":NOT_A_MANAGED_LANE" in s:
            blockers.append({"why": "MANAGED_LANE_NOT_FRESH", "lane": s,
                             "consequence": ("its resting orders are "
                                             "unexempted and will re-latch "
                                             "UNMANAGED_RESTING_ORDER")})
    return {"eligible": not blockers, "blockers": blockers,
            "latch": halt.get("record") or {}}


# -------------------------------------------------------------- campaign
def campaign_rows(campaign: Optional[Mapping[str, Any]], *,
                  now: Optional[dt.datetime] = None
                  ) -> List[Dict[str, Any]]:
    """Owner approval, its window, and the EXACT market ids it covers.

    The market check is an exact set comparison, not a prefix test. A
    campaign approved for 2225z/2230z/2235z must not be able to run 2240z
    because the slug happens to start the same way.
    """
    now = now or _utc()
    if campaign is None:
        return [_missing("campaign_approved", "campaign")]
    rows: List[Dict[str, Any]] = []

    rows.append(_row("campaign_approved", campaign.get("approved") is True,
                     why=None if campaign.get("approved") is True
                     else "NOT_APPROVED"))

    nonce = str(campaign.get("nonce") or "")
    rows.append(_row("campaign_nonce", len(nonce) >= 8,
                     length=len(nonce),
                     why=None if len(nonce) >= 8 else "NONCE_TOO_SHORT"))

    issued, expires = _ts(campaign.get("issued_utc")), _ts(
        campaign.get("expires_utc"))
    if issued is None or expires is None:
        rows.append(_row("campaign_window", UNKNOWN, why="WINDOW_UNREADABLE",
                         issued_utc=campaign.get("issued_utc"),
                         expires_utc=campaign.get("expires_utc")))
    elif now >= expires:
        rows.append(_row("campaign_window", False, why="CAMPAIGN_EXPIRED",
                         expires_utc=expires.isoformat(),
                         now_utc=now.isoformat()))
    elif now < issued:
        rows.append(_row("campaign_window", False, why="CAMPAIGN_NOT_YET",
                         issued_utc=issued.isoformat()))
    else:
        rows.append(_row("campaign_window", True,
                         expires_utc=expires.isoformat()))

    approved = [str(m) for m in (campaign.get("markets") or [])]
    configured = [str(m) for m in (campaign.get("configured_markets") or [])]
    if not approved:
        rows.append(_row("campaign_markets", False, why="NO_MARKETS_APPROVED"))
    elif set(approved) != set(configured):
        rows.append(_row("campaign_markets", False, why="MARKET_SET_MISMATCH",
                         approved=sorted(approved),
                         configured=sorted(configured),
                         approved_not_configured=sorted(
                             set(approved) - set(configured)),
                         configured_not_approved=sorted(
                             set(configured) - set(approved))))
    else:
        rows.append(_row("campaign_markets", True, markets=sorted(approved)))

    #: WINDOW COUNT AGAINST THE REGISTRY'S OWN CAP, imported rather than
    #: copied so the preflight cannot disagree with the approval gate.
    try:
        from src import l5_campaign_approval as CA
        series = CA.series_of(approved[0]) if approved else None
        cap = CA.max_windows_for(series) if series else None
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        rows.append(_row("campaign_window_count", UNKNOWN,
                         why="REGISTRY_UNAVAILABLE",
                         error=type(exc).__name__))
    else:
        if cap is None:
            rows.append(_row("campaign_window_count", UNKNOWN,
                             why="SERIES_UNRECOGNIZED",
                             market=approved[0] if approved else None))
        elif len(approved) > int(cap):
            rows.append(_row("campaign_window_count", False,
                             why="TOO_MANY_WINDOWS",
                             windows=len(approved), cap=int(cap)))
        else:
            rows.append(_row("campaign_window_count", True,
                             windows=len(approved), cap=int(cap)))
    return rows


# ---------------------------------------------------------------- deploy
def deploy_rows(deploy: Optional[Mapping[str, Any]]
                ) -> List[Dict[str, Any]]:
    """Is the code running what was approved, with the config we think?

    A campaign approved against a reviewed SHA and then run on a different
    build is not the campaign that was approved, however small the diff.
    """
    if deploy is None:
        return [_missing("deployed_sha", "deploy")]
    rows: List[Dict[str, Any]] = []

    running = str(deploy.get("running_sha") or "").strip()
    approved = str(deploy.get("approved_sha") or "").strip()
    if not running or not approved:
        rows.append(_row("deployed_sha", UNKNOWN, why="SHA_UNREADABLE",
                         running_sha=running or None,
                         approved_sha=approved or None))
    else:
        #: compare on the shorter of the two, so a short approved sha and a
        #: full running one agree when they are the same commit.
        n = min(len(running), len(approved))
        same = running[:n] == approved[:n] and n >= 7
        rows.append(_row("deployed_sha", same, running_sha=running,
                         approved_sha=approved,
                         why=None if same else "SHA_MISMATCH"))

    status = str(deploy.get("status") or "").strip().upper()
    rows.append(_row("deployment_healthy", status in ("SUCCESS", "DEPLOYING"),
                     status=status or None,
                     why=None if status in ("SUCCESS", "DEPLOYING")
                     else "DEPLOYMENT_NOT_HEALTHY"))

    #: EFFECTIVE config, read back from the running process, not the
    #: variables we believe we set. Those differed on 2026-11-17 14:50z,
    #: when arming variables were written mid-window and the running
    #: container kept its boot-time view for the whole window.
    eff = deploy.get("effective_config")
    if not isinstance(eff, Mapping) or not eff:
        rows.append(_row("effective_config", UNKNOWN,
                         why="CONFIG_NOT_READ_BACK"))
    else:
        want = deploy.get("expected_config") or {}
        diff = {k: {"expected": v, "effective": eff.get(k)}
                for k, v in want.items()
                if str(eff.get(k)) != str(v)}
        rows.append(_row("effective_config", not diff,
                         keys=len(eff), diff=diff or None,
                         why=None if not diff else "CONFIG_MISMATCH"))
    return rows


# --------------------------------------------------------------- budgets
def budget_rows(budgets: Optional[Mapping[str, Any]]
                ) -> List[Dict[str, Any]]:
    """Every submission class must be able to fund the whole campaign.

    THE 19:30z LESSON, GENERALISED. `L5_LIVE_AUTH_MAX_COMPLETE` was 2
    while the controller needed 23 in a single window, so the balancing
    leg -- the risk-REDUCING one -- was the leg that starved. Tickets are
    consumed per attempt and never refunded, so a class that cannot fund
    the campaign does not degrade gracefully: it strands inventory.

    Each class is checked as remaining >= required. A class with no
    configured budget is BLOCKED, not treated as unlimited.
    """
    if budgets is None:
        return [_missing("submission_budgets", "budgets")]
    rows: List[Dict[str, Any]] = []
    try:
        from src import l5_live_authorization as LA
        classes = tuple(LA.SUBMISSION_CLASSES)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return [_row("submission_budgets", UNKNOWN,
                     why="AUTHORIZATION_MODULE_UNAVAILABLE",
                     error=type(exc).__name__)]

    for klass in classes:
        obs = budgets.get(klass) or budgets.get(klass.lower())
        if not isinstance(obs, Mapping):
            rows.append(_row("budget_%s" % klass.lower(), UNKNOWN,
                             why="CLASS_BUDGET_UNREAD", klass=klass))
            continue
        configured = obs.get("configured")
        remaining, required = obs.get("remaining"), obs.get("required")
        if configured is None:
            rows.append(_row("budget_%s" % klass.lower(), False,
                             why="CLASS_BUDGET_NOT_CONFIGURED", klass=klass))
            continue
        try:
            configured_i = int(configured)
            remaining_i = int(remaining)
            required_i = int(required)
        except (TypeError, ValueError):
            rows.append(_row("budget_%s" % klass.lower(), UNKNOWN,
                             why="CLASS_BUDGET_UNREADABLE", klass=klass,
                             configured=configured, remaining=remaining,
                             required=required))
            continue
        ok = configured_i > 0 and remaining_i >= required_i
        rows.append(_row("budget_%s" % klass.lower(), ok, klass=klass,
                         configured=configured_i, remaining=remaining_i,
                         required=required_i,
                         why=None if ok else "CLASS_BUDGET_INSUFFICIENT"))

    #: the aggregate backstop binds independently of the per-class ones,
    #: so a campaign can be funded class-by-class and still be refused.
    total = budgets.get("TOTAL") or budgets.get("total")
    if not isinstance(total, Mapping):
        rows.append(_row("budget_total", UNKNOWN, why="TOTAL_BUDGET_UNREAD"))
    else:
        rem, req = _d(total.get("remaining")), _d(total.get("required"))
        if rem is None or req is None:
            rows.append(_row("budget_total", UNKNOWN,
                             why="TOTAL_BUDGET_UNREADABLE"))
        else:
            rows.append(_row("budget_total", rem >= req,
                             remaining=str(rem), required=str(req),
                             why=None if rem >= req
                             else "TOTAL_BUDGET_INSUFFICIENT"))
    return rows


# ---------------------------------------------------------------- ranker
def ranker_rows(ranker: Optional[Mapping[str, Any]]
                ) -> List[Dict[str, Any]]:
    """Is the inventory ranker actually ON in the running process?

    It defaults to the refusing value, which is correct, and it has been
    silently off for a campaign twice -- once because it had no env reader
    at all. "We set the variable" is not the observation; "the process
    reports it enabled" is.
    """
    if ranker is None:
        return [_missing("ranker_enabled", "ranker")]
    on = ranker.get("enabled")
    rows = [_row("ranker_enabled", on is True,
                 saw=repr(on)[:40],
                 why=None if on is True else "RANKER_NOT_ENABLED")]
    #: a ranker that is on but raising falls back to HOLD, which looks
    #: like a quiet, well-behaved session and places nothing.
    faults = ranker.get("faults")
    if faults is None:
        rows.append(_row("ranker_healthy", UNKNOWN, why="FAULTS_UNREAD"))
    else:
        try:
            n = int(faults)
        except (TypeError, ValueError):
            rows.append(_row("ranker_healthy", UNKNOWN,
                             why="FAULTS_UNREADABLE", saw=repr(faults)[:40]))
        else:
            rows.append(_row("ranker_healthy", n == 0, faults=n,
                             why=None if n == 0 else "RANKER_FAULTING"))
    return rows


# -------------------------------------------------------------------- EV
def ev_rows(ev: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """EV gate in force, and discovery authorized if the model is unmeasured.

    An UNMEASURED fill model is admissible only under an explicit
    discovery token -- never under a plain "YES" -- because an unmeasured
    rate is a guess, and a guess behind an EV gate is an EV gate that
    passes everything.
    """
    if ev is None:
        return [_missing("ev_gate", "ev")]
    rows: List[Dict[str, Any]] = []

    min_ev = _d(ev.get("min_ev_per_share"))
    if min_ev is None:
        rows.append(_row("ev_gate", UNKNOWN, why="MIN_EV_UNREADABLE",
                         saw=repr(ev.get("min_ev_per_share"))[:40]))
    else:
        rows.append(_row("ev_gate", min_ev > 0, min_ev_per_share=str(min_ev),
                         why=None if min_ev > 0 else "EV_GATE_NOT_IN_FORCE"))

    model = str(ev.get("fill_model") or "").strip().upper()
    token = str(ev.get("discovery_token") or "").strip()
    if model in ("", "UNKNOWN"):
        rows.append(_row("ev_fill_model", UNKNOWN, why="FILL_MODEL_UNREAD"))
    elif model == "MEASURED":
        rows.append(_row("ev_fill_model", True, fill_model=model))
    else:
        authorized = token == "UNMEASURED_DISCOVERY_AUTHORIZED"
        rows.append(_row("ev_fill_model", authorized, fill_model=model,
                         why=None if authorized
                         else "UNMEASURED_WITHOUT_DISCOVERY_AUTHORIZATION"))

    taker = ev.get("taker_allowed")
    #: stated explicitly, never defaulted -- the same rule the persistent
    #: envelope applies, for the same reason.
    rows.append(_row("taker_disabled", taker is False, saw=repr(taker)[:40],
                     why=None if taker is False else "TAKER_NOT_STATED_OFF"))
    return rows


# -------------------------------------------------------------- exposure
def exposure_rows(exposure: Optional[Mapping[str, Any]]
                  ) -> List[Dict[str, Any]]:
    """Account-wide exposure and cumulative loss, against their caps.

    ACCOUNT-WIDE, not lane-wide. The venue account is shared with sibling
    services, and on 2026-11-17 a sibling's $1.60 unmatched leg was the
    thing that stopped the BTC5m lane. Exposure read only through our own
    lane's books would have reported zero and been useless.
    """
    if exposure is None:
        return [_missing("account_exposure", "exposure")]
    rows: List[Dict[str, Any]] = []

    for name, key, cap_key in (
            ("account_exposure", "aggregate_usd", "max_aggregate_usd"),
            ("per_market_exposure", "per_market_usd", "max_per_market_usd")):
        have, cap = _d(exposure.get(key)), _d(exposure.get(cap_key))
        if have is None or cap is None:
            rows.append(_row(name, UNKNOWN, why="EXPOSURE_UNREADABLE",
                             value=exposure.get(key), cap=exposure.get(cap_key)))
        else:
            rows.append(_row(name, have <= cap, usd=str(have), cap_usd=str(cap),
                             why=None if have <= cap else "EXPOSURE_OVER_CAP"))

    #: cumulative realized loss is a NEGATIVE number against a POSITIVE
    #: cap. Signs get this check wrong in the direction that lets trading
    #: continue, so it is written as an explicit comparison on the loss
    #: magnitude rather than on the signed P&L.
    pnl, cap = _d(exposure.get("cumulative_realized_usd")), _d(
        exposure.get("max_daily_loss_usd"))
    if pnl is None or cap is None:
        rows.append(_row("cumulative_loss", UNKNOWN, why="PNL_UNREADABLE",
                         cumulative_realized_usd=exposure.get(
                             "cumulative_realized_usd"),
                         max_daily_loss_usd=exposure.get("max_daily_loss_usd")))
    else:
        loss = -pnl if pnl < 0 else D("0")
        rows.append(_row("cumulative_loss", loss <= cap,
                         cumulative_realized_usd=str(pnl),
                         loss_usd=str(loss), cap_usd=str(cap),
                         why=None if loss <= cap
                         else "DAILY_LOSS_LIMIT_REACHED"))
    return rows


# -------------------------------------------------------------- watchdog
def watchdog_rows(watchdog: Optional[Mapping[str, Any]], *,
                  now: Optional[dt.datetime] = None
                  ) -> List[Dict[str, Any]]:
    """Is the watchdog healthy, current, and reconciling durably?

    And -- the check that would have saved the 2225z campaign -- is every
    DECLARED managed lane fresh enough to earn its age exemption? A lane
    outside the skew band is a latent account-wide halt with a fuse equal
    to `max_order_age_s`.
    """
    now = now or _utc()
    if watchdog is None:
        return [_missing("watchdog_fresh", "watchdog")]
    rows: List[Dict[str, Any]] = []

    t = _ts(watchdog.get("as_of_utc"))
    if t is None:
        rows.append(_row("watchdog_fresh", UNKNOWN, why="SWEEP_TIME_UNREADABLE"))
    else:
        age = (now - t).total_seconds()
        fresh = -LANE_SKEW_S <= age <= SWEEP_MAX_AGE_S
        rows.append(_row("watchdog_fresh", fresh, age_s=age,
                         max_age_s=SWEEP_MAX_AGE_S,
                         why=None if fresh else "SWEEP_STALE_OR_SKEWED"))

    rows.append(_row("watchdog_can_cancel",
                     watchdog.get("cancel_enabled") is True,
                     saw=repr(watchdog.get("cancel_enabled"))[:40],
                     why=None if watchdog.get("cancel_enabled") is True
                     else "CANCEL_DISABLED"))

    err = watchdog.get("read_error")
    rows.append(_row("watchdog_reads_venue", err is None, read_error=err,
                     why=None if err is None else "VENUE_READ_ERROR"))

    overdue = watchdog.get("overdue")
    if overdue is None:
        rows.append(_row("no_overdue_orders", UNKNOWN, why="OVERDUE_UNREAD"))
    else:
        rows.append(_row("no_overdue_orders", len(overdue) == 0,
                         overdue=list(overdue)[:5], count=len(overdue),
                         why=None if not overdue else "OVERDUE_ORDERS_RESTING"))

    #: THE LATENT-LATCH CHECK. Every lane the operator declared must be
    #: readable, claiming to manage its own resting orders, and inside the
    #: freshness/skew band -- which is exactly what `l5_managed_lanes`
    #: requires before it will exempt anything.
    lanes = watchdog.get("managed_lanes")
    if lanes is None:
        rows.append(_row("managed_lanes_fresh", UNKNOWN,
                         why="MANAGED_LANES_UNREAD"))
    else:
        bad = [str(n) for n in lanes
               if ":STALE:" in str(n) or ":UNREACHABLE:" in str(n)
               or ":NOT_A_MANAGED_LANE" in str(n)]
        rows.append(_row("managed_lanes_fresh", not bad,
                         lanes=[str(n) for n in lanes], not_fresh=bad or None,
                         why=None if not bad else "MANAGED_LANE_NOT_FRESH",
                         consequence=None if not bad else
                         ("an unexempted lane order will latch "
                          "UNMANAGED_RESTING_ORDER account-wide")))

    #: our own lane's registry must be readable, or the watchdog cannot
    #: attribute our orders and will judge them by age alone.
    regs = watchdog.get("registries")
    ours = str(watchdog.get("our_registry") or "")
    if regs is None or not ours:
        rows.append(_row("our_registry_readable", UNKNOWN,
                         why="REGISTRY_NOTES_UNREAD"))
    else:
        note = next((str(n) for n in regs if str(n).startswith(ours)), None)
        ok = note is not None and note.endswith(":OK")
        rows.append(_row("our_registry_readable", ok, note=note,
                         why=None if ok else "OUR_REGISTRY_NOT_READABLE"))
    return rows


# -------------------------------------------------------------- executor
def executor_rows(executor: Optional[Mapping[str, Any]], *,
                  now: Optional[dt.datetime] = None
                  ) -> List[Dict[str, Any]]:
    """Can the executor actually run the window, and will it stay up?

    `arming_refusal` is reported as an OBSERVATION, and a non-None value
    blocks. This is NOT the preflight arming anything: the chain in
    `l5_btc5m_execution` is what permits a submission, and it is expected
    to refuse while the lane is disarmed. A campaign is READY only once
    the owner has armed it and that chain therefore returns None.
    """
    now = now or _utc()
    if executor is None:
        return [_missing("executor_ready", "executor")]
    rows: List[Dict[str, Any]] = []

    refusal = executor.get("arming_refusal", UNKNOWN)
    if refusal is UNKNOWN or (isinstance(refusal, str)
                              and refusal.strip() == "" and refusal != ""):
        rows.append(_row("executor_ready", UNKNOWN,
                         why="ARMING_REFUSAL_UNREAD"))
    else:
        rows.append(_row("executor_ready", refusal is None,
                         arming_refusal=refusal,
                         why=None if refusal is None else str(refusal)))

    #: A DESIGNED EXIT IS STILL AN EXIT. The 2226z boot exited 0 on the
    #: latched halt, which is correct behaviour and also means no window
    #: was worked. A campaign whose process is not running is not ready,
    #: however well-behaved the reason.
    running = executor.get("running")
    rows.append(_row("executor_running", running is True,
                     saw=repr(running)[:40],
                     why=None if running is True else "EXECUTOR_NOT_RUNNING"))

    exits = executor.get("recent_exits")
    if exits is None:
        rows.append(_row("no_premature_exit", UNKNOWN, why="EXITS_UNREAD"))
    else:
        rows.append(_row("no_premature_exit", len(exits) == 0,
                         recent_exits=list(exits)[:5], count=len(exits),
                         why=None if not exits else "RECENT_PROCESS_EXIT"))

    #: the market the process booted with, against the one approved. A
    #: process holding a stale NOT_AFTER or a previous window's market id
    #: reports healthy and works nothing.
    not_after = _ts(executor.get("not_after_utc"))
    if executor.get("not_after_utc") in (None, ""):
        rows.append(_row("not_after_future", False, why="NOT_AFTER_MISSING"))
    elif not_after is None:
        rows.append(_row("not_after_future", UNKNOWN,
                         why="NOT_AFTER_UNREADABLE",
                         saw=repr(executor.get("not_after_utc"))[:40]))
    else:
        rows.append(_row("not_after_future", not_after > now,
                         not_after_utc=not_after.isoformat(),
                         why=None if not_after > now else "NOT_AFTER_PASSED"))
    return rows


# --------------------------------------------------------------- verdict
def preflight(*, halt: Optional[Mapping[str, Any]] = None,
              campaign: Optional[Mapping[str, Any]] = None,
              deploy: Optional[Mapping[str, Any]] = None,
              budgets: Optional[Mapping[str, Any]] = None,
              ranker: Optional[Mapping[str, Any]] = None,
              ev: Optional[Mapping[str, Any]] = None,
              exposure: Optional[Mapping[str, Any]] = None,
              watchdog: Optional[Mapping[str, Any]] = None,
              executor: Optional[Mapping[str, Any]] = None,
              now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """READY or BLOCKED, with every blocking condition named.

    Every argument defaults to None, and None is BLOCKED. Calling
    `preflight()` with no arguments returns BLOCKED on all nine groups,
    which is the correct answer to "may we trade?" asked with no
    evidence -- and is the behaviour pinned first in the test suite.
    """
    now = now or _utc()
    groups: Dict[str, List[Dict[str, Any]]] = {
        "halt": halt_rows(halt, now=now),
        "campaign": campaign_rows(campaign, now=now),
        "deploy": deploy_rows(deploy),
        "budgets": budget_rows(budgets),
        "ranker": ranker_rows(ranker),
        "ev": ev_rows(ev),
        "exposure": exposure_rows(exposure),
        "watchdog": watchdog_rows(watchdog, now=now),
        "executor": executor_rows(executor, now=now),
    }
    checks: List[Dict[str, Any]] = []
    blocking: List[Dict[str, Any]] = []
    for group in GROUPS:
        for row in groups[group]:
            row = dict(row, group=group)
            checks.append(row)
            if not _is_pass(row):
                blocking.append(row)
    return {"verdict": "READY" if not blocking else "BLOCKED",
            "ok": not blocking,
            "as_of_utc": now.isoformat(),
            "checks": checks,
            "blocking": blocking,
            "blocking_count": len(blocking),
            "groups_blocked": sorted({str(r.get("group")) for r in blocking}),
            "reset_eligibility": reset_eligibility(halt, watchdog, now=now),
            "note": ("READY means every checked precondition passed. It is "
                     "not an authorization to trade.")}


def summary(verdict: Mapping[str, Any]) -> str:
    """One operator-readable block. No colour, no emoji, no persuasion."""
    out = ["L5_BTC5M_CAMPAIGN_PREFLIGHT %s" % verdict.get("verdict"),
           "  as_of_utc: %s" % verdict.get("as_of_utc")]
    if verdict.get("ok"):
        out.append("  all %d checks passed" % len(verdict.get("checks") or []))
    else:
        out.append("  %d blocking condition(s) in: %s"
                   % (verdict.get("blocking_count"),
                      ", ".join(verdict.get("groups_blocked") or [])))
        for row in (verdict.get("blocking") or []):
            out.append("  BLOCKED %s/%s ok=%r why=%s"
                       % (row.get("group"), row.get("check"),
                          row.get("ok"), row.get("why")))
    elig = verdict.get("reset_eligibility") or {}
    if elig.get("eligible") is not UNKNOWN:
        out.append("  reset_eligible: %r" % elig.get("eligible"))
        for b in (elig.get("blockers") or []):
            out.append("    reset blocked: %s" % b.get("why"))
    return "\n".join(out)
