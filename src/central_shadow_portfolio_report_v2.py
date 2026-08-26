"""Fail-closed wrapper for central shadow portfolio reporting.

Weather research rows may carry large model EV while settlement, fee, book, or model
certification is still incomplete. The legacy central report intentionally accepts
already-computed sleeve summaries and therefore cannot infer that distinction unless the
caller repeats every weather blocker. This wrapper makes the boundary explicit:
weather sleeves are promotable/annualizable only when the canonical weather certification
audit has affirmatively cleared, the selected-side schema has independently cleared, and
no hard blocker remains.

This module is read-only and cannot authorize order entry or live probes.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.central_shadow_portfolio_report import Sleeve, rank_sleeves as _rank_sleeves

WEATHER_CERTIFICATION_BLOCKER = "WEATHER_CERTIFICATION_PREREQS_UNVERIFIED"
WEATHER_SELECTED_SIDE_BLOCKER = "WEATHER_SELECTED_SIDE_SCHEMA_UNVERIFIED"
MALFORMED_HARD_BLOCKER_TELEMETRY = "MALFORMED_HARD_BLOCKER_TELEMETRY"


def _strict_weather_certified(row: dict[str, Any]) -> bool:
    return (
        row.get("executable_shadow_prereqs_verified") is True
        and row.get("selected_side_schema_verified") is True
    )


def _normalize_hard_blockers(value: Any) -> tuple[list[str], bool]:
    """Return normalized blockers and whether the telemetry itself was valid.

    Do not stringify malformed entries. Values such as ``None``, ``False`` or an
    empty string must not collapse into apparently clean blocker telemetry.
    """
    if not isinstance(value, (list, tuple, set)):
        return [MALFORMED_HARD_BLOCKER_TELEMETRY], False

    blockers: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            return [MALFORMED_HARD_BLOCKER_TELEMETRY], False
        blocker = raw.strip()
        if blocker not in blockers:
            blockers.append(blocker)
    return blockers, True


def _guard_weather_row(row: dict[str, Any]) -> dict[str, Any]:
    guarded = dict(row)
    if str(guarded.get("category") or "").strip().lower() != "weather":
        return guarded

    blockers, blocker_telemetry_valid = _normalize_hard_blockers(
        guarded.get("hard_blockers", ())
    )
    prereqs_verified = guarded.get("executable_shadow_prereqs_verified") is True
    selected_side_verified = guarded.get("selected_side_schema_verified") is True
    certified = prereqs_verified and selected_side_verified

    if not prereqs_verified and WEATHER_CERTIFICATION_BLOCKER not in blockers:
        blockers.append(WEATHER_CERTIFICATION_BLOCKER)
    if not selected_side_verified and WEATHER_SELECTED_SIDE_BLOCKER not in blockers:
        blockers.append(WEATHER_SELECTED_SIDE_BLOCKER)

    # Certification and blocker state are separate evidence surfaces. A producer can
    # be internally contradictory (for example certified=true while still carrying
    # TWC_SOURCE_ACCESS_REQUIRED). Such a row remains useful research evidence but must
    # never be annualized or promoted until the blocker list is actually empty.
    projection_verified = certified and blocker_telemetry_valid and not blockers

    guarded["hard_blockers"] = blockers
    guarded["weather_certification_verified"] = certified
    guarded["weather_selected_side_schema_verified"] = selected_side_verified
    guarded["weather_hard_blocker_telemetry_verified"] = blocker_telemetry_valid
    guarded["weather_projection_verified"] = projection_verified

    # Even a verified scanner window must not turn uncertified or still-blocked weather
    # model EV into a $/day capacity number. Preserve raw research P/L fields for
    # diagnostics, but fail closed at the projection boundary.
    if not projection_verified:
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
        "executable_shadow_prereqs_verified=true plus selected_side_schema_verified=true "
        "from the canonical weather certification path and an empty, structurally valid "
        "hard-blocker set."
    )
    return report
