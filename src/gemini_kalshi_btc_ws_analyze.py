"""Analyze read-only BTC Gemini/Kalshi WebSocket shadow logs.

Conservative event-driven analysis for fee-positive BTC cross-venue episodes.
Survival requires a new still-positive event at/after each latency threshold.
Repeated resting depth at the same executable price pair is consumable only once
within the same matched 15-minute contract; if displayed quantity later grows,
only the increment is newly consumable. Contract rollover always resets episode
and consumable-liquidity identity.
Settlement remains a separate INDEX_MISMATCH_RESEARCH_ONLY gate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal
import json
from pathlib import Path
from statistics import median
from typing import Any

D = Decimal
THRESHOLDS_MS = (50, 100, 250, 500)


@dataclass
class Episode:
    market_key: str
    direction: str
    start_ms: int
    last_positive_ms: int
    entry: dict[str, Any]
    observations: list[tuple[int, dict[str, Any]]] = field(default_factory=list)


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("asset") == "BTC" and row.get("read_only") is True:
                    rows.append(row)
    rows.sort(key=lambda r: int(r["receive_ts_ms"]))
    return rows


def _market_key(row: dict[str, Any]) -> str:
    """Stable identity for one matched Gemini/Kalshi 15-minute contract."""
    gemini = str(row.get("gemini_symbol") or "")
    kalshi = str(row.get("kalshi_ticker") or "")
    window = str(row.get("window_end_utc") or "")
    if gemini or kalshi:
        return "|".join((gemini, kalshi, window))
    # Older/synthetic logs may not carry market IDs. Keep them analyzable while
    # making the missing identity explicit instead of silently sharing a real key.
    return f"UNKNOWN_MARKET|{window}"


def _liquidity_key(row: dict[str, Any], direction: str) -> str:
    market = _market_key(row)
    if direction == "KALSHI_YES_GEMINI_NO":
        # Gemini NO ask is 1 - Gemini YES bid; size is YES bid size.
        return "|".join((
            market,
            direction,
            str(row.get("kalshi_yes_ask")),
            str(row.get("gemini_yes_bid")),
        ))
    if direction == "GEMINI_YES_KALSHI_NO":
        # Kalshi NO ask is 1 - Kalshi YES bid; size is YES bid size.
        return "|".join((
            market,
            direction,
            str(row.get("gemini_yes_ask")),
            str(row.get("kalshi_yes_bid")),
        ))
    return "|".join((market, direction))


def _candidates(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in row.get("both_taker_fee_candidates") or []:
        if not isinstance(raw, dict):
            continue
        direction = str(raw.get("direction") or "")
        if not direction:
            continue
        try:
            if D(str(raw["locked_profit"])) <= 0 or D(str(raw["quantity"])) <= 0:
                continue
        except (KeyError, ValueError):
            continue
        candidate = dict(raw)
        candidate["liquidity_key"] = _liquidity_key(row, direction)
        out[direction] = candidate
    return out


def episodes(rows: list[dict[str, Any]]) -> list[Episode]:
    active: dict[tuple[str, str], Episode] = {}
    done: list[Episode] = []
    for row in rows:
        ts = int(row["receive_ts_ms"])
        market = _market_key(row)
        current = _candidates(row)

        # A new contract can arrive without an explicit negative event for the old
        # one. Close old-market episodes immediately so a rollover can never be
        # interpreted as 50/100/250/500ms survival of the previous contract.
        for key in list(active):
            if key[0] != market:
                done.append(active.pop(key))

        for key in list(active):
            if key[0] == market and key[1] not in current:
                done.append(active.pop(key))

        for direction, candidate in current.items():
            key = (market, direction)
            ep = active.get(key)
            if ep is None:
                ep = Episode(market, direction, ts, ts, candidate, [])
                active[key] = ep
            ep.last_positive_ms = ts
            ep.observations.append((ts, candidate))

    done.extend(active.values())
    done.sort(key=lambda e: (e.start_ms, e.market_key, e.direction))
    return done


def _first_at_or_after(ep: Episode, threshold_ms: int) -> dict[str, Any] | None:
    target = ep.start_ms + threshold_ms
    for ts, candidate in ep.observations:
        if ts >= target:
            return candidate
    return None


def _consume(candidate: dict[str, Any], seen: dict[str, Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """Return fresh qty, scaled P&L and capital for this contract+price pair."""
    qty = D(str(candidate["quantity"]))
    key = str(candidate.get("liquidity_key") or candidate.get("direction") or "")
    prior = seen.get(key, D("0"))
    fresh = max(D("0"), qty - prior)
    if qty > prior:
        seen[key] = qty
    if fresh <= 0 or qty <= 0:
        return D("0"), D("0"), D("0")
    scale = fresh / qty
    return (
        fresh,
        D(str(candidate["locked_profit"])) * scale,
        D(str(candidate["net_cost"])) * scale,
    )


def _scenario(eps: list[Episode], threshold_ms: int | None) -> dict[str, Any]:
    seen: dict[str, Decimal] = {}
    survivors = 0
    pnl = D("0")
    capital = D("0")
    quantity = D("0")
    consumable_episodes = 0
    for ep in eps:
        candidate = ep.entry if threshold_ms is None else _first_at_or_after(ep, threshold_ms)
        if candidate is None:
            continue
        survivors += 1
        fresh_qty, fresh_pnl, fresh_capital = _consume(candidate, seen)
        if fresh_qty <= 0:
            continue
        consumable_episodes += 1
        quantity += fresh_qty
        pnl += fresh_pnl
        capital += fresh_capital
    return {
        "surviving_episodes": survivors,
        "consumable_episodes": consumable_episodes,
        "consumable_shadow_pnl_dollars": str(pnl),
        "consumable_capital_deployed_dollars": str(capital),
        "consumable_contracts": str(quantity),
        "consumable_pnl_per_dollar_deployed": str(pnl / capital) if capital > 0 else None,
    }


def analyze(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eps = episodes(rows)
    timestamps = [int(r["receive_ts_ms"]) for r in rows]
    deltas = [b - a for a, b in zip(timestamps, timestamps[1:]) if b > a]
    quote_skews = [
        abs(int(r["gemini_receive_ts_ms"]) - int(r["kalshi_receive_ts_ms"]))
        for r in rows
        if r.get("gemini_receive_ts_ms") is not None and r.get("kalshi_receive_ts_ms") is not None
    ]
    k_latencies = [
        int(r["kalshi_exchange_to_receive_ms"])
        for r in rows
        if r.get("kalshi_exchange_to_receive_ms") is not None
        and int(r["kalshi_exchange_to_receive_ms"]) >= 0
    ]

    threshold_results: dict[str, Any] = {}
    for threshold in THRESHOLDS_MS:
        result = _scenario(eps, threshold)
        result["survival_rate"] = (result["surviving_episodes"] / len(eps)) if eps else None
        threshold_results[str(threshold)] = result

    by_direction: dict[str, int] = {}
    for ep in eps:
        by_direction[ep.direction] = by_direction.get(ep.direction, 0) + 1

    entry = _scenario(eps, None)
    observation_ms = (timestamps[-1] - timestamps[0]) if len(timestamps) >= 2 else 0
    markets = sorted({_market_key(row) for row in rows})

    return {
        "rows": len(rows),
        "matched_contracts_observed": len(markets),
        "unique_directional_positive_episodes": len(eps),
        "episodes_by_direction": dict(sorted(by_direction.items())),
        "entry_consumable_episodes": entry["consumable_episodes"],
        "entry_consumable_shadow_pnl_dollars": entry["consumable_shadow_pnl_dollars"],
        "entry_consumable_capital_deployed_dollars": entry["consumable_capital_deployed_dollars"],
        "entry_consumable_contracts": entry["consumable_contracts"],
        "entry_consumable_pnl_per_dollar_deployed": entry["consumable_pnl_per_dollar_deployed"],
        "survival": threshold_results,
        "observation_ms": observation_ms,
        "event_interval_median_ms": median(deltas) if deltas else None,
        "quote_skew_median_ms": median(quote_skews) if quote_skews else None,
        "quote_skew_p95_ms": _percentile(quote_skews, 0.95),
        "kalshi_exchange_to_receive_median_ms": median(k_latencies) if k_latencies else None,
        "kalshi_exchange_to_receive_p95_ms": _percentile(k_latencies, 0.95),
        "survival_method": "REQUIRES_NEW_POSITIVE_EVENT_AT_OR_AFTER_THRESHOLD_WITHIN_SAME_MATCHED_CONTRACT",
        "liquidity_method": "SAME_MATCHED_CONTRACT_AND_EXECUTABLE_PRICE_PAIR_COUNTS_ONLY_DISPLAYED_SIZE_GROWTH_AS_FRESH",
        "pnl_method": "CONSUMABLE_SIZE_ONE_EXECUTION_PER_DIRECTIONAL_EPISODE",
        "settlement_status": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * q)
    return ordered[idx]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(load_rows(args.paths)), separators=(",", ":")))


if __name__ == "__main__":
    main()
