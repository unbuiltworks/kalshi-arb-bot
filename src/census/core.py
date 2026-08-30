"""Venue-agnostic census math over normalized window tapes.

A window tape is a chronological list of public trades:

    TapeTrade(ts, maker_side, count, up_price, down_price)

`maker_side` is the side a passive two-sided accumulator would have ACQUIRED
had it been the resting counterparty: the opposite of the taker. On a unified
binary book (Kalshi style) a taker buying YES fills what is economically a NO
bid, so taker_side=yes -> maker acquires DOWN/NO, taker_side=no -> maker
acquires UP/YES.

Everything here is a ceiling computation: no queue competition, no missed
quotes, no adverse selection. Real strategies can only do worse; a venue that
fails at the ceiling fails, full stop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

UP = "UP"
DOWN = "DOWN"

# lambda sweep for residual-adjusted EV, in dollars per residual contract.
LAMBDA_SWEEP = (0.06, 0.12, 0.24)
# L5's realized pair edge, used only for the "at benchmark economics" line.
BENCHMARK_PAIR_EDGE = 0.039


@dataclass(frozen=True)
class TapeTrade:
    ts: int
    maker_side: str          # UP | DOWN -- side the passive maker acquires
    count: float             # contracts
    up_price: float          # price of the UP/YES leg, 0..1
    down_price: float        # price of the DOWN/NO leg, 0..1

    @property
    def acquire_price(self) -> float:
        return self.up_price if self.maker_side == UP else self.down_price


@dataclass
class CeilingResult:
    q: float | None          # per-event absorption cap (None = unlimited)
    residual_cap: float | None
    matched: float = 0.0
    up_qty: float = 0.0
    down_qty: float = 0.0
    up_cost: float = 0.0
    down_cost: float = 0.0
    absorbed_events: int = 0

    @property
    def residual(self) -> float:
        return abs(self.up_qty - self.down_qty)

    @property
    def pair_cost(self) -> float | None:
        if self.up_qty > 0 and self.down_qty > 0:
            return self.up_cost / self.up_qty + self.down_cost / self.down_qty
        return None

    @property
    def gross_locked(self) -> float:
        pc = self.pair_cost
        return self.matched * (1.0 - pc) if pc is not None else 0.0

    def adjusted_ev(self, lam: float) -> float:
        return self.gross_locked - lam * self.residual

    def as_dict(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "residual_cap": self.residual_cap,
            "matched": round(self.matched, 2),
            "residual": round(self.residual, 2),
            "matched_to_residual": (
                round(self.matched / self.residual, 3) if self.residual > 1e-9 else None
            ),
            "pair_cost": round(self.pair_cost, 4) if self.pair_cost is not None else None,
            "gross_locked_usd": round(self.gross_locked, 2),
            "adjusted_ev_usd": {
                f"lambda_{int(l * 100)}c": round(self.adjusted_ev(l), 2) for l in LAMBDA_SWEEP
            },
            "absorbed_events": self.absorbed_events,
        }


def sequential_ceiling(
    tape: Sequence[TapeTrade],
    q: float | None = None,
    residual_cap: float | None = None,
) -> CeilingResult:
    """Greedy matched-formation ceiling in tape order.

    At each trade the maker may absorb up to min(q, count) contracts of the
    trade's maker_side, further limited so post-absorption |up - down| never
    exceeds residual_cap. Greedy is optimal here for the matched total: taking
    imbalance-reducing flow is always weakly better, and imbalance-increasing
    flow is taken exactly up to the cap, which never blocks a later match that
    the cap itself would not have blocked.
    """
    result = CeilingResult(q=q, residual_cap=residual_cap)
    for trade in tape:
        take = trade.count if q is None else min(q, trade.count)
        if residual_cap is not None:
            imbalance = result.up_qty - result.down_qty  # signed, + = long UP
            if trade.maker_side == UP:
                room = residual_cap - imbalance
            else:
                room = residual_cap + imbalance
            # Absorbing toward balance frees room; absorbing away from balance
            # consumes it. `room` is how much this side may add before the cap
            # binds, counting the balance-restoring headroom.
            take = min(take, max(room, 0.0))
        if take <= 0:
            continue
        if trade.maker_side == UP:
            result.up_qty += take
            result.up_cost += take * trade.acquire_price
        else:
            result.down_qty += take
            result.down_cost += take * trade.acquire_price
        result.absorbed_events += 1
    result.matched = min(result.up_qty, result.down_qty)
    return result


@dataclass
class WindowCensus:
    window_id: str
    label: str
    open_ts: int | None
    close_ts: int | None
    trades: int = 0
    contracts: float = 0.0
    up_side_trades: int = 0
    down_side_trades: int = 0
    both_sides: bool = False
    alternation_rate: float | None = None
    clip_median: float | None = None
    clip_mean: float | None = None
    ceilings: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "label": self.label,
            "open_ts": self.open_ts,
            "close_ts": self.close_ts,
            "trades": self.trades,
            "contracts": round(self.contracts, 1),
            "up_side_trades": self.up_side_trades,
            "down_side_trades": self.down_side_trades,
            "both_sides": self.both_sides,
            "alternation_rate": (
                round(self.alternation_rate, 3) if self.alternation_rate is not None else None
            ),
            "clip_median": self.clip_median,
            "clip_mean": round(self.clip_mean, 1) if self.clip_mean is not None else None,
            "ceilings": self.ceilings,
        }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def census_window(
    window_id: str,
    label: str,
    tape: Sequence[TapeTrade],
    open_ts: int | None = None,
    close_ts: int | None = None,
    q_values: Sequence[float | None] = (10.0, None),
    residual_caps: Sequence[float | None] = (10.0, 25.0, 50.0, None),
) -> WindowCensus:
    tape = sorted(tape, key=lambda t: t.ts)
    out = WindowCensus(window_id=window_id, label=label, open_ts=open_ts, close_ts=close_ts)
    out.trades = len(tape)
    out.contracts = sum(t.count for t in tape)
    out.up_side_trades = sum(1 for t in tape if t.maker_side == UP)
    out.down_side_trades = len(tape) - out.up_side_trades
    out.both_sides = out.up_side_trades > 0 and out.down_side_trades > 0
    if len(tape) >= 2:
        flips = sum(1 for a, b in zip(tape, tape[1:]) if a.maker_side != b.maker_side)
        out.alternation_rate = flips / (len(tape) - 1)
    sizes = [t.count for t in tape]
    out.clip_median = _median(sizes)
    out.clip_mean = (sum(sizes) / len(sizes)) if sizes else None
    for q in q_values:
        for cap in residual_caps:
            out.ceilings.append(sequential_ceiling(tape, q=q, residual_cap=cap).as_dict())
    return out


def aggregate(windows: Iterable[WindowCensus]) -> dict[str, Any]:
    windows = list(windows)
    traded = [w for w in windows if w.trades > 0]
    agg: dict[str, Any] = {
        "windows_seen": len(windows),
        "windows_with_trades": len(traded),
        "total_trades": sum(w.trades for w in traded),
        "total_contracts": round(sum(w.contracts for w in traded), 1),
        "mean_trades_per_traded_window": (
            round(sum(w.trades for w in traded) / len(traded), 2) if traded else None
        ),
        "windows_both_sides": sum(1 for w in traded if w.both_sides),
        "mean_alternation_rate": (
            round(
                sum(w.alternation_rate for w in traded if w.alternation_rate is not None)
                / max(1, sum(1 for w in traded if w.alternation_rate is not None)),
                3,
            )
            if traded
            else None
        ),
        "clip_median_overall": _median(
            [t for w in traded for t in ([w.clip_median] if w.clip_median else [])]
        ),
        "by_config": {},
        "benchmarks": {
            "l5_polymarket_realized": {
                "matched_to_residual": 4.38,
                "fills_per_window": 43.3,
                "alternation": 0.372,
                "clip_median": 13.5,
                "pair_edge_usd": BENCHMARK_PAIR_EDGE,
            },
            "gemini_first_tape_ceiling": {"matched_to_residual": 0.287},
        },
    }
    # Sum each (q, cap) configuration across windows.
    sums: dict[tuple, dict[str, float]] = {}
    for w in traded:
        for c in w.ceilings:
            key = (c["q"], c["residual_cap"])
            entry = sums.setdefault(
                key, {"matched": 0.0, "residual": 0.0, "gross": 0.0}
            )
            entry["matched"] += c["matched"]
            entry["residual"] += c["residual"]
            entry["gross"] += c["gross_locked_usd"]
    for (q, cap), entry in sorted(
        sums.items(), key=lambda kv: (kv[0][0] or math.inf, kv[0][1] or math.inf)
    ):
        agg["by_config"][f"q={q or 'inf'}_R={cap or 'inf'}"] = {
            "matched": round(entry["matched"], 1),
            "residual": round(entry["residual"], 1),
            "matched_to_residual": (
                round(entry["matched"] / entry["residual"], 3)
                if entry["residual"] > 1e-9
                else None
            ),
            "gross_locked_usd": round(entry["gross"], 2),
            "adjusted_ev_usd": {
                f"lambda_{int(l * 100)}c": round(entry["gross"] - l * entry["residual"], 2)
                for l in LAMBDA_SWEEP
            },
            "gross_at_benchmark_pair_edge_usd": round(
                entry["matched"] * BENCHMARK_PAIR_EDGE, 2
            ),
        }
    return agg
