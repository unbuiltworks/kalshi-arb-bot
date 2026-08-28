"""Stricter read-only weather portfolio metric binding.

The v1 guard derives ``executable_probability`` from scanner-snapshot counts. Snapshot
occupancy is useful timing evidence, but it is not an independent-opportunity sample:
one long-lived resting quote can contribute many positive snapshots. This wrapper keeps
all v1 book/fee/model/scope/time checks and additionally requires execution probability
to be derived from unique OPEN episodes in the same complete, deduplicated observation
window. Repeated resting depth/MOVE samples therefore cannot increase the numerator or
denominator.

This module is research-only and cannot authorize order entry or a live probe.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.weather_portfolio_metric_binding_guard import audit_weather_portfolio_metric_binding

D = Decimal

EPISODE_BASIS_UNVERIFIED = "WEATHER_EXECUTION_EPISODE_BASIS_UNVERIFIED"
EPISODE_PROBABILITY_UNVERIFIED = "WEATHER_EXECUTION_EPISODE_PROBABILITY_UNVERIFIED"
UNIQUE_EPISODE_BASIS = "UNIQUE_OPEN_EPISODES_COMPLETE_WINDOW"


def _episode_probability(row: dict[str, Any]) -> tuple[Decimal | None, int | None, int | None]:
    successes = row.get("executable_probability_success_episode_count")
    total = row.get("executable_probability_unique_episode_count")
    if (
        not isinstance(successes, int)
        or isinstance(successes, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or total <= 0
        or successes < 0
        or successes > total
    ):
        return None, None, None
    return D(successes) / D(total), successes, total


def audit_weather_portfolio_metric_binding_v2(row: dict[str, Any]) -> dict[str, Any]:
    """Require independent unique-episode evidence for weather execution probability."""
    base = audit_weather_portfolio_metric_binding(row)
    blockers = list(base.get("blockers") or [])

    episode_probability, success_episodes, unique_episodes = _episode_probability(row)
    producer_probability = None
    try:
        if isinstance(row.get("executable_probability"), bool):
            raise ValueError
        producer_probability = D(str(row.get("executable_probability")))
        if not producer_probability.is_finite():
            producer_probability = None
    except Exception:
        producer_probability = None

    episode_basis_verified = (
        row.get("executable_probability_episode_basis_verified") is True
        and row.get("executable_probability_episode_basis") == UNIQUE_EPISODE_BASIS
        and row.get("executable_probability_resting_liquidity_deduplicated") is True
        and row.get("executable_probability_open_move_close_lifecycle_verified") is True
        and row.get("executable_probability_complete_window_verified") is True
        and episode_probability is not None
    )
    if not episode_basis_verified:
        blockers.append(EPISODE_BASIS_UNVERIFIED)

    episode_probability_verified = (
        episode_basis_verified
        and producer_probability is not None
        and D("0") <= producer_probability <= D("1")
        and producer_probability == episode_probability
    )
    if not episode_probability_verified:
        blockers.append(EPISODE_PROBABILITY_UNVERIFIED)

    verified = (
        base.get("weather_portfolio_metric_binding_verified") is True
        and episode_probability_verified
    )

    return {
        **base,
        "weather_portfolio_metric_binding_verified": verified,
        "weather_execution_episode_basis_verified": episode_basis_verified,
        "weather_execution_episode_probability_verified": episode_probability_verified,
        "verified_executable_probability": (
            None if not episode_probability_verified else str(episode_probability)
        ),
        "executable_probability_success_episode_count": success_episodes,
        "executable_probability_unique_episode_count": unique_episodes,
        "blockers": list(dict.fromkeys(blockers)),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
