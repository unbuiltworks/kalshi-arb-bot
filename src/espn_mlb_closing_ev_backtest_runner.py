"""Research-only compatibility runner for the ESPN/DraftKings MLB close backtest.

Kalshi's hardened MLB normalizer emits canonical participant/team IDs in lower
case, while the shared sportsbook canonicalizer returns upper case. Normalize the
ESPN side to lower case before physical matching. No strategy parameters change.
"""
from __future__ import annotations

import src.espn_mlb_closing_ev_backtest as backtest

_original_canonical_team = backtest.canonical_team


def _canonical_team_lower(value):
    team = _original_canonical_team(value)
    return team.lower() if team else team


backtest.canonical_team = _canonical_team_lower


if __name__ == "__main__":
    backtest.main()
