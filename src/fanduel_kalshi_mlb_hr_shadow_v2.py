"""Safer FanDuel vs Kalshi MLB HR shadow wrapper using ticker-decoded teams.

Kalshi HR event titles can use shortened city labels (for example ``Cincinnati``)
that are not guaranteed to exist in the general MLB name alias table.  The Kalshi
event ticker already encodes the two canonical team abbreviations and scheduled
clock, so this wrapper replaces only the HR market identity helper with the same
fail-closed ticker decoder used by the direct Gemini/Kalshi prop scanner.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import Any, Mapping

from src import fanduel_kalshi_mlb_hr_shadow as base
from src.gemini_kalshi_mlb_prop_shadow import _kalshi_teams
from src.strict_mlb_prop_schedule_guard import kalshi_event_start_utc

D = Decimal


def _kalshi_market_identity(event: Mapping[str, Any], market: Mapping[str, Any]) -> dict[str, Any] | None:
    teams = _kalshi_teams(event)
    start = kalshi_event_start_utc(event, market)
    match = base.HR_TITLE_RE.fullmatch(str(market.get("title") or "").strip())
    ticker = str(market.get("ticker") or "")
    if teams is None or start is None or match is None or not ticker:
        return None
    return {
        "teams": teams,
        "start_ms": int(start.timestamp() * 1000),
        "player": match.group("player").strip(),
        "player_key": base._name(match.group("player")),
        "threshold": D(match.group("threshold")),
        "ticker": ticker,
    }


# Patch only the identity helper used by base.run_once; all pricing, fee, depth,
# FanDuel normalization, and fail-closed economics remain unchanged.
base._kalshi_market_identity = _kalshi_market_identity

_fanduel_legs = base._fanduel_legs
evaluate_pair = base.evaluate_pair
run_once = base.run_once


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), separators=(",", ":"), default=str), flush=True)
