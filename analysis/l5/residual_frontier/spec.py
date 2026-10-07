"""RESIDUAL-EFFICIENCY FRONTIER -- the frozen rule grid and metric definitions.

Lane B. Preregistered in `analysis/l5/residual_frontier/PREREGISTRATION.md`,
which carries the fingerprint literal asserted here.

WHAT THIS IS. The frozen no-rebate audit (protocol 3fb8fccb) priced L5's own
historical cash with rebates/rewards set to exactly $0 and found:

    matched-pair economics    +$22,568.25
    directional residual      -$25,313.78
    covered net ex incentives   -$2,745.53   (n=2,218 events, 22 observed days)

The matched engine makes money; the leftover directional exposure gives it all
back. This spec freezes a COMPACT grid of contemporaneous residual-bounding
rules, each justified by an L5 state variable the repository already measures,
and the metric definitions under which every cell is scored identically.

NOT OUR REALIZED PROFIT. Every dollar produced under this spec is a HISTORICAL
REPLAY of another trader's public Polymarket activity, counterfactually
modified. It is not shadow, not a probe, not submitted, not filled, not
settled, and never realized. No order is placed by anything in this package.

CONTEMPORANEOUS STATE ONLY. A rule receives a frozen `DecisionState` carrying
only what was observable at the decision instant: inventory built from fills
already TAKEN by this cell, the candidate fill's own side/price/size, and the
time-to-expiry derived from the event slug. Settlement outcome, redemption
cash, event totals, peak-of-event residual and any later fill are structurally
absent from that object. `tests/test_residual_frontier.py` asserts the field
set, so the constraint is enforced rather than promised.

NO POST-HOC THRESHOLD SEARCH. The cap ladders below were placed on measured
percentiles of L5's OWN state distributions before any P&L was computed:
peak |residual| per event p10/p25/p50/p75/p90/p99 = 19.5 / 46.5 / 91.5 /
173.5 / 334.1 / 755.3 shares, terminal residual fraction p10/p25/p50/p75 =
0.04 / 0.12 / 0.38 / 1.00, and fill time-to-expiry p10 = 84 s. That scale
calibration is a property of the state distribution, not of returns, and is
disclosed in the preregistration.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple

PROTOCOL = "RESIDUAL-EFFICIENCY-FRONTIER-LANE-B"
PARENT_PROTOCOL = "PREREGISTRATION_NOREBATE.md @ 3fb8fccb"
POPULATION = "data/l5/raw/activity.jsonl -> the 2,218-event frozen Q1 corpus"

# ---------------------------------------------------------------- state fields

#: The COMPLETE set of fields a rule may read. Anything not here is a
#: post-decision field and its presence on DecisionState is a test failure.
ALLOWED_STATE_FIELDS: Tuple[str, ...] = (
    "shares_up",        # shares of UP held from fills THIS CELL already took
    "shares_down",      # shares of DOWN held from fills THIS CELL already took
    "cand_is_up",       # candidate fill's outcome side
    "cand_size",        # candidate fill's size in shares
    "cand_price",       # candidate fill's price
    "tte_s",            # seconds from the candidate fill to window close
    "duration_s",       # 300 or 900, from the slug family
    "taken_count",      # fills this cell has already taken in this event
)

#: Fields that exist in the corpus and are FORBIDDEN inside a rule. Named so
#: the test can prove none of them leaked onto the state object.
FORBIDDEN_STATE_FIELDS: Tuple[str, ...] = (
    "winner_index", "settled", "resolved", "redeem_cash", "redeem_proceeds",
    "event_net", "event_matched_shares", "event_peak_residual",
    "event_total_fills", "future_fills", "remaining_fills", "pnl",
    "matched_pair_economics", "directional_residual", "era_total",
)

# ------------------------------------------------------------ rule families

#: Each family names the OBSERVED L5 state variable that justifies it and
#: where in this repository that variable is evidenced.
FAMILIES: Dict[str, Dict[str, Any]] = {
    "BASELINE": {
        "reads": [],
        "evidenced_by": "analysis/l5/norebate/Q1_NOREBATE.json FULL_CORPUS",
        "what": ("take every L5 fill. Reproduces the frozen audit's matched "
                 "component exactly and is the denominator for every "
                 "'avoided' and 'retained' metric."),
    },
    "ABS": {
        "reads": ["peak_resid"],
        "evidenced_by": ("analysis/l5/CANON_REGIMES.md daily observable "
                         "`peak_resid` = peak |up-down| share residual; "
                         "analysis/l5/norebate/REGIME.json per_day "
                         "`residual_shares`"),
        "what": ("block a candidate fill whose resulting |residual| would "
                 "exceed CAP shares."),
    },
    "FRAC": {
        "reads": ["term_resid_frac"],
        "evidenced_by": ("analysis/l5/CANON_REGIMES.md daily observable "
                         "`term_resid_frac` = terminal residual / gross "
                         "shares"),
        "what": ("block a candidate fill whose resulting |residual| would "
                 "exceed FRAC * resulting gross inventory."),
    },
    "TTE": {
        "reads": ["first_tte_s"],
        "evidenced_by": ("analysis/l5/CANON_REGIMES.md daily observable "
                         "`first_tte_s`; analysis/l5/EXTEND_AND_SWITCH.md "
                         "'inside the last 30 seconds P(STOP) jumps by an "
                         "order of magnitude' -- L5's own clock already "
                         "governs STOP, so a clock-tightened cap is a "
                         "sharpening of observed behaviour, not an "
                         "invention."),
        "what": ("multiply the ABS cap by TIGHTEN once tte_s < TTE_S."),
    },
    "COMBO": {
        "reads": ["peak_resid", "term_resid_frac", "first_tte_s"],
        "evidenced_by": "the three families above, intersected",
        "what": "a fill must satisfy every active bound.",
    },
}

#: Suppression semantics. EXTEND_ONLY is the preregistered primary: once the
#: cap binds, ADDITIONAL SAME-SIDE exposure is refused while a COMPLETE (a
#: fill on the lighter side, which REDUCES |residual|) is still allowed. This
#: mirrors the live shell, where `residual_cap_units` gates EXTEND and not
#: COMPLETE (analysis/l5/residual_frontier/FRONTIER_SUPERSEDED_20260914.md
#: Result 4). ALL_FILLS is carried only as the contrast arm that shows what
#: the EXTEND_ONLY semantic is worth.
SEMANTICS: Tuple[str, ...] = ("EXTEND_ONLY", "ALL_FILLS")

#: A candidate fill is a COMPLETE when it lands on the side with strictly
#: fewer shares held (it reduces |residual|); otherwise it is an EXTEND. With
#: an empty or balanced book every fill is an EXTEND, which is also how
#: analysis/l5/EXTEND_AND_SWITCH.md classifies an OPEN leg's successors.
ACTION_RULE = ("COMPLETE iff the candidate's own side currently holds "
               "strictly fewer shares than the other side; else EXTEND")

# ------------------------------------------------------------------- the grid

#: Cap ladder in SHARES, placed on measured peak-|residual| percentiles.
ABS_CAPS: Tuple[float, ...] = (25.0, 50.0, 100.0, 200.0, 400.0, 800.0)
#: Residual-fraction ladder, placed on measured terminal-residual-fraction
#: percentiles.
FRAC_CAPS: Tuple[float, ...] = (0.10, 0.20, 0.35, 0.50, 0.75)
#: TTE tightening: (threshold seconds, multiplier applied to the ABS cap).
TTE_TIGHTENINGS: Tuple[Tuple[int, float], ...] = (
    (120, 0.50), (120, 0.25), (60, 0.50), (60, 0.25))
#: The ABS cap the TTE family tightens, chosen as the measured p75 of peak
#: |residual| (173.5 shares) rounded to the ladder.
TTE_BASE_ABS_CAP = 200.0
#: COMBO arm: ABS x FRAC.
COMBO_ABS: Tuple[float, ...] = (50.0, 100.0, 200.0)
COMBO_FRAC: Tuple[float, ...] = (0.20, 0.35)
#: COMBO arm: ABS x FRAC x TTE, one tightening only.
COMBO3_ABS: Tuple[float, ...] = (100.0, 200.0)
COMBO3_FRAC = 0.35
COMBO3_TTE: Tuple[int, float] = (60, 0.50)


def build_grid() -> List[Dict[str, Any]]:
    """The complete, ordered, frozen cell list. Deterministic, no I/O."""
    cells: List[Dict[str, Any]] = [{
        "cell_id": "BASELINE",
        "family": "BASELINE",
        "semantics": "ALL_FILLS",
        "abs_cap": None, "frac_cap": None,
        "tte_s": None, "tte_tighten": None,
    }]
    for sem in SEMANTICS:
        for cap in ABS_CAPS:
            cells.append({
                "cell_id": "ABS%g_%s" % (cap, sem),
                "family": "ABS", "semantics": sem,
                "abs_cap": cap, "frac_cap": None,
                "tte_s": None, "tte_tighten": None})
    for f in FRAC_CAPS:
        cells.append({
            "cell_id": "FRAC%g_EXTEND_ONLY" % f,
            "family": "FRAC", "semantics": "EXTEND_ONLY",
            "abs_cap": None, "frac_cap": f,
            "tte_s": None, "tte_tighten": None})
    for t, mult in TTE_TIGHTENINGS:
        cells.append({
            "cell_id": "TTE%dx%g_EXTEND_ONLY" % (t, mult),
            "family": "TTE", "semantics": "EXTEND_ONLY",
            "abs_cap": TTE_BASE_ABS_CAP, "frac_cap": None,
            "tte_s": t, "tte_tighten": mult})
    for cap in COMBO_ABS:
        for f in COMBO_FRAC:
            cells.append({
                "cell_id": "ABS%g+FRAC%g_EXTEND_ONLY" % (cap, f),
                "family": "COMBO", "semantics": "EXTEND_ONLY",
                "abs_cap": cap, "frac_cap": f,
                "tte_s": None, "tte_tighten": None})
    for cap in COMBO3_ABS:
        cells.append({
            "cell_id": "ABS%g+FRAC%g+TTE%dx%g_EXTEND_ONLY" % (
                cap, COMBO3_FRAC, COMBO3_TTE[0], COMBO3_TTE[1]),
            "family": "COMBO", "semantics": "EXTEND_ONLY",
            "abs_cap": cap, "frac_cap": COMBO3_FRAC,
            "tte_s": COMBO3_TTE[0], "tte_tighten": COMBO3_TTE[1]})
    return cells


GRID: List[Dict[str, Any]] = build_grid()
N_CELLS = len(GRID)

# ------------------------------------------------------------ metric defs

#: Applied IDENTICALLY to every cell, baseline included. The test asserts the
#: scorer emits exactly this key set for every cell with no exceptions.
METRICS: Dict[str, str] = {
    "matched_retained_usd": (
        "sum over events of matched_shares * (1 - pair_cost), where "
        "matched_shares = min(up, down) of the fills THIS CELL took and "
        "pair_cost = VWAP_up + VWAP_down of those same fills, ex fee. "
        "Reconciles to the frozen audit's matched component on BASELINE."),
    "residual_pnl_usd": (
        "sum over events of (settlement payout on the retained residual) "
        "- (VWAP cost of the retained residual), where payout = |residual| "
        "if the residual side is the event's resolved winner else 0. The "
        "winner is the outcomeIndex of the event's REDEEM record, a "
        "resolver validated at median |redeem cash - modelled payout| = "
        "$0.0000 and exact on 89.2% of 2,000 resolved events. SCORING "
        "ONLY -- never visible to a rule."),
    "residual_loss_avoided_usd": (
        "residual_pnl_usd(cell) - residual_pnl_usd(BASELINE). Positive "
        "means the cell kept less of the losing residual."),
    "fees_usd": (
        "sum of max(0, usdcSize - price*size) over the fills THIS CELL "
        "took: the CHARGED wedge, never the 0.07*p*(1-p) formula."),
    "net_cash_ex_incentives_usd": (
        "matched_retained_usd + residual_pnl_usd - fees_usd. Rebates and "
        "rewards are exactly $0 by construction, as in protocol 3fb8fccb."),
    "turnover_retained_usd": (
        "sum of usdcSize over the fills THIS CELL took."),
    "turnover_retained_frac": (
        "turnover_retained_usd / turnover_retained_usd(BASELINE)."),
    "events_touched": "events in which this cell took at least one fill.",
    "events_per_observed_day": (
        "events_touched / 22 observed days. Observed days only -- the "
        "May-July coverage hole is a hole, not a zero, and no figure here "
        "divides by calendar span."),
    "worst_drawdown_usd": (
        "most negative (cumulative net - running peak) over events ordered "
        "by first-fill timestamp, using the cell's own per-event net."),
    "suppressed_action_frac": (
        "1 - (fills taken / fills available). The fraction of original L5 "
        "actions this cell refused."),
    "suppressed_extend_frac": (
        "fraction of the available EXTEND fills this cell refused."),
    "suppressed_complete_frac": (
        "fraction of the available COMPLETE fills this cell refused."),
    "net_bp_of_turnover": (
        "10000 * net_cash_ex_incentives_usd / turnover_retained_usd. "
        "Reported so a cell cannot look good merely by trading less."),
}

STRATA: Dict[str, str] = {
    "FULL_CORPUS": "all 2,218 frozen Q1 events",
    "E1_GENUINELY_PRE_REBATE": "era from protocol 3fb8fccb (before 2026-04-21)",
    "E2_EARLY_REBATE": "era from protocol 3fb8fccb (April, on/after 04-21)",
    "E3_INCENTIVE_ERA": "era from protocol 3fb8fccb (after April)",
    "REGIME_PRE_2026-04-25": (
        "primary regime partition from analysis/l5/CANON_REGIMES.md "
        "change points ['2026-04-25','2026-06-23','2026-08-19']"),
    "REGIME_2026-08-19_ON": "same partition, final segment",
    "SETTLEMENT_RESOLVED": (
        "the 2,000 events carrying a REDEEM record, so the residual's "
        "settlement outcome is source-backed"),
    "SETTLEMENT_UNRESOLVED": (
        "the 218 events with NO REDEEM record. Residual payout is scored "
        "$0 there, which is what the feed shows; 212 of the 218 are "
        "one-sided, so $0 is the correct outcome for a lost one-sided "
        "bet. The other 6 carry 1,829.86 matched shares whose complete-set "
        "redemption of $1,829.86 is GUARANTEED and ABSENT from the feed. "
        "Reported separately, never silently pooled."),
}

NO_HINDSIGHT_CLAUSE = (
    "A rule may read only ALLOWED_STATE_FIELDS, evaluated at the candidate "
    "fill's own timestamp, built from fills the cell itself already took. "
    "Settlement, redemption, event aggregates and any later fill are "
    "structurally unavailable to it. Thresholds were placed on state-"
    "distribution percentiles before any P&L existed and are frozen by the "
    "fingerprint below; no threshold is tuned on an outcome.")

OOS_HOLD_CLAUSE = (
    "No out-of-sample test is run under this spec. The split is NAMED and "
    "LEFT UNRUN in FRONTIER.md.")

SPEC: Dict[str, Any] = {
    "protocol": PROTOCOL,
    "parent_protocol": PARENT_PROTOCOL,
    "population": POPULATION,
    "allowed_state_fields": list(ALLOWED_STATE_FIELDS),
    "forbidden_state_fields": list(FORBIDDEN_STATE_FIELDS),
    "families": FAMILIES,
    "semantics": list(SEMANTICS),
    "action_rule": ACTION_RULE,
    "abs_caps_shares": list(ABS_CAPS),
    "frac_caps": list(FRAC_CAPS),
    "tte_tightenings": [list(x) for x in TTE_TIGHTENINGS],
    "tte_base_abs_cap": TTE_BASE_ABS_CAP,
    "combo_abs": list(COMBO_ABS),
    "combo_frac": list(COMBO_FRAC),
    "combo3_abs": list(COMBO3_ABS),
    "combo3_frac": COMBO3_FRAC,
    "combo3_tte": list(COMBO3_TTE),
    "grid": GRID,
    "n_cells": N_CELLS,
    "metrics": METRICS,
    "strata": STRATA,
    "no_hindsight_clause": NO_HINDSIGHT_CLAUSE,
    "oos_hold_clause": OOS_HOLD_CLAUSE,
    "rebates_rewards_usd": 0.0,
    "profit_label": "historical replay",
    "is_realized_profit": False,
}


def fingerprint(spec: Dict[str, Any] = SPEC) -> str:
    """sha256 over the canonical sorted-key JSON of the frozen spec."""
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


#: HARD-CODED LITERAL so assert_frozen() cannot be vacuous.
FRONTIER_FINGERPRINT = \
    "4b16c92bc658583265d014c196a3becf42540cad20c2e98999f954bea16e074d"


def assert_frozen(spec: Dict[str, Any] = SPEC) -> None:
    got = fingerprint(spec)
    if got != FRONTIER_FINGERPRINT:
        raise SystemExit(
            "%s is not the preregistered spec: %s != %s"
            % (PROTOCOL, got, FRONTIER_FINGERPRINT))
    if len(GRID) != len({c["cell_id"] for c in GRID}):
        raise SystemExit("%s grid has duplicate cell_ids" % PROTOCOL)
