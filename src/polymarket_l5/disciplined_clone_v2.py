"""No-hindsight L5 fill-path replay using the harvest's exact accounting semantics.

This v2 exists because the first replay used price*shares rather than the source
CSV's normalized `dollars` field and therefore failed baseline P&L reconciliation.
All accepted fills here are applied through the same InventoryState/apply_trade and
derived_window_pnl functions used to produce l5_windows_summary.csv.

This is analysis only. It assumes a hypothetical smaller clone receives a fixed
fraction of the observed fills at the same timestamps. It does not assume equal
queue position or model rebates.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .disciplined_clone import RECENT_START_UTC, load_sequences, load_summary, _f, _iso_ts, _norm_outcome
from .inventory import InventoryState, apply_trade
from .pnl import derived_window_pnl


def _source_outcome(value: Any) -> str:
    x = _norm_outcome(value)
    return "Up" if x == "UP" else ("Down" if x == "DOWN" else "")


def _candidate_unit_cost(row: dict[str, str]) -> float:
    shares = _f(row.get("shares"))
    dollars = _f(row.get("dollars"))
    if shares <= 0:
        return _f(row.get("price"))
    return dollars / shares


def _marginal_pair_cost(state: InventoryState, outcome: str, unit_cost: float) -> float | None:
    if outcome == "Up" and state.down.qty > 0:
        return unit_cost + state.down.wavg_price
    if outcome == "Down" and state.up.qty > 0:
        return unit_cost + state.up.wavg_price
    return None


def _projected_residual_ratio(state: InventoryState, outcome: str, qty: float) -> tuple[float, float]:
    before = abs(state.up.qty - state.down.qty)
    up = state.up.qty + (qty if outcome == "Up" else 0.0)
    down = state.down.qty + (qty if outcome == "Down" else 0.0)
    after = abs(up - down)
    high = max(up, down)
    return (0.0 if high <= 1e-12 else after / high), after - before


def replay_window(
    rows: list[dict[str, str]],
    summary: dict[str, str],
    *,
    scale: float,
    pair_ceiling: float | None,
    residual_cap: float | None,
) -> dict[str, Any]:
    state = InventoryState()
    accepted_cashflows: list[tuple[float, float]] = []
    rejected_pair = 0
    rejected_residual = 0
    accepted = 0
    observed = 0

    for row in rows:
        if str(row.get("side") or "").upper() != "BUY":
            # Current harvested short-window sample reports zero SELL fills. Fail
            # closed if that changes so source accounting cannot silently diverge.
            raise RuntimeError(f"unexpected non-BUY fill in {summary.get('window_id')}: {row.get('side')}")
        outcome = _source_outcome(row.get("outcome"))
        if outcome not in {"Up", "Down"}:
            continue
        raw_shares = _f(row.get("shares"))
        raw_dollars = _f(row.get("dollars"))
        if raw_shares <= 0:
            continue
        shares = raw_shares * scale
        dollars = raw_dollars * scale
        unit_cost = 0.0 if shares <= 0 else dollars / shares
        observed += 1

        pair_cost = _marginal_pair_cost(state, outcome, unit_cost)
        if pair_ceiling is not None and pair_cost is not None and pair_cost > pair_ceiling + 1e-12:
            rejected_pair += 1
            continue

        if residual_cap is not None and state.up.qty > 1e-12 and state.down.qty > 1e-12:
            projected, residual_delta = _projected_residual_ratio(state, outcome, shares)
            if residual_delta > 1e-12 and projected > residual_cap + 1e-12:
                rejected_residual += 1
                continue

        accepted_row = dict(row)
        accepted_row["outcome"] = outcome
        accepted_row["shares"] = shares
        accepted_row["dollars"] = dollars
        accepted_row["price"] = _f(row.get("price"))
        apply_trade(state, accepted_row)
        accepted += 1
        accepted_cashflows.append((_f(row.get("timestamp")), dollars))

    winner = summary.get("winning_outcome") or ""
    pnl = derived_window_pnl(state, winner)
    end_ts = _iso_ts(summary.get("window_end_utc") or "")
    spend = state.gross_dollars_bought
    high = max(state.up.qty, state.down.qty)
    residual_ratio = 0.0 if high <= 1e-12 else abs(state.up.qty - state.down.qty) / high
    return {
        "window_id": summary.get("window_id"),
        "accepted_fills": accepted,
        "observed_buy_fills": observed,
        "rejected_pair_ceiling": rejected_pair,
        "rejected_residual_cap": rejected_residual,
        "accepted_spend": spend,
        "settlement_pnl": pnl,
        "residual_ratio": residual_ratio,
        "accepted_cashflows": accepted_cashflows,
        "release_cashflow": (end_ts, spend) if end_ts else None,
    }


def peak_committed_capital(windows: list[dict[str, Any]]) -> float:
    events: list[tuple[float, int, float]] = []
    for w in windows:
        events.extend((ts, 1, dollars) for ts, dollars in w["accepted_cashflows"])
        if w.get("release_cashflow"):
            ts, dollars = w["release_cashflow"]
            events.append((ts, -1, dollars))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = peak = 0.0
    for _, direction, dollars in events:
        cur += dollars if direction > 0 else -dollars
        cur = max(cur, 0.0)
        peak = max(peak, cur)
    return peak


def run_grid(sequence_csv: Path, summary_csv: Path, scale: float) -> dict[str, Any]:
    summaries = load_summary(summary_csv)
    groups = load_sequences(sequence_csv, recent_only=True)
    common = [(wid, seq, summaries[wid]) for wid, seq in groups.items() if wid in summaries]
    configs = [
        ("replay_all", None, None),
        ("pair_101", 1.01, None),
        ("pair_100", 1.00, None),
        ("pair_099", 0.99, None),
        ("pair_098", 0.98, None),
        ("pair_099_residual35", 0.99, 0.35),
        ("pair_099_residual20", 0.99, 0.20),
    ]
    policies: list[dict[str, Any]] = []
    baseline_source_total = 0.0
    baseline_mismatch = 0

    for name, ceiling, residual in configs:
        windows = [replay_window(seq, summary, scale=scale, pair_ceiling=ceiling, residual_cap=residual) for _, seq, summary in common]
        resolved = [w for w in windows if w["settlement_pnl"] is not None]
        spend = sum(w["accepted_spend"] for w in windows)
        pnl = sum(float(w["settlement_pnl"]) for w in resolved)
        policies.append({
            "name": name,
            "pair_ceiling": ceiling,
            "residual_cap": residual,
            "accepted_spend": spend,
            "settlement_pnl": pnl,
            "pnl_per_1000_accepted_spend": None if spend <= 0 else 1000 * pnl / spend,
            "peak_committed_capital_upper_bound": peak_committed_capital(windows),
            "pair_ceiling_rejections": sum(w["rejected_pair_ceiling"] for w in windows),
            "residual_cap_rejections": sum(w["rejected_residual_cap"] for w in windows),
            "final_residual_ratio_lt_20pct_rate": sum(1 for w in windows if w["residual_ratio"] < 0.20) / len(windows),
            "resolved_windows": len(resolved),
        })
        if name == "replay_all":
            for _, _, summary in common:
                raw = summary.get("pnl_derived_resolution")
                if raw not in (None, ""):
                    baseline_source_total += _f(raw) * scale
            replay_by_id = {w["window_id"]: w for w in windows}
            for wid, _, summary in common:
                raw = summary.get("pnl_derived_resolution")
                if raw in (None, ""):
                    continue
                got = replay_by_id[wid]["settlement_pnl"]
                want = _f(raw) * scale
                if got is None or abs(float(got) - want) > 2e-5:
                    baseline_mismatch += 1

    baseline = policies[0]
    for p in policies:
        p["pnl_delta_vs_replay_all"] = p["settlement_pnl"] - baseline["settlement_pnl"]
        p["turnover_ratio_vs_replay_all"] = None if baseline["accepted_spend"] <= 0 else p["accepted_spend"] / baseline["accepted_spend"]

    return {
        "classification": "ANALYSIS_ONLY_NO_HINDSIGHT_FILL_PATH_REPLAY_V2_SOURCE_ACCOUNTING",
        "recent_start_utc": RECENT_START_UTC,
        "scale": scale,
        "window_count": len(common),
        "baseline_source_derived_pnl_scaled": baseline_source_total,
        "baseline_replay_pnl": baseline["settlement_pnl"],
        "baseline_pnl_difference": baseline["settlement_pnl"] - baseline_source_total,
        "baseline_window_mismatch_count": baseline_mismatch,
        "policies": policies,
        "guardrails": {
            "source_inventory_accounting_reused": True,
            "source_dollars_field_reused": True,
            "same_fill_transfer_assumption": True,
            "same_queue_position_assumed": False,
            "rebates_modeled": False,
            "future_outcome_used_for_fill_decisions": False,
            "live_order_entry": False,
            "realized_pnl": False,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sequences", default="analysis/l5/l5_window_sequences.csv")
    p.add_argument("--summary", default="analysis/l5/l5_windows_summary.csv")
    p.add_argument("--scale", type=float, default=0.10)
    args = p.parse_args()
    out = run_grid(Path(args.sequences), Path(args.summary), args.scale)
    print("L5_DISCIPLINED_CLONE_REPLAY_V2=" + json.dumps(out, separators=(",", ":"), sort_keys=True))
    print("L5_DISCIPLINED_CLONE_REPLAY_V2_COMPLETE")


if __name__ == "__main__":
    main()
