"""Read-only bankroll recycling simulation for BTC Gemini/Kalshi WS shadow logs.

This answers a narrower practical question than aggregate shadow P/L: given a
fixed Kalshi bankroll and a per-trade dollar cap, how much of the observed
fee-positive liquidity could have been attempted without reusing capital before
it is released?

The simulator remains execution-shadow research only. It assumes both legs fill
at the observed quotes and does not solve Gemini/Kalshi settlement-index mismatch.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_DOWN
import json
from pathlib import Path
from typing import Any

from src.gemini_kalshi_btc_ws_analyze import D, _consume, episodes, load_rows
from src.gemini_kalshi_btc_ws_capital import (
    _candidate_at,
    _window_end_ms,
    settlement_delay_scenarios_from_summary,
)
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

QTY_STEP = D("0.01")


def _leg_prices(candidate: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Return (kalshi_price, gemini_price) from the candidate liquidity identity."""
    direction = str(candidate.get("direction") or "")
    parts = str(candidate.get("liquidity_key") or "").split("|")
    if len(parts) < 2:
        raise ValueError("candidate is missing executable price identity")

    if direction == "KALSHI_YES_GEMINI_NO":
        kalshi_price = D(parts[-2])
        gemini_price = D("1") - D(parts[-1])
    elif direction == "GEMINI_YES_KALSHI_NO":
        gemini_price = D(parts[-2])
        kalshi_price = D("1") - D(parts[-1])
    else:
        raise ValueError(f"unknown direction={direction!r}")

    if not (D("0") <= kalshi_price <= D("1") and D("0") <= gemini_price <= D("1")):
        raise ValueError("candidate has invalid leg price")
    return kalshi_price, gemini_price


def _costs(
    candidate: dict[str, Any],
    qty: Decimal,
    *,
    kalshi_fee_multiplier: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    kalshi_price, gemini_price = _leg_prices(candidate)
    k_fee = kalshi_quadratic_taker_fee(
        kalshi_price, qty, fee_multiplier=kalshi_fee_multiplier
    )
    g_fee = gemini_predictions_taker_fee(gemini_price, qty)
    k_cost = kalshi_price * qty + k_fee
    g_cost = gemini_price * qty + g_fee
    total = k_cost + g_cost
    profit = qty - total
    return k_cost, g_cost, total, profit


def _floor_qty(qty: Decimal) -> Decimal:
    if qty <= 0:
        return D("0")
    return (qty / QTY_STEP).to_integral_value(rounding=ROUND_DOWN) * QTY_STEP


def _max_affordable_qty(
    candidate: dict[str, Any],
    fresh_qty: Decimal,
    *,
    trade_cap: Decimal,
    kalshi_available: Decimal,
    gemini_available: Decimal | None,
    kalshi_fee_multiplier: Decimal,
) -> Decimal:
    """Binary-search the largest 0.01-contract quantity satisfying all caps."""
    high = _floor_qty(fresh_qty)
    if high <= 0:
        return D("0")

    def ok(qty: Decimal) -> bool:
        k_cost, g_cost, total, profit = _costs(
            candidate, qty, kalshi_fee_multiplier=kalshi_fee_multiplier
        )
        return (
            profit > 0
            and total <= trade_cap
            and k_cost <= kalshi_available
            and (gemini_available is None or g_cost <= gemini_available)
        )

    if ok(high):
        return high

    lo_steps = 0
    hi_steps = int(high / QTY_STEP)
    while lo_steps < hi_steps:
        mid_steps = (lo_steps + hi_steps + 1) // 2
        qty = QTY_STEP * D(mid_steps)
        if ok(qty):
            lo_steps = mid_steps
        else:
            hi_steps = mid_steps - 1
    return QTY_STEP * D(lo_steps)


def simulate(
    rows: list[dict[str, Any]],
    *,
    threshold_ms: int,
    kalshi_bankroll: Decimal,
    trade_cap: Decimal,
    kalshi_settlement_delay_s: Decimal = D("0"),
    gemini_bankroll: Decimal | None = None,
    gemini_settlement_delay_s: Decimal = D("0"),
    kalshi_fee_multiplier: Decimal = D("1"),
) -> dict[str, Any]:
    if threshold_ms < 0:
        raise ValueError("threshold_ms must be non-negative")
    for value, name in (
        (kalshi_bankroll, "kalshi_bankroll"),
        (trade_cap, "trade_cap"),
        (kalshi_settlement_delay_s, "kalshi_settlement_delay_s"),
        (gemini_settlement_delay_s, "gemini_settlement_delay_s"),
        (kalshi_fee_multiplier, "kalshi_fee_multiplier"),
    ):
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    if kalshi_bankroll <= 0 or trade_cap <= 0:
        raise ValueError("bankroll and trade cap must be positive")
    if gemini_bankroll is not None and gemini_bankroll <= 0:
        raise ValueError("gemini_bankroll must be positive when supplied")

    eps = episodes(rows)
    picked: list[tuple[int, Any, dict[str, Any]]] = []
    for ep in eps:
        item = _candidate_at(ep, threshold_ms)
        if item is not None:
            ts, candidate = item
            picked.append((ts, ep, candidate))
    picked.sort(key=lambda x: (x[0], x[1].market_key, x[1].direction))

    seen: dict[str, Decimal] = {}
    k_locks: list[tuple[int, Decimal]] = []
    g_locks: list[tuple[int, Decimal]] = []
    k_locked = D("0")
    g_locked = D("0")
    peak_k_locked = D("0")
    peak_g_locked = D("0")
    total_k_deployed = D("0")
    total_g_deployed = D("0")
    total_profit = D("0")
    total_contracts = D("0")
    trades = 0
    candidate_episodes = 0
    skipped_no_fresh = 0
    skipped_no_capital = 0
    partial_trades = 0

    for ts, ep, candidate in picked:
        # Release venue capital that would already be available at this event time.
        next_k: list[tuple[int, Decimal]] = []
        for release_ms, amount in k_locks:
            if release_ms <= ts:
                k_locked -= amount
            else:
                next_k.append((release_ms, amount))
        k_locks = next_k

        next_g: list[tuple[int, Decimal]] = []
        for release_ms, amount in g_locks:
            if release_ms <= ts:
                g_locked -= amount
            else:
                next_g.append((release_ms, amount))
        g_locks = next_g

        fresh_qty, _, _ = _consume(candidate, seen)
        if fresh_qty <= 0:
            skipped_no_fresh += 1
            continue
        candidate_episodes += 1

        k_available = max(D("0"), kalshi_bankroll - k_locked)
        g_available = None if gemini_bankroll is None else max(D("0"), gemini_bankroll - g_locked)
        qty = _max_affordable_qty(
            candidate,
            fresh_qty,
            trade_cap=trade_cap,
            kalshi_available=k_available,
            gemini_available=g_available,
            kalshi_fee_multiplier=kalshi_fee_multiplier,
        )
        if qty <= 0:
            skipped_no_capital += 1
            continue
        if qty < _floor_qty(fresh_qty):
            partial_trades += 1

        k_cost, g_cost, total_cost, profit = _costs(
            candidate, qty, kalshi_fee_multiplier=kalshi_fee_multiplier
        )
        if profit <= 0:
            continue

        end_ms = _window_end_ms(ep)
        if end_ms is None:
            # Fail closed: if we cannot identify release time, do not count the trade.
            continue
        k_release = end_ms + int(kalshi_settlement_delay_s * D("1000"))
        g_release = end_ms + int(gemini_settlement_delay_s * D("1000"))
        k_locks.append((k_release, k_cost))
        g_locks.append((g_release, g_cost))
        k_locked += k_cost
        g_locked += g_cost
        peak_k_locked = max(peak_k_locked, k_locked)
        peak_g_locked = max(peak_g_locked, g_locked)
        total_k_deployed += k_cost
        total_g_deployed += g_cost
        total_profit += profit
        total_contracts += qty
        trades += 1

    timestamps = [int(r["receive_ts_ms"]) for r in rows]
    observation_ms = timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else 0
    return {
        "threshold_ms": threshold_ms,
        "kalshi_bankroll_dollars": str(kalshi_bankroll),
        "gemini_bankroll_dollars": str(gemini_bankroll) if gemini_bankroll is not None else None,
        "gemini_bankroll_mode": "CAPPED" if gemini_bankroll is not None else "UNCONSTRAINED_MEASURE_REQUIRED_PEAK",
        "trade_cap_total_cost_dollars": str(trade_cap),
        "candidate_consumable_episodes": candidate_episodes,
        "trades_taken": trades,
        "partial_trades": partial_trades,
        "skipped_no_fresh_liquidity": skipped_no_fresh,
        "skipped_insufficient_capital_or_cap": skipped_no_capital,
        "contracts_traded": str(total_contracts),
        "shadow_profit_dollars": str(total_profit),
        "kalshi_total_capital_deployed_dollars": str(total_k_deployed),
        "gemini_total_capital_deployed_dollars": str(total_g_deployed),
        "total_capital_deployed_dollars": str(total_k_deployed + total_g_deployed),
        "peak_kalshi_capital_locked_dollars": str(peak_k_locked),
        "peak_gemini_capital_locked_dollars": str(peak_g_locked),
        "shadow_profit_on_kalshi_bankroll": str(total_profit / kalshi_bankroll),
        "kalshi_settlement_delay_s_assumed": str(kalshi_settlement_delay_s),
        "gemini_settlement_delay_s_assumed": str(gemini_settlement_delay_s),
        "observation_ms": observation_ms,
        "projection_warning": "SHORT_SHADOW_SAMPLE_NOT_A_FORECAST",
        "execution_warning": "ASSUMES_BOTH_LEGS_FILL_AT_OBSERVED_QUOTES",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def scenarios(
    rows: list[dict[str, Any]],
    *,
    threshold_ms: int,
    kalshi_bankroll: Decimal,
    trade_caps: list[Decimal],
    settlement_delays_s: dict[str, Decimal] | None = None,
    gemini_bankroll: Decimal | None = None,
    kalshi_fee_multiplier: Decimal = D("1"),
) -> dict[str, Any]:
    delays = {"window_end": D("0")}
    delays.update(settlement_delays_s or {})
    out: dict[str, Any] = {}
    for delay_label, delay_s in delays.items():
        out[delay_label] = {
            str(cap): simulate(
                rows,
                threshold_ms=threshold_ms,
                kalshi_bankroll=kalshi_bankroll,
                trade_cap=cap,
                kalshi_settlement_delay_s=delay_s,
                gemini_bankroll=gemini_bankroll,
                kalshi_fee_multiplier=kalshi_fee_multiplier,
            )
            for cap in trade_caps
        }
    return {
        "scenarios": out,
        "trade_caps_dollars": [str(v) for v in trade_caps],
        "threshold_ms": threshold_ms,
        "kalshi_bankroll_dollars": str(kalshi_bankroll),
        "gemini_bankroll_dollars": str(gemini_bankroll) if gemini_bankroll is not None else None,
        "settlement_delay_labels": list(delays),
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--threshold-ms", type=int, default=10)
    parser.add_argument("--kalshi-bankroll", type=Decimal, default=D("4000"))
    parser.add_argument("--gemini-bankroll", type=Decimal)
    parser.add_argument("--trade-caps", nargs="+", type=Decimal, default=[D("100"), D("250"), D("500")])
    parser.add_argument("--kalshi-fee-multiplier", type=Decimal, default=D("1"))
    parser.add_argument("--settlement-latency-summary", type=Path)
    args = parser.parse_args()

    delays = None
    if args.settlement_latency_summary is not None:
        payload = json.loads(args.settlement_latency_summary.read_text(encoding="utf-8"))
        delays = settlement_delay_scenarios_from_summary(payload)

    result = scenarios(
        load_rows(args.paths),
        threshold_ms=args.threshold_ms,
        kalshi_bankroll=args.kalshi_bankroll,
        trade_caps=list(args.trade_caps),
        settlement_delays_s=delays,
        gemini_bankroll=args.gemini_bankroll,
        kalshi_fee_multiplier=args.kalshi_fee_multiplier,
    )
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
