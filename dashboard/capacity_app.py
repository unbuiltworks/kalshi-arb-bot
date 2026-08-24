"""ARB Command Center capacity funnel layered on the existing lane dashboard.

The existing lane_app owns ingestion, realized P&L semantics, and the HTML UI. This
module reuses that exact app and adds a read-only `/api/capacity-funnel` endpoint so
capacity telemetry can be measured without redefining realized profit.

Stages are deliberately distinct:
1. shadow_capacity: read-only capacity observations; latest/peak estimates only.
2. executable: deduplicated LIVE/LIVE_PROBE executable-opportunity events.
3. executed_locked: executable opportunities whose trade later has all required legs filled.
4. realized: authoritative TRADE_REALIZED net P&L only.

Shadow capacity is NOT summed as if it were realized daily profit. Repeated scans of
the same capacity key are deduplicated, and the endpoint labels the resulting number
as observed peak capacity rather than executable or realized dollars.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Query

import app as core
import lane_app as lane

app = lane.app


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    return numerator / denominator * Decimal("100") if denominator else Decimal("0")


@app.get("/api/capacity-funnel")
def capacity_funnel(hours: int = Query(default=24, ge=1, le=168)) -> dict[str, Any]:
    # `capacity_key` should identify the same economic route across repeated scans.
    # Fall back to trade_id for older producers. We keep only each key's peak observed
    # locked profit inside the requested window so heartbeat frequency cannot inflate it.
    sql = f"""
    WITH window_events AS (
      SELECT *
      FROM trade_events
      WHERE event_ts >= NOW() - (%s * INTERVAL '1 hour')
    ),
    capacity_by_key AS (
      SELECT
        COALESCE(NULLIF(metadata->>'capacity_key',''), trade_id) AS capacity_key,
        MAX(COALESCE(opportunity_net_profit,0)) AS peak_profit,
        MAX(COALESCE(quantity,0)) AS peak_quantity,
        MAX(event_ts) AS last_seen
      FROM window_events
      WHERE event_type='CAPACITY_OBSERVATION'
        AND mode IN ('SHADOW','PAPER','LIVE_PROBE','LIVE')
        AND status IN ('ECONOMIC_CAPACITY','SHADOW_CAPACITY')
      GROUP BY COALESCE(NULLIF(metadata->>'capacity_key',''), trade_id)
    ),
    executable_by_trade AS (
      SELECT
        trade_id,
        MAX(COALESCE(opportunity_net_profit,0)) AS locked_profit,
        MAX(COALESCE(quantity,0)) AS quantity,
        MAX(event_ts) AS last_seen
      FROM window_events
      WHERE event_type='EXECUTABLE_OPPORTUNITY'
        AND mode IN ('LIVE','LIVE_PROBE')
        AND status='EXECUTABLE'
      GROUP BY trade_id
    ),
    rolled AS ({core.TRADE_ROLLUP_SQL}),
    fully_filled AS (
      SELECT trade_id
      FROM rolled
      WHERE mode IN ('LIVE','LIVE_PROBE')
        AND COALESCE(legs_required,0) > 0
        AND COALESCE(legs_filled,0) >= COALESCE(legs_required,0)
        AND COALESCE(first_fill_at, latest_event_ts) >= NOW() - (%s * INTERVAL '1 hour')
    ),
    executed_locked AS (
      SELECT e.*
      FROM executable_by_trade e
      JOIN fully_filled f USING (trade_id)
    ),
    realized AS (
      SELECT *
      FROM rolled
      WHERE mode IN ('LIVE','LIVE_PROBE')
        AND realized
        AND realized_at >= NOW() - (%s * INTERVAL '1 hour')
    )
    SELECT
      COALESCE((SELECT SUM(peak_profit) FROM capacity_by_key),0) AS shadow_peak_profit,
      COALESCE((SELECT COUNT(*) FROM capacity_by_key),0) AS shadow_capacity_keys,
      COALESCE((SELECT MAX(peak_profit) FROM capacity_by_key),0) AS largest_shadow_peak_profit,
      COALESCE((SELECT SUM(locked_profit) FROM executable_by_trade),0) AS executable_profit,
      COALESCE((SELECT COUNT(*) FROM executable_by_trade),0) AS executable_trades,
      COALESCE((SELECT SUM(locked_profit) FROM executed_locked),0) AS executed_locked_profit,
      COALESCE((SELECT COUNT(*) FROM executed_locked),0) AS executed_locked_trades,
      COALESCE((SELECT SUM(net_pnl) FROM realized),0) AS realized_net_pnl,
      COALESCE((SELECT COUNT(*) FROM realized),0) AS realized_trades,
      COALESCE((SELECT SUM(capital_filled) FROM realized),0) AS realized_capital,
      COALESCE((SELECT SUM(fees) FROM realized),0) AS realized_fees
    """
    with core.db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (hours, hours, hours))
            row = dict(cur.fetchone())

    target = Decimal(core.DAILY_TARGET)
    shadow = Decimal(str(row.get("shadow_peak_profit") or 0))
    executable = Decimal(str(row.get("executable_profit") or 0))
    executed = Decimal(str(row.get("executed_locked_profit") or 0))
    realized = Decimal(str(row.get("realized_net_pnl") or 0))
    realized_capital = Decimal(str(row.get("realized_capital") or 0))

    result = {
        "window_hours": hours,
        "daily_target": target,
        "stages": {
            "shadow_capacity": {
                "observed_peak_profit": shadow,
                "unique_capacity_keys": row.get("shadow_capacity_keys", 0),
                "largest_single_peak_profit": row.get("largest_shadow_peak_profit", 0),
                "target_coverage_pct": _pct(shadow, target),
                "classification": "READ_ONLY_NON_ADDITIVE_CAPACITY_ESTIMATE",
            },
            "executable": {
                "deduped_locked_profit": executable,
                "trades": row.get("executable_trades", 0),
                "target_coverage_pct": _pct(executable, target),
                "classification": "EXECUTABLE_NOT_REALIZED",
            },
            "executed_locked": {
                "locked_profit": executed,
                "trades": row.get("executed_locked_trades", 0),
                "capture_pct_of_executable": _pct(executed, executable),
                "classification": "FULLY_FILLED_NOT_NECESSARILY_SETTLED",
            },
            "realized": {
                "net_pnl": realized,
                "trades": row.get("realized_trades", 0),
                "capital": realized_capital,
                "fees": row.get("realized_fees", 0),
                "roi_on_realized_capital_pct": _pct(realized, realized_capital),
                "target_progress_pct": _pct(realized, target),
                "remaining_to_target": max(target - realized, Decimal("0")),
                "classification": "AUTHORITATIVE_SETTLED_REALIZED",
            },
        },
        "warnings": [
            "Shadow capacity is deduplicated peak observed capacity, not daily executable profit and not realized P&L.",
            "Executable opportunity is deduplicated by trade_id; repeated scanner heartbeats do not count repeatedly.",
            "Executed locked requires all recorded legs filled but remains separate from settlement realization.",
            "Only TRADE_REALIZED rows contribute to realized net P&L.",
        ],
    }
    return core.jsonable(result)
