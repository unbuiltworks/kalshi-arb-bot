"""Settlement-aware fair-value helpers for short-duration Gemini Predictions.

Pure math only: no network, credentials, or order entry. The model uses the known
Up/Down strike plus a caller-supplied proxy for the Kaiko settlement index and a
short-horizon log-volatility estimate. It is intended as a gate on *new*
directional inventory, not as a replacement for the inventory pair-completion
policy.

The settlement proxy/basis must be calibrated before live use. A market midpoint
alone is not independent alpha.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
import math

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

ONE = D("1")


@dataclass(frozen=True)
class FairValueEstimate:
    probability_up: D
    probability_down: D
    log_moneyness: D
    total_log_sigma: D


@dataclass(frozen=True)
class FairValueDecision:
    side: str | None
    fair_probability: D
    quote_price: D | None
    maker_fee: D
    net_edge_before_rebate: D
    actionable: bool
    reason: str


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def estimate_up_probability(
    *,
    settlement_proxy_price: D,
    strike_price: D,
    seconds_to_expiry: float,
    log_sigma_per_sqrt_second: D,
    settlement_basis_sigma_bps: D = D("0"),
) -> FairValueEstimate:
    """Estimate P(settlement >= strike) under a zero-drift lognormal bridge.

    ``settlement_basis_sigma_bps`` is an explicit uncertainty reserve between the
    tradable proxy price and Gemini's Kaiko settlement index. It widens the
    terminal distribution rather than pretending the proxy equals the index.
    """
    spot = D(str(settlement_proxy_price))
    strike = D(str(strike_price))
    sigma = D(str(log_sigma_per_sqrt_second))
    basis_bps = D(str(settlement_basis_sigma_bps))
    if spot <= 0 or strike <= 0:
        raise ValueError("prices must be positive")
    if seconds_to_expiry < 0:
        raise ValueError("seconds_to_expiry cannot be negative")
    if sigma < 0 or basis_bps < 0:
        raise ValueError("volatility inputs cannot be negative")

    log_moneyness = math.log(float(spot / strike))
    market_var = float(sigma) ** 2 * float(seconds_to_expiry)
    basis_sigma_log = float(basis_bps) / 10_000.0
    total_sigma = math.sqrt(max(0.0, market_var + basis_sigma_log**2))

    if total_sigma <= 0:
        p_up = 1.0 if spot >= strike else 0.0
    else:
        p_up = normal_cdf(log_moneyness / total_sigma)
    p_up = _clamp01(p_up)
    p_down = 1.0 - p_up
    return FairValueEstimate(
        probability_up=D(str(p_up)),
        probability_down=D(str(p_down)),
        log_moneyness=D(str(log_moneyness)),
        total_log_sigma=D(str(total_sigma)),
    )


def fair_value_gate(
    *,
    estimate: FairValueEstimate,
    yes_maker_quote: D,
    no_maker_quote: D,
    min_net_edge: D,
    rebate_eligible_only: bool = False,
) -> FairValueDecision:
    """Choose a new directional maker entry only when independent fair value wins.

    Maker rebates are excluded from ``net_edge_before_rebate``. If
    ``rebate_eligible_only`` is true, quotes outside Gemini's published 20-80c
    maker-rebate band are refused rather than using an assumed future rebate.
    """
    yes = D(str(yes_maker_quote))
    no = D(str(no_maker_quote))
    floor = D(str(min_net_edge))
    if not (D("0") < yes < ONE and D("0") < no < ONE):
        raise ValueError("maker quotes must be in (0, 1)")
    if floor < 0:
        raise ValueError("min_net_edge must be non-negative")

    yes_fee = gemini_predictions_maker_fee(yes, D("1"))
    no_fee = gemini_predictions_maker_fee(no, D("1"))
    yes_edge = estimate.probability_up - yes - yes_fee
    no_edge = estimate.probability_down - no - no_fee

    if yes_edge >= no_edge:
        side = "YES"
        fair = estimate.probability_up
        quote = yes
        fee = yes_fee
        edge = yes_edge
    else:
        side = "NO"
        fair = estimate.probability_down
        quote = no
        fee = no_fee
        edge = no_edge

    if rebate_eligible_only and not (D("0.20") <= quote <= D("0.80")):
        return FairValueDecision(
            side=None,
            fair_probability=fair,
            quote_price=quote,
            maker_fee=fee,
            net_edge_before_rebate=edge,
            actionable=False,
            reason="Best directional quote is outside the configured maker-rebate band.",
        )
    if edge < floor:
        return FairValueDecision(
            side=None,
            fair_probability=fair,
            quote_price=quote,
            maker_fee=fee,
            net_edge_before_rebate=edge,
            actionable=False,
            reason="Independent fair value does not clear the after-fee edge reserve.",
        )
    return FairValueDecision(
        side=side,
        fair_probability=fair,
        quote_price=quote,
        maker_fee=fee,
        net_edge_before_rebate=edge,
        actionable=True,
        reason="Independent settlement-aware fair value clears the after-fee edge reserve.",
    )
