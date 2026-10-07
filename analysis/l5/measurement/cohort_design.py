"""E1 / E2 prospective-cohort arithmetic: observability floors, the dynamic
rebate floor, stopping boundaries, and worst-case dollars.

NOT AUTHORIZED. NOT ARMED. This module places no order, holds no credential,
imports nothing from any `live_exec*` path, opens no socket and reads no
environment variable. It is pure arithmetic, standard library only (plus
read-only imports of frozen repository constants), and it is enforced as such
by test.

E2's functions are STRUCTURALLY GATED: every E2 entry point requires an
explicit `e1_verdict="PASS"` argument and raises otherwise. The gate is in the
code, not only in the prose.

NO PARAMETER FITTING, AND NOTHING HERE IS CHOSEN FOR EXPECTED RETURN. Every
quantity below is derived from one of exactly four kinds of input:

  (a) the venue's own grid                  0.01 price tick, 0.01 qty grid
  (b) a frozen repository bound, cited      analysis/l5/stage6/bounds.py
  (c) the published fee formula             src/pmus_fee_schedule.py
      fingerprint ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629
  (d) a standard statistical constant       z_{0.975}, Wald's SPRT boundaries

Where a number could only be obtained by assuming an unknown, the result is
returned as a FUNCTION of that unknown over a grid, never as a point.

Reuses `analysis/l5/measurement/sample_size.py` (28 passing tests) for the
Beta posterior-width, Wald SPRT, order-statistic quantile and per-share
mean machinery rather than restating any of it.
"""
from __future__ import annotations

import math
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Dict, List, Optional, Tuple

from analysis.l5.measurement.sample_size import (
    MAKER_THETA,
    MAX_FIRST_LEG_PRICE,
    STAGE6_BREAKEVEN_MATCHED_FRACTION,
    STAGE6_OBSERVED_MATCHED_FRACTION,
    Z95,
    cascade_placements,
    maker_rebate_usd,
    n_for_mean_halfwidth,
    n_for_posterior_width,
    n_for_quantile,
    per_leg_pnl_sd_usd_per_share,
    sprt_operating_characteristics,
    worst_case_per_first_leg_usd,
)

CENT = D("0.01")

# ------------------------------------------------------------- frozen inputs
#: analysis/l5/stage6/bounds.py :: VENUE_MIN_QTY and the quantity grid.
VENUE_QTY_GRID = D("0.01")
VENUE_MIN_QTY = D("0.01")
#: analysis/l5/stage6/bounds.py :: VENUE_MIN_PRICE / VENUE_MAX_PRICE
VENUE_MIN_PRICE = D("0.01")
#: analysis/l5/stage6/bounds.py :: PAIR_COST_CEILING (CLAUDE.md, copied)
PAIR_COST_CEILING = D("0.96")
#: analysis/l5/stage6/bounds.py :: MIN_LOCKED_PAIR_GROSS_USD
MIN_LOCKED_PAIR_GROSS_USD = D("1.00") - PAIR_COST_CEILING  # $0.04
#: src/pmus_fee_schedule.py, entry effective 2026-09-17T04:00:00+00:00
TAKER_THETA = D("0.0695")

#: STAGE6_HISTORICAL_VALIDATION.md Q3 -- right-censoring on the proxy subset
STAGE6_RIGHT_CENSORING = 0.3228
#: STAGE6_HISTORICAL_VALIDATION.md Q4 -- provable-residual loss rate
STAGE6_PROVABLE_RESIDUAL_LOSS_RATE = 1.000


# =========================================================================
# PART A -- OBSERVABILITY: how many distinguishable fill states exist, and
#           how many does each estimand actually REQUIRE
# =========================================================================

def distinguishable_fill_states(qty: D, grid: D = VENUE_QTY_GRID) -> int:
    """Non-zero filled quantities representable on the venue grid at `qty`.

    A fill is reported as a quantity on the grid, so the observable outcomes
    of one placement are {0, g, 2g, ..., qty}. The ZERO state is always
    available and is what the fill-probability estimand uses; the count
    returned here is the NON-ZERO states, which is what partial-fill
    structure can resolve.

    At qty == grid this returns 1: there is exactly ONE fill state, so a
    partial fill is arithmetically indistinguishable from a full one.
    """
    q = D(str(qty))
    g = D(str(grid))
    if q < g:
        raise ValueError("qty below the venue quantity grid")
    n = (q / g).to_integral_value()
    if n * g != q:
        raise ValueError("qty is not on the venue quantity grid")
    return int(n)


def fill_fraction_resolution(qty: D, grid: D = VENUE_QTY_GRID) -> float:
    """Coarsest resolvable step in the FILLED FRACTION at `qty`.

    Partial-fill structure is a distribution over filled_qty / placed_qty.
    The grid quantises that fraction into `distinguishable_fill_states`
    steps, so no feature of the distribution finer than 1/states exists in
    the data at all. This is a property of arithmetic, not of sample size:
    more observations never recover it.
    """
    return 1.0 / distinguishable_fill_states(qty, grid)


def min_qty_for_fill_fraction_resolution(eps: float,
                                         grid: D = VENUE_QTY_GRID) -> D:
    """Smallest grid quantity whose filled-fraction steps are <= `eps`."""
    if not 0 < eps <= 1:
        raise ValueError("eps must be in (0, 1]")
    states = math.ceil(1.0 / eps)
    return (D(states) * D(str(grid))).quantize(D("0.01"))


def min_qty_for_cent_observable(per_share_usd: D,
                               grid: D = VENUE_QTY_GRID) -> D:
    """Smallest grid quantity at which a per-share cashflow of
    `per_share_usd` rounds to a NON-ZERO cent.

    The venue reports cash in cents with banker's rounding, so a per-share
    economic quantity smaller than half a cent per placement is reported as
    $0.00 and is unobservable end-to-end however many times it occurs. The
    requirement is STRICT for the same reason the rebate floor is strict:
    ROUND_HALF_EVEN sends exactly $0.005 to $0.00.
    """
    v = D(str(per_share_usd))
    if v <= 0:
        raise ValueError("per_share_usd must be positive")
    q = D(str(grid))
    c = q
    while c <= D("10000"):
        if (c * v).quantize(CENT, rounding=ROUND_HALF_EVEN) > 0:
            return c
        c += q
    return D("NaN")


#: The eight preregistered estimands, each with the quantity floor it needs
#: in order to be observable AT ALL, and why. `floor_shares` is NOT a
#: recommendation; it is the point below which the estimand returns no
#: information regardless of sample size.
ESTIMAND_OBSERVABILITY: Dict[str, Dict[str, object]] = {}


def _build_estimand_observability() -> Dict[str, Dict[str, object]]:
    locked_floor = min_qty_for_cent_observable(MIN_LOCKED_PAIR_GROSS_USD)
    return {
        "1_first_leg_maker_fill_probability": {
            "sample_unit": "placement",
            "floor_shares": str(VENUE_MIN_QTY),
            "why_floor": (
                "needs only the ZERO state and one non-zero state; our own "
                "fill is reported to us by the venue, so the estimand is "
                "resolved at the venue minimum and is size-independent."),
            "states_required": 1,
        },
        "2_complement_completion_given_first_leg_fill": {
            "sample_unit": "one first-leg fill",
            "floor_shares": str(VENUE_MIN_QTY),
            "why_floor": (
                "conditional on a fill existing, completion is a property of "
                "the complement's price and the market's subsequent path, "
                "not of our size at these magnitudes. One non-zero state "
                "suffices to condition on."),
            "states_required": 1,
        },
        "3_partial_fill_structure": {
            "sample_unit": "one first-leg fill",
            "floor_shares": str(min_qty_for_fill_fraction_resolution(0.5)),
            "why_floor": (
                "a DISTRIBUTION over filled fraction needs at least two "
                "non-zero states to separate partial from full at all "
                "(0.02 shares). Finer features need "
                "1/eps states: 0.10 shares for decile resolution, 0.20 for "
                "eps=0.05, 1.00 for eps=0.01. At the venue minimum there is "
                "exactly ONE fill state and the estimand is empty."),
            "states_required": 2,
        },
        "4_time_to_completion_distribution": {
            "sample_unit": "one completion (right-censored at handover)",
            "floor_shares": str(VENUE_MIN_QTY),
            "why_floor": "seconds are seconds; size-independent.",
            "states_required": 1,
        },
        "5_residual_frequency_and_duration": {
            "sample_unit": "one fill (frequency) / one residual (duration)",
            "floor_shares": str(VENUE_MIN_QTY),
            "why_floor": (
                "frequency is 1 - estimand 2 and costs no observation; "
                "duration is seconds and is size-independent."),
            "states_required": 1,
        },
        "6_cancellation_opportunity": {
            "sample_unit": "one cancel attempt (one per placement)",
            "floor_shares": str(min_qty_for_fill_fraction_resolution(0.5)),
            "why_floor": (
                "cancel-succeeded vs fill-won-the-race is binary and needs "
                "one non-zero state. But the DIAGNOSTIC state of a race is a "
                "PARTIAL fill at cancel time, and at the venue minimum a "
                "partially-raced cancel is indistinguishable from a fully "
                "raced one, so 0.02 shares is the floor for the race's "
                "structure rather than only its occurrence."),
            "states_required": 2,
        },
        "7_adverse_selection": {
            "sample_unit": "one first-leg fill",
            "floor_shares": str(VENUE_MIN_QTY),
            "why_floor": (
                "the direction the market moved after our passive leg filled "
                "is a per-share property of the market, measured from the "
                "book feed we already record; size-independent."),
            "states_required": 1,
        },
        "8_settlement_end_to_end": {
            "sample_unit": "one settled market",
            "floor_shares": str(locked_floor),
            "why_floor": (
                "settlement cash is reported in CENTS with banker's "
                "rounding. The locked gross on a pair completed AT the "
                f"frozen pair-cost ceiling is ${MIN_LOCKED_PAIR_GROSS_USD} "
                "per share, so below "
                f"{locked_floor} shares a completed pair's gain rounds to "
                "$0.00 and the end-to-end settlement cashflow cannot be "
                "confirmed as non-zero at all. This floor comes from cent "
                "rounding against a frozen CLAUDE.md pair ceiling, NOT from "
                "expected return."),
            "states_required": 1,
        },
    }


ESTIMAND_OBSERVABILITY = _build_estimand_observability()


def e1_minimum_quantity() -> Dict[str, object]:
    """E1's minimum useful quantity, DERIVED, with every binding constraint.

    The minimum is the maximum of the per-estimand observability floors,
    because E1's job is that all eight estimands return information. It is
    NOT an optimum of anything and nothing about expected return enters.
    """
    floors = {k: D(v["floor_shares"])            # type: ignore[index]
              for k, v in ESTIMAND_OBSERVABILITY.items()}
    binding = max(floors.values())
    binders = sorted(k for k, v in floors.items() if v == binding)
    qty = binding
    return {
        "derived_minimum_shares": str(qty),
        "binding_estimands": binders,
        "per_estimand_floor_shares": {k: str(v) for k, v in floors.items()},
        "distinguishable_fill_states": distinguishable_fill_states(qty),
        "fill_fraction_resolution": fill_fraction_resolution(qty),
        "worst_case_per_placement_usd": str(worst_case_per_first_leg_usd(qty)),
        "rebate_bearing_anywhere": bool(
            maker_rebate_usd(D("0.50"), qty) > 0),
        "not_fitted": True,
        "chosen_for": "observability of all eight estimands",
        "not_chosen_for": "expected return, venue sizing, or L5's own sizing",
        "owner_chooses": True,
    }


#: Reference sizes carried in every dollar table. 0.01/0.10/1.00 are the
#: prior document's grid; the fourth is the REBATE-BEARING size, which is
#: price-dependent and is therefore computed, never assumed to be 1.62.
def reference_sizes(band_lo: D = VENUE_MIN_PRICE,
                    band_hi: D = MAX_FIRST_LEG_PRICE) -> List[D]:
    return [D("0.01"), D("0.10"), D("1.00"),
            min_rebate_bearing_contracts_across_band(band_lo, band_hi)]


# =========================================================================
# PART B -- THE DYNAMIC REBATE FLOOR, from the real formula and real cent
#           rounding. Price-dependent. Never assumed to be 1.62.
# =========================================================================

def _rebate_threshold_contracts(price: D) -> D:
    """Exact real-valued contract count at which the raw rebate equals the
    half-cent rounding boundary: theta * C * p * (1-p) == 0.005.

    Returned as an exact Decimal ratio where the division terminates and to
    full Decimal precision otherwise. The FLOOR is strictly above this
    value, because ROUND_HALF_EVEN sends exactly $0.005 to $0.00 (0 is the
    even digit), so equality earns nothing.
    """
    p = D(str(price))
    if not (D("0") < p < D("1")):
        raise ValueError("price must be in (0,1)")
    return (D("0.005") / MAKER_THETA) / (p * (D(1) - p))


def min_rebate_bearing_contracts(price: D,
                                 grid: D = VENUE_QTY_GRID) -> D:
    """The TRUE minimum rebate-bearing contract count at `price`.

    Exact and closed-form: the smallest multiple of the venue quantity grid
    STRICTLY greater than `_rebate_threshold_contracts(price)`. When the
    threshold itself lands exactly on the grid -- which happens, e.g.
    p = 0.20 gives exactly 2.50 and p = 0.50 gives exactly 1.60 -- strictness
    pushes the floor one grid step up, to 2.51 and 1.61 respectively. Getting
    that case wrong is the single easiest way to place an order that earns
    exactly $0.00 while believing it is rebate-bearing.
    """
    t = _rebate_threshold_contracts(price)
    g = D(str(grid))
    steps = (t / g).to_integral_value(rounding="ROUND_FLOOR")
    c = steps * g
    if c <= t:                      # strict: equality earns $0.00
        c = c + g
    return c.quantize(D("0.01"))


def min_rebate_bearing_contracts_across_band(
        lo: D = VENUE_MIN_PRICE,
        hi: D = MAX_FIRST_LEG_PRICE,
        grid: D = VENUE_QTY_GRID) -> D:
    """Contracts needed to be rebate-bearing at EVERY price on the grid in
    [lo, hi].

    p(1-p) is smallest at the band edge furthest from 0.50, and the floor
    scales as 1/(p(1-p)), so this is the max over the band and it BLOWS UP
    toward the cheap end. "1.91 shares covers p <= 0.60" is only true for a
    band that stops at p = 0.30; the full placeable range reaches down to
    p = 0.01, where the floor is 40.41 contracts.
    """
    lo_d, hi_d, g = D(str(lo)), D(str(hi)), D(str(grid))
    if lo_d > hi_d:
        raise ValueError("lo above hi")
    worst = D("0")
    p = lo_d
    while p <= hi_d:
        c = min_rebate_bearing_contracts(p, g)
        if c > worst:
            worst = c
        p += D("0.01")
    return worst


def rebate_floor_table(prices: Optional[List[D]] = None
                       ) -> List[Dict[str, object]]:
    """The floor, the raw rebate at the floor, and the raw rebate one grid
    step BELOW the floor -- which must always be <= $0.005 and round to zero.
    """
    if prices is None:
        prices = [D("0.01"), D("0.05"), D("0.10"), D("0.20"), D("0.30"),
                  D("0.40"), D("0.45"), D("0.50"), D("0.55"), D("0.60")]
    rows = []
    for p in prices:
        c = min_rebate_bearing_contracts(p)
        below = (c - VENUE_QTY_GRID).quantize(D("0.01"))
        raw_at = MAKER_THETA * c * p * (D(1) - p)
        raw_below = MAKER_THETA * below * p * (D(1) - p)
        rows.append({
            "price": str(p),
            "p_times_1_minus_p": str((p * (D(1) - p)).normalize()),
            "min_rebate_bearing_contracts": str(c),
            "raw_rebate_at_floor_usd": str(raw_at),
            "rounded_rebate_at_floor_usd": str(maker_rebate_usd(p, c)),
            "contracts_one_step_below": str(below),
            "raw_rebate_one_step_below_usd": str(raw_below),
            "rounded_one_step_below_usd": str(maker_rebate_usd(p, below)),
            "threshold_lands_on_grid": bool(
                _rebate_threshold_contracts(p) ==
                _rebate_threshold_contracts(p).quantize(D("0.01"))),
        })
    return rows


def max_maker_rebate_per_share_usd(
        lo: D = VENUE_MIN_PRICE,
        hi: D = MAX_FIRST_LEG_PRICE) -> D:
    """The most a maker rebate can EVER be worth per share on the first-leg
    price band, cent rounding included and taken generously.

    raw/share = theta * p(1-p) <= theta/4 = $0.003125 at p = 0.50. Cent
    rounding can add at most half a cent to the credited total, which is
    worth most per share at the SMALLEST rebate-bearing size. So the generous
    per-share ceiling is max over the band of
    (theta*C*p(1-p) + 0.005) / C at C = the floor.

    This is used only to decide when a measured per-share loss is beyond ANY
    attainable maker rebate's reach. It is a bound, never an expectation, and
    it is never treated as income: per CLAUDE.md a rebate is income only once
    credited and source-accounted.
    """
    lo_d, hi_d = D(str(lo)), D(str(hi))
    best = D("0")
    p = lo_d
    while p <= hi_d:
        c = min_rebate_bearing_contracts(p)
        per_share = (MAKER_THETA * c * p * (D(1) - p) + D("0.005")) / c
        if per_share > best:
            best = per_share
        p += D("0.01")
    return best.quantize(D("0.000001"), rounding=ROUND_HALF_EVEN)


# =========================================================================
# PART C -- E1 STOPPING GATES: eight estimands on the existing machinery
# =========================================================================

#: Precision targets. One row per estimand, a GRID of options, fixed before
#: the first placement. The values are the prior protocol's own grid reused
#: unchanged so no new threshold is invented here.
E1_PRECISION = {
    "proportion_posterior_width": 0.10,
    "quantile_eps": 0.05,
    "per_share_halfwidth_usd": 0.02,
}


def e1_gate_table() -> Dict[str, Dict[str, object]]:
    """Each of the eight estimands: method, unit, n, and what falsifies it."""
    w = E1_PRECISION["proportion_posterior_width"]
    eps = E1_PRECISION["quantile_eps"]
    hw = E1_PRECISION["per_share_halfwidth_usd"]
    sd = per_leg_pnl_sd_usd_per_share(STAGE6_OBSERVED_MATCHED_FRACTION)
    oc = sprt_operating_characteristics(STAGE6_OBSERVED_MATCHED_FRACTION,
                                        STAGE6_BREAKEVEN_MATCHED_FRACTION)
    n_prop = n_for_posterior_width(w)
    return {
        "1_first_leg_maker_fill_probability": {
            "method": "Bayesian Beta(1,1) posterior-width stopping",
            "unit": "placement",
            "n": n_prop,
            "denominated_in": "placements",
            "falsified_by": (
                "a 95% posterior upper bound below the cheapest priced "
                "cascade row (p_fill = 0.10): the fill-denominated estimands "
                "then cost more than any row this design has priced."),
        },
        "2_complement_completion_given_first_leg_fill": {
            "method": ("truncated Wald SPRT, H0 = 0.6772 (Stage-6 observed "
                       "proxy matched fraction) vs H1 = 0.7873 (Stage-6 "
                       "derived break-even). Neither point chosen here."),
            "unit": "first-leg fill",
            "n": n_prop,
            "denominated_in": "fills",
            "expected_n_under_h0": oc["expected_n_under_h0"],
            "expected_n_under_h1": oc["expected_n_under_h1"],
            "expected_n_at_indifference": oc["expected_n_at_indifference"],
            "alpha": oc["alpha"], "beta": oc["beta"],
            "falsified_by": "the walk crossing the H0 boundary at -2.944439",
        },
        "3_partial_fill_structure": {
            "method": ("empirical distribution of filled fraction with "
                       "distribution-free order-statistic quantile CIs, the "
                       "same machinery as the duration estimands. Reported "
                       "as a distribution; no mean is an estimand."),
            "unit": "first-leg fill",
            "n": n_for_quantile(0.50, eps),
            "denominated_in": "fills",
            "falsified_by": (
                "every fill landing in the top fill-fraction state, which "
                "would say partial fills do not occur at our size -- a "
                "positive finding, and the one the venue minimum cannot "
                "produce because at 0.01 shares that state is the only one."),
        },
        "4_time_to_completion_distribution": {
            "method": ("Kaplan-Meier with distribution-free order-statistic "
                       "quantile intervals; right-censored at the frozen "
                       "handover TTE, never imputed. No mean is reported."),
            "unit": "completion",
            "n": n_for_quantile(0.90, eps),
            "denominated_in": "completions",
            "fills_needed": math.ceil(n_for_quantile(0.90, eps)
                                      / STAGE6_OBSERVED_MATCHED_FRACTION),
            "falsified_by": (
                "censoring so heavy that the p90 sits above the censoring "
                "point, in which case it is NOT IDENTIFIED and is reported "
                "as such rather than modelled through."),
        },
        "5_residual_frequency_and_duration": {
            "method": ("frequency is 1 - estimand 2 on the same "
                       "observations and costs nothing extra; duration uses "
                       "the Kaplan-Meier quantile CI."),
            "unit": "fill (frequency) / residual (duration)",
            "n": n_for_quantile(0.90, eps),
            "denominated_in": "residuals",
            "fills_needed": math.ceil(
                n_for_quantile(0.90, eps)
                / (1 - STAGE6_OBSERVED_MATCHED_FRACTION)),
            "falsified_by": (
                "residual frequency incompatible with 1 - estimand 2, which "
                "would mean the inventory ledger and the fill ledger "
                "disagree -- an instrument fault, not a result."),
        },
        "6_cancellation_opportunity": {
            "method": ("Beta(1,1) posterior on P(cancel lands before any "
                       "fill | cancel attempted), read off S6P's existing "
                       "CANCEL_FIRST_LEG -> POST_CANCEL_SWEEP_FIRST_LEG "
                       "event pair, which already counts a fill that raced "
                       "the cancel. Race latency reported as a distribution "
                       "on the same quantile machinery."),
            "unit": "cancel attempt",
            "n": n_prop,
            "denominated_in": "placements",
            "extra_cost": ("ZERO. The frozen machine issues exactly one "
                           "cancel attempt per placement, so this estimand "
                           "is free in dollars and in placements."),
            "falsified_by": (
                "any post-cancel sweep returning a fill with a venue "
                "timestamp AFTER the cancel acknowledgement, which would "
                "mean cancellation does not bound exposure and would "
                "invalidate the worst-case arithmetic rather than merely "
                "estimate a rate."),
        },
        "7_adverse_selection": {
            "method": ("Beta(1,1) posterior on P(midpoint of the token we "
                       "bought is lower at t+h than at our fill | our "
                       "passive leg filled), plus the signed move as a "
                       "distribution. Horizons h preregistered as a fixed "
                       "set, not searched: the frozen handover TTE and the "
                       "window's own geometry supply them."),
            "unit": "first-leg fill",
            "n": n_prop,
            "denominated_in": "fills",
            "historical_prior_NOT_inherited": (
                f"the historical lane's provable-residual loss rate of "
                f"{STAGE6_PROVABLE_RESIDUAL_LOSS_RATE} is a demonstration "
                "that the proxy's fill rule and outcome rule are biased in "
                "the SAME direction. It is not a forecast and is NOT used "
                "as a prior, a hypothesis point or a boundary here."),
            "falsified_by": (
                "a 95% posterior interval straddling 0.50 with the signed "
                "move's interval straddling zero, i.e. our passive fills "
                "are not systematically on the wrong side."),
        },
        "8_settlement_end_to_end": {
            "method": ("settlement reconciliation through the frozen "
                       "analysis/l5/stage6/settlement.py lifecycle "
                       "SUBMITTED -> RESTING -> FILLED -> SETTLED -> "
                       "REALIZED. Nothing is labelled REALIZED without a "
                       "settlement record and confirmed cash; per-share "
                       "economics by normal CI over the matched/residual "
                       "mixture."),
            "unit": "settled market",
            "n": n_for_mean_halfwidth(sd, hw),
            "denominated_in": "fills",
            "sd_usd_per_share": sd,
            "falsified_by": (
                "a settlement record whose confirmed cash disagrees with "
                "the fill ledger, or any cashflow that cannot be attributed "
                "to an identified source -- either of which is reported as "
                "an instrument fault and NOT as economics."),
        },
    }


def e1_binding_gates() -> Dict[str, int]:
    """The placement- and fill-denominated requirements, and the binding one."""
    g = e1_gate_table()
    placement_gates = {k: int(v["n"]) for k, v in g.items()           # type: ignore[index]
                       if v["denominated_in"] == "placements"}
    fill_gates: Dict[str, int] = {}
    for k, v in g.items():
        if v["denominated_in"] == "fills":
            fill_gates[k] = int(v["n"])                               # type: ignore[index]
        elif v["denominated_in"] in ("completions", "residuals"):
            fill_gates[k] = int(v["fills_needed"])                    # type: ignore[index]
    return {
        "placement_gate": max(placement_gates.values()),
        "fill_gate": max(fill_gates.values()),
        "binding_fill_estimand": max(fill_gates, key=lambda k: fill_gates[k]),
        "per_gate_placements": placement_gates,
        "per_gate_fills": fill_gates,
    }


def e1_placements(p_fill: float) -> int:
    """Placements to clear every E1 gate, as a function of the unknown p_fill."""
    b = e1_binding_gates()
    return max(b["placement_gate"],
               cascade_placements(b["fill_gate"], p_fill))


#: The hard truncation. Not a new number: it is the placement count of the
#: CHEAPEST p_fill row this design prices, so the instrument cannot run
#: beyond arithmetic that has been shown to the owner.
P_FILL_GRID = [0.10, 0.25, 0.50, 0.87]


def e1_hard_truncation_placements() -> int:
    return e1_placements(min(P_FILL_GRID))


# ----------------------------------------------------------- the boundaries

def beta_ci(k: int, n: int, level: float = 0.95) -> Tuple[float, float]:
    """Equal-tailed credible interval for Beta(1+k, 1+n-k).

    Uses the exact Beta quantile only where it is available in closed form
    without a special-function library: the two boundary cases k = 0 and
    k = n, which are the ones the stopping rules actually turn on, reduce to
    a power. Interior cases fall back to the normal approximation on the
    posterior mean, which is what `n_for_posterior_width` already assumes, so
    the two are consistent by construction.
    """
    if not 0 <= k <= n or n <= 0:
        raise ValueError("require 0 <= k <= n, n > 0")
    tail = (1.0 - level) / 2.0
    if k == 0:
        return (0.0, 1.0 - tail ** (1.0 / (n + 1)))
    if k == n:
        return (tail ** (1.0 / (n + 1)), 1.0)
    m = (1.0 + k) / (2.0 + n)
    half = Z95 * math.sqrt(m * (1 - m) / (n + 3))
    return (max(0.0, m - half), min(1.0, m + half))


def n_consecutive_for_upper_bound(bound: float, level: float = 0.95) -> int:
    """Consecutive zero-success observations until the 95% posterior upper
    bound falls below `bound`. Closed form from `beta_ci`'s k = 0 case."""
    if not 0 < bound < 1:
        raise ValueError("bound must be in (0,1)")
    tail = (1.0 - level) / 2.0
    n = 1
    while n <= 100000:
        if 1.0 - tail ** (1.0 / (n + 1)) < bound:
            return n
        n += 1
    raise RuntimeError("no n found")


#: FAIL boundary for estimand 1, DERIVED not chosen: below this the
#: fill-denominated estimands cost more placements than the cheapest row
#: this design prices, so E1 cannot complete within its own truncation.
E1_FAIL_P_FILL_UPPER = min(P_FILL_GRID)


def e1_fail_boundary_consecutive_misses() -> int:
    """Consecutive no-fill placements that alone trigger the estimand-1 FAIL."""
    return n_consecutive_for_upper_bound(E1_FAIL_P_FILL_UPPER)


def e1_economics_foreclosure_usd_per_share() -> D:
    """The per-share expectancy below which NO attainable maker rebate can
    make the sequence non-negative, so E2 cannot be justified by economics.

    Derived from Part B's generous per-share rebate ceiling. Negated because
    the boundary is a loss.
    """
    return -max_maker_rebate_per_share_usd()


def e1_decision_rules() -> Dict[str, object]:
    """PASS / FAIL / CONTINUE, all three, with numeric boundaries and the
    error rate attached to each component.

    PASS means E2 IS JUSTIFIED. It does not mean profitable, and it is not an
    authorization: the owner authorizes, and only after E1 is frozen.
    """
    b = e1_binding_gates()
    oc = sprt_operating_characteristics(STAGE6_OBSERVED_MATCHED_FRACTION,
                                        STAGE6_BREAKEVEN_MATCHED_FRACTION)
    trunc = e1_hard_truncation_placements()
    forec = e1_economics_foreclosure_usd_per_share()
    return {
        "PASS": {
            "meaning": "E2 IS JUSTIFIED. Not an authorization, not a profit claim.",
            "all_of": [
                f"every one of the eight estimands reached its preregistered "
                f"precision: {b['placement_gate']} placements AND "
                f"{b['fill_gate']} fills",
                f"estimand 1's 95% posterior interval excludes "
                f"p_fill <= {E1_FAIL_P_FILL_UPPER}, i.e. the lower credible "
                f"bound is above {E1_FAIL_P_FILL_UPPER}",
                "estimand 6 recorded ZERO post-cancel-acknowledgement fills, "
                "i.e. cancellation provably bounds exposure",
                "estimand 8 reconciled every settled market against the fill "
                "ledger with no unattributed cashflow",
                f"estimand 8's per-share expectancy 95% UPPER bound is above "
                f"${forec} per share, i.e. the measured economics are not "
                f"beyond the reach of any attainable maker rebate",
            ],
            "error_rate": (
                "each component is a 95% interval or an alpha = 0.05 test. "
                "A false PASS requires every component to mislead jointly, "
                "so its probability is below the smallest component's "
                "0.05; the conservative union bound on ANY single component "
                "misleading is 5 x 0.05 = 0.25 and is reported rather than "
                "hidden. No multiplicity correction is applied ACROSS the "
                "eight estimands because they are eight separate questions "
                "with eight separate reports, not one hypothesis tested "
                "eight ways."),
        },
        "FAIL": {
            "meaning": ("STOP. E2 is not reachable, and the finding is "
                        "itself the result."),
            "any_of": [
                {
                    "rule": (f"estimand 1's 95% posterior UPPER bound on "
                             f"p_fill falls below {E1_FAIL_P_FILL_UPPER}"),
                    "numeric": (f"triggered outright by "
                                f"{e1_fail_boundary_consecutive_misses()} "
                                f"consecutive no-fill placements"),
                    "learned": ("a passive first leg does not fill at our "
                                "price under this selector. That answers the "
                                "question Stage-6 could not, negatively."),
                    "error_rate": "2.5% one-sided (95% equal-tailed interval)",
                },
                {
                    "rule": ("the estimand-2 SPRT walk crosses the H0 "
                             "boundary AND estimand 8's per-share "
                             "expectancy 95% upper bound is below "
                             f"${forec}"),
                    "numeric": (f"log-LR <= {oc['log_b']:.6f}; triggered "
                                f"outright by 8 consecutive non-completions "
                                f"(8 x {oc['log_lr_failure']:.6f} = "
                                f"{8 * oc['log_lr_failure']:.6f})"),
                    "learned": ("completion sits at or below the historical "
                                "proxy level and the per-share loss exceeds "
                                "the arithmetic ceiling on any maker rebate, "
                                "so the rebate-bearing cohort cannot close "
                                "the gap and need not be run."),
                    "error_rate": f"beta = {oc['beta']} for the SPRT "
                                  "component, 2.5% one-sided for the "
                                  "economics component",
                },
                {
                    "rule": ("estimand 6 records ANY fill whose venue "
                             "timestamp is after the cancel acknowledgement"),
                    "numeric": "n = 1 occurrence",
                    "learned": ("cancellation does not bound exposure, so "
                                "the worst-case arithmetic every bound in "
                                "analysis/l5/stage6/bounds.py rests on is "
                                "invalid. This is a HARD stop and it "
                                "invalidates the design, not just the run."),
                    "error_rate": ("none -- this is an observation of a "
                                   "structural fact, not a test"),
                },
                {
                    "rule": ("estimand 8 reports any cashflow without "
                             "sufficient source identity"),
                    "numeric": "n = 1 occurrence",
                    "learned": ("the settlement ledger cannot be trusted, "
                                "so nothing may be labelled REALIZED. "
                                "Reported as an instrument fault."),
                    "error_rate": "none -- an observation, not a test",
                },
            ],
        },
        "CONTINUE": {
            "meaning": "neither PASS nor FAIL yet; keep measuring.",
            "hard_truncation_placements": trunc,
            "why_that_number": (
                "it is the placement count of the CHEAPEST p_fill row this "
                "design prices (p_fill = "
                f"{min(P_FILL_GRID)}), so the instrument cannot run beyond "
                "arithmetic the owner has already been shown. It is not a "
                "new threshold."),
            "on_truncation": (
                "verdict INCONCLUSIVE, which is NEITHER PASS NOR FAIL. "
                "Report every posterior and every censored observation as "
                "they stand. E2 remains unauthorizable. An INCONCLUSIVE "
                "truncation still bounds how far the truth can be from each "
                "hypothesis and is decision-useful."),
            "cannot_run_forever": True,
        },
        "ABORT_IS_NOT_A_VERDICT": (
            "a cumulative-loss abort is a FAILURE TO MEASURE, reported as "
            "such with the partial posteriors, and never reported as FAIL or "
            "as a result. The cumulative bound itself is an OWNER VALUE and "
            "is deliberately not set here."),
    }


# =========================================================================
# PART D -- WORST-CASE DOLLARS
# =========================================================================

def worst_case_table(sizes: Optional[List[D]] = None
                     ) -> List[Dict[str, object]]:
    """Worst case = placements x qty x $0.60, i.e. every placement filling in
    full at the frozen price ceiling and every complement never filling, the
    residual settling to zero. A BOUND, not a forecast: it is 1/p_complete
    times more pessimistic than any plausible path."""
    if sizes is None:
        sizes = reference_sizes()
    b = e1_binding_gates()
    rows = []
    for qty in sizes:
        per = worst_case_per_first_leg_usd(qty)
        row: Dict[str, object] = {
            "qty_shares": str(qty),
            "worst_case_per_placement_usd": str(per),
            "worst_case_per_session_usd": str(per),  # MAX_FIRST_LEGS = 1
            "distinguishable_fill_states": distinguishable_fill_states(qty),
            "fill_fraction_resolution": fill_fraction_resolution(qty),
            "rebate_bearing_at_p_0.50":
                bool(maker_rebate_usd(D("0.50"), qty) > 0),
            "estimand_1_only_placements": b["placement_gate"],
            "estimand_1_only_worst_case_usd":
                str((per * b["placement_gate"]).quantize(CENT)),
            "all_gates_fills_needed": b["fill_gate"],
        }
        for pf in P_FILL_GRID:
            n = e1_placements(pf)
            row[f"placements_at_p_fill_{pf}"] = n
            row[f"all_gates_worst_case_usd_at_p_fill_{pf}"] = \
                str((per * n).quantize(CENT))
        rows.append(row)
    return rows


# =========================================================================
# PART E -- E2, STRUCTURALLY GATED ON AN E1 PASS
# =========================================================================

class E2NotAuthorized(RuntimeError):
    """Raised whenever an E2 entry point is called without an E1 PASS."""


def _require_e1_pass(e1_verdict: str) -> None:
    if e1_verdict != "PASS":
        raise E2NotAuthorized(
            "E2 DOES NOT EXIST AS AN AUTHORIZABLE ARTIFACT unless cohort E1 "
            f"returns PASS. Received e1_verdict={e1_verdict!r}. An "
            "INCONCLUSIVE truncation is not a PASS, an ABORT is not a PASS, "
            "and a PASS is still not an authorization -- the owner "
            "authorizes, after E1 is frozen and they approve it.")


#: E2's seven economic lines. They are NEVER summed into one number while
#: any line is UNMEASURED, and maker rebates, taker rebates and rewards are
#: separate lines from trading P&L (CLAUDE.md).
E2_ECONOMIC_LINES = (
    "matched_pair_pnl",
    "directional_residual_pnl",
    "spread_capture",
    "maker_rebates_CREDITED_ONLY",
    "liquidity_rewards_CREDITED_ONLY",
    "fees",
    "settlement_cashflow",
)


def e2_quantity(price: D, *, e1_verdict: str,
                band_lo: D = VENUE_MIN_PRICE,
                band_hi: D = MAX_FIRST_LEG_PRICE) -> Dict[str, object]:
    """E2's rebate-bearing quantity, computed from the LIVE price, plus the
    quantity needed to be rebate-bearing across the whole first-leg band."""
    _require_e1_pass(e1_verdict)
    at_price = min_rebate_bearing_contracts(price)
    across = min_rebate_bearing_contracts_across_band(band_lo, band_hi)
    return {
        "price": str(price),
        "min_rebate_bearing_contracts_at_this_price": str(at_price),
        "worst_case_per_placement_at_this_price_usd":
            str(worst_case_per_first_leg_usd(at_price)),
        "band": [str(band_lo), str(band_hi)],
        "min_rebate_bearing_contracts_across_band": str(across),
        "worst_case_per_placement_across_band_usd":
            str(worst_case_per_first_leg_usd(across)),
        "assumed_1_62": False,
        "note": ("1.62 is the floor at p = 0.45 and p = 0.55 ONLY. It is "
                 "1.61 at p = 0.50, 1.67 at p = 0.60, 1.91 at p = 0.30 and "
                 "40.41 at p = 0.01. Any single hard-coded figure is wrong "
                 "almost everywhere on the band."),
    }


def e2_gate_table(*, e1_verdict: str) -> Dict[str, Dict[str, object]]:
    """E2's own stopping gates. Credited-only throughout."""
    _require_e1_pass(e1_verdict)
    w = E1_PRECISION["proportion_posterior_width"]
    hw = E1_PRECISION["per_share_halfwidth_usd"]
    sd = per_leg_pnl_sd_usd_per_share(STAGE6_OBSERVED_MATCHED_FRACTION)
    return {
        "R1_credit_occurrence": {
            "estimand": ("P(the venue CREDITS a maker rebate | our maker "
                         "fill at or above the price-dependent floor), read "
                         "as a NEGATIVE commissionNotionalCollected on our "
                         "own execution per "
                         "analysis/l5/stage6/settlement.py::"
                         "credited_rebates_usd. An ABSENT field is absent, "
                         "never read as zero credited."),
            "method": "Beta(1,1) posterior width",
            "unit": "qualifying maker fill",
            "n": n_for_posterior_width(w),
            "fail_rule": (f"{n_consecutive_for_upper_bound(0.10)} "
                          "consecutive qualifying fills with no credit puts "
                          "the 95% posterior upper bound below 0.10: the "
                          "rebate is not being credited to this account and "
                          "every rebate figure stays ESTIMATED forever."),
        },
        "R2_credited_amount_per_share": {
            "estimand": ("mean CREDITED maker rebate per share, and its "
                         "agreement with the published formula"),
            "method": "normal CI on the per-share mean",
            "unit": "qualifying maker fill",
            "n": n_for_mean_halfwidth(float(max_maker_rebate_per_share_usd()),
                                      0.0005),
            "falsified_by": ("credited amounts disagreeing with "
                             "round_half_even(0.0125*C*p*(1-p), $0.01), "
                             "which would mean the frozen fee schedule no "
                             "longer describes the venue and must be "
                             "re-captured and re-fingerprinted."),
        },
        "R3_seven_line_economics": {
            "estimand": ("the seven lines, each with its own interval: "
                         + ", ".join(E2_ECONOMIC_LINES)),
            "method": ("per-line normal CI; matched and residual P&L on the "
                       "same two-point mixture machinery as E1"),
            "unit": "settled market",
            "n": n_for_mean_halfwidth(sd, hw),
            "summation_rule": ("NEVER summed into one number while any line "
                               "is UNMEASURED. A line with no observations "
                               "is reported UNMEASURED, not zero."),
            "falsified_by": ("any line that cannot be attributed to a "
                             "venue-sourced record"),
        },
        "R4_taker_cost": {
            "estimand": ("taker cost actually charged on any crossing leg, "
                         f"priced at theta = {TAKER_THETA} effective "
                         "2026-09-17T04:00Z"),
            "method": "direct accounting against the frozen schedule",
            "unit": "crossing fill",
            "n": 0,
            "notes": ("the taker coefficient has NO cent floor and NO size "
                      "threshold. Fills in the 2026-09-17 -> 2026-10-01 "
                      "window are DATE-AMBIGUOUS per the schedule's recorded "
                      "source conflict, must be rated at the higher "
                      "coefficient and FLAGGED, never quietly rated. Taker "
                      "REBATE tiers are "
                      "taker_rebate_available_to_this_account: False and may "
                      "not be assumed at any size."),
        },
    }


def e2_decision_rules(*, e1_verdict: str) -> Dict[str, object]:
    _require_e1_pass(e1_verdict)
    g = e2_gate_table(e1_verdict=e1_verdict)
    return {
        "PASS": {
            "meaning": ("credited maker economics are measured, and the "
                        "seven lines are each measured. Evidence for a "
                        "scaling decision, NOT a scaling authorization."),
            "all_of": [
                f"R1 reached {g['R1_credit_occurrence']['n']} qualifying "
                "fills with its 95% lower bound above 0.90",
                "R2's credited amounts reproduce the published formula",
                f"all seven lines measured at "
                f"{g['R3_seven_line_economics']['n']} settled markets, none "
                "UNMEASURED",
                "R4 charged no taker cost outside the frozen schedule",
            ],
            "error_rate": ("95% intervals throughout; conservative union "
                           "bound on any single component misleading "
                           "4 x 0.05 = 0.20"),
        },
        "FAIL": {
            "meaning": "STOP. Credited maker economics do not exist for us.",
            "any_of": [
                g["R1_credit_occurrence"]["fail_rule"],
                "R2 disagrees with the published formula (schedule stale)",
                "any of the seven lines is unattributable to a venue record",
            ],
            "error_rate": "2.5% one-sided per interval component",
        },
        "CONTINUE": {
            "hard_truncation_placements": e1_hard_truncation_placements(),
            "on_truncation": ("INCONCLUSIVE. Rebates stay ESTIMATED, kept "
                              "in a separate account from trading P&L, and "
                              "never netted against a cost."),
        },
        "ABORT_IS_NOT_A_VERDICT": True,
    }


def e2_worst_case_table(*, e1_verdict: str,
                        sizes: Optional[List[D]] = None
                        ) -> List[Dict[str, object]]:
    _require_e1_pass(e1_verdict)
    if sizes is None:
        sizes = [
            min_rebate_bearing_contracts(D("0.50")),
            min_rebate_bearing_contracts(D("0.45")),
            min_rebate_bearing_contracts(D("0.30")),
            min_rebate_bearing_contracts_across_band(D("0.20"), D("0.60")),
            min_rebate_bearing_contracts_across_band(D("0.10"), D("0.60")),
            min_rebate_bearing_contracts_across_band(),
        ]
    return worst_case_table(sizes)


if __name__ == "__main__":  # pragma: no cover
    import json
    print("NOT AUTHORIZED / NOT ARMED -- arithmetic only\n")
    print("E1 minimum quantity, derived:")
    print(json.dumps(e1_minimum_quantity(), indent=2, default=str))
    print("\nE1 binding gates:")
    print(json.dumps(e1_binding_gates(), indent=2, default=str))
    print("\nE1 decision rules:")
    print(json.dumps(e1_decision_rules(), indent=2, default=str))
    print("\nE1 worst-case dollars:")
    print(json.dumps(worst_case_table(), indent=2, default=str))
    print("\nrebate floor across the band:")
    print(json.dumps(rebate_floor_table(), indent=2, default=str))
    print(f"\nacross p in [0.01, 0.60]: "
          f"{min_rebate_bearing_contracts_across_band()} contracts")
    print(f"max maker rebate per share: "
          f"${max_maker_rebate_per_share_usd()}")
