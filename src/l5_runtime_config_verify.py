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
    "aggregate_exposure_usd": D("5"),
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
    "approval_exposure_ceiling_usd": D("5"),
    "approval_daily_loss_ceiling_usd": D("5"),
    # at DEPLOY time, before activation
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


def verify() -> Dict[str, Any]:
    """Every approved figure, read effectively. `PASS` only if all hold."""
    from src import l5_campaign_approval as CA
    from src import l5_defensive_completion as DEF
    from src import l5_live_authorization as AUTH
    from src import l5_pmus_live_runner as R
    from src import l5_two_sided_probe as TS

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

        # --------------------------- the state this build must deploy IN
        _row("defensive_execution_enabled", "DEF.enabled()",
             DEF.enabled, A["defensive_execution_enabled"]),
    ]

    # REPORTED, NOT REQUIRED. These are the activation switches: they are
    # what the owner flips, so this tool states them and does not grade
    # them. Asserting a value here would make the verifier fail at exactly
    # the moment activation succeeds.
    observed: List[Dict[str, Any]] = []
    for name, source, read in (
            ("shadow_only", "R.shadow_only()", R.shadow_only),
            ("experiment_id", "AUTH.experiment_id()", AUTH.experiment_id),
            ("acc_max_attempts", "AUTH.acc_max_attempts()",
             AUTH.acc_max_attempts)):
        try:
            observed.append({"name": name, "source": source,
                             "effective": _s(read())})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            observed.append({"name": name, "source": source,
                             "effective": None,
                             "error": type(exc).__name__})

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


def main() -> int:
    out = verify()
    print(json.dumps(out, indent=1, sort_keys=False))
    return 0 if out["PASS"] else 1


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
