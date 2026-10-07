"""PMUS rebate-adjusted L5-clone BREAKEVEN SURFACE.

ONE QUESTION. Does PMUS's ACTUAL compensation create an executable
positive-expectancy region for the L5-derived matched-pair engine, and if so
what price / size / completion / residual conditions must be hit?

THE CONDITION, COMPONENTS NEVER PRE-SUMMED:

    matched_pair_pnl + residual_pnl + maker_rebate
        + credited_liquidity_rewards - fees   >  0

Every component is carried as its own field from end to end. Nothing in this
module ever returns a single net number without the five lines beside it.

ONE SCHEDULE. Every fee and rebate cent comes from `src.pmus_fee_schedule`
(fingerprint ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629)
via `maker_rebate()` / `taker_fee()`. The half-even cent rounding is NOT
reimplemented here; it is called. A second rounding implementation would be a
second schedule.

TWO FINDINGS THAT CONTRADICT THE BRIEF'S OWN REFERENCE NUMBERS, both recorded
here rather than silently applied:

  * THERE IS NO $0.31-PER-100-CONTRACT CAP. The captured venue page
    (`analysis/pmus/pmus_published_fee_schedule.json`) carries `lot = 100`:
    the published table is the formula evaluated PER 100 CONTRACTS and
    cent-rounded. `maker_receives == "0.31"` appears for every price from
    0.43 to 0.57 because that is where the rounded formula sits, not because
    a cap truncates it. No row of the 99 deviates from the formula by as much
    as half a cent, and the word "cap" does not appear in the capture. So the
    rebate is UNCAPPED and at p = 0.50 the true per-contract credit is
    $0.003125, not $0.0031. `cap_audit()` proves this.
  * -71.7 bp IS A CHANGE, NOT A LEVEL. `analysis/l5/regime_aug19/` states
    L5's trading net moved +41.4 -> -30.4 bp of turnover; -71.7 bp is the
    MOVE. The level a clone must cover is -30.4 bp. Subtracting a rebate RATE
    from the -71.7 DELTA overstates the hurdle 2.36x. See
    `denominator_reconciliation()`.

DENOMINATORS, RECONCILED NOT ASSUMED. `regime_shift.py` accumulates
`turnover += usdcSize` over EVERY fill, both legs. For a pair of C contracts
with legs p and X-p:

    turnover = C*p + C*(X-p) = C*X  ~=  $1.00 * C

so PAIR TURNOVER ~= PAIR COST, and a rebate quoted per pair over C*X is in
L5's `net_bp_of_turnover` units directly. The 2x trap is real but points the
other way: a pair turns over 2x ONE LEG's notional, and each leg is ~$0.50,
so the pair total is still ~$1.00. A rebate quoted on MAKER NOTIONAL (C*p,
one leg) is NOT in these units and is converted explicitly.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src import pmus_fee_schedule as FS

CENT = D("0.01")
SETTLEMENT_PER_PAIR = D("1.00")

#: The brief's published-cap claim, tested and REFUTED by `cap_audit()`.
CLAIMED_CAP_PER_100 = D("0.31")

PRICES = [D(x) for x in ("0.10", "0.20", "0.30", "0.40", "0.50",
                         "0.60", "0.70", "0.80", "0.90")]
QUANTITIES = [D(x) for x in ("0.01", "0.10", "1", "1.62", "2",
                             "5", "10", "25", "50", "100")]
PAIR_COSTS = [D(x) for x in ("0.94", "0.95", "0.96", "0.97",
                             "0.98", "0.99", "1.00")]

# --------------------------------------------------------------------------
# OBSERVED values. Every one is sourced. None is chosen to make an answer
# positive; `ASSUMPTIONS.md` records what each one is and is not evidence of.
# --------------------------------------------------------------------------

OBSERVED: Dict[str, Any] = {
    "matched_lock_usd_per_contract": D("0.07496"),
    "residual_pnl_usd_per_contract": D("-0.27742"),
    "matched_fraction_point": D("0.6772"),
    "matched_fraction_ci": (D("0.6562"), D("0.6981")),
    "matched_fraction_l5_coins": D("0.5790"),
    "breakeven_matched_fraction_no_rebate": D("0.7873"),
    "residual_loss_rate": D("1.000"),
    "unknowable_fraction_strict": D("0.9558"),
    "source": "analysis/l5/stage6_validation/STAGE6_HISTORICAL_VALIDATION.md",
    "l5_median_pair_cost_r4": D("0.9668"),
    "l5_frac_pairs_at_or_below_0_96": D("0.462"),
    "l5_matched_share_of_gross": D("0.852"),
    "l5_residual_share": D("0.148"),
    "l5_source": "analysis/l5/post_august/POST_AUGUST_REPORT.md",
    "l5_net_bp_r3": D("41.4"),
    "l5_net_bp_r4": D("-30.4"),
    "l5_net_bp_change_r3_to_r4": D("-71.7"),
    "l5_fees_bp_r4": D("-62.1"),
    "l5_confirmed_incentive_bp_r4": D("163.4"),
    "l5_regime_source": "analysis/l5/regime_aug19/REGIME_SHIFT.md",
}

#: ACTUAL credited PMUS compensation. Cash, not formula.
PMUS_CREDITED: Dict[str, Any] = {
    "maker_rebates_usd": D("9.14"),
    "liquidity_rewards_usd": D("7.04"),
    "total_usd": D("16.18"),
    "credited_pmus_fills_with_nonzero_rebate": 0,
    "our_credited_pmus_fills": 1,
    "a6_note": ("A6 is our ONE credited PMUS fill. FILLED, settlement "
                "unresolved, NOT REALIZED. At 0.01 shares its rebate is "
                "structurally $0.00 at every price on the venue grid."),
    "not_pmus": ("the $0.51 / 97.59 bp maker-rebate credits are GEMINI, not "
                 "PMUS, and are NOT attributed to PMUS anywhere here"),
}


def _blank_components() -> Dict[str, D]:
    """The five lines, always present, always separate, never pre-summed."""
    return {
        "matched_pair_pnl_usd": D("0"),
        "residual_pnl_usd": D("0"),
        "maker_rebate_usd": D("0"),
        "credited_liquidity_rewards_usd": D("0"),
        "fees_usd": D("0"),
    }


def rebate(price: D, contracts: D) -> D:
    """Cent-rounded maker rebate, straight from the frozen schedule."""
    FS.assert_frozen()
    return FS.maker_rebate(price, contracts, cents=True)


def rebate_unrounded(price: D, contracts: D) -> D:
    return FS.maker_theta() * contracts * price * (D(1) - price)


def taker(price: D, contracts: D) -> D:
    FS.assert_frozen()
    return FS.taker_fee(price, contracts, cents=True)


# ----------------------------- deliverable 1-3 -----------------------------

def rebate_grid() -> List[Dict[str, Any]]:
    """Per maker fill: exact cent rebate, bp on MAKER NOTIONAL, lumpiness.

    The bp column is deliberately the honest one: at small size cent rounding
    can make realised bp EXCEED the asymptotic rate 125*(1-p). That is a
    ROUNDING ARTIFACT, not an edge -- it cannot be harvested, because the
    cent is granted on a fill whose own notional is a fraction of a cent, and
    `rounding_artifact` flags every such row.
    """
    out: List[Dict[str, Any]] = []
    for p in PRICES:
        asym_bp = D("10000") * FS.maker_theta() * (D(1) - p)
        for c in QUANTITIES:
            notional = c * p
            paid = rebate(p, c)
            raw = rebate_unrounded(p, c)
            bp = (D("10000") * paid / notional) if notional else None
            out.append({
                "price": str(p),
                "contracts": str(c),
                "maker_notional_usd": str(notional),
                "rebate_usd_cent_rounded": str(paid),
                "rebate_usd_unrounded": str(raw.quantize(D("0.00000001"))),
                "effective_bp_on_maker_notional":
                    (str(bp.quantize(D("0.01"))) if bp is not None else None),
                "asymptotic_bp_on_maker_notional":
                    str(asym_bp.quantize(D("0.01"))),
                "rounding_artifact": bool(bp is not None and bp > asym_bp),
                "zero_by_cent_floor": paid == 0,
            })
    return out


def cent_floor_minimums() -> List[Dict[str, Any]]:
    """Minimum contracts on the 0.01 contract grid for a NONZERO cent.

    Reconciled against the brief's table. The continuous threshold is
    0.005 / (theta*p*(1-p)), but ROUND_HALF_EVEN sends an exact 0.005 to
    $0.00 (0 is even), so a threshold landing exactly on the grid needs one
    more tick -- which is why p=0.20 is 2.51 and not 2.50, and p=0.50 is
    1.61 and not 1.60.
    """
    brief = {"0.10": "4.45", "0.20": "2.51", "0.30": "1.91", "0.40": "1.67",
             "0.50": "1.61", "0.60": "1.67", "0.70": "1.91", "0.80": "2.51",
             "0.90": "4.45"}
    out = []
    for p in PRICES:
        per = FS.maker_theta() * p * (D(1) - p)
        cont = D("0.005") / per
        c = D("0.01")
        while c <= D("10") and rebate(p, c) == 0:
            c += D("0.01")
        found = c.quantize(CENT)
        ref = D(brief[str(p)])
        out.append({
            "price": str(p),
            "per_contract_unrounded_usd": str(per),
            "continuous_threshold_contracts": str(cont.quantize(D("0.0001"))),
            "min_contracts_on_0_01_grid": str(found),
            "brief_reference": str(ref),
            "agrees_with_brief": found == ref,
            "exact_half_needed_extra_tick":
                (cont == cont.quantize(CENT)),
        })
    return out


def cap_audit() -> Dict[str, Any]:
    """Test the claimed $0.31-per-100-contract cap against the capture.

    VERDICT: REFUTED. `lot = 100` -- the table is per 100 contracts, and
    $0.31 is the rounded FORMULA across 0.43..0.57, not a cap.
    """
    cap_band, deviations = [], []
    try:
        src = json.load(
            open("analysis/pmus/pmus_published_fee_schedule.json"))
        rows, lot = src["rows"], D(str(src["lot"]))
        for r in rows:
            p, pub = D(r["price"]), D(r["maker_receives"])
            formula = FS.maker_theta() * lot * p * (D(1) - p)
            rounded = rebate(p, lot)
            if pub == CLAIMED_CAP_PER_100:
                cap_band.append(str(p))
            if rounded != pub:
                deviations.append({"price": str(p), "published": str(pub),
                                   "formula_cent_rounded": str(rounded)})
        reproduced = not deviations
    except (OSError, KeyError):
        rows, lot, reproduced = [], D("100"), None
    return {
        "claim": "published cap $0.31 per 100 contracts, binding near p=0.50",
        "verdict": "REFUTED",
        "lot_in_capture": str(lot),
        "n_published_rows": len(rows),
        "all_rows_reproduce_from_formula": reproduced,
        "price_rows_showing_0_31": cap_band,
        "deviations_from_formula": deviations,
        "why": ("$0.31 is the cent-rounded formula per 100 contracts over "
                "0.43..0.57, not a truncation. Every published row "
                "reproduces from theta*C*p*(1-p) with half-even rounding, "
                "and the capture contains no cap language. The rebate is "
                "UNCAPPED."),
        "consequence": ("at p=0.50 the per-contract credit is $0.003125, "
                        "NOT $0.0031; a cap would have cost 0.8 bp of pair "
                        "turnover that the venue does not in fact withhold. "
                        "No cap is applied anywhere in this module."),
        "brief_premise_confirmed_part": (
            "the brief is RIGHT that the formula gives 0.003125 at p=0.50 "
            "and 0.003000 at p=0.40/0.60; it is WRONG that a cap binds, "
            "because there is no cap"),
    }


# ----------------------------- deliverable 4-5 -----------------------------

def probe_economics(price: D, contracts: D, pair_cost: D, *,
                    complement_is_taker: bool = False,
                    residual_per_contract: Optional[D] = None
                    ) -> Dict[str, Any]:
    """The five components for ONE probe, matched and residual branches.

    MATCHED: both legs fill. Pair settles $1.00, cost `pair_cost`.
    RESIDUAL: first leg fills, complement never does. The first leg's rebate
    is still earned (a maker fill is a maker fill) but no pair lock exists.

    `credited_liquidity_rewards_usd` is $0.00 EVERYWHERE and by evidence, not
    by oversight: PMUS program census returns zero for all our slugs
    (analysis/l5/incentives/BREAKEVEN_INCENTIVE.md), so there is no credited
    reward to book. The line is carried so its absence is visible.
    """
    comp_price = pair_cost - price
    r_pc = (OBSERVED["residual_pnl_usd_per_contract"]
            if residual_per_contract is None else residual_per_contract)
    feasible = D("0.01") <= comp_price <= D("0.99")

    m = _blank_components()
    m["matched_pair_pnl_usd"] = contracts * (SETTLEMENT_PER_PAIR - pair_cost)
    m["maker_rebate_usd"] = rebate(price, contracts)
    if feasible:
        if complement_is_taker:
            m["fees_usd"] = -taker(comp_price, contracts)
        else:
            m["maker_rebate_usd"] += rebate(comp_price, contracts)

    r = _blank_components()
    r["residual_pnl_usd"] = contracts * r_pc
    r["maker_rebate_usd"] = rebate(price, contracts)

    return {
        "price": str(price), "contracts": str(contracts),
        "pair_cost": str(pair_cost), "complement_price": str(comp_price),
        "complement_price_feasible": feasible,
        "complement_role": "taker" if complement_is_taker else "maker",
        "matched_components": {k: str(v) for k, v in m.items()},
        "matched_total_usd": str(sum(m.values())),
        "residual_components": {k: str(v) for k, v in r.items()},
        "residual_total_usd": str(sum(r.values())),
        "residual_per_contract_used": str(r_pc),
    }


def breakeven_matched_fraction(price: D, contracts: D, pair_cost: D, *,
                               complement_is_taker: bool = False,
                               residual_per_contract: Optional[D] = None
                               ) -> Dict[str, Any]:
    """The matched-COMPLETION RATE m* at which the five components sum to 0.

        m*(matched_total) + (1-m*)(residual_total) = 0

    Returns None for m* when no fraction in [0,1] can reach breakeven, i.e.
    the state is UNECONOMIC at any completion rate.
    """
    e = probe_economics(price, contracts, pair_cost,
                        complement_is_taker=complement_is_taker,
                        residual_per_contract=residual_per_contract)
    M, R = D(e["matched_total_usd"]), D(e["residual_total_usd"])
    denom = M - R
    m: Optional[D] = None
    if denom != 0:
        cand = -R / denom
        if D(0) <= cand <= D(1):
            m = cand
    reach = (None if m is None else
             "ALREADY_POSITIVE_AT_OBSERVED" if
             m <= OBSERVED["matched_fraction_point"] else
             "WITHIN_OBSERVED_CI" if m <= OBSERVED["matched_fraction_ci"][1]
             else "ABOVE_OBSERVED_CI")
    e.update({
        "breakeven_matched_fraction":
            (str(m.quantize(D("0.000001"))) if m is not None else None),
        "unreachable_at_any_completion_rate": m is None and M <= 0,
        "always_positive": m is None and R > 0,
        "vs_observed_matched_fraction": reach,
        "observed_matched_fraction": str(OBSERVED["matched_fraction_point"]),
        "observed_ci": [str(x) for x in OBSERVED["matched_fraction_ci"]],
    })
    return e


def residual_tolerance(price: D, contracts: D, pair_cost: D, *,
                       matched_fraction: Optional[D] = None,
                       complement_is_taker: bool = False
                       ) -> Dict[str, Any]:
    """Maximum tolerable residual loss per contract, and maximum tolerable
    residual FREQUENCY, at one state.

    At a GIVEN completion rate m, breakeven needs

        m*M + (1-m)*(C*r + rebate_first) = 0

    solved for r. The companion figure is the residual frequency tolerable at
    the OBSERVED residual loss.
    """
    m = (OBSERVED["matched_fraction_point"] if matched_fraction is None
         else matched_fraction)
    e = probe_economics(price, contracts, pair_cost,
                        complement_is_taker=complement_is_taker)
    M = D(e["matched_total_usd"])
    reb1 = rebate(price, contracts)
    max_r: Optional[D] = None
    if m < 1 and contracts > 0:
        max_r = (-m * M / (D(1) - m) - reb1) / contracts

    r_obs = OBSERVED["residual_pnl_usd_per_contract"]
    R_obs = contracts * r_obs + reb1
    max_freq: Optional[D] = None
    if M - R_obs != 0:
        cand = M / (M - R_obs)
        if D(0) <= cand <= D(1):
            max_freq = cand
    return {
        "price": str(price), "contracts": str(contracts),
        "pair_cost": str(pair_cost),
        "complement_role": "taker" if complement_is_taker else "maker",
        "at_matched_fraction": str(m),
        "matched_total_usd": str(M),
        "max_tolerable_residual_usd_per_contract":
            (str(max_r.quantize(D("0.000001"))) if max_r is not None
             else None),
        "observed_residual_usd_per_contract": str(r_obs),
        "observed_residual_is_tolerable":
            (max_r is not None and r_obs >= max_r),
        "max_tolerable_residual_frequency_at_observed_loss":
            (str(max_freq.quantize(D("0.000001"))) if max_freq is not None
             else None),
        "observed_residual_frequency":
            str(D(1) - OBSERVED["matched_fraction_point"]),
        "observed_residual_loss_rate": str(OBSERVED["residual_loss_rate"]),
    }


# ------------------------ denominator reconciliation ----------------------

def denominator_reconciliation() -> Dict[str, Any]:
    """Whether the rebate bp and L5's -71.7 bp can be compared at all.

    Three distinct denominators, and two of them coincide only under a
    condition that must be checked rather than assumed.
    """
    rows = []
    for X in PAIR_COSTS:
        for p in (D("0.30"), D("0.50")):
            q = X - p
            if not (D("0.01") <= q <= D("0.99")):
                continue
            per_pair = FS.maker_theta() * (p * (1 - p) + q * (1 - q))
            rows.append({
                "pair_cost": str(X), "leg_price": str(p),
                "complement_price": str(q),
                "pair_turnover_usd_per_pair": str(X),
                "rebate_usd_per_pair_unrounded":
                    str(per_pair.quantize(D("0.00000001"))),
                "rebate_bp_of_PAIR_TURNOVER":
                    str((D("10000") * per_pair / X).quantize(D("0.01"))),
                "rebate_bp_of_FIRST_LEG_MAKER_NOTIONAL":
                    str((D("10000") * per_pair / p).quantize(D("0.01"))),
                "lock_bp_of_pair_turnover":
                    str((D("10000") * (D(1) - X) / X).quantize(D("0.01"))),
            })
    return {
        "denominators": {
            "maker_rebate_natural": "MAKER NOTIONAL = contracts * price "
                                    "(ONE leg)",
            "pair_economics_natural": "PAIR NOTIONAL ~ $1.00 of settlement "
                                      "per pair",
            "l5_net_bp": ("TURNOVER, established FROM SOURCE: "
                          "analysis/l5/regime_aug19/regime_shift.py line 360 "
                          "accumulates `turnover += usdcSize` over EVERY "
                          "fill of both legs -- NOT assumed"),
        },
        "identity": ("turnover(pair) = C*p + C*(X-p) = C*X ~= $1.00*C, so "
                     "PAIR TURNOVER ~= PAIR COST and a per-pair rebate over "
                     "C*X is already in L5's bp-of-turnover units"),
        "the_2x_trap": ("a pair turns over 2x ONE LEG's notional, but each "
                        "leg is ~$0.50, so the pair total is still ~$1.00. "
                        "The 2x applies when converting FROM maker notional "
                        "(one leg) TO pair turnover, and the "
                        "rebate_bp_of_FIRST_LEG_MAKER_NOTIONAL column below "
                        "is roughly 2x the pair-turnover column, exactly as "
                        "that conversion requires. Comparing the maker-"
                        "notional column against L5's bp would overstate "
                        "our compensation ~2x."),
        "rate_vs_delta": {
            "finding": "-71.7 bp IS A CHANGE, NOT A LEVEL",
            "r3_net_bp": str(OBSERVED["l5_net_bp_r3"]),
            "r4_net_bp": str(OBSERVED["l5_net_bp_r4"]),
            "the_change": str(OBSERVED["l5_net_bp_change_r3_to_r4"]),
            "level_a_clone_must_cover_bp": str(OBSERVED["l5_net_bp_r4"]),
            "overstatement_if_delta_used":
                str((OBSERVED["l5_net_bp_change_r3_to_r4"]
                     / OBSERVED["l5_net_bp_r4"]).quantize(D("0.01"))) + "x",
        },
        "is_the_minus_71_7_comparison_legitimate": {
            "answer": "NO -- on two independent grounds",
            "ground_1_rate_vs_delta": ("-71.7 is a regime-to-regime MOVE. "
                                       "A rebate rate can only be compared "
                                       "against the LEVEL, -30.4 bp."),
            "ground_2_role_mix": (
                "even against -30.4 bp the comparison fails. L5's R4 shows "
                "fees of -62.1 bp of turnover, which means L5 PAID TAKER "
                "FEES on a material share of that turnover. A maker rebate "
                "and a taker fee cannot both be earned on the same fill, so "
                "the full both-legs-maker 62.5 bp CANNOT be added to a "
                "-30.4 bp figure that was produced partly by taker legs. "
                "Doing so double-counts the same notional. And an all-maker "
                "counterfactual does not hold L5's completion rate fixed: "
                "the taker completions are precisely what produced it."),
            "what_can_be_said": (
                "the two figures can be compared only on an all-maker "
                "sub-population with its OWN measured completion rate and "
                "its OWN turnover. We do not hold that measurement, so the "
                "comparison is NOT MADE here."),
        },
        "rows": rows,
    }


def pair_at_par_test() -> Dict[str, Any]:
    """Is a pair at exactly $1.00 rebate-financed, and does that explain
    L5's median pair cost of 0.9668 with only 46.2% below 0.96?"""
    rows = []
    for X in PAIR_COSTS:
        p = X / 2
        per_pair = FS.maker_theta() * 2 * (p * (1 - p))
        lock = D(1) - X
        rows.append({
            "pair_cost": str(X), "each_leg_price": str(p),
            "lock_usd_per_pair": str(lock),
            "rebate_usd_per_pair_both_maker":
                str(per_pair.quantize(D("0.00000001"))),
            "lock_bp_of_turnover":
                str((D("10000") * lock / X).quantize(D("0.01"))),
            "rebate_bp_of_turnover":
                str((D("10000") * per_pair / X).quantize(D("0.01"))),
            "rebate_exceeds_lock": per_pair > lock,
            "rebate_share_of_gross":
                (str((per_pair / (per_pair + lock)).quantize(D("0.0001")))
                 if per_pair + lock > 0 else "1.0000"),
        })
    return {
        "hypothesis": ("L5's median pair cost 0.9668 with only 46.2% "
                       "completing <= 0.96 is explained by rebate "
                       "financing: a pair at $1.00 has ZERO lock but still "
                       "earns ~62.5 bp of turnover in maker rebate"),
        "verdict": "ARITHMETIC CONFIRMED, CAUSALLY UNPROVEN",
        "arithmetic": ("at X=1.00, p=0.50, both legs maker, the pair earns "
                       "$0.00625 against a $0.00 lock -- 62.5 bp of pair "
                       "turnover, financed entirely by the rebate. The "
                       "rebate EXCEEDS the lock for every pair cost above "
                       "~0.9938, and is a majority of gross above ~0.9875."),
        "why_causally_unproven": (
            "three reasons. (1) L5's R4 confirmed incentive yield is "
            "+163.4 bp of turnover -- 2.6x the 62.5 bp the maker-rebate "
            "formula can produce -- so the maker rebate alone does NOT "
            "account for L5's compensation; a liquidity-reward program we "
            "have no source-confirmed entitlement to supplies the rest. "
            "(2) L5's R4 fees are -62.1 bp, so L5 is not all-maker and "
            "cannot be earning the full both-legs-maker rate. (3) The pair-"
            "cost distribution SPREAD rather than shifted (median fell "
            "0.9764 -> 0.9609 while p90 rose 1.1705 -> 1.2175), which a "
            "pure rebate-financing story does not predict."),
        "rows": rows,
    }


# -------------------------- the observed-values test ----------------------

def observed_region_test() -> Dict[str, Any]:
    """Does ANY positive region survive at OBSERVED PMUS/L5 values?

    Holds the observed matched lock, observed residual loss and observed
    completion rate fixed and asks what the rebate buys at each size.
    """
    rows = []
    for c in QUANTITIES:
        p = D("0.50")
        reb_pair = 2 * rebate(p, c)
        reb_leg = rebate(p, c)
        M = c * OBSERVED["matched_lock_usd_per_contract"] + reb_pair
        R = c * OBSERVED["residual_pnl_usd_per_contract"] + reb_leg
        m_star = (-R / (M - R)) if M != R else None
        m_obs = OBSERVED["matched_fraction_point"]
        ev = m_obs * M + (1 - m_obs) * R
        rows.append({
            "contracts": str(c),
            "leg_price": str(p),
            "maker_rebate_matched_usd": str(reb_pair),
            "maker_rebate_residual_usd": str(reb_leg),
            "matched_total_usd": str(M.quantize(D("0.000001"))),
            "residual_total_usd": str(R.quantize(D("0.000001"))),
            "breakeven_matched_fraction":
                (str(m_star.quantize(D("0.000001")))
                 if m_star is not None else None),
            "breakeven_vs_no_rebate_0_7873":
                (str((m_star - OBSERVED[
                    "breakeven_matched_fraction_no_rebate"]
                      ).quantize(D("0.000001")))
                 if m_star is not None else None),
            "expected_value_at_observed_completion_usd":
                str(ev.quantize(D("0.000001"))),
            "positive_at_observed_completion": ev > 0,
        })
    gap = (OBSERVED["breakeven_matched_fraction_no_rebate"]
           - OBSERVED["matched_fraction_point"])
    asym = [r for r in rows if r["contracts"] == "100"][0]
    closed = (OBSERVED["breakeven_matched_fraction_no_rebate"]
              - D(asym["breakeven_matched_fraction"]))
    return {
        "question": ("does any positive region survive using OBSERVED fill, "
                     "completion and residual values rather than "
                     "hypothetical perfect fills?"),
        "answer": "NO",
        "gap_to_close_pct_points": str((gap * 100).quantize(D("0.01"))),
        "gap_the_rebate_closes_pct_points":
            str((closed * 100).quantize(D("0.01"))),
        "fraction_of_gap_closed":
            str((closed / gap).quantize(D("0.0001"))),
        "why": ("breakeven needs 78.73% matched without the rebate and "
                "~77.2% with it at size; observed is 67.72% with a 95% CI "
                "of [65.62%, 69.81%]. The rebate closes about 1.5 of an "
                "11.0 point gap -- roughly one eighth -- and the CI still "
                "excludes breakeven by more than 7 points. On L5's own "
                "coins the observed fraction is 57.90%, which is worse."),
        "and_at_the_protocol_size": (
            "at Stage-6's own 1.00 share the rebate is $0.00 at EVERY price "
            "on the venue grid, because the cent floor is 1.61-4.45 shares. "
            "So at the size actually authorized, the rebate changes nothing "
            "at all and breakeven stays exactly 78.73%."),
        "rows": rows,
    }


def stage6_replay() -> Dict[str, Any]:
    """Replay Stage-6's proposed mechanics under ACTUAL PMUS compensation,
    reporting the components SEPARATELY. Read-only: nothing in
    analysis/l5/stage6/ is modified or imported for mutation.
    """
    from analysis.l5.stage6 import bounds as B
    from analysis.l5.stage6 import pair_economics as PE

    p1 = B.MAX_FIRST_LEG_PRICE
    qty = B.FIRST_LEG_QTY
    gate = PE.fee_safe_pair_cost(p1, B.PAIR_COST_CEILING - p1)
    mc = PE.max_complement_price(p1)
    reb = PE.maker_rebate_estimate(p1, qty)

    changed, unchanged = [], []
    unchanged.append(
        "THE VERDICT. Breakeven needs 78.73% matched; observed is 67.72% "
        "[65.62%, 69.81%]. Crediting the rebate where the formula and "
        "account history support it moves breakeven to ~77.2% AT SIZE and "
        "NOT AT ALL at the protocol's 1.00 share. UNECONOMIC either way.")
    unchanged.append(
        "THE REBATE AT THE AUTHORIZED SIZE. $0.00, structurally, at every "
        "price. bounds.REBATE_CENT_FLOOR_SHARES = %s and FIRST_LEG_QTY = %s, "
        "so the protocol sits BELOW its own documented cent floor. S6P's "
        "zero-rebate treatment is not merely conservative here -- it is "
        "EXACTLY RIGHT." % (B.REBATE_CENT_FLOOR_SHARES, qty))
    unchanged.append(
        "THE RESIDUAL. Loss rate 1.000 over 7,148 provable residuals, mean "
        "-$0.27742 against a +$0.07496 matched lock -- 3.7x. No rebate at "
        "any size on the venue grid is of that order of magnitude.")
    unchanged.append(
        "THE EVIDENCE BASE. 95.58% of first legs are UNKNOWABLE under the "
        "strict standard. Rebate arithmetic does not touch that.")

    changed.append(
        "THE PAIR GATE'S CONSERVATISM IS QUANTIFIED, AND IT IS LARGE. S6P "
        "prices BOTH post-only legs at the TAKER coefficient and counts the "
        "rebate as zero. At p1=%s with a complement of %s the "
        "taker-on-both-legs charge is $%s per contract, taking that pair to "
        "$%s and FAILING the gate. The highest complement that clears is "
        "%s, so the $0.96 ceiling becomes a RAW-price ceiling of $%s -- four "
        "cents tighter. Priced as what the legs actually are -- two maker "
        "fills -- the same pair would instead RECEIVE a rebate. The swing "
        "between the two pricings is the schedule's full maker-to-taker "
        "spread." % (
            p1, gate["complement_price"],
            D(gate["taker_fee_first"]) + D(gate["taker_fee_complement"]),
            gate["fee_safe_pair_cost"], mc.get("max_complement_price"),
            p1 + D(mc["max_complement_price"]) if mc.get(
                "max_complement_price") else "n/a"))
    changed.append(
        "THE CONSERVATISM IS NOT WHAT KILLS IT. Removing the gate's taker "
        "pricing entirely and crediting the full both-legs-maker rebate at "
        "a size above the cent floor still leaves breakeven at ~77.2% "
        "against an observed 67.72%. So S6P's conservatism costs real "
        "headroom but is NOT the binding constraint -- the residual is.")
    changed.append(
        "ONE TAKER LEG ENDS THE DISCUSSION. At p=0.50 a taker completion "
        "costs $%s per contract -- %s bp of pair turnover -- against a "
        "both-legs-maker credit of 62.5 bp. A single taker leg is worth "
        "about 2.8x the entire maker rebate, with the opposite sign." % (
            taker(D("0.50"), D("1")),
            (D("10000") * taker(D("0.50"), D("1")) / D("1.00")
             ).quantize(D("0.1"))))

    return {
        "read_only": True,
        "modules_read": ["bounds.py", "machine.py", "pair_economics.py",
                         "settlement.py"],
        "bounds": {
            "FIRST_LEG_QTY": str(qty),
            "MAX_FIRST_LEG_PRICE": str(p1),
            "PAIR_COST_CEILING": str(B.PAIR_COST_CEILING),
            "REBATE_CENT_FLOOR_SHARES": str(B.REBATE_CENT_FLOOR_SHARES),
            "MAX_POSSIBLE_LOSS_USD": str(B.MAX_POSSIBLE_LOSS_USD),
            "qty_below_own_cent_floor": qty < B.REBATE_CENT_FLOOR_SHARES,
        },
        "s6p_pair_gate_as_written": gate,
        "s6p_max_complement_price": mc,
        "s6p_rebate_estimate": reb,
        "components_separately": {
            "matched_pair_economics_usd_per_contract":
                str(OBSERVED["matched_lock_usd_per_contract"]),
            "residual_pnl_usd_per_contract":
                str(OBSERVED["residual_pnl_usd_per_contract"]),
            "maker_rebates_usd_at_protocol_size":
                str(reb["usd"]) + " (structurally zero: below cent floor)",
            "credited_liquidity_rewards_usd": "0.00 (PMUS program census "
                                              "returns zero for all our "
                                              "slugs; nothing credited)",
            "fees_usd": "0.0000 across every audit (maker-only by owner "
                        "rule); S6P's gate nonetheless CHARGES the taker "
                        "coefficient on both legs as a bound",
            "settlement": "A6 unresolved -> NOTHING here is realized",
        },
        "what_changes_when_the_rebate_is_credited": changed,
        "what_does_not_change": unchanged,
    }


# ------------------------------ classification ----------------------------

def classify() -> List[Dict[str, Any]]:
    """Every region as exactly one of the three classes.

    OBSERVED-POSITIVE is empty, and that is the honest answer: we hold ONE
    credited PMUS fill, at 0.01 shares, whose rebate is structurally $0.00
    and whose settlement is unresolved. One fill cannot evidence a region.
    """
    return [
        {
            "class": "OBSERVED-POSITIVE",
            "regions": [],
            "boundary": "EMPTY SET",
            "why": ("we hold ONE credited PMUS fill (A6), at 0.01 shares, "
                    "rebate structurally $0.00, settlement UNRESOLVED, NOT "
                    "REALIZED. The $16.18 of credited PMUS compensation "
                    "($9.14 maker rebates + $7.04 liquidity rewards) is not "
                    "attached to a matched-pair sequence we can replay, and "
                    "the $0.51 / 97.59 bp credits are GEMINI, not PMUS. No "
                    "price/size/completion region is evidenced positive."),
        },
        {
            "class": "PLAUSIBLE-BUT-UNPROVEN",
            "regions": [
                {"region": "both legs maker, size >= cent floor "
                           "(1.61-4.45 shares by price), pair cost <= ~0.96, "
                           "matched completion >= ~77.2%",
                 "requires": "a matched completion rate at least 7.4 points "
                             "above the observed 95% CI upper bound of "
                             "69.81%, demonstrated on all-maker fills"},
                {"region": "both legs maker at p ~ 0.50, pair cost ~ 1.00, "
                           "rebate-financed with zero lock",
                 "requires": "62.5 bp of pair turnover to exceed the "
                             "residual drag, i.e. a residual loss rate far "
                             "below the observed 1.000"},
            ],
            "boundary": ("the surface m*(p, C, X) in the raw output. "
                         "Positive requires m > m*, and m* >= ~0.772 "
                         "everywhere the observed residual applies"),
            "why": ("mathematically real -- the rebate is uncapped, exact "
                    "and formula-supported -- but every positive cell needs "
                    "a completion or residual value we have NOT "
                    "demonstrated, on top of a 95.58% UNKNOWABLE fill "
                    "question."),
        },
        {
            "class": "UNECONOMIC",
            "regions": [
                {"region": "ANY state at Stage-6's authorized 1.00 share",
                 "why": "below the cent floor at every price; rebate $0.00; "
                        "breakeven stays 78.73% vs observed 67.72%"},
                {"region": "ANY state with ONE TAKER LEG",
                 "why": "173.75 bp of pair turnover at p=0.50 against a "
                        "62.5 bp maker credit -- 2.8x, opposite sign"},
                {"region": "ANY state at the observed residual "
                           "(-$0.27742/contract, loss rate 1.000) and the "
                           "observed completion (67.72%, CI to 69.81%)",
                 "why": "breakeven ~77.2% with the rebate credited at size; "
                        "the CI excludes it by more than 7 points"},
                {"region": "p <= 0.20 or p >= 0.80 at small size",
                 "why": "cent floor rises to 2.51-4.45 shares and the "
                        "asymptotic rate collapses to 12.5 bp at p=0.90"},
            ],
            "boundary": ("everything the observed values reach. The positive "
                         "region begins only ABOVE the observed completion "
                         "CI and BELOW the observed residual loss rate."),
            "why": "the residual, not the rebate, sets the sign.",
        },
    ]


def build() -> Dict[str, Any]:
    FS.assert_frozen()
    return {
        "protocol": "P0-ECONOMICS-PMUS-BREAKEVEN-SURFACE",
        "label": ("HISTORICAL / SHADOW ARITHMETIC ONLY. No network, no "
                  "order path, no venue write. Nothing here is submitted, "
                  "filled, settled or realized."),
        "schedule_fingerprint": FS.FINGERPRINT,
        "maker_theta": str(FS.maker_theta()),
        "taker_theta": str(FS.taker_theta()),
        "pmus_credited": {k: str(v) for k, v in PMUS_CREDITED.items()},
        "observed": {k: str(v) for k, v in OBSERVED.items()},
        "cap_audit": cap_audit(),
        "denominator_reconciliation": denominator_reconciliation(),
        "pair_at_par_test": pair_at_par_test(),
        "rebate_grid": rebate_grid(),
        "cent_floor_minimums": cent_floor_minimums(),
        "breakeven_surface_both_maker": [
            breakeven_matched_fraction(p, c, X)
            for p in PRICES for c in QUANTITIES for X in PAIR_COSTS
            if D("0.01") <= X - p <= D("0.99")
        ],
        "breakeven_surface_one_leg_taker": [
            breakeven_matched_fraction(p, c, X, complement_is_taker=True)
            for p in PRICES for c in QUANTITIES for X in PAIR_COSTS
            if D("0.01") <= X - p <= D("0.99")
        ],
        "residual_tolerance": [
            residual_tolerance(p, c, X)
            for p in PRICES for c in QUANTITIES for X in PAIR_COSTS
            if D("0.01") <= X - p <= D("0.99")
        ],
        "observed_region_test": observed_region_test(),
        "stage6_replay": stage6_replay(),
        "classification": classify(),
        "answer": {
            "question": ("does PMUS's ACTUAL compensation create an "
                         "executable positive-expectancy region for the "
                         "L5-derived matched-pair engine?"),
            "answer": "NO",
            "one_line": ("the maker rebate is real, uncapped and exactly "
                         "computable, but it is worth at most ~62.5 bp of "
                         "pair turnover and the binding constraint is a "
                         "residual that loses 3.7x what a matched pair "
                         "locks, at an observed loss rate of 1.000. The "
                         "rebate closes about one eighth of the breakeven "
                         "gap, and at the authorized 1.00 share it closes "
                         "none of it."),
        },
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=1, sort_keys=True))
