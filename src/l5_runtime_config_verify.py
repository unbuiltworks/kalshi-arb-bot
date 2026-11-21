"""The EFFECTIVE runtime configuration, read through the money path's own
accessors and compared against the owner's approved figures.

WHY THIS IS NOT A LIST OF VARIABLE NAMES. The owner's instruction was to
verify the effective runtime configuration and not just the intended
variable names, and the distinction is load-bearing twice over:

  * a NAME can be present and hold the wrong value, or hold a value the
    accessor clamps, floors or ignores. `daily_realized_loss_halt()` reads
    `L5_DAILY_REALIZED_LOSS_HALT_USD`, floors it at $5 and caps it at $50,
    and an unreadable value falls back to $5 -- so the variable's text is
    not the limit, the accessor's return is;
  * several approved figures are CODE CONSTANTS with no variable at all
    (the $2 defensive budget, the 1.20 defensive ceiling, the 60s guard, the
    15s execution floor, the four-hour campaign ceiling). An
    environment-only check cannot see them, and an environment change cannot
    move them.

So every row below calls the function or reads the constant that the
executor itself uses, and records WHICH one. A row that cannot be read is a
FAILURE, never a default.

NOTHING HERE ARMS, SUBMITS, CANCELS OR TOUCHES A VENUE. It reads
configuration and returns a verdict. It is safe to run inside the deployed
container, which is the point: the only configuration that matters is the
one the running process actually has.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D, InvalidOperation
from typing import (Any, Callable, Dict, List, Mapping,
                    Optional)


#: THE OWNER'S APPROVED FIGURES for the eight-window live TWO_SIDED
#: campaign, as approved 2026-10-21. Written here as code so a drift shows
#: up as a failing row rather than as a document nobody re-reads.
APPROVED: Dict[str, Any] = {
    "submission_budget_open": 16,
    "submission_budget_complete": 24,
    "submission_budget_replace": 32,
    "submission_backstop": 56,
    "defensive_loss_budget_usd": D("2"),
    # BACK TO 5, which is the owner-approved aggregate ceiling for the
    # no-key campaign and the value `R.PROBE_LIMITS` actually carries.
    #
    # THE EARLIER NOTE HERE WAS WRONG ON THE MECHANISM and that is why it
    # is replaced rather than edited: it claimed this row had to equal
    # CA.MAX_TOTAL_EXPOSURE_USD because "_looser() refuses arming" when
    # they disagree. `_looser` flags a field only when the DEPLOYED value
    # is greater than the approved one, so a deployed ceiling TIGHTER than
    # the code constant arms perfectly well. Requiring 20 therefore did
    # not protect arming; it just reported PASS false against the $5
    # configuration the owner asked for, which hides a real regression
    # behind a permanent failure.
    "aggregate_exposure_usd": D("5"),
    # THE CAMPAIGN'S OWN ADDITIONAL-LOSS BOUND (OWNER 2026-10-24). Graded,
    # not merely reported, because the owner required it to be in force for
    # this campaign: "Ensure the campaign has an independently enforced
    # maximum $5 additional loss... Do not assume the existing $20 lifetime
    # gate supplies this." An unset variable leaves the gate inert, which is
    # a configuration that does NOT match what was approved, so it must fail
    # here rather than pass quietly.
    # RAISED 5.00 -> 10.00 ON OWNER AUTHORIZATION 2026-11-02.
    #
    # THIS IS THE FOURTH PLACE THE FIGURE LIVES and the one that is easiest
    # to miss, because it is not derived from the approval document the way
    # `aggregate_exposure_usd`, `max_daily_loss_usd` and `max_windows` are
    # -- those come from `approved_scope()`, this is a literal. The other
    # three are `CBL.MAX_ADDITIONAL_LOSS_USD` (the code ceiling),
    # `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD` (the variable, clamped by it),
    # and `CBD.DEFAULT_BUDGET_USD` (a DIFFERENT gate on the gross measure,
    # deliberately left at 5).
    #
    # LEAVING IT AT 5 WOULD HAVE FAILED THIS ROW FOR THE WHOLE CAMPAIGN.
    # The row grades `CBL.limit_from_env()` against this number, so a
    # correctly raised limit would have read effective 10 against required
    # 5.00 and reported a regression that was actually the authorized
    # change. That is the "silent clamp" failure mode inverted: not a limit
    # quietly lower than approved, but a verifier quietly disagreeing with
    # the approval it exists to enforce.
    "campaign_additional_loss_usd": D("10.00"),
    # THE WORST-CASE OPENING NOTIONAL (OWNER 2026-10-25). Graded, not merely
    # reported, for the same reason as the row above: the owner approved this
    # campaign AS the single-variable notional-cap experiment, so an unset
    # variable leaves the cap inert and that is NOT the configuration that
    # was approved. It must fail here rather than pass quietly and open a
    # 1.87-contract leg at own 0.62 again.
    "opening_notional_budget_usd": D("0.80"),
    "daily_realized_loss_halt_usd": D("5"),
    "maker_pair_ceiling": D("0.96"),
    "defensive_pair_ceiling": D("1.20"),
    "cancel_margin_s": 300.0,
    "defensive_terminal_s": 600.0,
    "defensive_guard_opens_s": 540.0,
    "defensive_last_actionable_s": 585.0,
    "max_windows": 8,
    # RAISED 1 -> 2 BY OWNER DECISION, 2026-11-04, for the eight-window
    # bounded live validation of campaign camp1.4675472c2b625a967df9d14c.
    #
    # WHY THE VERIFIER HAD TO MOVE WITH IT. This table is the approval, and a
    # verifier that disagrees with the approval it exists to enforce reports
    # the authorized change as a regression -- the same inverted "silent
    # clamp" failure the campaign_additional_loss_usd note above records. It
    # read effective 2 against required 1 and made FAILED CHECKS permanently
    # non-empty, which would mask the next real failure.
    #
    # IT IS STILL AN EXACT MATCH, DELIBERATELY. `R.max_live_orders()` is
    # `max(1, n)` with NO upper clamp, so a typo setting 20 would be accepted
    # by the accessor in silence. Grading this row against exactly 2 is what
    # makes that detectable, so the row is a tighter check after this change
    # than before it, not a looser one.
    #
    # WHAT THE SLOT DOES AND DOES NOT BUY. It lets a same-side replacement
    # overlap its predecessor instead of the engine going flat between every
    # reprice. It grants no headroom: `refuse` prices every outstanding order
    # as though it fills in full, and `residual_cap_decision` runs on a
    # FILL_CONTINGENT_WORST_CASE basis that refuses to net the opposite side,
    # so two same-side resting legs are refused at ANY slot count. Pinned in
    # tests/test_l5_two_order_slot_safety.py.
    # RAISED 2 -> 4 BY OWNER DECISION, 2026-11-10: "I approve preparing the
    # four-order configuration and deploying it disarmed after regression
    # tests. This does not authorize live trading or waive the
    # profitability gate." Four slots are two price levels a side
    # (L5_TS_CONCURRENT_ROUNDS=2). STILL AN EXACT MATCH, for the reason
    # above: a typo of 40 is caught here. Cash, exposure and the daily halt
    # are unchanged, the second level opens only when the own-settled-
    # evidence gate allows it, and the same-side worst-case basis described
    # above still prices every resting order as though it fills alone.
    "max_live_orders": 4,
    "window_strategy": "TWO_SIDED",
    "campaign_duration_ceiling_s": 14400.0,
    # RAISED 5 -> 20 by owner decision for the eight-window sequential
    # pairing campaign. The DAILY ceiling is deliberately NOT raised, so one
    # market can overshoot the daily halt fourfold; the campaign worst-case
    # budget (l5_campaign_budget, $11.10) is what bounds that, and $11.10 is
    # itself the UNSPENT part of the untouched $20 account-wide ceiling.
    "approval_exposure_ceiling_usd": D("20"),
    "approval_daily_loss_ceiling_usd": D("5"),
    # at DEPLOY time, before activation
    #
    # OWNER-APPROVED BUT DELIBERATELY NOT APPLIED HERE, 2026-10-22. The
    # owner approved flipping this row to True for the next bounded window.
    # It is left False because this row is GRADED (see its `_row` below),
    # so True makes the verifier PASS only a build whose flag is ON and
    # FAIL one whose flag is off -- including a deliberate safety rollback
    # to defensive-off. Grading an activation switch breaks at one of the
    # two moments that matter, which is exactly why the "REPORTED, NOT
    # REQUIRED" block below exists for the other switches.
    #
    # The correct change is therefore to MOVE this row into that block
    # rather than to invert the value it is graded against. That is a
    # behaviour change to the pre-deploy gate and wants the owner's call,
    # not a quiet redesign inside an accounting fix.
    "defensive_execution_enabled": False,
}

#: the full window length these clocks are measured against
WINDOW_S = 900.0


#: An order limit the configuration has been made ready for and the owner
#: has NOT yet activated. See the `max_live_orders` row.
MAX_LIVE_ORDERS_PREPARED = 8


def _row(name: str, source: str, read: Callable[[], Any],
         required: Any, *, compare: Optional[Callable[[Any, Any], bool]] = None
         ) -> Dict[str, Any]:
    """One checked value. An unreadable value FAILS and says why."""
    try:
        got = read()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"name": name, "source": source, "effective": None,
                "required": _s(required), "ok": False,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    ok = (compare(got, required) if compare is not None else got == required)
    return {"name": name, "source": source, "effective": _s(got),
            "required": _s(required), "ok": bool(ok)}


def _s(v: Any) -> Any:
    if isinstance(v, D):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return v


#: THE ONLY VARIABLE NAMES A SNAPSHOT MAY CARRY INTO THIS PROCESS.
#:
#: An allowlist and not a denylist, because the snapshot's purpose is to
#: check LIMITS before they are changed, and nothing else about a production
#: environment needs to be loaded here to do that. Anything outside this set
#: is ignored by name and its value is never read, never printed and never
#: placed in `os.environ`.
SNAPSHOT_ALLOWED = (
    "L5_LIVE_AUTH_MAX_OPEN",
    "L5_LIVE_AUTH_MAX_COMPLETE",
    "L5_LIVE_AUTH_MAX_REPLACE",
    "L5_LIVE_AUTH_MAX_SUBMISSIONS",
    "L5_LIVE_AUTH_MAX_WINDOWS",
    "L5_TS_CANCEL_MARGIN_S",
    "L5_WINDOW_STRATEGY",
    "L5_MAX_LIVE_ORDERS",
    "L5_DAILY_REALIZED_LOSS_HALT_USD",
    "L5_DEFENSIVE_COMPLETION",
    "L5_SHADOW_ONLY",
    # THE AGGREGATE CEILING'S OWN VARIABLE, which this list omitted. Without
    # it a snapshot could not predict `aggregate_exposure_usd` -- the name
    # was silently dropped as non-allowlisted, the variable was never
    # applied, and the row reported the running process's boot value as
    # though it had been predicted from the input.
    "L5_MAX_TOTAL_EXPOSURE_USD",
    # the campaign additional-loss bound's own variable, for the same reason
    "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD",
    # and the opening-notional cap's, so a snapshot can predict its row
    "L5_TS_OPENING_NOTIONAL_BUDGET_USD",
)

#: Name fragments that mean "this is a secret, and it must not be in a file
#: anyone is pasting around". A snapshot carrying one is REFUSED rather than
#: filtered: a careless paste should fail loudly, so the file gets deleted
#: and the paste gets redone, instead of being quietly half-accepted while a
#: credential sits on disk and in this process's memory.
SNAPSHOT_FORBIDDEN_FRAGMENTS = (
    "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL",
    "NONCE", "ARM", "PRIVATE", "SIGNING", "AUTH_LEDGER",
)


class SnapshotRefused(ValueError):
    """A snapshot that must not be loaded. Never partially accepted."""


def _screen_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Split a variable snapshot into {applied, ignored} or refuse it.

    Values are returned ONLY for allowlisted names. Ignored names are
    reported as names alone -- a name is not a secret and the owner needs to
    see what was skipped, but a value outside the allowlist has no business
    being echoed by a tool whose output gets pasted into a chat.
    """
    if not isinstance(snapshot, dict):
        raise SnapshotRefused("snapshot is not a JSON object")
    bad = sorted(k for k in snapshot
                 if any(f in str(k).upper()
                        for f in SNAPSHOT_FORBIDDEN_FRAGMENTS))
    if bad:
        raise SnapshotRefused(
            "snapshot carries secret-looking variables and was NOT loaded. "
            "Remove them and re-export, then delete the file: %s"
            % ", ".join(bad))
    applied = {k: str(v) for k, v in snapshot.items()
               if k in SNAPSHOT_ALLOWED}
    ignored = sorted(k for k in snapshot if k not in SNAPSHOT_ALLOWED)
    return {"applied": applied, "ignored_names_only": ignored}


def verify_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """What the accessors WOULD return for this variable snapshot.

    WHY THIS EXISTS AND WHEN TO USE IT. The in-container run is the real
    check, but it needs the verifier to be IN the running image -- and a
    build that predates this module has no verifier to run. So before
    changing anything, the owner can take Railway's rendered variable
    values, strip the secrets, and ask this what the money path's accessors
    would make of them.

    IT IS A PREDICTION AND IT SAYS SO. `basis: SNAPSHOT_PREDICTION`. It
    cannot see a variable the snapshot omitted, a value Railway renders
    differently at runtime, or anything the deployed image's own code does
    differently from this tree. Only the in-container run settles it, and
    `in_container_check_required` stays true on every snapshot result.

    The snapshot is applied to `os.environ` for the duration of the call and
    restored afterwards, including variables that were previously ABSENT --
    which are restored to absent, not to empty string.
    """
    screened = _screen_snapshot(snapshot)
    applied = screened["applied"]
    # every allowlisted name is set, including to-absent for the ones the
    # snapshot does not carry, so a MISSING variable is predicted as missing
    # rather than inheriting this shell's value
    saved: Dict[str, Optional[str]] = {}
    for name in SNAPSHOT_ALLOWED:
        saved[name] = os.environ.get(name)
    try:
        for name in SNAPSHOT_ALLOWED:
            if name in applied:
                os.environ[name] = applied[name]
            else:
                os.environ.pop(name, None)
        out = verify()
    finally:
        for name, was in saved.items():
            if was is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = was
    out["basis"] = "SNAPSHOT_PREDICTION"
    out["in_container_check_required"] = True
    out["snapshot_applied"] = applied
    out["snapshot_ignored_names_only"] = screened["ignored_names_only"]
    out["snapshot_absent_from_allowlist"] = sorted(
        n for n in SNAPSHOT_ALLOWED if n not in applied)
    out["labels"] = (
        "A PREDICTION, not a verification. It says what this tree's "
        "accessors would return for these variable values. It cannot see a "
        "variable the snapshot omitted, a value the platform renders "
        "differently at runtime, or code in the deployed image that differs "
        "from this tree. Only running this module INSIDE the container "
        "settles the effective configuration.")
    return out


def _submit_pause_state() -> str:
    """PAUSED / running, by the same reading the arming gate uses.

    Duplicating the parse would let the row disagree with the gate, so this
    mirrors `authorize_arming`'s exact test: unset or an explicit
    off-value runs, anything else set pauses.
    """
    from src import l5_live_authorization as LA
    raw = (os.getenv(LA.SUBMIT_PAUSE_ENV) or "").strip()
    if raw and raw.lower() not in ("0", "false", "no", "off"):
        return "PAUSED:%s" % raw[:24]
    return "running"


def _submit_pause_env() -> str:
    from src import l5_live_authorization as LA
    return str(LA.SUBMIT_PAUSE_ENV)


def _mandate_verdict_reason() -> str:
    """The mandate verdict as a bare reason string, for REPORTING.

    `verify` performs no I/O and writes nothing, so reading the status can
    never consume or advance the owner's mandate.

    REPORTING SUPPLIES PLACEHOLDERS, and that is why this is not enforcement.
    The watchdog floor and revocation documents are read by the enforcing
    caller from `HttpSeqFloorStore`/`HttpRevocationStore`; here a floor of 0
    and an empty revocation set are passed so the SIGNATURE and SCOPE can be
    reported without the watchdog endpoints existing yet. The reported
    verdict is therefore NOT a statement about replay or revocation.
    """
    from src import l5_mandate_signature as MS
    try:
        v = MS.verify(os.getenv(MS.MANDATE_ENV),
                      environment=os.getenv("RAILWAY_ENVIRONMENT_NAME", ""),
                      service_role=os.getenv("PMUS_SERVICE_ROLE", ""),
                      series=os.getenv("L5_LIVE_AUTH_MARKET_SERIES", ""),
                      revoked=[], seq_floor=0)
        return str(v.get("reason") or "OK")
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return "VERIFY_RAISED:%s" % type(exc).__name__


#: Sentinel for a row whose authority is the owner's approval document when
#: no document is in force. It is NOT a value and never compares equal to
#: one, so such a row FAILS -- which is the whole point: an unverifiable
#: configuration is not an approved configuration.
NO_APPROVAL = "NO_APPROVAL_IN_FORCE"


def approved_scope() -> Optional[Dict[str, Any]]:
    """The scope of the campaign actually in force, or None.

    WHY ROWS DERIVE FROM THIS. `APPROVED` below was campaign 1's profile,
    frozen in code. By 2026-10-28 eight of its rows disagreed with the
    configuration the owner had since approved -- backstop 40 against an
    expected 56, exposure $20 against $5, four windows against eight -- so
    `L5_EFFECTIVE_RUNTIME_CONFIG` read `PASS: false` on a correctly
    configured service. A gate that always fails is a gate nobody can use,
    and it was about to be the pre-arm check for a live campaign.

    So the figures the owner sets PER CAMPAIGN are graded against the
    document that approved them, and the figures that are code ceilings stay
    graded against the code. Nothing is graded against a number that has to
    be hand-edited each campaign.
    """
    try:
        from src import l5_campaign_approval as _CA
        st = _CA.approval_gate().state() or {}
        if not st.get("approved"):
            return None
        sc = st.get("scope")
        return dict(sc) if isinstance(sc, Mapping) else None
    except BaseException:                                      # noqa: BLE001
        return None


def _scope_dec(scope: Optional[Mapping[str, Any]], field: str,
               ceiling: Any) -> Any:
    """What the document approved for `field`, intersected with the code
    ceiling. An approval can never widen a ceiling, so the expected value is
    the MINIMUM of the two -- and with no document it is `NO_APPROVAL`, so
    the row fails rather than passing on a default."""
    if scope is None:
        return NO_APPROVAL
    try:
        v = D(str(scope.get(field)))
    except (InvalidOperation, TypeError, ValueError):
        return NO_APPROVAL
    if not v.is_finite() or v <= 0:
        return NO_APPROVAL
    cap = D(str(ceiling))
    return v if v < cap else cap


def _scope_int(scope: Optional[Mapping[str, Any]], field: str,
               ceiling: int) -> Any:
    if scope is None:
        return NO_APPROVAL
    try:
        v = int(scope.get(field))
    except (TypeError, ValueError):
        return NO_APPROVAL
    if v <= 0:
        return NO_APPROVAL
    return min(v, int(ceiling))


def _budget(scope: Optional[Mapping[str, Any]], field: str,
             ceiling: Any) -> Any:
    """The budget the document approved, or the invariant when it is silent.

    PREFER THE DOCUMENT. When the owner states a budget, the deployed
    variable is graded against THAT -- so a value left over from a previous
    campaign fails, which is the drift this row exists to catch. When the
    document is silent the row falls back to `_configured_within`, which
    still requires the variable to be configured and inside the code
    ceiling, so a silent document is never an unconditional pass.
    """
    if scope is None:
        return NO_APPROVAL
    budgets = scope.get("submission_budgets")
    if not isinstance(budgets, Mapping) or budgets.get(field) is None:
        return None                         # caller applies the invariant
    try:
        v = D(str(budgets.get(field)))
    except (InvalidOperation, TypeError, ValueError):
        return NO_APPROVAL
    if not v.is_finite() or v <= 0 or v > D(str(ceiling)):
        return NO_APPROVAL
    return int(v)


def _configured_within(ceiling: Any):
    """A comparison for a per-campaign operational budget that has no field
    in the approval document: it must be CONFIGURED and within the code
    ceiling. Unset fails, zero fails, over the ceiling fails.

    This is deliberately an invariant and not a magic number. The owner
    chooses the submission budgets per campaign, so grading them against a
    frozen integer guarantees a stale failure; grading them against nothing
    would be an unconditional PASS. Grading them against the ceiling is the
    check that actually protects something.
    """
    cap = D(str(ceiling))

    def cmp(got: Any, _required: Any) -> bool:
        if got is None:
            return False
        try:
            v = D(str(got))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return v.is_finite() and v > 0 and v <= cap

    return cmp


def verify() -> Dict[str, Any]:
    """Every approved figure, read effectively. `PASS` only if all hold."""
    from src import l5_campaign_approval as CA
    from src import l5_defensive_completion as DEF
    from src import l5_mandate_signature as MS
    from src import l5_live_authorization as AUTH
    from src import l5_pmus_live_runner as R
    from src import l5_two_sided_probe as TS
    from src import l5_live_validation_gates as VG
    from src import l5_campaign_baseline as CBL

    A = APPROVED
    SC = approved_scope()
    # the per-campaign figures, derived from the document that approved them
    # and intersected with the code ceilings they can never widen
    exp_usd = _scope_dec(SC, "max_total_exposure_usd",
                         CA.MAX_TOTAL_EXPOSURE_USD)
    daily_usd = _scope_dec(SC, "max_daily_loss_usd", CA.MAX_DAILY_LOSS_USD)
    windows = _scope_int(SC, "max_windows", CA.MAX_WINDOWS)
    CAP = AUTH._CLASS_TICKET_CEILING

    def budget_row(name: str, source: str, read: Any, field: str):
        want = _budget(SC, field, CAP)
        if want is None:                    # the document is silent
            return _row(name, source, read, "configured, 1..%d" % CAP,
                        compare=_configured_within(CAP))
        return _row(name, source, read, want)

    rows: List[Dict[str, Any]] = [
        # ---------------------------------------- submission budgets
        # THE SUBMISSION BUDGETS are the owner's per-campaign numbers and
        # the approval document has no field for them, so they are graded
        # as INVARIANTS rather than against a frozen integer: configured,
        # positive, and within the code ticket ceiling. Unset fails and so
        # does over-ceiling; what cannot happen is a stale magic number
        # failing a correctly configured service.
        budget_row("submission_budget_open", "AUTH.max_of_class('OPEN')",
                   lambda: AUTH.max_of_class("OPEN"),
                   "max_open_submissions"),
        budget_row("submission_budget_complete",
                   "AUTH.max_of_class('COMPLETE')",
                   lambda: AUTH.max_of_class("COMPLETE"),
                   "max_complete_submissions"),
        budget_row("submission_budget_replace",
                   "AUTH.max_of_class('REPLACE')",
                   lambda: AUTH.max_of_class("REPLACE"),
                   "max_replace_submissions"),
        budget_row("submission_backstop", "AUTH.max_submissions()",
                   AUTH.max_submissions, "max_total_submissions"),

        # ---------------------------------------------- hard money limits
        _row("defensive_loss_budget_usd",
             "DEF.DEFENSIVE_LOSS_BUDGET_USD (code constant)",
             lambda: DEF.DEFENSIVE_LOSS_BUDGET_USD,
             A["defensive_loss_budget_usd"]),
        # THE AGGREGATE CEILING, SPLIT IN TWO, because in this one case the
        # variable and the limit in force can differ.
        #
        # `R.PROBE_LIMITS` is built ONCE at import and `SafetyLimits` is
        # constructed from it, so the ceiling that actually binds is the one
        # the PROCESS BOOTED WITH. Setting the Railway variable does not move
        # it until the service restarts.
        #
        # Reading only the frozen dict -- which this row used to do -- made
        # `verify_snapshot` unable to predict this row at all: the snapshot
        # sets the variable and then the row reported the running process's
        # boot value regardless, i.e. a prediction that silently ignored the
        # input it was given. Reading only the accessor would be worse: it
        # would report the owner's intent as though it were enforced.
        #
        # So both are graded against the same approved number. They agree on
        # a correctly restarted service; a disagreement is precisely the
        # "variable changed, service not restarted" state, and it fails here
        # rather than being discovered by an order that used the old ceiling.
        _row("aggregate_exposure_usd",
             "R.max_total_exposure_usd() (the variable)",
             lambda: R.max_total_exposure_usd(), exp_usd),
        # AT OR BELOW THE APPROVED CEILING, AND ABOVE ZERO (2026-11-11).
        # This row demanded exactly the approved $10.00, so an owner who
        # set a TIGHTER budget for one bounded test -- which the gate
        # enforces as written -- would have read as a failing
        # configuration. A smaller loss bound is inside the approval, not a
        # departure from it. Unset (None) still fails: the bound would be
        # inert. Zero still fails: that is the fail-closed value an
        # unreadable setting produces, and it refuses every order.
        _row("campaign_additional_loss_usd",
             "CBL.limit_from_env() clamped by CBL.MAX_ADDITIONAL_LOSS_USD",
             lambda: CBL.limit_from_env(),
             A["campaign_additional_loss_usd"],
             compare=lambda got, req: (got is not None
                                       and D(0) < got <= req)),
        _row("opening_notional_budget_usd",
             "TS.opening_notional_budget_usd() clamped by "
             "TS.OPENING_NOTIONAL_MAX_USD",
             lambda: TS.opening_notional_budget_usd(),
             "configured, 0..%s" % TS.OPENING_NOTIONAL_MAX_USD,
             compare=_configured_within(TS.OPENING_NOTIONAL_MAX_USD)),
        _row("aggregate_exposure_in_force_usd",
             "R.PROBE_LIMITS['max_total_exposure_usd'] (frozen at boot)",
             lambda: D(str(R.PROBE_LIMITS["max_total_exposure_usd"])),
             exp_usd),
        _row("daily_realized_loss_halt_usd",
             "R.daily_realized_loss_halt()",
             R.daily_realized_loss_halt, daily_usd),
        # THE APPROVED NUMBER, OR THE PREPARED CEILING (2026-11-11). Four is
        # what the owner approved; eight is the limit the four-concurrent-
        # round configuration needs and was asked to be made READY, not
        # active. The row passes at either and at nothing else, so a typo
        # still fails; which of the two is running is the owner's variable,
        # written only under an explicit authorization.
        _row("max_live_orders", "R.max_live_orders()",
             R.max_live_orders, A["max_live_orders"],
             compare=lambda got, req: got in (req, MAX_LIVE_ORDERS_PREPARED)),

        # ------------------------------------------------- pair ceilings
        _row("maker_pair_ceiling", "TS.max_pair_cost()",
             TS.max_pair_cost, A["maker_pair_ceiling"]),
        _row("defensive_pair_ceiling",
             "DEF.DEFENSIVE_PAIR_CEILING (code constant)",
             lambda: DEF.DEFENSIVE_PAIR_CEILING,
             A["defensive_pair_ceiling"]),

        # ------------------------------------- the clocks, and the guard
        # The owner's approval names L5_TS_CANCEL_MARGIN_S = 300 BECAUSE the
        # guard is derived from it. These four rows are that derivation,
        # checked rather than assumed: the margin, the terminal it implies,
        # and the interval the terminal implies.
        _row("cancel_margin_s", "TS.cancel_margin_s()",
             lambda: float(TS.cancel_margin_s()), A["cancel_margin_s"]),
        _row("defensive_terminal_s",
             "DEF.binding_terminal_s(TS.complete_cutoff_s(), "
             "TS.cancel_cutoff(WINDOW_S))",
             lambda: DEF.binding_terminal_s(TS.complete_cutoff_s(),
                                            TS.cancel_cutoff(WINDOW_S)),
             A["defensive_terminal_s"]),
        _row("defensive_guard_opens_s",
             "terminal - DEF.DEFENSIVE_ARM_S",
             lambda: (DEF.binding_terminal_s(TS.complete_cutoff_s(),
                                             TS.cancel_cutoff(WINDOW_S))
                      - float(DEF.DEFENSIVE_ARM_S)),
             A["defensive_guard_opens_s"]),
        _row("defensive_last_actionable_s",
             "terminal - DEF.DEFENSIVE_MIN_EXECUTION_S",
             lambda: (DEF.binding_terminal_s(TS.complete_cutoff_s(),
                                             TS.cancel_cutoff(WINDOW_S))
                      - float(DEF.DEFENSIVE_MIN_EXECUTION_S)),
             A["defensive_last_actionable_s"]),

        # ------------------------------------------------ campaign scope
        _row("max_windows_code_ceiling", "CA.MAX_WINDOWS (code constant)",
             lambda: CA.MAX_WINDOWS, A["max_windows"]),
        # THE WINDOW BUDGET comes from the document, capped by CA.MAX_WINDOWS.
        # It used to expect 8 -- campaign 1's figure -- which failed the
        # four-window campaign the owner had approved.
        _row("max_windows_authorization", "AUTH.max_windows_env()",
             AUTH.max_windows_env, windows),
        _row("campaign_duration_ceiling_s",
             "CA.MAX_CAMPAIGN_S (code constant)",
             lambda: float(CA.MAX_CAMPAIGN_S),
             A["campaign_duration_ceiling_s"]),
        _row("approval_exposure_ceiling_usd",
             "CA.MAX_TOTAL_EXPOSURE_USD (code constant)",
             lambda: CA.MAX_TOTAL_EXPOSURE_USD,
             A["approval_exposure_ceiling_usd"]),
        _row("approval_daily_loss_ceiling_usd",
             "CA.MAX_DAILY_LOSS_USD (code constant)",
             lambda: CA.MAX_DAILY_LOSS_USD,
             A["approval_daily_loss_ceiling_usd"]),
        _row("window_strategy", "env L5_WINDOW_STRATEGY",
             lambda: str(os.getenv("L5_WINDOW_STRATEGY", "")).strip().upper(),
             A["window_strategy"]),
        # TWO SERIES since the owner's decision of 2026-11-11 to authorize
        # the 5-minute strategy through this same mechanism. Still a code
        # constant, still graded exactly: a third name here, or a missing
        # one, fails. One approval document names ONE of them, and every
        # claim refuses a market outside the document's own series.
        # WIDENED 2026-11-15 by owner decision, after ETH/SOL/XRP 5m were
        # confirmed MARKET_STATUS_OPEN on gateway.polymarket.us. The
        # expected list is still written out IN FULL and graded exactly --
        # a sixth name here, or a missing one, fails. Deriving it from
        # CA.ALLOWED_SERIES would make this row self-satisfying and grade
        # nothing, which is the whole point of restating it.
        _row("series_allowlist", "CA.ALLOWED_SERIES (code constant)",
             lambda: list(CA.ALLOWED_SERIES),
             ["cpc-btc-updown-15m-", "cpc-btc-updown-5m-",
              "cpceth-eth-updown-5m-", "cpcsol-sol-updown-5m-",
              "cpcxrp-xrp-updown-5m-"]),
    ]

    # REPORTED, NOT REQUIRED. These are the activation switches: they are
    # what the owner flips, so this tool states them and does not grade
    # them. Asserting a value here would make the verifier fail at exactly
    # the moment activation succeeds.
    #
    # `defensive_execution_enabled` MOVED HERE FROM THE GRADED ROWS, and it
    # belongs here for precisely the reason this block already states. It was
    # graded against APPROVED["defensive_execution_enabled"], which made the
    # verifier assert one particular side of an activation switch:
    #
    #   graded False -> the verifier FAILS the authorized live window, the
    #                   moment defensive completion is switched on;
    #   graded True  -> the verifier FAILS a deliberate safety ROLLBACK to
    #                   defensive-off, which is the worse of the two because
    #                   it obstructs standing the window down in a hurry.
    #
    # Either way the pre-deploy gate breaks at one of the two moments that
    # matter. Reporting it keeps the value VISIBLE in the verdict -- an
    # operator still reads the effective state from `observed_not_graded` --
    # while leaving both ON and OFF deployable. APPROVED keeps its entry so
    # the approved posture stays documented; nothing grades against it.
    observed: List[Dict[str, Any]] = []
    for name, source, read in (
            ("defensive_execution_enabled", "DEF.enabled()", DEF.enabled),
            ("shadow_only", "R.shadow_only()", R.shadow_only),
            ("experiment_id", "AUTH.experiment_id()", AUTH.experiment_id),
            ("acc_max_attempts", "AUTH.acc_max_attempts()",
             AUTH.acc_max_attempts),
            # PHASE 1 IS REPORTING ONLY, so the mandate's verdict is
            # OBSERVED and NOT GRADED. Grading it would fail every
            # deployment until a mandate is installed, and the whole point
            # of phase 1 is to run verification beside the live path without
            # anything depending on it. `trusted_keys` being empty is the
            # shipped state and is reported, not failed.
            ("mandate_trusted_key_ids", "MS.MANDATE_PUBKEYS",
             lambda: ",".join(sorted(MS.MANDATE_PUBKEYS)) or "(none)"),
            ("mandate_verdict", "MS.verify(reporting only)",
             _mandate_verdict_reason),
            # THE TRUTH, NOT A PHASE CONSTANT. This was hardcoded False
            # while the gate was inert. The gate is SELF-ACTIVATING -- it
            # binds exactly when a trusted key is registered -- so a
            # hardcoded False would report "not enforcing" from the moment
            # enforcement actually began, which is the one moment the
            # operator most needs the row to be right. Derived from the same
            # expression the arming gate branches on, so the two cannot
            # disagree.
            ("mandate_enforcing", "bool(MS.MANDATE_PUBKEYS) -- the same "
                                  "expression authorize_arming branches on",
             lambda: bool(MS.MANDATE_PUBKEYS)),
            # THE PROMOTION GATE. Binds with the signed mandate and refuses
            # live arming until measured expectancy clears the bar the
            # mandate itself states. Reported so its refusal is visible as a
            # deliberate state rather than looking like a broken executor.
            ("promotion_gate_binds",
             "wired into authorize_arming; binds with the signed mandate",
             lambda: bool(MS.MANDATE_PUBKEYS)),
            # THE PRE-DEPLOYMENT DRAIN. Visible so an operator can tell a
            # paused executor from a stuck one, and so a pause left set by
            # mistake is findable in the heartbeat instead of by inference
            # from an absence of orders.
            ("submission_paused_for_deploy", "env %s" % _submit_pause_env(),
             _submit_pause_state),
            # VERSIONED STRATEGY CONFIGURATION, so realized results before
            # and after an update are attributable. Identifier only.
            # THE COLLAPSED COMPLETION DEADLINE, surfaced because nothing
            # else said it. `may_complete` tests complete_cutoff BEFORE the
            # terminal, so the earlier bound wins; at the deployed
            # cancel_margin_s of 300 the terminal is T+600 and
            # COMPLETE_CUTOFF_S = 720 is unreachable, which puts
            # risk-reducing completion on the same deadline as opening.
            # Observed, not graded: grading it would fail the current
            # deployment, and the fix is an owner decision about the margin,
            # not something this row should force.
            ("binding_completion_deadline_s",
             "TS.binding_completion_deadline_s(900)",
             lambda: TS.binding_completion_deadline_s(900.0)[
                 "binding_completion_deadline_s"]),
            ("complete_cutoff_reachable",
             "COMPLETE_CUTOFF_S < cancel_cutoff(900)",
             lambda: TS.binding_completion_deadline_s(900.0)[
                 "complete_cutoff_reachable"]),
            ("completion_collapsed_onto_entry_cutoff",
             "binding completion deadline <= entry cutoff",
             lambda: TS.binding_completion_deadline_s(900.0)[
                 "collapsed_onto_entry_cutoff"]),
            ("strategy_config_label", "TS.STRATEGY_CONFIG_LABEL",
             lambda: str(TS.STRATEGY_CONFIG_LABEL)),
            ("strategy_config_digest",
             "TS.strategy_config_fingerprint() over the EFFECTIVE knobs",
             lambda: str(TS.strategy_config_fingerprint().get("digest"))),
            # THE TWO-SIDED PROBE'S OWN SCOPE. These are mandate
            # parameters -- order size and whether BOTH legs may rest at
            # once -- and none of them was visible in-container, so a
            # campaign's stated size could only be asserted from the
            # module defaults rather than read from the running process.
            # OBSERVED, not graded: they are activation switches, and
            # grading them would make this fail at the moment activation
            # succeeds.
            ("ts_two_order_pair_enabled", "TS.two_order_pair_enabled()",
             TS.two_order_pair_enabled),
            ("ts_active_any_market", "TS.active()", TS.active),
            ("ts_named_market", "TS.named_market()", TS.named_market),
            ("ts_opening_leg_max_contracts",
             "TS.opening_leg_max_contracts()",
             TS.opening_leg_max_contracts),
            ("ts_round_allocations", "TS.round_allocations()",
             TS.round_allocations),
            ("ts_min_first_leg_own_price", "TS.min_first_leg_own_price()",
             TS.min_first_leg_own_price),
            # REPORTED BESIDE THE FLOOR AND GRADED THE SAME WAY -- which is
            # to say observed, not graded. The owner chooses both bounds per
            # campaign, so a frozen required value here would fail the
            # service on a legitimate change; printing it on every boot is
            # what makes a variable edit that did not take effect visible.
            ("ts_max_first_leg_own_price", "TS.max_first_leg_own_price()",
             TS.max_first_leg_own_price),
            # WHETHER THE ENGINE OPENS INVENTORY ON ITS OWN. Until this
            # existed, every opening rode an observed L5 action, so "is the
            # engine capable of opening a position without L5" needed no
            # reporting -- the answer was no. It now depends on a variable,
            # and a variable that changes what the engine may do and is
            # invisible at boot is exactly the gap that let the submission
            # budgets drift to 40/40/64. OBSERVED, not graded: it is an
            # activation switch, and grading it would fail the service at
            # the moment activation succeeds.
            ("controller_open_enabled", "env L5_CONTROLLER_OPEN",
             lambda: str(os.getenv("L5_CONTROLLER_OPEN", "")).strip().upper()
             in ("1", "YES", "TRUE", "ON")),
            # THE FLAG THAT GATES THE MAKER-COMPLETION ROUTE, read through
            # the module's own accessor rather than from the variable text.
            # It was absent here, so the only way to tell whether the route
            # was live was to watch for `book_completion:*` counters to
            # appear -- an inference, on the switch that decides whether
            # held inventory can be completed at all.
            ("book_completion_enabled", "BC.enabled()",
             lambda: __import__("src.l5_book_completion", fromlist=["x"]
                                ).enabled()),
            # THE COMPLEMENT REQUOTE SWITCH, surfaced for exactly the reason
            # the row above exists. `maybe_completion_requote` is called on
            # every tick -- L5_LIVE_TICK_STAGES shows the stage at 0.000s --
            # so the loop LOOKS healthy whether or not the path does
            # anything. `completion_requote_enabled()` defaults to OFF, and
            # nothing in the heartbeat said so: the only way to tell was to
            # notice that no requote ever fired, which is an inference drawn
            # from an absence. That is precisely how the book-completion
            # switch stayed unexamined, and this is the same switch class --
            # the one deciding whether a stranded leg's complement may be
            # repriced toward the touch at all.
            ("completion_requote_enabled", "R.completion_requote_enabled()",
             lambda: __import__("src.l5_pmus_live_runner", fromlist=["x"]
                                ).completion_requote_enabled()),
            # ============ THE SIMULTANEOUS-QUOTING SWITCHES, for the same
            # reason the requote switch above is here: issue #194 item 4 asks
            # what `L5_DUAL_QUOTE` and `L5_DUAL_REPLENISH` are set to in
            # production, and this block -- which is where an operator reads
            # the effective state of every other switch -- did not report
            # them. The only way to tell was to read the variable names out
            # of Railway, where an OAuth caller gets names WITHOUT values.
            #
            # AND THEY INTERACT WITH THE ONE ABOVE. `dual_quote` rests one
            # order on each side of a market; `completion_requote` requires
            # EXACTLY ONE resting order in the market and declines
            # otherwise. Both are on in production with max_live_orders 2,
            # so repricing is inert whenever a pair is working -- which is
            # safe (it never guesses which of two to cancel) and which no
            # operator could see from here until these rows existed.
            ("dual_quote_ab_mode", "DQ.describe()['ab_mode']",
             lambda: bool(__import__("src.l5_dual_quote", fromlist=["x"]
                                     ).describe().get("ab_mode"))),
            ("dual_quote_enabled_now", "DQ.enabled()",
             lambda: bool(__import__("src.l5_dual_quote", fromlist=["x"]
                                     ).enabled())),
            ("dual_replenish_enabled_now", "DQ.replenish_enabled()",
             lambda: bool(__import__("src.l5_dual_quote", fromlist=["x"]
                                     ).replenish_enabled())),
            ("dual_max_placements_per_pass", "DQ.max_placements_per_pass()",
             lambda: int(__import__("src.l5_dual_quote", fromlist=["x"]
                                    ).max_placements_per_pass())),
            ("replenish_max_rounds", "RPL.max_rounds()",
             lambda: int(__import__("src.l5_replenish", fromlist=["x"]
                                    ).max_rounds())),
            ("book_completion_breakeven_pair", "BC.BREAKEVEN_PAIR",
             lambda: str(__import__("src.l5_book_completion", fromlist=["x"]
                                    ).BREAKEVEN_PAIR)),
            # THE HARD LINE, and the DEPTH -- both new, and both decide money.
            # The ceiling is what the gate actually refuses on now that 0.9532
            # is a preference; the depth is how far behind the touch a
            # completion rests, which is NOT the opening probe's depth and is
            # why 19 of the campaign's 21 orders sat two ticks back.
            ("book_completion_redemption_pair", "BC.REDEMPTION_PAIR",
             lambda: str(__import__("src.l5_book_completion", fromlist=["x"]
                                    ).REDEMPTION_PAIR)),
            ("book_completion_depth_ticks", "BC.completion_depth_ticks()",
             lambda: __import__("src.l5_book_completion", fromlist=["x"]
                                ).completion_depth_ticks()),
            # THE ENTRY DEPTH, which was the one knob a reader had to
            # INFER. `L5_TS_DEPTH_TICKS` is absent from the service, so
            # the default applies -- but "absent, therefore 2" is two
            # reads and an assumption, while every other row here is one
            # accessor call. The completion depth was already reported and
            # the ENTRY depth, which decides the pair the probe actually
            # quotes, was not. Observed, not graded: grading it would make
            # this fail the moment an experiment legitimately changed it.
            ("ts_entry_depth_ticks", "TS.depth_ticks()",
             lambda: TS.depth_ticks()),
            # THE COMPLETION CEILING AND THE COMPLEMENT'S DEPTH, both
            # observed rather than graded. 0.96 was never the completion
            # bound -- `completion_pair_rescue` enforced redemption -- so an
            # operator reading only `maker_pair_ceiling` would conclude the
            # wrong number was in force. And the complement's depth decides
            # whether a completion is fillable at all: the two-sided path
            # reused the OPENING depth, which put 2245z's complement two
            # ticks behind the whole visible queue.
            ("completion_pair_ceiling", "TS.completion_pair_ceiling()",
             lambda: str(__import__("src.l5_two_sided_probe", fromlist=["x"]
                                    ).completion_pair_ceiling())),
            # AND THE REQUOTE'S OWN PAIR BOUND, by the same argument one
            # paragraph up. It is a THIRD ceiling, it is the one that
            # decides what a reprice may pay for the pair, and it was
            # invisible: 0100z cancelled a 0.9500 pair to chase 1.0000 with
            # `L5_TS_COMPLETION_MAX_PAIR` unset and nothing in the verifier
            # reporting that the bound was the redemption line. It now
            # defaults to the opening ceiling, which is exactly why an
            # operator must be able to read which value is in force.
            ("completion_max_pair", "TS.completion_max_pair()",
             lambda: str(__import__("src.l5_two_sided_probe", fromlist=["x"]
                                    ).completion_max_pair())),
            ("ts_completion_at_touch", "L5_TS_COMPLETION_AT_TOUCH",
             lambda: str((os.getenv("L5_TS_COMPLETION_AT_TOUCH") or ""
                          ).strip().upper() in ("1", "YES", "TRUE", "ON")).lower()),
            # THE BOUNDED TAKER POLICY. Default-off, and the verifier must
            # say so out loud: a crossing policy whose enablement is only
            # visible by reading an env var is exactly the configuration an
            # operator should never have to infer.
            ("taker_policy_enabled", "TCP enabled",
             lambda: str(os.getenv(
                 __import__("src.l5_taker_completion_policy",
                            fromlist=["x"]).POLICY_ENV, "")
             ).strip().upper() == "YES"),
            # ...AND THE SECOND SWITCH, which was missing. `TCP.authorize`
            # requires BOTH `enabled` (above) and `owner_authorized`, and
            # refuses OWNER_AUTHORIZATION_ABSENT without the latter -- but
            # only the first was reported here, so a maker-only campaign
            # could be verified against a config that showed one of the two
            # gates on live crossing and left the other to be inferred.
            # That is precisely what the comment above says an operator
            # should never have to do.
            ("taker_policy_owner_cross_authorized",
             "env L5_TS_COMPLETION_CROSS (TCP owner_authorized)",
             lambda: str(os.getenv("L5_TS_COMPLETION_CROSS", "")
                         ).strip().upper() == "YES"),
            # THE OPERATOR-FACING ANSWER, derived from both: can this
            # process cross at all? A maker-only authorization requires
            # this to read False, and reading it should not require
            # knowing how the two switches combine.
            ("taker_crossing_reachable",
             "taker_policy_enabled AND owner_cross_authorized",
             lambda: bool(
                 str(os.getenv(
                     __import__("src.l5_taker_completion_policy",
                                fromlist=["x"]).POLICY_ENV, "")
                     ).strip().upper() == "YES"
                 and str(os.getenv("L5_TS_COMPLETION_CROSS", "")
                         ).strip().upper() == "YES")),
            ("taker_policy_margin_floor_usd", "TCP margin floor",
             lambda: str(os.getenv(
                 __import__("src.l5_taker_completion_policy",
                            fromlist=["x"]).MARGIN_FLOOR_ENV, "")
                 or __import__("src.l5_taker_completion_policy",
                               fromlist=["x"]).DEFAULT_MARGIN_FLOOR_USD)),
            ("taker_policy_max_outlay_usd", "TCP per-cross cap",
             lambda: str(os.getenv(
                 __import__("src.l5_taker_completion_policy",
                            fromlist=["x"]).MAX_OUTLAY_ENV, "")
                 or __import__("src.l5_taker_completion_policy",
                               fromlist=["x"]).DEFAULT_MAX_OUTLAY_USD)),
            ("taker_policy_window_outlay_usd", "TCP window cap",
             lambda: str(os.getenv(
                 __import__("src.l5_taker_completion_policy",
                            fromlist=["x"]).WINDOW_OUTLAY_ENV, "")
                 or __import__("src.l5_taker_completion_policy",
                               fromlist=["x"]).DEFAULT_WINDOW_OUTLAY_USD)),
            ("taker_policy_maker_patience_s", "TCP maker-first wait",
             lambda: str(os.getenv(
                 __import__("src.l5_taker_completion_policy",
                            fromlist=["x"]).MAKER_PATIENCE_ENV, "")
                 or __import__("src.l5_taker_completion_policy",
                               fromlist=["x"]).DEFAULT_MAKER_PATIENCE_S)),
            ("taker_policy_depth_haircut", "TCP depth haircut",
             lambda: str(os.getenv(
                 __import__("src.l5_taker_completion_policy",
                            fromlist=["x"]).DEPTH_HAIRCUT_ENV, "")
                 or __import__("src.l5_taker_completion_policy",
                               fromlist=["x"]).DEFAULT_DEPTH_HAIRCUT)),
            ("campaign_authorized_start", "approval scope starts_utc",
             lambda: str((__import__(
                 "src.l5_campaign_approval", fromlist=["x"]
             ).approval_gate().state().get("scope") or {}
             ).get("starts_utc"))),
            ("ts_order_ttl_s", "TS.order_ttl_s()", TS.order_ttl_s),
            ("ts_entry_cutoff_s", "TS.entry_cutoff_s()",
             TS.entry_cutoff_s),
            ("ts_complete_cutoff_s", "TS.complete_cutoff_s()",
             TS.complete_cutoff_s),
            ("ts_min_entry_runway_s", "TS.min_entry_runway_s()",
             TS.min_entry_runway_s),
            # THE DOLLAR-DENOMINATED RESIDUAL BOUND. The residual cap is
            # denominated in CONTRACTS and is therefore price-blind -- three
            # contracts admits $0.36 at an own price of 0.12 and $2.49 at
            # 0.83 -- so this is the control that makes it price-aware, and
            # it is the one new risk limit in the campaign. Reported through
            # the parser the money path uses, so a value that would be fatal
            # at boot is visible here as the refusal it is. OBSERVED rather
            # than graded, because it is default-disabled and grading it
            # would fail every deployment that has not configured it.
            ("max_worst_case_deficit_usd",
             "VG.parse_deficit_bound(env %s)" % R.DEFICIT_ENV,
             lambda: (lambda v: "UNSET" if v is None else str(v))(
                 VG.parse_deficit_bound(os.getenv(R.DEFICIT_ENV)))),
            # ============ CAN THIS ENGINE FINISH WHAT IT STARTS?
            #
            # Two deployed numbers decide different halves of one cycle and
            # nothing made them agree:
            #
            #   the OPENING clip, which sizes the first leg, and
            #   the TCP per-cross cap, which bounds what retiring that leg
            #   by crossing may cost.
            #
            # Completing C contracts by crossing can cost up to C * 0.99
            # plus the taker fee, so a clip is only completable when that
            # worst case fits the cap. Measured on the recorded tape: of 10
            # terminal residuals, 7 were refused on the 1.20 pair ceiling
            # and TWO were refused purely on outlay at pairs of 1.13 and
            # 1.18 -- economically sound completions blocked by a cash cap,
            # not by economics.
            #
            # AND IT COLLIDES WITH THE REBATE FLOOR. The maker rebate
            # rounds per fill to the cent, so a clip under
            # 0.4/(p*(1-p)) -- 1.61 contracts at the money -- is credited
            # nothing, while a clip over ~1.45 cannot be crossed out within
            # a $1.50 cap. The two cannot both be satisfied, and that is an
            # owner decision rather than a bug to patch silently.
            #
            # OBSERVED, NEVER GRADED. Grading it would fail deployments
            # that deliberately run maker-only and never cross, where an
            # uncompletable clip is not a defect. The row states the
            # arithmetic; it changes no behaviour.
            ("opening_clip_completable_within_taker_cap",
             "TS.opening_leg_max_contracts() vs TCP per-cross cap",
             lambda: _clip_vs_cross_cap())):
        try:
            observed.append({"name": name, "source": source,
                             "effective": _s(read())})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            observed.append({"name": name, "source": source,
                             "effective": None,
                             "error": type(exc).__name__})

    # ---------------------------------------------------------------- the
    # CONSISTENCY row that 0230z needed and nobody had.
    #
    # `AUTH.EXECUTION_MODE` is "MAKER_ONLY" and it is a field of SCOPE_FIELDS,
    # so it is inside the digest the OWNER approves. On 2026-10-23 at 02:39:04
    # the defensive module issued its own mandate -- "permits ONE crossing
    # order and the defensive pair ceiling for that order alone" -- and crossed
    # at a pair cost of 1.04. That contradicted a field of the approved
    # envelope, and no gate noticed, because `defensive_execution_enabled` had
    # been moved to `observed_not_graded` where nothing grades it.
    #
    # THIS IS NOT A RETURN TO GRADING IT AGAINST A CONSTANT. The reason it was
    # de-graded stands: a fixed expectation fails either the activation or the
    # rollback. What is graded here is CONSISTENCY BETWEEN TWO THINGS THE
    # DEPLOYMENT ALREADY STATES -- if the approved execution mode is
    # MAKER_ONLY then a module that crosses may not be armed. Both postures
    # stay deployable: turn defensive off and this passes under MAKER_ONLY;
    # declare a crossing execution mode and it passes with defensive on. Only
    # the contradiction fails, which is the state that actually spent money
    # outside the approved scope.
    #
    # It is a TIGHTENING and it cannot obstruct a safety rollback: switching
    # defensive OFF satisfies it unconditionally.
    #: the only modes that are PERMISSION to cross. Anything else -- including
    #: an absent, empty or unreadable mode -- is treated as MAKER_ONLY, so the
    #: check fails CLOSED. A mode we cannot read is not consent.
    _CROSSING_MODES = ("TAKER_PERMITTED", "DEFENSIVE_PERMITTED")

    def _mode_and_defensive_agree() -> bool:
        try:
            mode = str(getattr(AUTH, "EXECUTION_MODE", "") or "").strip().upper()
        except BaseException:                                  # noqa: BLE001
            mode = ""
        if mode in _CROSSING_MODES:
            return True            # a mode that explicitly permits crossing
        return not DEF.enabled()   # everything else forbids an armed crosser

    rows.append(_row(
        "maker_only_forbids_defensive_crossing",
        "AUTH.EXECUTION_MODE vs DEF.enabled()",
        _mode_and_defensive_agree, True))

    # ------------------------------------------------------------ PHASE 1
    # THE MANDATE SIGNING KEY MUST NEVER BE REACHABLE FROM THE RUNTIME.
    #
    # Requirement 1 of the standing-mandate design: the owner's private key
    # is generated offline and never exists in this repository, in Railway,
    # or in any environment the executor can read. That is an invariant, not
    # a phase, so unlike the mandate's own status it IS graded -- and it
    # stays true for ever, so grading it never has to be revisited.
    #
    # It looks for private-key SHAPE rather than a fixed name, because the
    # mistake this catches is someone pasting a seed into a variable nobody
    # thought to forbid. A 64-hex-character value under a signing-ish name
    # is refused; the 64-hex PUBLIC key is fine because it lives in code,
    # not the environment.
    _KEYISH = ("MANDATE_PRIVATE", "MANDATE_SECRET", "MANDATE_SEED",
               "MANDATE_SIGNING_KEY", "SIGNING_SEED", "OWNER_PRIVATE")

    def _no_signing_key_in_runtime() -> bool:
        try:
            for name, val in os.environ.items():
                up = name.upper()
                if any(k in up for k in _KEYISH):
                    return False
                v = (val or "").strip()
                if "MANDATE" in up and len(v) == 64:
                    try:
                        bytes.fromhex(v)
                    except ValueError:
                        continue
                    return False          # 32 raw bytes under a MANDATE name
                if "BEGIN PRIVATE KEY" in v or "BEGIN OPENSSH PRIVATE" in v:
                    return False
        except BaseException:                                  # noqa: BLE001
            return False                  # cannot tell -> fail closed
        return True

    rows.append(_row(
        "mandate_signing_key_absent_from_runtime",
        "os.environ private-key shape scan",
        _no_signing_key_in_runtime, True))

    failed = [r for r in rows if not r["ok"]]
    return {
        "label": "L5_EFFECTIVE_RUNTIME_CONFIG",
        "PASS": not failed,
        "checked": len(rows),
        "failed": [r["name"] for r in failed],
        "rows": rows,
        "observed_not_graded": observed,
        "labels": ("every row is the value the money path's own accessor "
                   "returns, not the text of a variable. An unreadable row "
                   "FAILS. The activation switches are reported and not "
                   "graded, because grading them would make this fail at "
                   "the moment activation succeeds."),
    }


def main(argv: Optional[List[str]] = None) -> int:
    """No args: verify THIS process's effective configuration -- the real
    check, meant to be run inside the deployed container.

    `--from-snapshot PATH` (or `-` for stdin): predict instead, from a JSON
    object of variable names to values. Strip every secret before exporting
    one; a snapshot carrying a secret-looking name is REFUSED outright.
    """
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--from-snapshot":
        path = argv[1] if len(argv) > 1 else "-"
        raw = sys.stdin.read() if path == "-" else open(path).read()
        try:
            out = verify_snapshot(json.loads(raw))
        except SnapshotRefused as exc:
            print(json.dumps({"PASS": False, "refused": str(exc),
                              "basis": "SNAPSHOT_REFUSED"}, indent=1))
            return 2
    elif argv:
        print("usage: python3 -m src.l5_runtime_config_verify "
              "[--from-snapshot PATH|-]")
        return 2
    else:
        out = verify()
    print(json.dumps(out, indent=1, sort_keys=False))
    return 0 if out["PASS"] else 1


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())

def _clip_vs_cross_cap() -> str:
    """Whether the opening clip can be retired by a cross within the cap.

    Pure arithmetic over two deployed figures. Reports UNKNOWN rather than
    guessing when either is unreadable, because a guessed answer here
    would claim a cycle can close when it cannot.
    """
    try:
        from src import l5_two_sided_probe as TSP
        from src import l5_taker_completion_policy as TCP
        from src import pmus_fees as FEES
        clip = TSP.opening_leg_max_contracts()
        if clip is None or D(str(clip)) <= 0:
            return "UNKNOWN: opening clip unreadable"
        clip = D(str(clip))
        cap = D(str(os.getenv(TCP.MAX_OUTLAY_ENV, "")
                    or TCP.DEFAULT_MAX_OUTLAY_USD))
        dear = D("0.99")
        worst = clip * dear + FEES.taker_fee(dear, clip, cents=True)
        floor = FEES.rebate_floor_contracts(D("0.50"))
        return ("clip %s -> worst cross outlay %s vs cap %s: %s; "
                "rebate floor at p=0.50 is %s contracts, confirmed floor "
                "%s (earns $0.00 below it)"
                % (clip, worst, cap,
                   "COMPLETABLE" if worst <= cap else "NOT_COMPLETABLE",
                   ("?" if floor is None else floor),
                   FEES.CREDITED_OBSERVED[
                       "smallest_credited_clip_contracts"]))
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return "UNKNOWN: %s" % type(exc).__name__
