"""Aggregate full-ladder delayed-hedge markout telemetry for K/P MLB maker shadow.

Read-only instrumentation only; no order transport is imported or authorized.
The existing pessimistic Kalshi maker-fill model is preserved. For every simulated
q=1 maker fill, the Polymarket opposite leg is marked at fill receipt and again at
80/250/500 ms using the *full current reconstructed ladder*, not only top-of-book.

A q=1 hedge is modeled executable when:
* the Polymarket market WS is connected and the overall stream has delivered a message
  within 1 second of the checkpoint;
* a full ladder is available for the market; and
* cumulative displayed depth can fill the entire q=1 order.

The modeled hedge price is the quantity-weighted average price required to sweep q=1.
The existing fee function is then applied to that q=1 VWAP. A market-specific post-fill
update is not required because the websocket book is stateful: no update can correctly
mean the resting ladder did not change.

Unobservable/unfillable checkpoints receive no executable credit and are also charged a
harsh full maker-leg loss in a diagnostic lower bound. That lower bound is not EV.
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
ONE = D("1")
DELAYS_MS = (80, 250, 500)
MAX_GLOBAL_WS_SILENCE_MS = 1000
logger = logging.getLogger("kalshi_arb.sports_kp_mlb_moneyline_maker_markout_shadow")

# Capture the full Polymarket ladder on the same accepted WS/REST payloads already used
# by PolyBookStore for top-of-book. This changes telemetry only; base quote behavior is
# untouched.
_ORIGINAL_POLY_INGEST = impl.PolyBookStore.ingest


def _normalized_poly_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("marketData")
    if isinstance(data, dict):
        return data
    data = payload.get("market_data")
    if isinstance(data, dict):
        return data
    return payload


def _capture_full_poly_ladder(self: Any, payload: Any, *, source: str) -> bool:
    accepted = _ORIGINAL_POLY_INGEST(self, payload, source=source)
    if not accepted:
        return False
    data = _normalized_poly_payload(payload)
    if data is None:
        return True
    slug = str(data.get("marketSlug") or data.get("market_slug") or "")
    if not slug:
        return True

    bids_raw = data.get("bids") or []
    asks_raw = data.get("offers") or data.get("asks") or []
    bids: list[tuple[Decimal, Decimal]] = []
    asks: list[tuple[Decimal, Decimal]] = []
    for raw in bids_raw:
        if not isinstance(raw, dict):
            continue
        px = self._amount(raw.get("px"))
        qty = self._quantity(raw)
        if px is not None and qty is not None and ZERO <= px <= ONE and qty > 0:
            bids.append((D(px), D(qty)))
    for raw in asks_raw:
        if not isinstance(raw, dict):
            continue
        px = self._amount(raw.get("px"))
        qty = self._quantity(raw)
        if px is not None and qty is not None and ZERO <= px <= ONE and qty > 0:
            asks.append((D(px), D(qty)))
    if not bids or not asks:
        return True

    # YES asks cheapest first; YES bids richest first so transformed NO asks are cheapest.
    asks.sort(key=lambda row: row[0])
    bids.sort(key=lambda row: row[0], reverse=True)
    quote = self._quotes.get(slug)
    received_ms = int(getattr(quote, "received_ms", int(time.time() * 1000)))
    ladders = getattr(self, "_arb_full_ladders", None)
    if ladders is None:
        ladders = {}
        setattr(self, "_arb_full_ladders", ladders)
    ladders[slug] = {
        "bids": bids,
        "asks": asks,
        "received_ms": received_ms,
        "source": source,
    }
    return True


impl.PolyBookStore.ingest = _capture_full_poly_ladder


def _bucket() -> dict[str, Any]:
    return {
        "completed": 0,
        "executable": 0,
        "hedge_missing": 0,
        "ws_unhealthy": 0,
        "full_ladder_missing": 0,
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
        self._fill_sweep_by_quote: dict[int, dict[str, Any]] = {}
        self.q1_sweep_immediate_pnl = ZERO
        self.q1_sweep_immediate_executable = 0
        self.q1_sweep_immediate_unfillable = 0

    def _q1_sweep(self, q: Any) -> dict[str, Any] | None:
        ladders = getattr(self.poly_store, "_arb_full_ladders", {})
        ladder = ladders.get(q.poly_slug)
        if not ladder:
            return None
        if q.poly_hedge_side == "YES":
            levels = [(D(px), D(qty)) for px, qty in ladder["asks"]]
        elif q.poly_hedge_side == "NO":
            # Buying NO is economically equivalent to lifting transformed YES bids.
            levels = [(ONE - D(px), D(qty)) for px, qty in ladder["bids"]]
            levels.sort(key=lambda row: row[0])
        else:
            return None

        remaining = ONE
        notional = ZERO
        levels_used = 0
        worst_price: Decimal | None = None
        total_depth = sum((qty for _, qty in levels), ZERO)
        top_price = levels[0][0] if levels else None
        top_depth = levels[0][1] if levels else ZERO
        for price, qty in levels:
            if remaining <= 0:
                break
            take = min(remaining, qty)
            if take <= 0:
                continue
            notional += take * price
            remaining -= take
            levels_used += 1
            worst_price = price
        fillable = remaining <= 0
        return {
            "fillable": fillable,
            "filled_quantity": str(ONE - max(ZERO, remaining)),
            "displayed_total_depth": str(total_depth),
            "top_price": str(top_price) if top_price is not None else None,
            "top_depth": str(top_depth),
            "q1_vwap": str(notional) if fillable else None,
            "worst_price": str(worst_price) if worst_price is not None else None,
            "levels_used": levels_used,
            "received_ms": int(ladder["received_ms"]),
            "source": str(ladder.get("source") or ""),
        }

    async def _record_fill(self, q: Any, trade: Any, fill_reason: str) -> None:
        # Capture a full q1 executable baseline at fill receipt. Keep the legacy
        # top-of-book immediate summary intact for comparison, but do not use it as the
        # markout baseline.
        sweep = self._q1_sweep(q)
        if sweep is not None and bool(sweep["fillable"]):
            avg_price = D(str(sweep["q1_vwap"]))
            profit_fee, _, _, _ = self._profit(q.maker_price, avg_price)
            self._fill_profit_by_quote[int(q.quote_id)] = profit_fee
            self._fill_sweep_by_quote[int(q.quote_id)] = sweep
            self.q1_sweep_immediate_pnl += profit_fee
            self.q1_sweep_immediate_executable += 1
        else:
            self.q1_sweep_immediate_unfillable += 1
        await super()._record_fill(q, trade, fill_reason)

    def _maker_worst_case(self, q: Any) -> Decimal:
        maker_fee = impl._kalshi_maker_fee_q1(D(str(q.maker_price)))
        return -(D(str(q.maker_price)) + D(str(maker_fee)))

    def _ws_health(self, checkpoint_ms: int) -> tuple[bool, int | None]:
        ws_last_ms = int(getattr(self.poly_store, "last_ws_message_ms", 0) or 0)
        silence = max(0, checkpoint_ms - ws_last_ms) if ws_last_ms else None
        healthy = bool(
            self.poly_store.connected
            and silence is not None
            and silence <= MAX_GLOBAL_WS_SILENCE_MS
        )
        return healthy, silence

    async def _delayed_hedge(self, q: Any, trade: Any, delay_ms: int) -> None:
        await asyncio.sleep(delay_ms / 1000)
        checkpoint_ms = int(time.time() * 1000)
        bucket = self.markouts[int(delay_ms)]
        bucket["completed"] += 1
        worst_case = self._maker_worst_case(q)
        ws_healthy, ws_silence_ms = self._ws_health(checkpoint_ms)
        sweep = self._q1_sweep(q)

        if sweep is None:
            bucket["full_ladder_missing"] += 1
            bucket["worst_case_lower_bound_sum"] += worst_case
            logger.warning("KPMM_HEDGE_MARKOUT %s", json.dumps({
                "quote_id": q.quote_id,
                "group_key": q.group_key,
                "delay_ms": delay_ms,
                "full_ladder_available": False,
                "poly_connected": bool(self.poly_store.connected),
                "poly_global_ws_silence_ms": ws_silence_ms,
                "worst_case_pnl_if_unhedged": str(worst_case),
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))
            return

        if not ws_healthy:
            bucket["ws_unhealthy"] += 1
        if not bool(sweep["fillable"]):
            bucket["depth_insufficient"] += 1

        executable = bool(ws_healthy and sweep["fillable"])
        profit_fee = profit_zero = maker_fee = hedge_fee = None
        markout_delta = None
        delayed_price = D(str(sweep["q1_vwap"])) if sweep["fillable"] else None
        if delayed_price is not None:
            profit_fee, profit_zero, maker_fee, hedge_fee = self._profit(q.maker_price, delayed_price)
            fill_profit = self._fill_profit_by_quote.get(int(q.quote_id))
            markout_delta = profit_fee - fill_profit if fill_profit is not None else None
        else:
            fill_profit = self._fill_profit_by_quote.get(int(q.quote_id))

        if executable and profit_fee is not None:
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

        ladder_received_ms = int(sweep["received_ms"])
        logger.info("KPMM_HEDGE_MARKOUT %s", json.dumps({
            "quote_id": q.quote_id,
            "group_key": q.group_key,
            "ticker": q.ticker,
            "delay_ms": delay_ms,
            "checkpoint_ms": checkpoint_ms,
            "fill_received_ms": int(trade.received_ms),
            "maker_price": str(q.maker_price),
            "poly_hedge_side": q.poly_hedge_side,
            "full_ladder_available": True,
            "poly_ladder_source": sweep["source"],
            "poly_ladder_received_ms": ladder_received_ms,
            "poly_ladder_age_ms": max(0, checkpoint_ms - ladder_received_ms),
            "post_fill_market_update_observed": ladder_received_ms >= int(trade.received_ms),
            "post_fill_market_update_required": False,
            "poly_connected": bool(self.poly_store.connected),
            "poly_global_ws_silence_ms": ws_silence_ms,
            "poly_global_ws_healthy_1000ms": ws_healthy,
            "q1_full_sweep_fillable": bool(sweep["fillable"]),
            "q1_filled_quantity": sweep["filled_quantity"],
            "q1_displayed_total_depth": sweep["displayed_total_depth"],
            "q1_top_price": sweep["top_price"],
            "q1_top_depth": sweep["top_depth"],
            "q1_vwap": sweep["q1_vwap"],
            "q1_worst_price": sweep["worst_price"],
            "q1_levels_used": sweep["levels_used"],
            "delayed_hedge_executable": executable,
            "fill_time_q1_sweep_profit_with_maker_fee": str(fill_profit) if fill_profit is not None else None,
            "profit_with_maker_fee_at_delay": str(profit_fee) if profit_fee is not None else None,
            "markout_delta_vs_fill_time": str(markout_delta) if markout_delta is not None else None,
            "profit_zero_maker_fee_upper": str(profit_zero) if profit_zero is not None else None,
            "maker_fee": str(maker_fee) if maker_fee is not None else None,
            "poly_taker_fee": str(hedge_fee) if hedge_fee is not None else None,
            "worst_case_pnl_if_unhedged": str(worst_case),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

    def _markout_summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "classification": "READ_ONLY_PESSIMISTIC_KALSHI_MAKER_PLUS_FULL_LADDER_DELAYED_POLY_TAKER_MARKOUT",
            "markout_started_ms": self.markout_started_ms,
            "simulated_q1_fills_total": int(self.stats.simulated_fills),
            "legacy_top_book_immediate_hedged_fills": int(self.shadow_fills_with_hedge),
            "legacy_top_book_immediate_pnl_usd": str(self.shadow_trading_pnl_at_fill),
            "q1_sweep_immediate_executable": self.q1_sweep_immediate_executable,
            "q1_sweep_immediate_unfillable": self.q1_sweep_immediate_unfillable,
            "q1_sweep_immediate_pnl_usd": str(self.q1_sweep_immediate_pnl),
            "delayed_book_credit_rule": "FULL_Q1_LADDER_SWEEP_PLUS_CONNECTED_GLOBAL_WS_SILENCE_LE_1000MS",
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
                "full_ladder_missing": int(b["full_ladder_missing"]),
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
        "KPMM_DELAYED_MARKOUT_ENABLED delays_ms=80,250,500 full_poly_ladder=true "
        "q1_vwap_sweep=true post_fill_market_update_required=false "
        "global_ws_silence_max_ms=1000 read_only=true order_entry=false"
    )
    impl.main()


if __name__ == "__main__":
    main()
