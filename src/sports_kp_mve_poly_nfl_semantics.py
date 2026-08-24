"""Strict Polymarket NFL proposition semantics for the MVE hedge shadow.

Fixes two fail-open/false-negative risks discovered from live Polymarket US data:
1) full-game alternate spread markets encode `Team wins by over X` as the SHORT
   team on the +/-X market, not the long team;
2) generic side descriptions such as `No` must never be parsed as NFL team codes.

This patch changes shadow matching only. It does not certify settlement rules or
make any cross-venue leg execution-eligible.
"""
from __future__ import annotations

from decimal import Decimal
import re
from typing import Any, Mapping

from src import sports_kp_mve_poly_pregame_shadow as poly
from src.sports_kp_mve_poly_nfl_aliases import team_code

D = Decimal
_PERIOD_MARKERS = ("-1q-", "-2q-", "-3q-", "-4q-", "-1h-", "-2h-")


def _side_team(side: Mapping[str, Any]) -> str | None:
    team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
    # Trust only explicit team metadata. Never side.description (`No`, `Over`,
    # `+20.50`, etc.) or generic identifiers as a team source.
    for value in (
        team.get("abbreviation"), team.get("displayAbbreviation"),
        team.get("name"), team.get("alias"), team.get("safeName"),
    ):
        code = team_code(value)
        if code:
            return code
    return None


def event_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    found: list[str] = []
    for participant in event.get("participants") or []:
        if not isinstance(participant, Mapping):
            continue
        team = participant.get("team") if isinstance(participant.get("team"), Mapping) else {}
        for value in (
            team.get("abbreviation"), team.get("displayAbbreviation"), team.get("name"),
            team.get("alias"), participant.get("name"),
        ):
            code = team_code(value)
            if code and code not in found:
                found.append(code); break
    for market in event.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        for side in market.get("marketSides") or []:
            if not isinstance(side, Mapping):
                continue
            code = _side_team(side)
            if code and code not in found:
                found.append(code)
        if len(found) >= 2:
            break
    unique = sorted(set(found))
    return tuple(unique) if len(unique) == 2 else None


def long_team(market: Mapping[str, Any], teams: tuple[str, str]) -> str | None:
    hits = sorted({
        code for side in (market.get("marketSides") or [])
        if isinstance(side, Mapping) and bool(side.get("long"))
        for code in [_side_team(side)] if code in teams
    })
    return hits[0] if len(hits) == 1 else None


def short_team(market: Mapping[str, Any], teams: tuple[str, str]) -> str | None:
    hits = sorted({
        code for side in (market.get("marketSides") or [])
        if isinstance(side, Mapping) and not bool(side.get("long"))
        for code in [_side_team(side)] if code in teams
    })
    return hits[0] if len(hits) == 1 else None


def poly_identity(event: Mapping[str, Any], market: Mapping[str, Any]):
    typ = poly._poly_type(market)
    if typ not in {"moneyline", "spread"}:
        return None
    slug = str(market.get("slug") or market.get("id") or "").lower()
    if not slug or market.get("active") is False or market.get("closed") is True or market.get("acceptingOrders") is False:
        return None
    teams = event_teams(event)
    start = poly._event_start(event, market)
    if teams is None or start is None:
        return None
    day = start.date().isoformat()

    if typ == "moneyline":
        # Full-game moneyline only. Period/derivative moneylines fail closed.
        if any(marker in slug for marker in _PERIOD_MARKERS):
            return None
        team = long_team(market, teams)
        if team is None:
            return None
        return ((day, teams, typ, team.lower(), "", "YES", None), slug)

    # Full-game alternate margin markets only. Live schema example:
    # title: `Pittsburgh Steelers wins by over 20.5 points`
    # long=true: NYJ +20.5; long=false: PIT -20.5; line=20.5.
    if not slug.startswith("asc-nfl-") or any(marker in slug for marker in _PERIOD_MARKERS):
        return None
    line = poly._d(market.get("line"))
    if line is None:
        return None
    threshold = abs(line)
    if threshold <= 0 or threshold * 2 != (threshold * 2).to_integral_value() or int(threshold * 2) % 2 != 1:
        return None
    proposition_team = short_team(market, teams)
    if proposition_team is None:
        return None

    title = str(market.get("title") or market.get("question") or "")
    m = re.search(r"\bwins?\s+by\s+over\s+([0-9]+(?:\.[0-9]+)?)\s+points?\b", title, re.I)
    if not m:
        return None
    try:
        if D(m.group(1)) != threshold:
            return None
    except Exception:
        return None
    # Require the explicit proposition team's full/alias label to occur in the
    # title. This prevents using an unrelated +/- spread that happens to share X.
    short_side = next((s for s in market.get("marketSides") or [] if isinstance(s, Mapping) and not bool(s.get("long")) and _side_team(s) == proposition_team), None)
    if not isinstance(short_side, Mapping):
        return None
    team_meta = short_side.get("team") if isinstance(short_side.get("team"), Mapping) else {}
    names = [str(team_meta.get(k) or "").strip() for k in ("name", "alias", "safeName")]
    if not any(name and name.lower() in title.lower() for name in names):
        return None
    return ((day, teams, "spread", proposition_team.lower(), "points", "GT", threshold), slug)


poly._event_teams = event_teams
poly._long_team = long_team
poly._poly_identity = poly_identity
