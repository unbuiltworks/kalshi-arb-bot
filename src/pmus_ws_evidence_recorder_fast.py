"""Recorder-only fast L5-state allocation over authenticated WS evidence.

This module wraps :mod:`src.pmus_ws_evidence_recorder` without changing its
network loop.  Discovery/subscription selection still happens on the existing
rediscovery cadence.  Between reconnects, however, the expensive L5 shadow
allocation is re-ranked on every status tick using only the already-subscribed
market slugs and the in-memory authenticated websocket state.

Safety / provenance:
- ZERO additional HTTP/REST calls; this module has no network client.
- ZERO new websocket frames; subscribe_frames delegates byte-for-byte to the
  guarded recorder implementation after only remembering the slug list.
- ZERO order path; execution remains outside this worker.
- SHADOW/THEORETICAL diagnostics only.

Issue #167: closes the fast intra-cycle allocation gap without shortening the
public discovery interval or forcing websocket reconnects.
"""
from __future__ import annotations

import os
import time
from typing import Any, Mapping, Optional, Sequence

from src import pmus_priority_queue as pq
from src import pmus_ws_evidence_recorder as rec

_BASE_SUBSCRIBE_FRAMES = rec.subscribe_frames
_BASE_BUILD_STATUS = rec.build_status
_CURRENT_SUBSCRIBED: tuple[str, ...] = ()


def capture_subscribe_frames(slugs: list[str]) -> list[dict]:
    """Remember the current WS universe, then delegate to the guarded sender.

    No frame is added, removed, or modified here.  The existing recorder's
    ``guard_outbound`` remains the only outbound-frame authority.
    """
    global _CURRENT_SUBSCRIBED
    _CURRENT_SUBSCRIBED = tuple(dict.fromkeys(str(s) for s in slugs if s))
    return _BASE_SUBSCRIBE_FRAMES(slugs)


def fast_l5_allocation(
    state: Mapping[str, Any],
    subscribed_slugs: Sequence[str],
    prior_alloc: Optional[set[str]],
    now: Optional[float] = None,
) -> tuple[set[str], dict]:
    """Pure in-memory rerank of the expensive L5 shadow seats.

    The outer planner decides how many L5 seats exist.  This function only
    reallocates those seats among markets already on the healthy websocket.
    It intentionally does *not* alter the subscription set, discovery timer,
    or planner hysteresis state.
    """
    now = time.time() if now is None else float(now)
    if prior_alloc is None:
        return set(), {
            "mode": "disabled_non_priority_planner",
            "network_get_calls_added": 0,
            "subscribed_candidates": len(tuple(subscribed_slugs)),
            "allocated": None,
            "changed_from_cycle_plan": False,
            "selected": [],
        }

    prior = set(prior_alloc)
    slugs = list(dict.fromkeys(str(s) for s in subscribed_slugs if s))
    limit = min(len(slugs), len(prior))
    if limit <= 0:
        return set(), {
            "mode": "intra_cycle_ws_only",
            "network_get_calls_added": 0,
            "subscribed_candidates": len(slugs),
            "allocated": 0,
            "changed_from_cycle_plan": bool(prior),
            "selected": [],
        }

    ranked: list[tuple[tuple[Any, ...], str, Optional[float]]] = []
    for slug in slugs:
        score = pq.score_market(state.get(slug), now).get("score")
        # Unknown authenticated evidence stays behind scored markets.  Prior
        # allocation is only a tie-breaker, damping churn without preventing
        # a real score change from moving a market into an L5 seat.
        key = (
            score is None,
            -(float(score) if score is not None else -1.0),
            0 if slug in prior else 1,
            slug,
        )
        ranked.append((key, slug, score))
    ranked.sort(key=lambda row: row[0])

    ordered = [slug for _, slug, _ in ranked[:limit]]
    selected = set(ordered)
    top_scores = [
        {"slug": slug, "score": (None if score is None else round(float(score), 6))}
        for _, slug, score in ranked[: min(limit, 10)]
    ]
    return selected, {
        "mode": "intra_cycle_ws_only",
        "source": "authenticated_ws_in_memory",
        "network_get_calls_added": 0,
        "subscribed_candidates": len(slugs),
        "allocated": len(selected),
        "changed_from_cycle_plan": selected != prior,
        "selected": ordered,
        "top_scores": top_scores,
        "status_interval_s": float(os.getenv(rec.STATUS_EVERY_ENV,
                                                str(rec.DEFAULT_STATUS_EVERY_S))),
    }


def fast_build_status(
    state: Mapping[str, Any],
    connected: bool,
    msgs: int,
    subscribed: int,
    now: Optional[float] = None,
    discovery: Optional[Mapping[str, Any]] = None,
    l5_alloc: Optional[set] = None,
) -> dict:
    """Drop-in status builder with intra-cycle L5 seat reallocation."""
    ts = time.time() if now is None else float(now)
    if l5_alloc is None:
        # Preserve rotation-planner semantics exactly: base build_status(None)
        # evaluates L5 state for all displayed rows.
        status = _BASE_BUILD_STATUS(
            state, connected, msgs, subscribed, now=ts,
            discovery=discovery, l5_alloc=None)
        status["fast_l5_rerank"] = {
            "mode": "disabled_non_priority_planner",
            "network_get_calls_added": 0,
            "subscribed_candidates": len(_CURRENT_SUBSCRIBED),
            "allocated": None,
            "changed_from_cycle_plan": False,
            "selected": [],
        }
        return status

    fast_alloc, diag = fast_l5_allocation(
        state, _CURRENT_SUBSCRIBED, set(l5_alloc), now=ts)
    status = _BASE_BUILD_STATUS(
        state, connected, msgs, subscribed, now=ts,
        discovery=discovery, l5_alloc=fast_alloc)
    status["fast_l5_rerank"] = diag
    return status


# Patch only the two recorder hooks needed for this wrapper.  The original run
# loop, discovery cadence, websocket connection, frame guard, hydration, tape
# persistence, spring_watch, and shutdown behavior remain untouched.
rec.subscribe_frames = capture_subscribe_frames
rec.build_status = fast_build_status


def main() -> int:
    return rec.main()


if __name__ == "__main__":
    raise SystemExit(main())
