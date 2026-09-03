"""Pure read-only allocator for expensive L5 shadow compute.

The normal priority score remains authoritative for most seats. A small,
configurable reserve ensures that already-subscribed markets which currently
match the spring probe observation regime (45-55c, genuine two-sided service,
>=10 authenticated trades/hour) receive L5-v2 state too. This moves compute
only: it does not subscribe/unsubscribe, change any venue gate, or touch an
execution path.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Mapping, Optional

SPRING_RESERVE_ENV = "PMUS_WS_L5_SPRING_RESERVE"
SPRING_RESERVE_DEFAULT = 4
SPRING_LO = 0.45
SPRING_HI = 0.55
SPRING_MIN_TRADES_1H = 10


def _recent_trade_count(s: Mapping[str, Any], now: float) -> int:
    n = 0
    for t in s.get("trades", []) or []:
        try:
            ts = float(t.get("t", 0)) if isinstance(t, Mapping) else float(t)
        except (TypeError, ValueError):
            continue
        if 0 <= now - ts <= 3600:
            n += 1
    return n


def _spring_rank(slug: str, state: Mapping[str, Any], now: float):
    s = state.get(slug) or {}
    try:
        bid = float(s.get("bid"))
    except (TypeError, ValueError):
        return None
    if not (SPRING_LO <= bid <= SPRING_HI):
        return None
    if not s.get("both_sides_served"):
        return None
    trades = _recent_trade_count(s, now)
    if trades < SPRING_MIN_TRADES_1H:
        return None
    try:
        depth = min(float(s.get("bid_qty") or 0), float(s.get("ask_qty") or 0))
    except (TypeError, ValueError):
        depth = 0.0
    # Highest authenticated flow first, then usable two-sided depth.
    return (-trades, -depth, slug)


def allocate_with_spring_reserve(
    base_allocator: Callable[..., list[str]],
    subscribed_slugs,
    state: Mapping[str, Any],
    targets: Optional[list[Mapping[str, Any]]] = None,
    now: Optional[float] = None,
    l5_infos: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list[str]:
    """Return the same total L5-seat count with a bounded spring reserve."""
    now = time.time() if now is None else now
    baseline = list(base_allocator(subscribed_slugs, state, targets=targets,
                                   now=now, l5_infos=l5_infos))
    if not baseline:
        return baseline
    try:
        reserve_n = int(os.getenv(SPRING_RESERVE_ENV,
                                  str(SPRING_RESERVE_DEFAULT)))
    except ValueError:
        reserve_n = SPRING_RESERVE_DEFAULT
    reserve_n = max(0, min(reserve_n, len(baseline)))
    if reserve_n == 0:
        return baseline

    ranked = []
    for slug in dict.fromkeys(subscribed_slugs):
        key = _spring_rank(slug, state, now)
        if key is not None:
            ranked.append((key, slug))
    ranked.sort(key=lambda x: x[0])
    reserved = [slug for _, slug in ranked[:reserve_n]]

    out = list(reserved)
    out.extend(slug for slug in baseline if slug not in out)
    # If a reserved slug displaced a baseline slug, keep the exact same seat
    # count. No subscription or planner state is changed here.
    return out[:len(baseline)]
