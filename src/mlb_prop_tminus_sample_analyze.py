"""Sample-witnessed T-minus timing for read-only MLB prop shadow economics.

This complements ``mlb_prop_tminus_analyze`` by retaining the exact synchronized
REST sample timestamp at which each unique positive episode's best conservative
paper profit was observed. It reuses the same fail-closed probe-row filter and
same episode-gap semantics as ``mlb_prop_snapshot_pair_quality``.

Repeated samples are descriptive only; profit is never summed across resting depth.
No auth, order entry, live readiness, or rules-gate behavior exists here.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable

from src.mlb_prop_snapshot_pair_quality import (
    DEFAULT_EPISODE_GAP_MS,
    _probe_rows,
    load_jsonl,
)
from src.mlb_prop_tminus_analyze import _bucket, resolve_public_starts


def analyze_sample_witnessed_tminus(
    snapshots: Iterable[dict[str, Any]],
    *,
    start_ms_by_poly_slug: dict[str, int | None],
    episode_gap_ms: int = DEFAULT_EPISODE_GAP_MS,
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
            # Stable tie-breaker: earliest synchronized witness for the maximum
            # conservative paper profit. Budget views were already collapsed by
            # _probe_rows, so this cannot double-count <=$1 and <=$5.
            best_profit = max(s["profit"] for s in group)
            best = next(s for s in group if s["profit"] == best_profit)
            opened_ms = int(group[0]["observed_ms"])
            best_ms = int(best["observed_ms"])
            start_ms = start_ms_by_poly_slug.get(key[1])
            t_open = start_ms - opened_ms if start_ms is not None else None
            t_best = start_ms - best_ms if start_ms is not None else None
            episodes.append(
                {
                    "family": key[0],
                    "poly_slug": key[1],
                    "kalshi_ticker": key[2],
                    "direction": key[3],
                    "sample_count": len(group),
                    "opened_ms": opened_ms,
                    "best_profit_observed_ms": best_ms,
                    "scheduled_start_ms": start_ms,
                    "t_minus_ms_at_open": t_open,
                    "t_minus_bucket_at_open": _bucket(t_open),
                    "t_minus_ms_at_best_observed_profit": t_best,
                    "t_minus_bucket_at_best_observed_profit": _bucket(t_best),
                    "best_observed_profit_usd": str(best_profit),
                    "capital_at_best_observed_profit_usd": str(best["cost"]),
                    "quantity_at_best_observed_profit": best["quantity"],
                    "receive_skew_ms_at_best_observed_profit": best["receive_skew_ms"],
                    "best_profit_timing_precision": "SYNCHRONIZED_REST_SAMPLE_WITNESS",
                    "continuous_subsecond_survival_proven": False,
                    "paper_shadow_only": True,
                    "live_probe_ready": False,
                }
            )

    episodes.sort(key=lambda e: (e["opened_ms"], e["poly_slug"], e["direction"]))
    total_best = sum((Decimal(ep["best_observed_profit_usd"]) for ep in episodes), Decimal("0"))
    return {
        "input_snapshots": input_snapshots,
        "unique_positive_episodes": len(episodes),
        "theoretical_unique_episode_profit_usd": str(total_best),
        "timing_precision": "SYNCHRONIZED_REST_SAMPLE_WITNESS",
        "timing_warning": (
            "Best-profit T-minus is witnessed by a synchronized positive REST sample. "
            "It does not prove continuous sub-second survival or exchange-origin quote age."
        ),
        "double_count_warning": (
            "Each exact positive episode contributes its best observed resting-liquidity profit once; "
            "repeated observations and <=$1/<= $5 budget views are not additive."
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "episodes": episodes,
    }


def _slugs_from_snapshots(snapshots: Iterable[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for snapshot in snapshots:
        for sample in _probe_rows(snapshot):
            out.add(str(sample["key"][1]))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--episode-gap-ms", type=int, default=DEFAULT_EPISODE_GAP_MS)
    args = parser.parse_args()
    snapshots = load_jsonl(args.jsonl)

    import asyncio

    starts = asyncio.run(resolve_public_starts(_slugs_from_snapshots(snapshots)))
    result = analyze_sample_witnessed_tminus(
        snapshots,
        start_ms_by_poly_slug=starts,
        episode_gap_ms=max(1, args.episode_gap_ms),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
