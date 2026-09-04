"""Production-tape coverage proof for the PMUS live-sports collector.

Answers, from a collector volume alone: how many DISTINCT live events and
markets were actually covered, in which categories, how the rotation
behaved, which markets were pinned and at what observed cadence, and where
the 60s polling-gap rule bites.

Point it at whichever durable root you want to prove:

    python3 -m src.pmus_coverage_report --root /state/pmus-sports --hours 1

READ ONLY. Reads the durable tape; performs no venue call, no mutation and
no order path. Every number is measured from recorded rows -- nothing is
inferred from configuration, because the point is to prove what the
collector DID rather than what it was set up to do.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_status_report as st
from src import pmus_spring_shadow_replay as replay

GAP_S = 60.0            # same threshold analyze_market fails a market on


def _ts(row: Mapping[str, Any]) -> Optional[float]:
    return st._ts(row)


def _pcts(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)
    def q(p): return s[min(len(s) - 1, int(len(s) * p))]
    return {"n": len(s), "min": round(s[0], 1), "p50": round(q(0.5), 1),
            "p90": round(q(0.9), 1), "max": round(s[-1], 1)}


def load_rows(root: Path, hours: float, now: Optional[float] = None) -> list:
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    since = datetime.fromtimestamp(now - hours * 3600, timezone.utc)
    return list(st.load_tape(root, since))


def coverage(rows: list, now: Optional[float] = None) -> dict:
    """Measured coverage, rotation, pinning and gap behaviour."""
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    books = [r for r in rows if r.get("kind") == "bbo_book" and r.get("slug")]
    discos = [r for r in rows if r.get("kind") == "discovery"]
    errors = [r for r in rows if r.get("kind") == "error"]

    # --- what the collector actually polled ---
    per_slug: dict[str, list[float]] = defaultdict(list)
    league_of: dict[str, str] = {}
    for r in books:
        t = _ts(r)
        if t is None:
            continue
        per_slug[r["slug"]].append(t)
        if r.get("league"):
            league_of[r["slug"]] = str(r["league"])

    # event identity lives on the discovery rows, not the book rows
    event_of: dict[str, str] = {}
    pin_reasons: dict[str, str] = {}
    programs_live: set[str] = set()
    rotation_cycles = 0
    discovered_markets: set[str] = set()
    for d in discos:
        for t in d.get("targets") or []:
            slug = t.get("slug")
            if not slug:
                continue
            discovered_markets.add(slug)
            if t.get("event_slug"):
                event_of[slug] = str(t["event_slug"])
            if t.get("league") and slug not in league_of:
                league_of[slug] = str(t["league"])
        diag = d.get("diagnostics") or {}
        rotation_cycles = max(rotation_cycles,
                              int(diag.get("rotation_cycle") or 0) + 1)
        for p in diag.get("pinned_markets") or []:
            if p.get("slug"):
                pin_reasons[str(p["slug"])] = str(p.get("reason") or "pinned")
        programs_live.update(diag.get("incentive_programs_live") or [])

    def cat(slug: str) -> str:
        return league_of.get(slug) or replay.slug_category(slug)

    # --- gaps, split by pinned vs rotating ---
    def gap_stats(slugs) -> dict:
        worst, over = [], 0
        for s in slugs:
            ts = sorted(per_slug[s])
            if len(ts) < 2:
                continue
            gaps = [b - a for a, b in zip(ts, ts[1:])]
            worst.append(max(gaps))
            over += 1 if max(gaps) > GAP_S else 0
        return {**_pcts(worst), "markets_over_60s": over}

    polled = set(per_slug)
    pinned = sorted(polled & set(pin_reasons))
    rotating = sorted(polled - set(pin_reasons))

    span = 0.0
    all_ts = [t for v in per_slug.values() for t in v]
    if all_ts:
        span = max(all_ts) - min(all_ts)

    return {
        "kind": "PMUS_COVERAGE_REPORT",
        "window": {
            "span_minutes": round(span / 60.0, 1),
            "newest_row_age_s": round(now - max(all_ts), 1) if all_ts else None,
            "book_rows": len(books),
            "discovery_rows": len(discos),
            "error_rows": len(errors),
            "error_stages": dict(Counter(
                str(e.get("stage")) for e in errors).most_common(6)),
        },
        "coverage": {
            "distinct_markets_polled": len(polled),
            "distinct_markets_discovered": len(discovered_markets),
            "distinct_live_events_polled": len(
                {event_of[s] for s in polled if s in event_of}),
            "markets_without_event_identity": sum(
                1 for s in polled if s not in event_of),
            "categories": dict(Counter(cat(s) for s in polled).most_common()),
            "category_count": len({cat(s) for s in polled}),
        },
        "rotation": {
            "discovery_cycles": rotation_cycles,
            "polled_vs_discovered_pct": (
                round(100.0 * len(polled) / len(discovered_markets), 1)
                if discovered_markets else None),
            "single_observation_markets": sum(
                1 for s in polled if len(per_slug[s]) == 1),
        },
        "pinning": {
            "pinned_markets": [
                {"slug": s, "reason": pin_reasons[s],
                 "polls": len(per_slug[s]),
                 "worst_gap_s": round(max(
                     (b - a for a, b in zip(sorted(per_slug[s]),
                                            sorted(per_slug[s])[1:])),
                     default=0.0), 1)}
                for s in pinned],
            "incentive_programs_live": sorted(programs_live),
        },
        "gaps": {
            "threshold_s": GAP_S,
            "pinned": gap_stats(pinned),
            "rotating": gap_stats(rotating),
        },
        "labels": ("READ-ONLY measurement of a durable collector tape; no "
                   "venue call, no mutation, no order path"),
    }


def l5_sequences(rows: list, now: Optional[float] = None) -> dict:
    """OPEN -> EXTEND -> COMPLETE relation counts over the same tape."""
    from src import pmus_sports_l5_decision as l5d
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    by_slug: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get("kind") == "bbo_book" and r.get("slug"):
            by_slug[r["slug"]].append(r)
    dec = l5d.slate_decisions(by_slug, now=now)
    rel = Counter()
    runs = Counter()
    per_market = []
    for row in dec.get("rows") or []:
        acts = [a for a in (row.get("actions_replayed") or [])
                if a.get("decision") == "quote"]
        seq = [str(a.get("relation")) for a in acts]
        rel.update(seq)
        if seq:
            runs[len(seq)] += 1
            per_market.append({
                "slug": row.get("slug"),
                "sequence": seq[:12],
                "completes": sum(1 for x in seq if x == "complete"),
                "fresh_15m": row.get("fresh_15m_status"),
            })
    return {
        "kind": "PMUS_L5_SEQUENCES",
        "model_sha": dec.get("model_sha"),
        "candidates_evaluated": dec.get("candidates_evaluated"),
        "relation_counts": dict(rel),
        "markets_with_any_action": len(per_market),
        "markets_reaching_complete": sum(
            1 for m in per_market if m["completes"] > 0),
        "sequence_length_hist": dict(sorted(runs.items())),
        "per_market": sorted(per_market,
                             key=lambda m: -m["completes"])[:12],
        "labels": ("SHADOW/THEORETICAL replay over recorded books; no order "
                   "was placed and realized_pnl is null by construction"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="/state/pmus-sports",
                    help="durable collector root to prove")
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--sequences", action="store_true",
                    help="also replay L5 OPEN/EXTEND/COMPLETE sequences")
    args = ap.parse_args()
    rows = load_rows(Path(args.root), args.hours)
    out: dict[str, Any] = {"root": args.root, "hours": args.hours,
                           **coverage(rows)}
    if args.sequences:
        out["l5_sequences"] = l5_sequences(rows)
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
