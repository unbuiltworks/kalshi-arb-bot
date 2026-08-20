"""Latency/adverse-selection stress layer for the maker shadow.

After each simulated maker fill, observe subsequent Gemini bookTicker updates and
revalue the hedge at 25/50/100/250ms. This remains read-only research telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from src import gemini_kalshi_maker_shadow as maker

D = Decimal
DEFAULT_DELAYS_MS = (25, 50, 100, 250)


@dataclass
class PendingStress:
    asset: str
    key: str
    direction: str
    policy: str
    target_profit: D
    maker_price: D
    maker_fee: D
    fill_qty: D
    fill_receive_ms: int
    remaining_delays: set[int] = field(default_factory=set)


def stress_economics(pending: PendingStress, gemini: maker.TopOfBook) -> dict[str, Any]:
    _, hedge_outcome = maker._direction_outcomes(pending.direction)
    hedge_price, hedge_size = maker.hedge_price_and_size(gemini, hedge_outcome)
    if hedge_size < maker.QTY or not (D("0") < hedge_price < D("1")):
        return {
            "hedge_depth_ok": False,
            "hedge_outcome": hedge_outcome,
            "hedge_price": str(hedge_price),
            "hedge_fee": None,
            "stressed_locked_profit_q1": None,
            "stressed_fill_profit": None,
            "survives_positive": False,
            "survives_target": False,
        }
    hedge_fee = maker.gemini_predictions_taker_fee(hedge_price, maker.QTY)
    profit_q1 = maker.QTY - (pending.maker_price + pending.maker_fee + hedge_price + hedge_fee)
    return {
        "hedge_depth_ok": True,
        "hedge_outcome": hedge_outcome,
        "hedge_price": str(hedge_price),
        "hedge_fee": str(hedge_fee),
        "stressed_locked_profit_q1": str(profit_q1),
        "stressed_fill_profit": str(profit_q1 * pending.fill_qty),
        "survives_positive": profit_q1 > 0,
        "survives_target": profit_q1 >= pending.target_profit,
    }


class HedgeLatencyStress:
    def __init__(self, output_dir: Path, delays_ms: tuple[int, ...] = DEFAULT_DELAYS_MS) -> None:
        self.output_dir = output_dir
        self.delays_ms = tuple(sorted({int(x) for x in delays_ms if int(x) >= 0}))
        self.pending: dict[str, list[PendingStress]] = {}

    def schedule_from_events(self, asset: str, receive_ms: int, events: list[dict[str, Any]]) -> None:
        bucket = self.pending.setdefault(asset, [])
        for row in events:
            if str(row.get("event") or "") != "SIMULATED_FILL":
                continue
            bucket.append(PendingStress(
                asset=asset,
                key=str(row.get("key") or ""),
                direction=str(row.get("direction") or ""),
                policy=str(row.get("policy") or ""),
                target_profit=D(str(row.get("target_profit") or "0")),
                maker_price=D(str(row.get("maker_price") or "0")),
                maker_fee=D(str(row.get("maker_fee") or "0")),
                fill_qty=D(str(row.get("fill_qty") or "0")),
                fill_receive_ms=int(receive_ms),
                remaining_delays=set(self.delays_ms),
            ))

    def observe_gemini(self, asset: str, receive_ms: int, gemini: maker.TopOfBook) -> list[dict[str, Any]]:
        emitted: list[dict[str, Any]] = []
        bucket = self.pending.get(asset, [])
        keep: list[PendingStress] = []
        for pending in bucket:
            elapsed = int(receive_ms) - pending.fill_receive_ms
            due = [d for d in pending.remaining_delays if elapsed >= d]
            for delay in sorted(due):
                # Require a Gemini book received at/after the intended delay, never
                # reuse the pre-fill book merely because wall time advanced.
                if int(gemini.ts_ms) < pending.fill_receive_ms + delay:
                    continue
                econ = stress_economics(pending, gemini)
                row = {
                    "event": "HEDGE_LATENCY_STRESS",
                    "asset": asset,
                    "receive_ts_ms": int(receive_ms),
                    "fill_receive_ts_ms": pending.fill_receive_ms,
                    "key": pending.key,
                    "direction": pending.direction,
                    "policy": pending.policy,
                    "target_profit": str(pending.target_profit),
                    "maker_price": str(pending.maker_price),
                    "maker_fee": str(pending.maker_fee),
                    "fill_qty": str(pending.fill_qty),
                    "delay_ms": delay,
                    "observed_elapsed_ms": elapsed,
                    "gemini_book_ts_ms": int(gemini.ts_ms),
                    **econ,
                    "settlement_status": maker.SETTLEMENT_STATUS,
                    "execution_eligible": False,
                    "read_only": True,
                    "order_entry": False,
                }
                emitted.append(row)
                pending.remaining_delays.discard(delay)
            if pending.remaining_delays:
                keep.append(pending)
        self.pending[asset] = keep
        return emitted

    def write_rows(self, asset: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"maker_stress_{asset.lower()}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def install(output_dir: Path, delays_ms: tuple[int, ...] = DEFAULT_DELAYS_MS) -> HedgeLatencyStress:
    """Instrument the existing maker module without changing its order-free core."""
    stress = HedgeLatencyStress(output_dir, delays_ms)
    original_process_trade = maker._process_trade
    original_enqueue = maker._enqueue

    def process_trade_with_stress(*, active, trade, receive_ms, gemini, fee_meta, max_hedge_age_ms, stats):
        events = original_process_trade(
            active=active,
            trade=trade,
            receive_ms=receive_ms,
            gemini=gemini,
            fee_meta=fee_meta,
            max_hedge_age_ms=max_hedge_age_ms,
            stats=stats,
        )
        stress.schedule_from_events(stats.asset, receive_ms, events)
        return events

    def enqueue_with_stress(queue, item, stats):
        kind, receive_ms, payload = item
        if kind == "gemini":
            rows = stress.observe_gemini(stats.asset, receive_ms, payload)
            stress.write_rows(stats.asset, rows)
        return original_enqueue(queue, item, stats)

    maker._process_trade = process_trade_with_stress
    maker._enqueue = enqueue_with_stress
    return stress
