"""Diagnostic runner for the isolated Kalshi MLB sharp-EV backtest.

The historical backtest module was branched from the generic three-venue MLB
normalizer. Production sports matching subsequently hardened Kalshi's MLB event
ticker timestamps as America/New_York local time before converting to UTC. Reuse
that guard here so historical physical-game matching has the same clock semantics
as production. No order entry is imported or enabled.
"""
from __future__ import annotations

import src.kalshi_mlb_sharp_ev_backtest as backtest
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern

# _kalshi_sides resolves this module global dynamically. Override only the
# normalizer dependency; all precommitted thresholds and accounting stay fixed.
backtest.normalize_kalshi_mlb = normalize_kalshi_mlb_eastern


if __name__ == "__main__":
    backtest.main()
