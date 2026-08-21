"""Runtime normalization patch for strict Polymarket NFL pregame matching.

Keeps the v11 proposition matcher fail-closed while accepting the full-name and
common team labels returned by sports feeds.  No price, rule, or execution gate
is changed here.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from src import sports_kp_mve_poly_pregame_shadow as poly

ALIASES = {
    "ARIZONA CARDINALS":"ARI", "CARDINALS":"ARI", "ARIZONA":"ARI",
    "ATLANTA FALCONS":"ATL", "FALCONS":"ATL", "ATLANTA":"ATL",
    "BALTIMORE RAVENS":"BAL", "RAVENS":"BAL", "BALTIMORE":"BAL",
    "BUFFALO BILLS":"BUF", "BILLS":"BUF", "BUFFALO":"BUF",
    "CAROLINA PANTHERS":"CAR", "PANTHERS":"CAR", "CAROLINA":"CAR",
    "CHICAGO BEARS":"CHI", "BEARS":"CHI", "CHICAGO":"CHI",
    "CINCINNATI BENGALS":"CIN", "BENGALS":"CIN", "CINCINNATI":"CIN",
    "CLEVELAND BROWNS":"CLE", "BROWNS":"CLE", "CLEVELAND":"CLE",
    "DALLAS COWBOYS":"DAL", "COWBOYS":"DAL", "DALLAS":"DAL",
    "DENVER BRONCOS":"DEN", "BRONCOS":"DEN", "DENVER":"DEN",
    "DETROIT LIONS":"DET", "LIONS":"DET", "DETROIT":"DET",
    "GREEN BAY PACKERS":"GB", "PACKERS":"GB", "GREEN BAY":"GB",
    "HOUSTON TEXANS":"HOU", "TEXANS":"HOU", "HOUSTON":"HOU",
    "INDIANAPOLIS COLTS":"IND", "COLTS":"IND", "INDIANAPOLIS":"IND",
    "JACKSONVILLE JAGUARS":"JAC", "JAGUARS":"JAC", "JACKSONVILLE":"JAC",
    "KANSAS CITY CHIEFS":"KC", "CHIEFS":"KC", "KANSAS CITY":"KC",
    "LAS VEGAS RAIDERS":"LV", "RAIDERS":"LV", "LAS VEGAS":"LV",
    "LOS ANGELES CHARGERS":"LAC", "LA CHARGERS":"LAC", "CHARGERS":"LAC",
    "LOS ANGELES RAMS":"LAR", "LA RAMS":"LAR", "RAMS":"LAR",
    "MIAMI DOLPHINS":"MIA", "DOLPHINS":"MIA", "MIAMI":"MIA",
    "MINNESOTA VIKINGS":"MIN", "VIKINGS":"MIN", "MINNESOTA":"MIN",
    "NEW ENGLAND PATRIOTS":"NE", "PATRIOTS":"NE", "NEW ENGLAND":"NE",
    "NEW ORLEANS SAINTS":"NO", "SAINTS":"NO", "NEW ORLEANS":"NO",
    "NEW YORK GIANTS":"NYG", "NY GIANTS":"NYG", "GIANTS":"NYG",
    "NEW YORK JETS":"NYJ", "NY JETS":"NYJ", "JETS":"NYJ",
    "PHILADELPHIA EAGLES":"PHI", "EAGLES":"PHI", "PHILADELPHIA":"PHI",
    "PITTSBURGH STEELERS":"PIT", "STEELERS":"PIT", "PITTSBURGH":"PIT",
    "SAN FRANCISCO 49ERS":"SF", "49ERS":"SF", "SAN FRANCISCO":"SF",
    "SEATTLE SEAHAWKS":"SEA", "SEAHAWKS":"SEA", "SEATTLE":"SEA",
    "TAMPA BAY BUCCANEERS":"TB", "BUCCANEERS":"TB", "BUCS":"TB", "TAMPA BAY":"TB",
    "TENNESSEE TITANS":"TEN", "TITANS":"TEN", "TENNESSEE":"TEN",
    "WASHINGTON COMMANDERS":"WSH", "COMMANDERS":"WSH", "WASHINGTON":"WSH", "WAS":"WSH",
}


def team_code(value: Any) -> str | None:
    raw = str(value or "").upper().strip()
    raw = re.sub(r"[^A-Z0-9]+", " ", raw).strip()
    direct = {"WAS":"WSH"}.get(raw, raw)
    if direct in poly.NFL_TEAMS:
        return direct
    return ALIASES.get(raw)


def market_type(market: Mapping[str, Any]) -> str:
    values = (
        market.get("sportsMarketTypeV2"), market.get("sportsMarketType"),
        market.get("marketType"), market.get("type"),
    )
    text = " ".join(str(x or "") for x in values).upper()
    text = text.replace("SPORTS_MARKET_TYPE_", "").replace("-", "_").replace(" ", "_")
    if "SPREAD" in text or "HANDICAP" in text:
        return "spread"
    if "MONEYLINE" in text or "MONEY_LINE" in text or "GAME_WINNER" in text or text.endswith("_WINNER"):
        return "moneyline"
    return ""


# The v11 matcher resolves these names dynamically at call time, so patching the
# module globals preserves all of its exact key/ambiguity checks.
poly._team_code = team_code
poly._poly_type = market_type
