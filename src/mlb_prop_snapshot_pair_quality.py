"""Fail-closed persistence/capacity analysis for strict MLB prop v4 JSONL snapshots.

This consumes repeated outputs from ``strict_mlb_prop_snapshot_v4.py`` and turns the
<= $1 / <= $5 top-probe views into one exact-pair sample stream without counting the
same resting book twice.  It deliberately does *not* claim continuous sub-second
survival from REST polling.  Instead it reports whether synchronized positive samples
were observed at/after 50/100/250/500 ms and whether the sampling cadence was dense
enough to make that threshold informative.

Read-only research only.  No authentication or order-entry code exists here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable

SURVIVAL_THRESHOLDS_MS = (50, 100, 250, 500)
DEFAULT_EPISODE_GAP_MS = 2_000
DEFAULT_MAX_INFORMATIVE_SAMPLE_GAP_MS = 150


def _d(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _probe_rows(snapshot: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield one best budget view per exact pair/direction from a v4 snapshot."""
    if snapshot.get("read_only") is not True or snapshot.get("order_entry") is not False:
        return []
    if int(snapshot.get("execution_eligible_pairs") or 0) != 0:
        return []

    best: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for field, budget in (("top_probe_1usd", "1"), ("top_probe_5usd", "5")):
        for row in snapshot.get(field) or []:
            if row.get("read_only") is not True or row.get("order_entry") is not False:
                continue
            if row.get("execution_eligible") is not False:
                continue
            if row.get("synchronized_book_pair") is not True:
                continue
            plan = row.get(f"probe_{budget}usd") or {}
            direction = str(plan.get("direction") or "")
            if not direction:
                continue
            profit = _d(plan.get("conservative_locked_profit"))
            cost = _d(plan.get("total_cost_upper_bound"))
            qty = int(plan.get("quantity") or 0)
            if profit is None or cost is None or profit <= 0 or cost <= 0 or qty <= 0:
                continue
            poly_ms = row.get("poly_received_ms")
            kalshi_ms = row.get("kalshi_received_ms")
            if not isinstance(poly_ms, int) or not isinstance(kalshi_ms, int):
                continue
            key = (
                str(row.get("family") or ""),
                str(row.get("poly_slug") or ""),
                str(row.get("kalshi_ticker") or ""),
                direction,
            )
            if not all(key[:3]):
                continue
            candidate = {
                "key": key,
                "observed_ms": max(poly_ms, kalshi_ms),
                "receive_skew_ms": abs(poly_ms - kalshi_ms),
                "profit": profit,
                "cost": cost,
                "quantity": qty,
                "budget": budget,
                "rules_status": row.get("rules_status") or row.get("rule_status"),
                "full_rules_audited": bool(row.get("full_rules_audited", False)),
                "live_probe_ready": bool(row.get("live_probe_ready", False)),
            }
            prior = best.get(key)
            if prior is None or candidate["profit"] > prior["profit"]:
                best[key] = candidate
    return best.values()


def analyze_snapshots(
    snapshots: Iterable[dict[str, Any]],
    *,
    episode_gap_ms: int = DEFAULT_EPISODE_GAP_MS,
    informative_sample_gap_ms: int = DEFAULT_MAX_INFORMATIVE_SAMPLE_GAP_MS,
) -> dict[str, Any]:
    samples_by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    input_snapshots = 0
    for snapshot in snapshots:
        input_snapshots += 1
        for sample in _probe_rows(snapshot):
            samples_by_key[sample["key"]].append(sample)

    episodes: list[dict[str, Any]] = []
    for key, samples in samples_by_key.items():
        samples.sort(key=lambda s: s["observed_ms"])
        groups: list[list[dict[str, Any]]] = []
        for sample in samples:
            if not groups or sample["observed_ms"] - groups[-1][-1]["observed_ms"] > episode_gap_ms:
                groups.append([sample])
            else:
                groups[-1].append(sample)

        for group in groups:
            first = group[0]
            last = group[-1]
            duration_ms = last["observed_ms"] - first["observed_ms"]
            gaps = [
                group[i]["observed_ms"] - group[i - 1]["observed_ms"]
                for i in range(1, len(group))
            ]
            max_gap = max(gaps) if gaps else None
            threshold_status: dict[str, str] = {}
            for threshold in SURVIVAL_THRESHOLDS_MS:
                witness_index = next(
                    (i for i, s in enumerate(group) if s["observed_ms"] - first["observed_ms"] >= threshold),
                    None,
                )
                if witness_index is None:
                    threshold_status[str(threshold)] = "NOT_OBSERVED"
                    continue
                prefix_gaps = gaps[:witness_index]
                if prefix_gaps and max(prefix_gaps) <= informative_sample_gap_ms:
                    threshold_status[str(threshold)] = "SAMPLED_POSITIVE_THROUGH_THRESHOLD"
                else:
                    threshold_status[str(threshold)] = "UNRESOLVED_SPARSE_REST_SAMPLING"

            episodes.append(
                {
                    "family": key[0],
                    "poly_slug": key[1],
                    "kalshi_ticker": key[2],
                    "direction": key[3],
                    "sample_count": len(group),
                    "opened_ms": first["observed_ms"],
                    "closed_observed_ms": last["observed_ms"],
                    "observed_duration_ms": duration_ms,
                    "max_inter_sample_gap_ms": max_gap,
                    "min_profit_usd": str(min(s["profit"] for s in group)),
                    "max_profit_usd": str(max(s["profit"] for s in group)),
                    "max_cost_usd": str(max(s["cost"] for s in group)),
                    "max_quantity": max(s["quantity"] for s in group),
                    "min_quantity": min(s["quantity"] for s in group),
                    "max_receive_skew_ms": max(s["receive_skew_ms"] for s in group),
                    "survival_sampling": threshold_status,
                    "continuous_subsecond_survival_proven": False,
                    "rules_status": first.get("rules_status"),
                    "full_rules_audited": all(s["full_rules_audited"] for s in group),
                    "live_probe_ready": False,
                    "paper_shadow_only": True,
                }
            )

    # Each exact episode contributes at most one unit of displayed resting-liquidity
    # economics: the best observed profit is descriptive, not additive across samples.
    theoretical_unique_episode_profit = sum(
        (Decimal(ep["max_profit_usd"]) for ep in episodes), Decimal("0")
    )
    capital_at_best = sum((Decimal(ep["max_cost_usd"]) for ep in episodes), Decimal("0"))
    return {
        "input_snapshots": input_snapshots,
        "unique_exact_pairs": len(samples_by_key),
        "unique_positive_episodes": len(episodes),
        "theoretical_unique_episode_profit_usd": str(theoretical_unique_episode_profit),
        "capital_if_each_unique_episode_taken_once_usd": str(capital_at_best),
        "survival_thresholds_ms": list(SURVIVAL_THRESHOLDS_MS),
        "informative_sample_gap_ms": informative_sample_gap_ms,
        "continuous_subsecond_survival_proven": False,
        "survival_warning": (
            "REST samples cannot prove continuous 50/100/250/500ms survival. "
            "Thresholds are marked informative only when repeated positive observations "
            "arrive densely enough; otherwise they remain unresolved."
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "episodes": sorted(episodes, key=lambda e: (e["opened_ms"], e["poly_slug"])),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--episode-gap-ms", type=int, default=DEFAULT_EPISODE_GAP_MS)
    parser.add_argument(
        "--informative-sample-gap-ms",
        type=int,
        default=DEFAULT_MAX_INFORMATIVE_SAMPLE_GAP_MS,
    )
    args = parser.parse_args()
    result = analyze_snapshots(
        load_jsonl(args.jsonl),
        episode_gap_ms=max(1, args.episode_gap_ms),
        informative_sample_gap_ms=max(1, args.informative_sample_gap_ms),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
