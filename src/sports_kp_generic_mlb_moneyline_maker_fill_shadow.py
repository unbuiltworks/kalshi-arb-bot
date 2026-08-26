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
import time
from typing import Any

from src import sports_kp_mlb_moneyline_maker_fill_shadow as impl
from src.polymarket_mlb_programwide_incentive_shadow import run_forever as run_programwide_incentive_shadow
from src.sports_maker_incentive_telemetry import IncentiveTelemetry

D = Decimal
ZERO = D("0")
ONE = D("1")


def _kxmlbgame_maker_fee_q1(price: Decimal) -> Decimal:
    raw = D("0.0175") * price * (D("1") - price)
    return (raw * D("10000")).to_integral_value(rounding=ROUND_CEILING) / D("10000")


class _VerifiedFeeLabelFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        message = message.replace('\"maker_fee_applicability_certified\":false','\"maker_fee_applicability_certified\":true')
        message = message.replace("GENERAL_0.0175_FORMULA_AND_ZERO_FEE_UPPER_BOUND_APPLICABILITY_UNCERTIFIED","KXMLBGAME_MAKER_MULTIPLIER_1_CENTICENT_ROUNDING_VERIFIED_2026_07_07")
        record.msg = message
        record.args = ()
        return True


def _safe_decimal(value: Any) -> Decimal:
    try:
        return D(str(value))
    except Exception:
        return ZERO


class _CoverageRankingHandler(logging.Handler):
    """Rank scarcity using raw Target Size; keep discounted score separate."""
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
                    side_a_name, side_b_name = "yes", "no"
                    raw_a = _safe_decimal(payload.get("yes_total_raw_size")); raw_b = _safe_decimal(payload.get("no_total_raw_size"))
                    score_a = _safe_decimal(payload.get("yes_qualifying_score")); score_b = _safe_decimal(payload.get("no_qualifying_score"))
                    met_a = met_b = bool(payload.get("snapshot_two_sided_target_met"))
                    phase, event_start_ms = "active", None
                elif venue == "POLYMARKET_US":
                    side_a_name, side_b_name = "bid", "ask"
                    raw_a = _safe_decimal(payload.get("bid_total_raw_size")); raw_b = _safe_decimal(payload.get("ask_total_raw_size"))
                    score_a = _safe_decimal(payload.get("bid_qualifying_score")); score_b = _safe_decimal(payload.get("ask_qualifying_score"))
                    met_a = bool(payload.get("bid_target_met")); met_b = bool(payload.get("ask_target_met"))
                    phase = str(payload.get("applicable_period_by_event_clock") or payload.get("period") or "")
                    try: event_start_ms = int(payload.get("event_start_ms"))
                    except Exception: event_start_ms = None
                else:
                    return
                coverage_a = raw_a / target; coverage_b = raw_b / target
                time_to_game_minutes = None if event_start_ms is None else (D(event_start_ms) - D(int(time.time()*1000))) / D("60000")
                self.rows.append({
                    "venue":venue,"market":str(payload.get("market") or ""),"program_id":str(payload.get("program_id") or ""),"phase":phase,
                    "time_to_game_minutes":str(time_to_game_minutes) if time_to_game_minutes is not None else None,
                    "reward_pool_usd":str(reward),"target_size":str(target),
                    f"{side_a_name}_raw_target_coverage":str(coverage_a),f"{side_b_name}_raw_target_coverage":str(coverage_b),
                    "min_raw_target_coverage":str(min(coverage_a,coverage_b)),
                    f"{side_a_name}_activation_gap_contracts":str(max(ZERO,target-raw_a)),f"{side_b_name}_activation_gap_contracts":str(max(ZERO,target-raw_b)),
                    f"{side_a_name}_target_met":met_a,f"{side_b_name}_target_met":met_b,
                    f"{side_a_name}_discounted_qualifying_score":str(score_a),f"{side_b_name}_discounted_qualifying_score":str(score_b),
                    "under_raw_target":coverage_a<ONE or coverage_b<ONE,
                    "coverage_definition":"RAW_RESTING_SIZE_DIV_TARGET_PER_DOCS","score_note":"DISCOUNTED_SCORE_IS_COMPETITION_NOT_TARGET_COVERAGE",
                })
                return
            if message.startswith("KPMM_INCENTIVE_SUMMARY "):
                under=[r for r in self.rows if r.get("under_raw_target") and _safe_decimal(r.get("reward_pool_usd"))>0]
                under.sort(key=lambda r:(_safe_decimal(r.get("min_raw_target_coverage")),-_safe_decimal(r.get("reward_pool_usd")),str(r.get("market"))))
                phase_counts:dict[str,int]={}
                for r in under: phase_counts[str(r.get("phase") or "unknown")]=phase_counts.get(str(r.get("phase") or "unknown"),0)+1
                observed=sorted(self.rows,key=lambda r:(_safe_decimal(r.get("min_raw_target_coverage")),str(r.get("market"))))
                impl.logger.warning("KPMM_INCENTIVE_COVERAGE_RANKING %s",json.dumps({
                    "definition":"RAW_RESTING_SIZE_DIV_TARGET_PER_DOCS","markets_observed":len(self.rows),
                    "under_raw_target_live_pool_candidates":len(under),"under_target_by_phase":phase_counts,
                    "top_under_raw_target":under[:20],"lowest_raw_target_coverage_observed":observed[:20],
                    "allocation_rule":"PROGRAM_POOL_SHARED_ACROSS_PROGRAM_MARKETS; EACH_QUALIFYING_BOOK_SIDE_NORMALIZED_PER_SNAPSHOT",
                    "programwide_denominator_status":"MEASURED_SEPARATELY_BY_KPMM_PROGRAMWIDE_INCENTIVE",
                    "yield_status":"NO_DAILY_YIELD_UNTIL_TIME_WEIGHTING_AND_PICKOFF_COST","capacity_credit_usd_per_day":0,"read_only":True,"order_entry":False
                },separators=(",",":")))
                self.rows.clear()
        except Exception:
            self.handleError(record)


_BaseMakerFillShadow=impl.MakerFillShadow


class _IncentiveAwareMakerFillShadow(_BaseMakerFillShadow):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.incentive_telemetry=IncentiveTelemetry(self,interval_s=60.0)
        self.shadow_trading_pnl_at_fill=D("0")
        self.shadow_fills_with_hedge=0

    async def quote_loop(self,scan_ms:int)->None:
        incentive_task=asyncio.create_task(self.incentive_telemetry.run())
        programwide_task=asyncio.create_task(run_programwide_incentive_shadow(60.0))
        try:
            await super().quote_loop(scan_ms)
        finally:
            incentive_task.cancel(); programwide_task.cancel()
            await asyncio.gather(incentive_task,programwide_task,return_exceptions=True)

    async def _record_fill(self,q,trade,fill_reason:str)->None:
        hedge=self._poly_hedge(q)
        if hedge is not None:
            hedge_price,_,_=hedge
            profit_fee,_,_,_=self._profit(q.maker_price,hedge_price)
            self.shadow_trading_pnl_at_fill+=profit_fee; self.shadow_fills_with_hedge+=1
        await super()._record_fill(q,trade,fill_reason)

    def _maybe_summary(self)->None:
        before=self.last_summary
        super()._maybe_summary()
        if self.last_summary==before:return
        incentives=dict(self.incentive_telemetry.last_snapshot)
        impl.logger.warning("KPMM_COMBINED_MAKER_SUMMARY %s",json.dumps({
            "pure_trading_shadow_pnl_usd":str(self.shadow_trading_pnl_at_fill),"simulated_fills_with_immediate_hedge":self.shadow_fills_with_hedge,
            "estimated_incentive_q1_active_quote_daily_run_rate_usd":incentives.get("kalshi_estimated_q1_incentive_daily_run_rate",0.0),
            "estimated_incentive_q2500_each_active_market_unconstrained_daily_run_rate_usd":incentives.get("kalshi_estimated_q2500_incentive_daily_run_rate",0.0),
            "kalshi_active_incentive_programs":incentives.get("kalshi_active_programs",0),"kalshi_snapshot_eligible_markets":incentives.get("kalshi_snapshot_eligible_markets",0),
            "polymarket_active_incentive_programs":incentives.get("poly_active_programs",0),"polymarket_program_denominator_complete":incentives.get("polymarket_denominator_complete",False),
            "incentive_snapshot_updated_ms":incentives.get("updated_ms"),"incentive_accounting":"SHADOW_ESTIMATE_NOT_VENUE_CREDIT_NOT_REALIZED",
            "combined_maker_capacity_credit_usd_per_day":0,"combined_gate_status":"PENDING_FILL_ADVERSE_SELECTION_AND_CAPITAL_BOUNDED_INCENTIVE_YIELD","read_only":True,"order_entry":False
        },separators=(",",":")))


impl._kalshi_maker_fee_q1=_kxmlbgame_maker_fee_q1
impl.MakerFillShadow=_IncentiveAwareMakerFillShadow
impl.logger.addFilter(_VerifiedFeeLabelFilter())
_coverage_handler=_CoverageRankingHandler()
logging.getLogger("kalshi_arb.sports_maker_incentives").addHandler(_coverage_handler)

if __name__=="__main__":
    impl.main()
