"""Fail-closed wrapper for central shadow portfolio reporting.

Weather research rows may carry large model EV while settlement, fee, book, model, or
capacity certification is still incomplete. The legacy central report intentionally
accepts already-computed sleeve summaries and therefore cannot infer that distinction
unless the caller repeats every weather blocker. This wrapper makes the boundary
explicit: weather sleeves are promotable/annualizable only when the canonical weather
certification audit is independently replayed and clears, the selected-side schema
independently clears, the portfolio ranking metrics are independently bound to the raw
selected-side economics, execution probability is derived from explicit deduplicated
canonical OPEN episode records that are time-bound to the audited pre-quote empirical
window, record-level execution success is independently proven from displayed depth,
and no hard blocker remains.

This module is read-only and cannot authorize order entry or live probes.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from src.central_shadow_portfolio_report import Sleeve, rank_sleeves as _rank_sleeves
from src.weather_certification_dimensions_v3 import audit_weather_certification_dimensions_v3
from src.weather_portfolio_metric_binding_guard_v6 import audit_weather_portfolio_metric_binding_v6

D = Decimal

WEATHER_CERTIFICATION_BLOCKER = "WEATHER_CERTIFICATION_PREREQS_UNVERIFIED"
WEATHER_SELECTED_SIDE_BLOCKER = "WEATHER_SELECTED_SIDE_SCHEMA_UNVERIFIED"
WEATHER_CANONICAL_AUDIT_REPLAY_BLOCKER = "WEATHER_CANONICAL_AUDIT_REPLAY_UNVERIFIED"
WEATHER_PORTFOLIO_METRIC_BINDING_BLOCKER = "WEATHER_PORTFOLIO_METRIC_BINDING_UNVERIFIED"
MALFORMED_HARD_BLOCKER_TELEMETRY = "MALFORMED_HARD_BLOCKER_TELEMETRY"


def _normalize_hard_blockers(value: Any) -> tuple[list[str], bool]:
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


def _replay_weather_audit(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    try:
        audit = audit_weather_certification_dimensions_v3(row)
    except Exception:
        return {}, False
    verified = (
        audit.get("executable_shadow_prereqs_verified") is True
        and audit.get("selected_side_schema_verified") is True
        and audit.get("read_only") is True
        and audit.get("order_entry") is False
        and audit.get("live_probe_ready") is False
    )
    return audit, verified


def _replay_weather_portfolio_metrics(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Bind ranking scalars to explicit depth-proven canonical OPEN evidence.

    Edge/depth are independently derived from the selected executable side. Execution
    probability must come from an explicit deduplicated population of unique OPEN
    episodes across a complete OPEN/MOVE/CLOSE observation window. V6 requires every
    counted episode ID to reconcile to a canonical read-only OPEN record for the exact
    market/side/quantity/price/min-edge cohort, binds each record's opening timestamp to
    the audited pre-quote empirical window, and independently derives each record's
    execution-success state from displayed consumable depth versus the scored quantity.
    Repeated resting/MOVE samples, invented episode labels, backfilled pre-window records,
    post-quote records, and producer-supplied success flags unsupported by depth therefore
    cannot increase empirical probability used by portfolio capacity reporting.
    """
    try:
        audit = audit_weather_portfolio_metric_binding_v6(row)
    except Exception:
        return {}, False
    verified = (
        audit.get("weather_portfolio_metric_binding_verified") is True
        and audit.get("weather_execution_episode_probability_verified") is True
        and audit.get("weather_execution_episode_identity_verified") is True
        and audit.get("weather_execution_episode_set_reconciled") is True
        and audit.get("weather_execution_episode_records_verified") is True
        and audit.get("weather_execution_episode_record_time_verified") is True
        and audit.get("weather_execution_episode_depth_verified") is True
        and audit.get("weather_execution_episode_success_depth_consistent") is True
        and audit.get("read_only") is True
        and audit.get("order_entry") is False
        and audit.get("live_probe_ready") is False
        and not audit.get("blockers")
    )
    return audit, verified


def _guard_weather_row(row: dict[str, Any]) -> dict[str, Any]:
    guarded = dict(row)
    if str(guarded.get("category") or "").strip().lower() != "weather":
        return guarded

    blockers, blocker_telemetry_valid = _normalize_hard_blockers(guarded.get("hard_blockers", ()))
    canonical_audit, canonical_verified = _replay_weather_audit(guarded)
    metric_audit, metric_verified = _replay_weather_portfolio_metrics(guarded)
    prereqs_verified = canonical_audit.get("executable_shadow_prereqs_verified") is True
    selected_side_verified = canonical_audit.get("selected_side_schema_verified") is True
    certified = prereqs_verified and selected_side_verified and canonical_verified

    if not canonical_verified and WEATHER_CANONICAL_AUDIT_REPLAY_BLOCKER not in blockers:
        blockers.append(WEATHER_CANONICAL_AUDIT_REPLAY_BLOCKER)
    if not prereqs_verified and WEATHER_CERTIFICATION_BLOCKER not in blockers:
        blockers.append(WEATHER_CERTIFICATION_BLOCKER)
    if not selected_side_verified and WEATHER_SELECTED_SIDE_BLOCKER not in blockers:
        blockers.append(WEATHER_SELECTED_SIDE_BLOCKER)
    if not metric_verified and WEATHER_PORTFOLIO_METRIC_BINDING_BLOCKER not in blockers:
        blockers.append(WEATHER_PORTFOLIO_METRIC_BINDING_BLOCKER)

    projection_verified = certified and metric_verified and blocker_telemetry_valid and not blockers

    guarded["hard_blockers"] = blockers
    guarded["weather_certification_verified"] = certified
    guarded["weather_selected_side_schema_verified"] = selected_side_verified
    guarded["weather_canonical_audit_replay_verified"] = canonical_verified
    guarded["weather_portfolio_metric_binding_verified"] = metric_verified
    guarded["weather_execution_episode_records_verified"] = (
        metric_audit.get("weather_execution_episode_records_verified") is True
    )
    guarded["weather_execution_episode_record_time_verified"] = (
        metric_audit.get("weather_execution_episode_record_time_verified") is True
    )
    guarded["weather_execution_episode_depth_verified"] = (
        metric_audit.get("weather_execution_episode_depth_verified") is True
    )
    guarded["weather_execution_episode_success_depth_consistent"] = (
        metric_audit.get("weather_execution_episode_success_depth_consistent") is True
    )
    guarded["weather_hard_blocker_telemetry_verified"] = blocker_telemetry_valid
    guarded["weather_projection_verified"] = projection_verified
    guarded["weather_canonical_audit"] = canonical_audit
    guarded["weather_portfolio_metric_audit"] = metric_audit

    if metric_verified:
        guarded["net_edge_per_dollar"] = metric_audit["derived_net_edge_per_dollar"]
        guarded["depth_dollars"] = metric_audit["derived_depth_dollars"]
        guarded["executable_probability"] = metric_audit["verified_executable_probability"]
    else:
        guarded["net_edge_per_dollar"] = D("0")
        guarded["depth_dollars"] = D("0")
        guarded["executable_probability"] = D("0")

    if not projection_verified:
        guarded["observation_window_verified"] = False

    return guarded


def rank_sleeves(rows: Iterable[dict[str, Any] | Sleeve]) -> dict[str, Any]:
    guarded_rows: list[dict[str, Any] | Sleeve] = []
    for row in rows:
        if isinstance(row, Sleeve):
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
        "Weather theoretical $/day and promotable score require an independent replay of "
        "the canonical v3 weather certification audit from raw evidence, the selected-side "
        "schema, and the v6 portfolio metric-binding audit. Edge/depth are re-derived from "
        "the selected executable side, and execution probability must reconcile to explicit "
        "deduplicated canonical OPEN episode records in a complete OPEN/MOVE/CLOSE window. "
        "Every counted OPEN record must be time-bound to that audited window and exist no "
        "later than the quote being ranked; record-level execution success is independently "
        "derived from displayed consumable depth versus the scored quantity. Repeated resting "
        "snapshots, MOVE observations, invented labels, pre-window records, post-quote lookahead, "
        "or producer success flags unsupported by depth cannot unlock projection. A structurally "
        "valid empty hard-blocker set is also mandatory."
    )
    return report
