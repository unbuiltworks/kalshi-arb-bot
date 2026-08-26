"""Fee-correct, incentive-aware wrapper for the read-only K/P MLB maker shadow.

This remains shadow-only. It adds the current KXMLBGAME maker-fee arithmetic
and live liquidity-incentive telemetry to the existing pessimistic maker-fill
measurement. Incentive values are estimates until a venue actually credits
rewards; they never count as realized P&L here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING
import json
import logging

from src import sports_kp_mlb_moneyline_maker_fill_shadow as impl
from src.sports_maker_incentive_telemetry import IncentiveTelemetry

D = Decimal


def _kxmlbgame_maker_fee_q1(price: Decimal) -> Decimal:
    raw = D("0.0175") * price * (D("1") - price)
    # Current Kalshi schedule: fee + position cost rounds up to a centicent.
    # MLB prices are cent ticks, so position cost already lies on that grid;
    # rounding the fee itself up to $0.0001 is equivalent for q=1.
    return (raw * D("10000")).to_integral_value(rounding=ROUND_CEILING) / D("10000")


class _VerifiedFeeLabelFilter(logging.Filter):
    """Keep inherited shadow telemetry labels consistent with verified KXMLBGAME fees."""
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        message = message.replace(
            '"maker_fee_applicability_certified":false',
            '"maker_fee_applicability_certified":true',
        )
        message = message.replace(
            "GENERAL_0.0175_FORMULA_AND_ZERO_FEE_UPPER_BOUND_APPLICABILITY_UNCERTIFIED",
            "KXMLBGAME_MAKER_MULTIPLIER_1_CENTICENT_ROUNDING_VERIFIED_2026_07_07",
        )
        record.msg = message
        record.args = ()
        return True


_BaseMakerFillShadow = impl.MakerFillShadow


class _IncentiveAwareMakerFillShadow(_BaseMakerFillShadow):
    """Existing fill shadow plus live reward-series measurement; no order entry."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.incentive_telemetry = IncentiveTelemetry(self, interval_s=60.0)
        self.shadow_trading_pnl_at_fill = D("0")
        self.shadow_fills_with_hedge = 0
        self._combined_last_summary = 0.0

    async def quote_loop(self, scan_ms: int) -> None:
        incentive_task = asyncio.create_task(self.incentive_telemetry.run())
        try:
            await super().quote_loop(scan_ms)
        finally:
            incentive_task.cancel()
            await asyncio.gather(incentive_task, return_exceptions=True)

    async def _record_fill(self, q, trade, fill_reason: str) -> None:
        # Keep a separate pure-trading shadow P&L column. This is the immediate
        # fill-time K-maker -> P-taker lock using the current hedge, not realized
        # P&L and not incentive income.
        hedge = self._poly_hedge(q)
        if hedge is not None:
            hedge_price, _, _ = hedge
            profit_fee, _, _, _ = self._profit(q.maker_price, hedge_price)
            self.shadow_trading_pnl_at_fill += profit_fee
            self.shadow_fills_with_hedge += 1
        await super()._record_fill(q, trade, fill_reason)

    def _maybe_summary(self) -> None:
        before = self.last_summary
        super()._maybe_summary()
        if self.last_summary == before:
            return
        incentives = dict(self.incentive_telemetry.last_snapshot)
        # q2500 is intentionally labeled unconstrained: the telemetry helper
        # evaluates that size independently on each active quoted market. It is
        # useful as a yield curve point but is NOT a $3.4k portfolio estimate.
        logger = impl.logger
        logger.warning("KPMM_COMBINED_MAKER_SUMMARY %s", json.dumps({
            "pure_trading_shadow_pnl_usd": str(self.shadow_trading_pnl_at_fill),
            "simulated_fills_with_immediate_hedge": self.shadow_fills_with_hedge,
            "estimated_incentive_q1_active_quote_daily_run_rate_usd": incentives.get("kalshi_estimated_q1_incentive_daily_run_rate", 0.0),
            "estimated_incentive_q2500_each_active_market_unconstrained_daily_run_rate_usd": incentives.get("kalshi_estimated_q2500_incentive_daily_run_rate", 0.0),
            "kalshi_active_incentive_programs": incentives.get("kalshi_active_programs", 0),
            "kalshi_snapshot_eligible_markets": incentives.get("kalshi_snapshot_eligible_markets", 0),
            "polymarket_active_incentive_programs": incentives.get("poly_active_programs", 0),
            "polymarket_program_denominator_complete": incentives.get("polymarket_denominator_complete", False),
            "incentive_snapshot_updated_ms": incentives.get("updated_ms"),
            "incentive_accounting": "SHADOW_ESTIMATE_NOT_VENUE_CREDIT_NOT_REALIZED",
            "combined_maker_capacity_credit_usd_per_day": 0,
            "combined_gate_status": "PENDING_FILL_ADVERSE_SELECTION_AND_CAPITAL_BOUNDED_INCENTIVE_YIELD",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


impl._kalshi_maker_fee_q1 = _kxmlbgame_maker_fee_q1
impl.MakerFillShadow = _IncentiveAwareMakerFillShadow
impl.logger.addFilter(_VerifiedFeeLabelFilter())


if __name__ == "__main__":
    impl.main()
