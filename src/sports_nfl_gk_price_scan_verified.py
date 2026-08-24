"""Verified join wrapper for the strict NFL G/K price scan.

SportsEventIdentity canonicalizes participants to lowercase. The raw Kalshi parser
intentionally keeps exchange ticker codes uppercase until identity construction.
Normalize only the pre-identity lookup key here; all market semantics remain in the
strict scanner and order entry remains impossible.

The exported ``run_once`` uses the request-efficient scanner: Gemini embedded books
are priced before Kalshi depth is requested, so exact counterpart keys with no
usable Gemini quote do not consume Kalshi orderbook capacity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

import src.sports_nfl_gk_price_scan as core
from src.mlb_game_side_normalization import NormalizedSide

ET = ZoneInfo("America/New_York")


def _case_aligned_gemini_match_index(
    rows: Iterable[NormalizedSide],
) -> dict[tuple[tuple[str, str], object, str], set[int]]:
    out: dict[tuple[tuple[str, str], object, str], set[int]] = defaultdict(set)
    for row in rows:
        ident = row.identity
        local_date = datetime.fromtimestamp(
            ident.event.scheduled_start_ms / 1000, tz=timezone.utc
        ).astimezone(ET).date()
        participants = tuple(str(team).upper() for team in ident.event.participants)
        out[(participants, local_date, ident.market_family)].add(
            ident.event.scheduled_start_ms
        )
    return out


core._gemini_match_index = _case_aligned_gemini_match_index

# Import only after installing the verified matcher patch.  The efficient scanner
# references ``core._gemini_match_index`` dynamically and otherwise leaves all
# strict identity/economic logic untouched.
from src.sports_nfl_gk_price_scan_efficient import run_once  # noqa: E402
