"""Strict Polymarket NFL full-game total semantics for the MVE hedge shadow.

Adds only whole-game Over/Under totals. Quarter, half, team-total, and other
period derivatives fail closed. This module changes read-only shadow matching;
it does not certify settlement equivalence or enable cross-venue order entry.
"""
from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Mapping

from src import sports_kp_mve_poly_pregame_shadow as poly
from src import sports_kp_mve_poly_nfl_semantics as nfl

D = Decimal
_PREVIOUS_IDENTITY = nfl.poly_identity
_PERIOD_MARKERS = ("-1q-", "-2q-", "-3q-", "-4q-", "-1h-", "-2h-")


def _raw_type(market: Mapping[str, Any]) -> str:
    return str(
        market.get("sportsMarketTypeV2")
        or market.get("sportsMarketType")
        or market.get("marketType")
        or ""
    ).upper()


def _side_descriptions(market: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    long_desc: set[str] = set()
    short_desc: set[str] = set()
    for side in market.get("marketSides") or []:
        if not isinstance(side, Mapping) or side.get("tradable") is False:
            continue
        desc = str(side.get("description") or "").strip().lower()
        if not desc:
            continue
        (long_desc if bool(side.get("long")) else short_desc).add(desc)
    return long_desc, short_desc


def poly_identity(event: Mapping[str, Any], market: Mapping[str, Any]):
    raw_type = _raw_type(market)
    is_total = "TOTAL" in raw_type
    if not is_total:
        return _PREVIOUS_IDENTITY(event, market)

    slug = str(market.get("slug") or market.get("id") or "").lower()
    if (
        not slug
        or not slug.startswith("tsc-nfl-")
        or "-total-" not in slug
        or "-tt-" in slug
        or any(marker in slug for marker in _PERIOD_MARKERS)
        or market.get("active") is False
        or market.get("closed") is True
        or market.get("acceptingOrders") is False
    ):
        return None

    teams = nfl.event_teams(event)
    start = poly._event_start(event, market)
    line = poly._d(market.get("line"))
    if teams is None or start is None or line is None or line <= 0:
        return None

    # Whole-game totals must expose the canonical Over/Under two-sided market.
    long_desc, short_desc = _side_descriptions(market)
    if "over" not in long_desc or "under" not in short_desc:
        return None

    # Corroborate the line independently from visible contract wording and slug.
    title = str(market.get("title") or market.get("question") or "").strip()
    m = re.fullmatch(r"Over\s+([0-9]+(?:\.[0-9]+)?)\s+total\s+points", title, re.I)
    if m is None:
        return None
    try:
        if D(m.group(1)) != line:
            return None
    except Exception:
        return None

    slug_line = re.search(r"-total-(\d+)pt(\d+)(?:-|$)", slug, re.I)
    if slug_line is not None:
        try:
            encoded = D(slug_line.group(1)) + D(slug_line.group(2)) / (D(10) ** len(slug_line.group(2)))
        except Exception:
            return None
        if encoded != line:
            return None

    day = start.date().isoformat()
    return ((day, teams, "game_total", "contest", "points", "GT", line), slug)


poly._poly_identity = poly_identity
