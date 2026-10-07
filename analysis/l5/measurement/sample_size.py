"""Sequential-measurement sample-size and worst-case-dollar arithmetic.

NOT AUTHORIZED. NOT ARMED. This module places no order, holds no credential,
imports nothing from any `live_exec*` path, and performs no network I/O. It is
pure arithmetic over numbers that are either (a) frozen elsewhere in this
repository and cited, or (b) standard statistical constants. It is the
calculator behind `SEQUENTIAL_MEASUREMENT_PROTOCOL.md`.

NO PARAMETER FITTING. Nothing here was chosen to improve an outcome. The two
SPRT hypothesis points are PRE-EXISTING measured/derived numbers taken from
`analysis/l5/stage6_validation/STAGE6_HISTORICAL_VALIDATION.md` (the observed
proxy matched fraction 0.6772 and the derived break-even matched fraction
0.7873); the posterior-width targets and the quantile precision targets are
stated as a GRID of options for the owner, not as a single tuned choice.

Frozen inputs, by citation:
  * first-leg price ceiling 0.60              analysis/l5/stage6/bounds.py
  * both legs are BUYs, worst case = residual analysis/l5/stage6/bounds.py
  * maker rebate = theta * C * p * (1-p),
    theta_maker = 0.0125, ROUND_HALF_EVEN to $0.01   src/pmus_fee_schedule.py
  * taker theta 0.0695 from 2026-09-17T04:00Z      src/pmus_fee_schedule.py
"""
from __future__ import annotations

import math
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Dict, List, Tuple

# --------------------------------------------------------------- frozen refs
#: analysis/l5/stage6/bounds.py :: MAX_FIRST_LEG_PRICE
MAX_FIRST_LEG_PRICE = D("0.60")
#: src/pmus_fee_schedule.py, latest entry
MAKER_THETA = D("0.0125")
TAKER_THETA = D("0.0695")
CENT = D("0.01")

#: STAGE6_HISTORICAL_VALIDATION.md section 5 -- observed proxy matched fraction
STAGE6_OBSERVED_MATCHED_FRACTION = 0.6772
#: STAGE6_HISTORICAL_VALIDATION.md section 5 -- derived break-even
STAGE6_BREAKEVEN_MATCHED_FRACTION = 0.7873
#: STAGE6_HISTORICAL_VALIDATION.md section 5 -- per-outcome economics, USD/share
STAGE6_MATCHED_MEAN_USD = 0.07496
STAGE6_RESIDUAL_MEAN_USD = -0.27742

Z95 = 1.959963984540054


# ----------------------------------------------------------- the rebate cliff

def maker_rebate_usd(price: D, contracts: D) -> D:
    """The PMUS maker rebate, cent-rounded exactly as the venue publishes it."""
    raw = MAKER_THETA * D(str(contracts)) * D(str(price)) * (D(1) - D(str(price)))
    return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)


def min_rebate_bearing_contracts(price: D) -> D:
    """Smallest contract count on the 0.01 grid whose rebate rounds above $0.00.

    ROUND_HALF_EVEN sends exactly $0.005 to $0.00 (0 is the even digit), so the
    requirement is STRICT: theta * C * p * (1-p) > 0.005.
    """
    c = D("0.01")
    while c <= D("1000"):
        if maker_rebate_usd(price, c) > D("0"):
            return c
        c += D("0.01")
    return D("NaN")


def rebate_bearing_price_band(contracts: D) -> List[D]:
    """Every price on the 0.01 grid, 0.01..0.99, at which `contracts` earns a cent."""
    out = []
    p = D("0.01")
    while p <= D("0.99"):
        if maker_rebate_usd(p, contracts) > D("0"):
            out.append(p)
        p += D("0.01")
    return out


# -------------------------------------------- estimand 1/2: binomial, Bayesian

def n_for_posterior_width(width: float, p_worst: float = 0.5) -> int:
    """Observations for a 95% interval of total width `width` on a proportion.

    Normal/Jeffreys-equivalent at the stated worst-case p (p=0.5 is the
    variance-maximising, i.e. most expensive, truth). width = 2*z*sqrt(pq/n).
    """
    if not 0 < width < 1:
        raise ValueError("width must be in (0,1)")
    return math.ceil((2 * Z95) ** 2 * p_worst * (1 - p_worst) / width ** 2)


# ------------------------------------------------------- estimand 2: the SPRT

def sprt_increments(p0: float, p1: float) -> Tuple[float, float]:
    """Log-likelihood-ratio increment for a success and for a failure."""
    if not 0 < p0 < 1 or not 0 < p1 < 1 or p0 >= p1:
        raise ValueError("require 0 < p0 < p1 < 1")
    return (math.log(p1 / p0), math.log((1 - p1) / (1 - p0)))


def sprt_boundaries(alpha: float, beta: float) -> Tuple[float, float]:
    """Wald's (log A, log B): accept H1 above log A, accept H0 below log B."""
    return (math.log((1 - beta) / alpha), math.log(beta / (1 - alpha)))


def sprt_operating_characteristics(p0: float, p1: float,
                                   alpha: float = 0.05,
                                   beta: float = 0.05) -> Dict[str, float]:
    """Wald's expected sample sizes under H0, under H1, and at indifference."""
    s, f = sprt_increments(p0, p1)
    log_a, log_b = sprt_boundaries(alpha, beta)

    ez0 = p0 * s + (1 - p0) * f
    ez1 = p1 * s + (1 - p1) * f
    e_n_h0 = ((1 - alpha) * log_b + alpha * log_a) / ez0
    e_n_h1 = (alpha * log_b + (1 - alpha) * log_a) / ez1

    # indifference point: the p at which E[z] = 0, where the walk has no drift
    p_ind = -f / (s - f)
    ez2 = p_ind * s * s + (1 - p_ind) * f * f
    e_n_worst = -log_a * log_b / ez2

    return {
        "p0": p0, "p1": p1, "alpha": alpha, "beta": beta,
        "log_lr_success": s, "log_lr_failure": f,
        "log_a": log_a, "log_b": log_b,
        "expected_n_under_h0": e_n_h0,
        "expected_n_under_h1": e_n_h1,
        "indifference_p": p_ind,
        "expected_n_at_indifference": e_n_worst,
    }


# ----------------------------------- estimand 3/4: distribution-free quantiles

def n_for_quantile(q: float, eps: float) -> int:
    """Observations so the 95% CI for the q-th quantile spans +/- `eps` in
    PROBABILITY (coverage) terms: n >= q(1-q)(z/eps)^2.

    This is the distribution-free (binomial / order-statistic) requirement. It
    bounds where the quantile sits on the CDF, not its value in seconds; the
    seconds-width then depends on the unknown density, which is stated as a
    limit rather than assumed away.
    """
    if not 0 < q < 1 or not 0 < eps < 1:
        raise ValueError("bad q or eps")
    return math.ceil(q * (1 - q) * (Z95 / eps) ** 2)


# ------------------------------------------- estimand 5: per-share expectancy

def per_leg_pnl_sd_usd_per_share(p_matched: float,
                                 matched_usd: float = STAGE6_MATCHED_MEAN_USD,
                                 residual_usd: float = STAGE6_RESIDUAL_MEAN_USD
                                 ) -> float:
    """SD of the two-point per-filled-leg P&L mixture, USD per share."""
    m = p_matched * matched_usd + (1 - p_matched) * residual_usd
    var = (p_matched * (matched_usd - m) ** 2
           + (1 - p_matched) * (residual_usd - m) ** 2)
    return math.sqrt(var)


def n_for_mean_halfwidth(sd: float, halfwidth: float) -> int:
    if halfwidth <= 0:
        raise ValueError("halfwidth must be positive")
    return math.ceil((Z95 * sd / halfwidth) ** 2)


# ------------------------------------------------------- worst-case dollars

def worst_case_per_first_leg_usd(qty: D,
                                 price: D = MAX_FIRST_LEG_PRICE) -> D:
    """Both legs are BUYs; the loss-maximising outcome is the first leg filling
    in full at the price ceiling and the complement never filling, the residual
    settling to zero. So the worst case is simply the cash paid out."""
    return (D(str(qty)) * D(str(price))).quantize(D("0.0001"))


def cascade_placements(fills_needed: int, p_fill: float) -> int:
    """Placements needed to obtain `fills_needed` first-leg fills."""
    if not 0 < p_fill <= 1:
        raise ValueError("p_fill must be in (0,1]")
    return math.ceil(fills_needed / p_fill)


# ------------------------------------------------------------------ reporting

SIZES = [D("0.01"), D("0.10"), D("1.00"), D("1.62")]
P_FILL_GRID = [0.10, 0.25, 0.50, 0.87]


def gate_table() -> Dict[str, Dict[str, object]]:
    """Every estimand's stopping gate, with its method and its sample unit."""
    sd = per_leg_pnl_sd_usd_per_share(STAGE6_OBSERVED_MATCHED_FRACTION)
    return {
        "E1_first_leg_maker_fill_prob": {
            "unit": "placement",
            "method": "Bayesian Beta(1,1) posterior-width stopping (no test: "
                      "no pre-existing decision boundary exists for this "
                      "quantity, which is the whole point of the lane)",
            "n_width_0.20": n_for_posterior_width(0.20),
            "n_width_0.10": n_for_posterior_width(0.10),
            "n_width_0.05": n_for_posterior_width(0.05),
        },
        "E2_completion_prob_given_fill": {
            "unit": "first-leg fill",
            "method": "truncated Wald SPRT, H0: p = 0.6772 (Stage-6 observed) "
                      "vs H1: p = 0.7873 (Stage-6 derived break-even), "
                      "alpha = beta = 0.05, truncated at the E1 width-0.10 n",
            "oc": sprt_operating_characteristics(
                STAGE6_OBSERVED_MATCHED_FRACTION,
                STAGE6_BREAKEVEN_MATCHED_FRACTION),
            "truncation_n": n_for_posterior_width(0.10),
        },
        "E3_time_to_completion_distribution": {
            "unit": "completion (Kaplan-Meier, right-censored at handover)",
            "method": "distribution-free order-statistic quantile CI",
            "n_median_eps_0.05": n_for_quantile(0.50, 0.05),
            "n_p90_eps_0.05": n_for_quantile(0.90, 0.05),
            "n_p90_eps_0.10": n_for_quantile(0.90, 0.10),
        },
        "E4_residual_frequency_and_duration": {
            "unit": "residual",
            "method": "frequency is 1 - E2 and costs no extra observation; "
                      "duration uses the same quantile CI as E3",
            "n_p90_eps_0.05": n_for_quantile(0.90, 0.05),
        },
        "E5_realized_matched_and_residual_economics": {
            "unit": "first-leg fill",
            "method": "per-share mean with a normal CI over the two-point "
                      "matched/residual mixture",
            "sd_usd_per_share": sd,
            "n_halfwidth_0.05": n_for_mean_halfwidth(sd, 0.05),
            "n_halfwidth_0.02": n_for_mean_halfwidth(sd, 0.02),
        },
    }


def binding_fill_gate() -> int:
    """The largest fill-denominated requirement across E2..E5."""
    g = gate_table()
    sprt_trunc = int(g["E2_completion_prob_given_fill"]["truncation_n"])
    # E3/E4 are completion- and residual-denominated; convert to fills using
    # the only completion probability we have a number for, Stage-6's observed
    # proxy fraction, and SAY SO. It is not an estimate of the live rate.
    pc = STAGE6_OBSERVED_MATCHED_FRACTION
    e3 = math.ceil(n_for_quantile(0.90, 0.05) / pc)
    e4 = math.ceil(n_for_quantile(0.90, 0.05) / (1 - pc))
    e5 = int(g["E5_realized_matched_and_residual_economics"]["n_halfwidth_0.02"])
    return max(sprt_trunc, e3, e4, e5)


def dollar_table() -> List[Dict[str, object]]:
    """The four-size worst-case dollar table."""
    fills = binding_fill_gate()
    e1_n = n_for_posterior_width(0.10)
    rows = []
    for qty in SIZES:
        per_leg = worst_case_per_first_leg_usd(qty)
        row = {
            "qty_shares": str(qty),
            "max_loss_per_first_leg_usd": str(per_leg),
            # MAX_FIRST_LEGS = 1 per session (bounds.py), so session == leg
            "max_loss_per_session_usd": str(per_leg),
            "e1_gate_placements": e1_n,
            "e1_gate_worst_case_usd": str((per_leg * e1_n).quantize(D("0.01"))),
            "all_gates_fills_needed": fills,
            "rebate_observable": maker_rebate_usd(D("0.50"), qty) > D("0"),
        }
        for pf in P_FILL_GRID:
            n = cascade_placements(fills, pf)
            n_total = max(n, e1_n)
            row[f"placements_at_p_fill_{pf}"] = n_total
            row[f"all_gates_worst_case_usd_at_p_fill_{pf}"] = \
                str((per_leg * n_total).quantize(D("0.01")))
        rows.append(row)
    return rows


def _fmt(rows: List[Dict[str, object]]) -> str:
    out = []
    for r in rows:
        out.append(f"qty {r['qty_shares']:>5} shares | "
                   f"per-leg {r['max_loss_per_first_leg_usd']} | "
                   f"rebate observable: {r['rebate_observable']}")
        out.append(f"   E1 gate {r['e1_gate_placements']} placements -> "
                   f"${r['e1_gate_worst_case_usd']}")
        for pf in P_FILL_GRID:
            out.append(f"   ALL gates @ p_fill={pf}: "
                       f"{r[f'placements_at_p_fill_{pf}']} placements -> "
                       f"${r[f'all_gates_worst_case_usd_at_p_fill_{pf}']}")
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover
    import json
    print("NOT AUTHORIZED / NOT ARMED -- arithmetic only\n")
    print(json.dumps(gate_table(), indent=2, default=str))
    print(f"\nbinding fill gate: {binding_fill_gate()} fills\n")
    print(_fmt(dollar_table()))
    print("\nrebate cliff:")
    for p in (D("0.30"), D("0.45"), D("0.50"), D("0.55"), D("0.60")):
        print(f"  p={p}: min rebate-bearing contracts = "
              f"{min_rebate_bearing_contracts(p)}")
    band = rebate_bearing_price_band(D("1.62"))
    print(f"  at 1.62 shares the rebate is non-zero only for p in "
          f"[{band[0]}, {band[-1]}] ({len(band)} ticks)")
