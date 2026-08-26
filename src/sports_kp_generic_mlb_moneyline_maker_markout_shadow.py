"""Aggregate delayed-hedge markout telemetry for the read-only K/P MLB maker shadow.

This is instrumentation only. It preserves the existing pessimistic Kalshi maker-fill
model and never submits orders. Every simulated fill is evaluated at 80/250/500 ms
using the then-current reconstructed Polymarket opposite ask.

Polymarket market-data is a stateful order book: no message after a Kalshi fill can
correctly mean that the resting ask did not change. Therefore a post-fill market-specific
update is NOT required. Delayed hedge credit instead requires: the Polymarket WS is
connected, the overall WS stream is live within 1 second of the checkpoint, the current
reconstructed market book is available, and q=1 ask depth remains displayed.

Missing/unobservable delayed books are not silently dropped. They are counted and also
contribute the maker leg's worst-case loss to a deliberately harsh diagnostic lower
bound. This lower bound is not an expected-value estimate.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

from src import sports_kp_generic_mlb_moneyline_maker_fill_shadow as generic

impl = generic.impl
D = Decimal
ZERO = D("0")
DELAYS_MS = (80, 250, 500)
MAX_GLOBAL_WS_SILENCE_MS = 1000
logger = logging.getLogger("kalshi_arb.sports_kp_mlb_moneyline_maker_markout_shadow")


def _bucket() -> dict[str, Any]:
    return {
        "completed": 0,
        "executable": 0,
        "hedge_missing": 0,
        "ws_unhealthy": 0,
        "depth_insufficient": 0,
        "pnl_sum": ZERO,
        "markout_delta_sum": ZERO,
        "worst_case_lower_bound_sum": ZERO,
        "gte_1c": 0,
        "gte_2c": 0,
        "gte_3c": 0,
    }


class _AggregateMarkoutMakerFillShadow(generic._IncentiveAwareMakerFillShadow):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.markout_started_ms = int(time.time() * 1000)
        self.markouts = {delay: _bucket() for delay in DELAYS_MS}
        self._fill_profit_by_quote: dict[int, Decimal] = {}

    async def _record_fill(self, q: Any, trade: Any, fill_reason: str) -> None:
        hedge = self._poly_hedge(q)
        if hedge is not None:
            hedge_price, hedge_depth, _ = hedge
            if D(str(hedge_depth)) >= D("1"):
                profit_fee, _, _, _ = self._profit(q.maker_price, hedge_price)
                self._fill_profit_by_quote[int(q.quote_id)] = profit_fee
        await super()._record_fill(q, trade, fill_reason)

    def _maker_worst_case(self, q: Any) -> Decimal:
        maker_fee = impl._kalshi_maker_fee_q1(D(str(q.maker_price)))
        return -(D(str(q.maker_price)) + D(str(maker_fee)))

    async def _delayed_hedge(self, q: Any, trade: Any, delay_ms: int) -> None:
        await asyncio.sleep(delay_ms / 1000)
        checkpoint_ms = int(time.time() * 1000)
        bucket = self.markouts[int(delay_ms)]
        bucket["completed"] += 1
        worst_case = self._maker_worst_case(q)

        hedge = self._poly_hedge(q)
        if hedge is None:
            bucket["hedge_missing"] += 1
            bucket["worst_case_lower_bound_sum"] += worst_case
            logger.warning("KPMM_HEDGE_MARKOUT %s", json.dumps({
                "quote_id": q.quote_id,
                "group_key": q.group_key,
                "delay_ms": delay_ms,
                "hedge_available": False,
                "poly_connected": bool(self.poly_store.connected),
                "worst_case_pnl_if_unhedged": str(worst_case),
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))
            return

        hedge_price, hedge_depth, received_ms = hedge
        hedge_price = D(str(hedge_price))
        hedge_depth = D(str(hedge_depth))
        received_ms = int(received_ms)
        market_book_age_ms = max(0, checkpoint_ms - received_ms)
        post_fill_market_update = received_ms >= int(trade.received_ms)
        ws_last_ms = int(getattr(self.poly_store, "last_ws_message_ms", 0) or 0)
        ws_silence_ms = max(0, checkpoint_ms - ws_last_ms) if ws_last_ms else None
        ws_healthy = bool(
            self.poly_store.connected
            and ws_silence_ms is not None
            and ws_silence_ms <= MAX_GLOBAL_WS_SILENCE_MS
        )
        depth_ok = hedge_depth >= D("1")
        executable = bool(ws_healthy and depth_ok)

        profit_fee, profit_zero, maker_fee, hedge_fee = self._profit(q.maker_price, hedge_price)
        fill_profit = self._fill_profit_by_quote.get(int(q.quote_id))
        markout_delta = profit_fee - fill_profit if fill_profit is not None else None

        if not ws_healthy:
            bucket["ws_unhealthy"] += 1
        elif not depth_ok:
            bucket["depth_insufficient"] += 1

        if executable:
            bucket["executable"] += 1
            bucket["pnl_sum"] += profit_fee
            bucket["worst_case_lower_bound_sum"] += profit_fee
            if markout_delta is not None:
                bucket["markout_delta_sum"] += markout_delta
            if profit_fee >= D("0.01"):
                bucket["gte_1c"] += 1
            if profit_fee >= D("0.02"):
                bucket["gte_2c"] += 1
            if profit_fee >= D("0.03"):
                bucket["gte_3c"] += 1
        else:
            bucket["worst_case_lower_bound_sum"] += worst_case

        logger.info("KPMM_HEDGE_MARKOUT %s", json.dumps({
            "quote_id": q.quote_id,
            "group_key": q.group_key,
            "ticker": q.ticker,
            "delay_ms": delay_ms,
            "checkpoint_ms": checkpoint_ms,
            "fill_received_ms": int(trade.received_ms),
            "maker_price": str(q.maker_price),
            "poly_hedge_price": str(hedge_price),
            "poly_hedge_depth": str(hedge_depth),
            "poly_market_book_received_ms": received_ms,
            "poly_market_book_age_at_checkpoint_ms": market_book_age_ms,
            "post_fill_market_update_observed": post_fill_market_update,
            "post_fill_market_update_required": False,
            "poly_connected": bool(self.poly_store.connected),
            "poly_last_ws_message_ms": ws_last_ms,
            "poly_global_ws_silence_ms": ws_silence_ms,
            "poly_global_ws_healthy_1000ms": ws_healthy,
            "q1_depth_ok": depth_ok,
            "delayed_hedge_executable": executable,
            "fill_time_profit_with_maker_fee": str(fill_profit) if fill_profit is not None else None,
            "profit_with_maker_fee_at_delay": str(profit_fee),
            "markout_delta_vs_fill_time": str(markout_delta) if markout_delta is not None else None,
            "profit_zero_maker_fee_upper": str(profit_zero),
            "maker_fee": str(maker_fee),
            "poly_taker_fee": str(hedge_fee),
            "worst_case_pnl_if_unhedged": str(worst_case),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

    def _markout_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "classification": "READ_ONLY_PESSIMISTIC_KALSHI_MAKER_PLUS_DELAYED_POLY_TAKER_MARKOUT",
            "markout_started_ms": self.markout_started_ms,
            "simulated_q1_fills_total": int(self.stats.simulated_fills),
            "immediate_hedged_fills": int(self.shadow_fills_with_hedge),
            "immediate_pnl_usd": str(self.shadow_trading_pnl_at_fill),
            "delayed_book_credit_rule": "CURRENT_RECONSTRUCTED_BOOK_PLUS_CONNECTED_GLOBAL_WS_SILENCE_LE_1000MS_PLUS_Q1_ASK_DEPTH_GE_1",
            "stateful_book_note": "POST_FILL_MARKET_UPDATE_NOT_REQUIRED_IF_WS_STREAM_REMAINS_LIVE",
            "unobservable_policy": "NO_EXECUTABLE_CREDIT_PLUS_WORST_CASE_UNHEDGED_LOSS_IN_LOWER_BOUND",
            "capacity_credit_usd_per_day": 0,
            "read_only": True,
            "order_entry": False,
        }
        for delay in DELAYS_MS:
            b = self.markouts[delay]
            completed = int(b["completed"])
            executable = int(b["executable"])
            pnl = D(str(b["pnl_sum"]))
            out[f"t{delay}ms"] = {
                "completed": completed,
                "executable": executable,
                "executable_coverage": (executable / completed) if completed else None,
                "hedge_missing": int(b["hedge_missing"]),
                "ws_unhealthy": int(b["ws_unhealthy"]),
                "depth_insufficient": int(b["depth_insufficient"]),
                "executable_pnl_usd": str(pnl),
                "avg_pnl_per_executable_fill": str(pnl / D(executable)) if executable else None,
                "markout_delta_vs_fill_time_sum": str(b["markout_delta_sum"]),
                "worst_case_full_sample_lower_bound_usd": str(b["worst_case_lower_bound_sum"]),
                "gte_1c": int(b["gte_1c"]),
                "gte_2c": int(b["gte_2c"]),
                "gte_3c": int(b["gte_3c"]),
            }
        return out

    def _maybe_summary(self) -> None:
        before = self.last_summary
        super()._maybe_summary()
        if self.last_summary == before:
            return
        logger.warning("KPMM_DELAYED_MARKOUT_SUMMARY %s", json.dumps(
            self._markout_summary(), separators=(",", ":"), default=str
        ))


impl.MakerFillShadow = _AggregateMarkoutMakerFillShadow


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.warning(
        "KPMM_DELAYED_MARKOUT_ENABLED delays_ms=80,250,500 stateful_poly_book=true "
        "post_fill_market_update_required=false global_ws_silence_max_ms=1000 "
        "q1_depth_required=true read_only=true order_entry=false"
    )
    impl.main()


if __name__ == "__main__":
    main()
