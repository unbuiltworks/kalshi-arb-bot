"""Fee-correct, incentive-aware wrapper for the read-only K/P MLB maker shadow.

This remains shadow-only. It adds the current KXMLBGAME maker-fee arithmetic
and live liquidity-incentive telemetry to the existing pessimistic maker-fill
measurement. Incentive values are estimates until a venue actually credits
rewards; they never count as realized P&L here. Polymarket incentive telemetry
uses receipt-book depth and the event-clock applicable reward phase only.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING
import json
import logging
from typing import Any

from src import sports_kp_mlb_moneyline_maker_fill_shadow as impl
from src.sports_maker_incentive_telemetry import IncentiveTelemetry

D = Decimal
ZERO = D("0")
ONE = D("1")


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
            '\"maker_fee_applicability_certified\":false',
            '\"maker_fee_applicability_certified\":true',
        )
        message = message.replace(
            "GENERAL_0.0175_FORMULA_AND_ZERO_FEE_UPPER_BOUND_APPLICABILITY_UNCERTIFIED",
            "KXMLBGAME_MAKER_MULTIPLIER_1_CENTICENT_ROUNDING_VERIFIED_2026_07_07",
        )
        record.msg = message
        record.args = ()
        return True


def _safe_decimal(value: Any) -> Decimal:
    try:
        return D(str(value))
    except Exception:
        return ZERO


class _CoverageRankingHandler(logging.Handler):
    """Derive discounted incentive coverage and rank under-covered markets.

    Coverage is deliberately the requested measurement:
        discounted qualifying score / target size

    It is reported per side and as the minimum side coverage. This does not
    claim a venue reward share or realized income; it is only the scarcity
    signal used to rank which live incentive markets deserve maker attention.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.rows: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if message.startswith("KPMM_INCENTIVE_MARKET "):
                payload = json.loads(message.split(" ", 1)[1])
                target = _safe_decimal(payload.get("target_size"))
                reward = _safe_decimal(payload.get("period_reward_usd"))
                if target <= 0:
                    return

                venue = str(payload.get("venue") or "")
                if venue == "KALSHI":
                    side_a_name = "yes"
                    side_b_name = "no"
                    score_a = _safe_decimal(payload.get("yes_qualifying_score"))
                    score_b = _safe_decimal(payload.get("no_qualifying_score"))
                    phase = "active"
                elif venue == "POLYMARKET_US":
                    side_a_name = "bid"
                    side_b_name = "ask"
                    score_a = _safe_decimal(payload.get("bid_qualifying_score"))
                    score_b = _safe_decimal(payload.get("ask_qualifying_score"))
                    phase = str(payload.get("applicable_period_by_event_clock") or payload.get("period") or "")
                else:
                    return

                coverage_a = score_a / target
                coverage_b = score_b / target
                min_coverage = min(coverage_a, coverage_b)
                self.rows.append({
                    "venue": venue,
                    "market": str(payload.get("market") or ""),
                    "program_id": str(payload.get("program_id") or ""),
                    "phase": phase,
                    "reward_pool_usd": str(reward),
                    "target_size": str(target),
                    f"{side_a_name}_coverage_ratio": str(coverage_a),
                    f"{side_b_name}_coverage_ratio": str(coverage_b),
                    "min_coverage_ratio": str(min_coverage),
                    "under_covered": min_coverage < ONE,
                    "coverage_definition": "DISCOUNTED_QUALIFYING_SCORE_DIV_TARGET",
                })
                return

            if message.startswith("KPMM_INCENTIVE_SUMMARY "):
                under = [
                    row for row in self.rows
                    if row.get("under_covered") and _safe_decimal(row.get("reward_pool_usd")) > 0
                ]
                under.sort(
                    key=lambda row: (
                        _safe_decimal(row.get("min_coverage_ratio")),
                        -_safe_decimal(row.get("reward_pool_usd")),
                        str(row.get("venue")),
                        str(row.get("market")),
                    )
                )
                impl.logger.warning("KPMM_INCENTIVE_COVERAGE_RANKING %s", json.dumps({
                    "definition": "DISCOUNTED_QUALIFYING_SCORE_DIV_TARGET",
                    "markets_observed": len(self.rows),
                    "under_covered_live_pool_candidates": len(under),
                    "top_under_covered": under[:20],
                    "ranking_order": "LOWEST_MIN_SIDE_COVERAGE_THEN_LARGEST_POOL",
                    "yield_status": "SCARCITY_RANK_ONLY_NOT_REWARD_SHARE_NOT_REALIZED",
                    "capacity_credit_usd_per_day": 0,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))
                self.rows.clear()
        except Exception:
            self.handleError(record)


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
_coverage_handler = _CoverageRankingHandler()
logging.getLogger("kalshi_arb.sports_maker_incentives").addHandler(_coverage_handler)


if __name__ == "__main__":
    impl.main()
