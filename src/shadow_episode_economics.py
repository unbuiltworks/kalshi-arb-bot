"""Offline episode-level economics for read-only cross-venue shadow samples.

This module deliberately sits outside the quote/order critical path. It converts
repeated positive snapshots of the same counterpart into one opportunity episode,
so a quote that survives for 500 ms is not counted as hundreds of independent
trades. It then reports survival, one-fill-per-episode shadow economics, capital,
and a clearly-labelled short-sample daily run-rate.

No venue clients or order-entry code are imported here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable


SURVIVAL_THRESHOLDS_MS = (5, 10, 20, 50, 100, 250, 500, 1000)


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _pair_key(row: dict[str, Any]) -> str:
    explicit = str(row.get("settlement_key") or row.get("pair_key") or "").strip()
    if explicit:
        return explicit
    return "|".join(
        str(row.get(k) or "")
        for k in ("family", "poly_slug", "kalshi_ticker")
    )


def _plan(row: dict[str, Any]) -> dict[str, Any] | None:
    plan = row.get("positive_conservative_plan") or row.get("plan")
    return plan if isinstance(plan, dict) else None


@dataclass(frozen=True)
class ShadowEpisode:
    pair_key: str
    category: str
    direction: str
    opened_ms: int
    last_positive_ms: int
    closed_ms: int
    samples: int
    opening_profit: Decimal
    minimum_profit: Decimal
    opening_cost: Decimal
    minimum_quantity: Decimal

    @property
    def lifetime_ms(self) -> int:
        return max(0, self.closed_ms - self.opened_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pair_key": self.pair_key,
            "category": self.category,
            "direction": self.direction,
            "opened_ms": self.opened_ms,
            "last_positive_ms": self.last_positive_ms,
            "closed_ms": self.closed_ms,
            "lifetime_ms": self.lifetime_ms,
            "samples": self.samples,
            "opening_profit": str(self.opening_profit),
            "minimum_profit": str(self.minimum_profit),
            "opening_cost": str(self.opening_cost),
            "minimum_quantity": str(self.minimum_quantity),
        }


def build_episodes(
    samples: Iterable[dict[str, Any]],
    *,
    max_gap_ms: int = 1000,
) -> list[ShadowEpisode]:
    """Collapse repeated positive snapshots into unique contiguous episodes.

    A new episode starts when the pair/direction changes from non-positive back to
    positive or when the positive-sample gap exceeds ``max_gap_ms``. The episode's
    closing timestamp is the first following sample timestamp for that pair when
    available; otherwise the last positive timestamp is used.
    """
    if max_gap_ms < 0:
        raise ValueError("max_gap_ms must be non-negative")

    rows = sorted((dict(row) for row in samples), key=lambda r: int(r.get("ts_ms") or 0))
    active: dict[tuple[str, str], dict[str, Any]] = {}
    last_seen_ts: dict[str, int] = {}
    out: list[ShadowEpisode] = []

    def close(key: tuple[str, str], closed_ms: int) -> None:
        state = active.pop(key, None)
        if state is None:
            return
        out.append(
            ShadowEpisode(
                pair_key=state["pair_key"],
                category=state["category"],
                direction=state["direction"],
                opened_ms=state["opened_ms"],
                last_positive_ms=state["last_positive_ms"],
                closed_ms=max(closed_ms, state["last_positive_ms"]),
                samples=state["samples"],
                opening_profit=state["opening_profit"],
                minimum_profit=state["minimum_profit"],
                opening_cost=state["opening_cost"],
                minimum_quantity=state["minimum_quantity"],
            )
        )

    for row in rows:
        ts = int(row.get("ts_ms") or 0)
        pair = _pair_key(row)
        if not pair:
            continue
        plan = _plan(row)
        direction = str((plan or {}).get("direction") or row.get("direction") or "")

        # Any later observation for this pair closes active directions that are no
        # longer positive/current. This prevents a stale opening from living forever.
        for key in [k for k in active if k[0] == pair and (plan is None or k[1] != direction)]:
            close(key, ts)

        if plan is None:
            last_seen_ts[pair] = ts
            continue

        profit = _d(plan.get("conservative_locked_profit", plan.get("profit", "0")))
        if profit <= 0:
            last_seen_ts[pair] = ts
            continue
        cost = _d(plan.get("total_cost_upper_bound", plan.get("total_cost", "0")))
        quantity = _d(plan.get("quantity", "0"))
        key = (pair, direction)
        state = active.get(key)
        if state is not None and ts - state["last_positive_ms"] > max_gap_ms:
            close(key, state["last_positive_ms"] + max_gap_ms)
            state = None

        if state is None:
            active[key] = {
                "pair_key": pair,
                "category": str(row.get("family") or row.get("category") or "unknown"),
                "direction": direction,
                "opened_ms": ts,
                "last_positive_ms": ts,
                "samples": 1,
                "opening_profit": profit,
                "minimum_profit": profit,
                "opening_cost": cost,
                "minimum_quantity": quantity,
            }
        else:
            state["last_positive_ms"] = ts
            state["samples"] += 1
            state["minimum_profit"] = min(state["minimum_profit"], profit)
            state["minimum_quantity"] = min(state["minimum_quantity"], quantity)
        last_seen_ts[pair] = ts

    for key, state in list(active.items()):
        close(key, last_seen_ts.get(state["pair_key"], state["last_positive_ms"]))

    out.sort(key=lambda e: (e.opened_ms, e.pair_key, e.direction))
    return out


def summarize_episodes(
    episodes: Iterable[ShadowEpisode],
    *,
    observation_start_ms: int | None = None,
    observation_end_ms: int | None = None,
) -> dict[str, Any]:
    """Return conservative one-fill-per-episode economics.

    ``opening_profit`` is counted once per episode rather than once per snapshot.
    The daily number is a mechanical shadow run-rate only and is explicitly labelled
    short-sample when less than 24 hours were observed.
    """
    rows = list(episodes)
    if observation_start_ms is None and rows:
        observation_start_ms = min(e.opened_ms for e in rows)
    if observation_end_ms is None and rows:
        observation_end_ms = max(e.closed_ms for e in rows)

    observed_ms = 0
    if observation_start_ms is not None and observation_end_ms is not None:
        observed_ms = max(0, observation_end_ms - observation_start_ms)

    one_fill_profit = sum((e.opening_profit for e in rows), Decimal("0"))
    one_fill_capital = sum((e.opening_cost for e in rows), Decimal("0"))
    minimum_profit_sum = sum((e.minimum_profit for e in rows), Decimal("0"))
    survival = {
        str(threshold): sum(e.lifetime_ms >= threshold for e in rows)
        for threshold in SURVIVAL_THRESHOLDS_MS
    }

    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({e.category for e in rows}):
        cat = [e for e in rows if e.category == category]
        by_category[category] = {
            "episodes": len(cat),
            "one_fill_shadow_profit": str(sum((e.opening_profit for e in cat), Decimal("0"))),
            "minimum_observed_profit_sum": str(sum((e.minimum_profit for e in cat), Decimal("0"))),
            "opening_capital_sum": str(sum((e.opening_cost for e in cat), Decimal("0"))),
        }

    daily = None
    if observed_ms > 0:
        daily = one_fill_profit * Decimal(86_400_000) / Decimal(observed_ms)

    return {
        "unique_episodes": len(rows),
        "observed_ms": observed_ms,
        "one_fill_per_episode_shadow_profit": str(one_fill_profit),
        "minimum_observed_profit_sum": str(minimum_profit_sum),
        "opening_capital_sum": str(one_fill_capital),
        "survival_ms": survival,
        "by_category": by_category,
        "shadow_profit_per_day_run_rate": str(daily) if daily is not None else None,
        "run_rate_status": (
            "SHORT_SHADOW_SAMPLE_NOT_A_FORECAST"
            if 0 < observed_ms < 86_400_000
            else "SHADOW_RUN_RATE_NOT_A_FORECAST"
            if observed_ms >= 86_400_000
            else "INSUFFICIENT_OBSERVATION_WINDOW"
        ),
        "read_only": True,
        "order_entry": False,
        "note": (
            "Repeated snapshots are collapsed into unique episodes. Profit assumes at most one fill at the opening observed plan per episode; it is shadow capacity math, not realized P&L or a forecast."
        ),
    }
