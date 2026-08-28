"""Read-only T-minus analysis for strict MLB prop shadow snapshots.

The v4/v5 scanners intentionally price only exact Polymarket US <-> Kalshi identities.
This analyzer adds game-start timing to the already de-duplicated positive episode view
without changing scanner/readiness behavior. It resolves each exact Polymarket market's
public ``gameStartTime`` (or ``eventStartTime``) and bins each unique opportunity once by
T-minus at episode open and at the best observed after-fee paper profit.

Repeated REST observations of the same resting depth are never additive. Missing or
unparseable start times fail closed into an UNKNOWN bucket. This module has no auth or
order-entry path.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable

import httpx

from src.mlb_prop_snapshot_pair_quality import analyze_snapshots, load_jsonl

POLY = "https://gateway.polymarket.us"

BUCKETS: tuple[tuple[str, int | None, int | None], ...] = (
    ("POST_START", None, 0),
    ("T_0_5M", 0, 5 * 60_000),
    ("T_5_15M", 5 * 60_000, 15 * 60_000),
    ("T_15_30M", 15 * 60_000, 30 * 60_000),
    ("T_30_60M", 30 * 60_000, 60 * 60_000),
    ("T_60_120M", 60 * 60_000, 120 * 60_000),
    ("T_GT_120M", 120 * 60_000, None),
)


def _parse_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _bucket(t_minus_ms: int | None) -> str:
    if t_minus_ms is None:
        return "UNKNOWN_START"
    if t_minus_ms <= 0:
        return "POST_START"
    for name, lower, upper in BUCKETS[1:]:
        if lower is not None and t_minus_ms < lower:
            continue
        if upper is None or t_minus_ms < upper:
            return name
    return "UNKNOWN_START"


def _episode_best_observed_ms(ep: dict[str, Any]) -> int:
    # Pair-quality intentionally stores only episode extrema, not every sample. Until
    # sample-level best timestamps are added upstream, use episode open as the
    # conservative timing witness rather than inventing sub-episode precision.
    return int(ep["opened_ms"])


def analyze_tminus(
    snapshots: Iterable[dict[str, Any]],
    *,
    start_ms_by_poly_slug: dict[str, int | None],
    episode_gap_ms: int = 2_000,
    informative_sample_gap_ms: int = 150,
) -> dict[str, Any]:
    base = analyze_snapshots(
        snapshots,
        episode_gap_ms=episode_gap_ms,
        informative_sample_gap_ms=informative_sample_gap_ms,
    )
    open_buckets: Counter[str] = Counter()
    timed_episodes: list[dict[str, Any]] = []

    for ep in base["episodes"]:
        start_ms = start_ms_by_poly_slug.get(str(ep["poly_slug"]))
        open_ms = int(ep["opened_ms"])
        best_ms = _episode_best_observed_ms(ep)
        t_open = start_ms - open_ms if start_ms is not None else None
        t_best = start_ms - best_ms if start_ms is not None else None
        bucket = _bucket(t_open)
        open_buckets[bucket] += 1
        timed = dict(ep)
        timed.update(
            {
                "scheduled_start_ms": start_ms,
                "t_minus_ms_at_open": t_open,
                "t_minus_minutes_at_open": (
                    str((Decimal(t_open) / Decimal(60_000)).quantize(Decimal("0.001")))
                    if t_open is not None
                    else None
                ),
                "t_minus_ms_at_best_observed_profit": t_best,
                "t_minus_bucket_at_open": bucket,
                "best_profit_timing_precision": "EPISODE_OPEN_CONSERVATIVE_PROXY",
            }
        )
        timed_episodes.append(timed)

    known = sum(v for k, v in open_buckets.items() if k != "UNKNOWN_START")
    return {
        "input_snapshots": base["input_snapshots"],
        "unique_positive_episodes": base["unique_positive_episodes"],
        "theoretical_unique_episode_profit_usd": base["theoretical_unique_episode_profit_usd"],
        "capital_if_each_unique_episode_taken_once_usd": base[
            "capital_if_each_unique_episode_taken_once_usd"
        ],
        "episodes_with_known_start": known,
        "episodes_with_unknown_start": open_buckets.get("UNKNOWN_START", 0),
        "episode_open_tminus_buckets": dict(sorted(open_buckets.items())),
        "double_count_warning": (
            "Each exact positive episode is counted once. Repeated observations and <=$1/<= $5 "
            "alternative sizing views are not additive capacity."
        ),
        "timing_warning": (
            "T-minus uses public scheduled game start; it does not prove exchange quote age or "
            "continuous sub-second survival. Best-profit timing currently uses episode-open as a "
            "conservative proxy because pair-quality summaries do not retain sample-level best timestamps."
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "episodes": timed_episodes,
    }


async def resolve_public_starts(poly_slugs: Iterable[str]) -> dict[str, int | None]:
    slugs = sorted({str(s) for s in poly_slugs if s})
    out: dict[str, int | None] = {}
    semaphore = asyncio.Semaphore(12)
    async with httpx.AsyncClient(timeout=15.0) as http:
        async def one(slug: str) -> None:
            async with semaphore:
                try:
                    response = await http.get(f"{POLY}/v1/markets/{slug}")
                    response.raise_for_status()
                    body = response.json()
                except Exception:
                    out[slug] = None
                    return
            payload = body.get("market") if isinstance(body.get("market"), dict) else body
            out[slug] = _parse_ms(
                payload.get("gameStartTime")
                or payload.get("eventStartTime")
                or payload.get("startTime")
            )

        await asyncio.gather(*(one(slug) for slug in slugs))
    return out


def _slugs_from_snapshots(snapshots: Iterable[dict[str, Any]]) -> set[str]:
    slugs: set[str] = set()
    for snapshot in snapshots:
        for field in ("top_probe_1usd", "top_probe_5usd"):
            for row in snapshot.get(field) or []:
                slug = str(row.get("poly_slug") or "")
                if slug:
                    slugs.add(slug)
    return slugs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--episode-gap-ms", type=int, default=2_000)
    parser.add_argument("--informative-sample-gap-ms", type=int, default=150)
    args = parser.parse_args()

    snapshots = load_jsonl(args.jsonl)
    starts = asyncio.run(resolve_public_starts(_slugs_from_snapshots(snapshots)))
    result = analyze_tminus(
        snapshots,
        start_ms_by_poly_slug=starts,
        episode_gap_ms=max(1, args.episode_gap_ms),
        informative_sample_gap_ms=max(1, args.informative_sample_gap_ms),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
