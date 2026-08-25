"""Fail-closed wrapper for central shadow portfolio reporting.

Weather research rows may carry large model EV while settlement, fee, book, or model
certification is still incomplete. The legacy central report intentionally accepts
already-computed sleeve summaries and therefore cannot infer that distinction unless the
caller repeats every weather blocker. This wrapper makes the boundary explicit:
weather sleeves are promotable/annualizable only when the canonical weather certification
audit has affirmatively cleared.

This module is read-only and cannot authorize order entry or live probes.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.central_shadow_portfolio_report import Sleeve, rank_sleeves as _rank_sleeves

WEATHER_CERTIFICATION_BLOCKER = "WEATHER_CERTIFICATION_PREREQS_UNVERIFIED"


def _strict_weather_certified(row: dict[str, Any]) -> bool:
    return row.get("executable_shadow_prereqs_verified") is True


def _guard_weather_row(row: dict[str, Any]) -> dict[str, Any]:
    guarded = dict(row)
    if str(guarded.get("category") or "").strip().lower() != "weather":
        return guarded

    blockers_raw = guarded.get("hard_blockers", ())
    if isinstance(blockers_raw, (list, tuple, set)):
        blockers = [str(value) for value in blockers_raw]
    else:
        blockers = ["MALFORMED_HARD_BLOCKER_TELEMETRY"]

    certified = _strict_weather_certified(guarded)
    if not certified and WEATHER_CERTIFICATION_BLOCKER not in blockers:
        blockers.append(WEATHER_CERTIFICATION_BLOCKER)

    guarded["hard_blockers"] = blockers
    guarded["weather_certification_verified"] = certified

    # Even a verified scanner window must not turn uncertified weather model EV into a
    # $/day capacity number. Preserve the underlying research P/L fields for diagnostics,
    # but fail closed at the projection boundary.
    if not certified:
        guarded["observation_window_verified"] = False

    return guarded


def rank_sleeves(rows: Iterable[dict[str, Any] | Sleeve]) -> dict[str, Any]:
    guarded_rows: list[dict[str, Any] | Sleeve] = []
    for row in rows:
        if isinstance(row, Sleeve):
            # Existing Sleeve objects predate weather certification metadata. Treat a
            # weather Sleeve as uncertified rather than silently trusting it.
            if row.category.strip().lower() == "weather":
                mapped = {
                    "name": row.name,
                    "category": row.category,
                    "venues": list(row.venues),
                    "net_edge_per_dollar": row.net_edge_per_dollar,
                    "executable_probability": row.executable_probability,
                    "depth_dollars": row.depth_dollars,
                    "capital_lock_hours": row.capital_lock_hours,
                    "settlement_confidence": row.settlement_confidence,
                    "basis_risk": row.basis_risk,
                    "hard_blockers": list(row.hard_blockers),
                    "observed_episodes": row.observed_episodes,
                    "observed_hours": row.observed_hours,
                    "shadow_profit_dollars": row.shadow_profit_dollars,
                    "observation_window_verified": row.observation_window_verified,
                    "settled_paper_trade_groups": row.settled_paper_trade_groups,
                    "realized_paper_profit_dollars": row.realized_paper_profit_dollars,
                    "settlement_window_verified": row.settlement_window_verified,
                }
                guarded_rows.append(_guard_weather_row(mapped))
            else:
                guarded_rows.append(row)
        else:
            guarded_rows.append(_guard_weather_row(row))

    report = _rank_sleeves(guarded_rows)
    report["weather_projection_policy"] = (
        "Weather theoretical $/day and promotable score require literal "
        "executable_shadow_prereqs_verified=true from the canonical weather certification audit."
    )
    return report
