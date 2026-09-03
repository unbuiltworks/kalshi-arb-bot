"""Read-only spring-watch enrichment for the PMUS WS evidence recorder.

This module does not discover markets, subscribe, mutate venue state, or alter
any execution gate. It adds already-computed theoretical slot evidence to the
recorder's advisory ``spring_watch`` rows and reserves a bounded subset of the
existing expensive L5-shadow compute seats for already-subscribed spring-shaped
markets, then delegates to the canonical read-only recorder.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from src import pmus_ws_evidence_recorder as base
from src import pmus_priority_queue as pq
from src.pmus_l5_compute_allocator import allocate_with_spring_reserve

_ORIGINAL_ALLOCATE_L5 = pq.allocate_l5


def _count(counts: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(counts.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def spring_watch(rows: Mapping[str, Mapping[str, Any]],
                 l5_alloc: Optional[set] = None,
                 limit: Optional[int] = None) -> list[dict]:
    """Canonical spring-watch ranking plus explicit completion evidence.

    Visibility only. ``repeated_completion`` is an advisory label derived
    from the same authenticated-tape theoretical slot counts already present
    on each row. It is NOT an execution admission and changes no gate.
    """
    picked = []
    for slug, r in rows.items():
        try:
            bid = float(r.get("bid"))
        except (TypeError, ValueError):
            continue
        if not (base.SPRING_WATCH_LO <= bid <= base.SPRING_WATCH_HI):
            continue
        if not r.get("both_sides_served"):
            continue
        try:
            depth = min(float(r.get("bid_qty") or 0),
                        float(r.get("ask_qty") or 0))
        except (TypeError, ValueError):
            depth = 0.0

        shadow = r.get("l5_shadow") or {}
        counts = dict(r.get("slot_counts_theoretical") or {})
        matched = _count(counts, "MATCHED")
        one_sided = _count(counts, "ONE_SIDED_HOLD")
        no_fill = _count(counts, "NO_FILL")
        picked.append(({
            "slug": slug,
            "bid": r.get("bid"),
            "ask": r.get("ask"),
            "bid_qty": r.get("bid_qty"),
            "ask_qty": r.get("ask_qty"),
            "trades_1h": r.get("trades_1h"),
            "two_sided_trades": r.get("two_sided_trades"),
            "gate_status": r.get("gate_status"),
            "slot_counts_theoretical": counts,
            "matched_slots": matched,
            "one_sided_holds": one_sided,
            "no_fill_slots": no_fill,
            "repeated_completion": matched >= 2,
            "l5_allocated": (slug in l5_alloc) if l5_alloc is not None
            else None,
            "off_distribution": shadow.get("off_distribution"),
            "labels": "SHADOW/THEORETICAL; advisory only, not an "
                      "execution admission",
        }, depth))

    picked.sort(key=lambda rd: (not rd[0].get("two_sided_trades"),
                                -int(rd[0].get("trades_1h") or 0),
                                -rd[1], str(rd[0]["slug"])))
    lim = base.SPRING_WATCH_LIMIT if limit is None else limit
    return [row for row, _ in picked[:max(0, lim)]]


def allocate_l5(subscribed_slugs, state, targets=None, now=None,
                l5_infos=None):
    """Keep total L5 seats fixed while reserving a few for spring watch."""
    return allocate_with_spring_reserve(
        _ORIGINAL_ALLOCATE_L5, subscribed_slugs, state, targets=targets,
        now=now, l5_infos=l5_infos)


def install() -> None:
    """Install read-only status and compute-allocation enrichments."""
    base.spring_watch = spring_watch
    pq.allocate_l5 = allocate_l5


def main() -> int:
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
