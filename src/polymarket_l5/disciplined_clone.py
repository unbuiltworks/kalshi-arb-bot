"""No-hindsight fill-path replay for a disciplined, smaller L5-style policy.

This is an analysis tool, not an execution backtest. It assumes a hypothetical clone
receives a fixed fraction of the observed trader's BUY fills at the same timestamps
and prices. That fill-transfer assumption is deliberately surfaced in every report.

Policy decisions use only information available at each observed fill:
- optional marginal matched-pair cost ceiling,
- optional residual-ratio cap after both outcomes have been acquired,
- no knowledge of the eventual winner when accepting/rejecting a fill.

Rejected expensive opposite-side fills leave the pre-existing residual exposed to
settlement, which gives the forced-hedge counterfactual Claude's aggregate analysis
could not answer from final window classifications alone.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

RECENT_START_UTC = "2026-08-28T07:45:27"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _iso_ts(value: str) -> float:
    if not value:
        return 0.0
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _norm_outcome(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"up", "yes"}:
        return "UP"
    if text in {"down", "no"}:
        return "DOWN"
    return text.upper()


@dataclass
class Lot:
    qty: float
    price: float


@dataclass
class ReplayState:
    up_qty: float = 0.0
    down_qty: float = 0.0
    up_spend: float = 0.0
    down_spend: float = 0.0
    up_unmatched: list[Lot] = field(default_factory=list)
    down_unmatched: list[Lot] = field(default_factory=list)
    accepted_fills: int = 0
    rejected_pair_ceiling: int = 0
    rejected_residual_cap: int = 0
    observed_buy_fills: int = 0

    @property
    def total_spend(self) -> float:
        return self.up_spend + self.down_spend

    @property
    def residual_ratio(self) -> float:
        high = max(self.up_qty, self.down_qty)
        return 0.0 if high <= 1e-12 else abs(self.up_qty - self.down_qty) / high


def _unmatched(state: ReplayState, outcome: str) -> list[Lot]:
    return state.up_unmatched if outcome == "UP" else state.down_unmatched


def _opposite(outcome: str) -> str:
    return "DOWN" if outcome == "UP" else "UP"


def marginal_pair_cost(state: ReplayState, *, outcome: str, price: float, qty: float) -> float | None:
    """Observed candidate price plus weighted cost of opposite unmatched inventory."""
    lots = _unmatched(state, _opposite(outcome))
    remaining = min(qty, sum(max(0.0, lot.qty) for lot in lots))
    if remaining <= 1e-12:
        return None
    needed = remaining
    opposite_cost = 0.0
    for lot in lots:
        take = min(needed, max(0.0, lot.qty))
        opposite_cost += take * lot.price
        needed -= take
        if needed <= 1e-12:
            break
    return price + opposite_cost / remaining


def _apply_unmatched(state: ReplayState, *, outcome: str, price: float, qty: float) -> None:
    """Net a newly accepted BUY against opposite unmatched lots, FIFO."""
    opposite_lots = _unmatched(state, _opposite(outcome))
    remaining = qty
    while remaining > 1e-12 and opposite_lots:
        lot = opposite_lots[0]
        take = min(remaining, lot.qty)
        lot.qty -= take
        remaining -= take
        if lot.qty <= 1e-12:
            opposite_lots.pop(0)
    if remaining > 1e-12:
        _unmatched(state, outcome).append(Lot(remaining, price))


def projected_residual_ratio(state: ReplayState, *, outcome: str, qty: float) -> tuple[float, float]:
    before = abs(state.up_qty - state.down_qty)
    up = state.up_qty + (qty if outcome == "UP" else 0.0)
    down = state.down_qty + (qty if outcome == "DOWN" else 0.0)
    after_abs = abs(up - down)
    high = max(up, down)
    ratio = 0.0 if high <= 1e-12 else after_abs / high
    return ratio, after_abs - before


def accept_buy(
    state: ReplayState,
    *,
    outcome: str,
    price: float,
    shares: float,
    pair_ceiling: float | None,
    residual_cap: float | None,
) -> tuple[bool, str, float | None]:
    state.observed_buy_fills += 1
    pair_cost = marginal_pair_cost(state, outcome=outcome, price=price, qty=shares)
    if pair_ceiling is not None and pair_cost is not None and pair_cost > pair_ceiling + 1e-12:
        state.rejected_pair_ceiling += 1
        return False, "PAIR_CEILING", pair_cost

    if residual_cap is not None and state.up_qty > 1e-12 and state.down_qty > 1e-12:
        projected, residual_delta = projected_residual_ratio(state, outcome=outcome, qty=shares)
        if residual_delta > 1e-12 and projected > residual_cap + 1e-12:
            state.rejected_residual_cap += 1
            return False, "RESIDUAL_CAP", pair_cost

    if outcome == "UP":
        state.up_qty += shares
        state.up_spend += shares * price
    elif outcome == "DOWN":
        state.down_qty += shares
        state.down_spend += shares * price
    else:
        return False, "UNKNOWN_OUTCOME", pair_cost
    _apply_unmatched(state, outcome=outcome, price=price, qty=shares)
    state.accepted_fills += 1
    return True, "ACCEPT", pair_cost


def settlement_pnl(state: ReplayState, winning_outcome: str) -> float | None:
    winner = _norm_outcome(winning_outcome)
    if winner == "UP":
        payout = state.up_qty
    elif winner == "DOWN":
        payout = state.down_qty
    else:
        return None
    return payout - state.total_spend


def load_summary(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["window_id"]: row for row in csv.DictReader(handle)}


def load_sequences(path: Path, *, recent_only: bool = True) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if recent_only and str(row.get("datetime_utc") or "") < RECENT_START_UTC:
                continue
            groups.setdefault(row["window_id"], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: (_f(r.get("timestamp")), _f(r.get("trade_seq"))))
    return groups


def replay_window(
    rows: list[dict[str, str]],
    summary: dict[str, str],
    *,
    scale: float,
    pair_ceiling: float | None,
    residual_cap: float | None,
) -> dict[str, Any]:
    state = ReplayState()
    sell_fills = 0
    decisions: list[dict[str, Any]] = []
    accepted_cashflows: list[tuple[float, float]] = []
    for row in rows:
        side = str(row.get("side") or "").upper()
        if side != "BUY":
            if side == "SELL":
                sell_fills += 1
            continue
        outcome = _norm_outcome(row.get("outcome"))
        price = _f(row.get("price"))
        shares = _f(row.get("shares")) * scale
        if shares <= 1e-12 or outcome not in {"UP", "DOWN"}:
            continue
        accepted, reason, pair_cost = accept_buy(
            state,
            outcome=outcome,
            price=price,
            shares=shares,
            pair_ceiling=pair_ceiling,
            residual_cap=residual_cap,
        )
        decisions.append({
            "timestamp": _f(row.get("timestamp")),
            "trade_seq": int(_f(row.get("trade_seq"))),
            "outcome": outcome,
            "price": price,
            "shares": shares,
            "accepted": accepted,
            "reason": reason,
            "marginal_pair_cost": pair_cost,
        })
        if accepted:
            accepted_cashflows.append((_f(row.get("timestamp")), shares * price))

    pnl = settlement_pnl(state, summary.get("winning_outcome") or "")
    end_ts = _iso_ts(summary.get("window_end_utc") or "")
    return {
        "window_id": summary.get("window_id"),
        "asset": summary.get("asset"),
        "window_minutes": int(_f(summary.get("window_minutes"))),
        "window_start_utc": summary.get("window_start_utc"),
        "window_end_utc": summary.get("window_end_utc"),
        "winning_outcome": summary.get("winning_outcome"),
        "sell_fills_ignored": sell_fills,
        "accepted_fills": state.accepted_fills,
        "observed_buy_fills": state.observed_buy_fills,
        "rejected_pair_ceiling": state.rejected_pair_ceiling,
        "rejected_residual_cap": state.rejected_residual_cap,
        "accepted_spend": state.total_spend,
        "up_qty": state.up_qty,
        "down_qty": state.down_qty,
        "residual_ratio": state.residual_ratio,
        "settlement_pnl": pnl,
        "accepted_cashflows": accepted_cashflows,
        "release_cashflow": (end_ts, state.total_spend) if end_ts else None,
        "decisions": decisions,
    }


def peak_committed_capital(windows: Iterable[dict[str, Any]]) -> float:
    events: list[tuple[float, int, float]] = []
    for w in windows:
        for ts, dollars in w["accepted_cashflows"]:
            events.append((ts, 1, dollars))
        release = w.get("release_cashflow")
        if release and release[0]:
            events.append((release[0], -1, release[1]))
    # At identical timestamps, release first, then new deployment.
    events.sort(key=lambda x: (x[0], x[1]))
    outstanding = 0.0
    peak = 0.0
    for _ts, direction, dollars in events:
        outstanding += dollars if direction > 0 else -dollars
        outstanding = max(0.0, outstanding)
        peak = max(peak, outstanding)
    return peak


def summarize_policy(windows: list[dict[str, Any]], *, name: str, scale: float, pair_ceiling: float | None, residual_cap: float | None) -> dict[str, Any]:
    resolved = [w for w in windows if w.get("settlement_pnl") is not None]
    pnl = [float(w["settlement_pnl"]) for w in resolved]
    total_spend = sum(float(w["accepted_spend"]) for w in windows)
    return {
        "name": name,
        "scale": scale,
        "pair_ceiling": pair_ceiling,
        "residual_cap": residual_cap,
        "windows": len(windows),
        "resolved_windows": len(resolved),
        "windows_with_ignored_sells": sum(1 for w in windows if w["sell_fills_ignored"]),
        "accepted_spend": total_spend,
        "settlement_pnl": sum(pnl),
        "pnl_per_1000_accepted_spend": None if total_spend <= 0 else 1000.0 * sum(pnl) / total_spend,
        "winning_window_rate": None if not pnl else sum(1 for x in pnl if x > 0) / len(pnl),
        "worst_window_pnl": None if not pnl else min(pnl),
        "best_window_pnl": None if not pnl else max(pnl),
        "peak_committed_capital_upper_bound": peak_committed_capital(windows),
        "pair_ceiling_rejections": sum(w["rejected_pair_ceiling"] for w in windows),
        "residual_cap_rejections": sum(w["rejected_residual_cap"] for w in windows),
        "final_residual_ratio_lt_20pct_rate": (
            None if not windows else sum(1 for w in windows if w["residual_ratio"] < 0.20) / len(windows)
        ),
    }


def run_grid(sequence_csv: Path, summary_csv: Path, *, scale: float = 0.10) -> dict[str, Any]:
    summaries = load_summary(summary_csv)
    groups = load_sequences(sequence_csv, recent_only=True)
    common = [(wid, rows, summaries[wid]) for wid, rows in groups.items() if wid in summaries]
    configs = [
        ("replay_all", None, None),
        ("pair_101", 1.01, None),
        ("pair_100", 1.00, None),
        ("pair_099", 0.99, None),
        ("pair_098", 0.98, None),
        ("pair_097", 0.97, None),
        ("pair_099_residual35", 0.99, 0.35),
        ("pair_099_residual20", 0.99, 0.20),
        ("pair_098_residual20", 0.98, 0.20),
    ]
    policies = []
    for name, ceiling, residual in configs:
        windows = [
            replay_window(rows, summary, scale=scale, pair_ceiling=ceiling, residual_cap=residual)
            for _wid, rows, summary in common
        ]
        policies.append(summarize_policy(windows, name=name, scale=scale, pair_ceiling=ceiling, residual_cap=residual))

    baseline = next(p for p in policies if p["name"] == "replay_all")
    for p in policies:
        p["pnl_delta_vs_replay_all"] = p["settlement_pnl"] - baseline["settlement_pnl"]
        p["turnover_ratio_vs_replay_all"] = (
            None if baseline["accepted_spend"] <= 0 else p["accepted_spend"] / baseline["accepted_spend"]
        )
    return {
        "classification": "ANALYSIS_ONLY_NO_HINDSIGHT_FILL_PATH_REPLAY",
        "recent_start_utc": RECENT_START_UTC,
        "scale": scale,
        "window_count": len(common),
        "policies": policies,
        "guardrails": {
            "same_fill_transfer_assumption": True,
            "same_queue_position_assumed": False,
            "incentives_modeled": False,
            "future_outcome_used_for_fill_decisions": False,
            "settlement_outcome_used_only_after_replay": True,
            "live_order_entry": False,
            "realized_pnl": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequences", default="analysis/l5/l5_window_sequences.csv")
    parser.add_argument("--summary", default="analysis/l5/l5_windows_summary.csv")
    parser.add_argument("--scale", type=float, default=0.10)
    args = parser.parse_args()
    import json
    report = run_grid(Path(args.sequences), Path(args.summary), scale=args.scale)
    print("L5_DISCIPLINED_CLONE_REPLAY=" + json.dumps(report, separators=(",", ":"), sort_keys=True))
    print("L5_DISCIPLINED_CLONE_REPLAY_COMPLETE")


if __name__ == "__main__":
    main()
