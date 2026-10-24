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
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Optional


#: THE OWNER'S APPROVED FIGURES for the eight-window live TWO_SIDED
#: campaign, as approved 2026-10-21. Written here as code so a drift shows
#: up as a failing row rather than as a document nobody re-reads.
APPROVED: Dict[str, Any] = {
    "submission_budget_open": 16,
    "submission_budget_complete": 24,
    "submission_budget_replace": 32,
    "submission_backstop": 56,
    "defensive_loss_budget_usd": D("2"),
    # RAISED 5 -> 20 in lockstep with CA.MAX_TOTAL_EXPOSURE_USD and
    # R.PROBE_LIMITS. The two must agree or _looser() refuses arming, so a
    # verifier row that still expected 5 would pass only a configuration
    # that could never arm.
    "aggregate_exposure_usd": D("20"),
    "daily_realized_loss_halt_usd": D("5"),
    "maker_pair_ceiling": D("0.96"),
    "defensive_pair_ceiling": D("1.20"),
    "cancel_margin_s": 300.0,
    "defensive_terminal_s": 600.0,
    "defensive_guard_opens_s": 540.0,
    "defensive_last_actionable_s": 585.0,
    "max_windows": 8,
    "max_live_orders": 1,
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


def verify() -> Dict[str, Any]:
    """Every approved figure, read effectively. `PASS` only if all hold."""
    from src import l5_campaign_approval as CA
    from src import l5_defensive_completion as DEF
    from src import l5_mandate_signature as MS
    from src import l5_live_authorization as AUTH
    from src import l5_pmus_live_runner as R
    from src import l5_two_sided_probe as TS
    from src import l5_live_validation_gates as VG

    A = APPROVED
    rows: List[Dict[str, Any]] = [
        # ---------------------------------------- submission budgets
        _row("submission_budget_open", "AUTH.max_of_class('OPEN')",
             lambda: AUTH.max_of_class("OPEN"), A["submission_budget_open"]),
        _row("submission_budget_complete", "AUTH.max_of_class('COMPLETE')",
             lambda: AUTH.max_of_class("COMPLETE"),
             A["submission_budget_complete"]),
        _row("submission_budget_replace", "AUTH.max_of_class('REPLACE')",
             lambda: AUTH.max_of_class("REPLACE"),
             A["submission_budget_replace"]),
        _row("submission_backstop", "AUTH.max_submissions()",
             AUTH.max_submissions, A["submission_backstop"]),

        # ---------------------------------------------- hard money limits
        _row("defensive_loss_budget_usd",
             "DEF.DEFENSIVE_LOSS_BUDGET_USD (code constant)",
             lambda: DEF.DEFENSIVE_LOSS_BUDGET_USD,
             A["defensive_loss_budget_usd"]),
        _row("aggregate_exposure_usd",
             "R.PROBE_LIMITS['max_total_exposure_usd']",
             lambda: D(str(R.PROBE_LIMITS["max_total_exposure_usd"])),
             A["aggregate_exposure_usd"]),
        _row("daily_realized_loss_halt_usd",
             "R.daily_realized_loss_halt()",
             R.daily_realized_loss_halt, A["daily_realized_loss_halt_usd"]),
        _row("max_live_orders", "R.max_live_orders()",
             R.max_live_orders, A["max_live_orders"]),

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
        _row("max_windows_authorization", "AUTH.max_windows_env()",
             AUTH.max_windows_env, A["max_windows"]),
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
        _row("series_allowlist", "CA.ALLOWED_SERIES (code constant)",
             lambda: list(CA.ALLOWED_SERIES), ["cpc-btc-updown-15m-"]),
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
            ("mandate_enforcing", "phase 1 constant", lambda: False),
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
                 VG.parse_deficit_bound(os.getenv(R.DEFICIT_ENV))))):
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
