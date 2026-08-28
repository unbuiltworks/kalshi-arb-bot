"""Public MLB game-state participation proofs for strict player-prop research.

This module is deliberately read-only. It uses MLB's public Stats API game schedule
and boxscore data to answer only the participation question required by the audited
Polymarket US <-> Kalshi player-prop rule guard:

* batter families: player was a starter and has recorded >= 1 plate appearance
* pitcher strikeouts: player was the starting pitcher and has recorded >= 1 pitch

It does not price markets, authenticate to a venue, or submit orders.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import re
from typing import Any, Iterable

import httpx

from src.mlb_prop_rule_equivalence import ParticipationProof

MLB_STATS = "https://statsapi.mlb.com/api"

# Canonical market-team codes used by current Polymarket/Kalshi MLB slugs/tickers.
TEAM_NAME_TO_CODE = {
    "Arizona Diamondbacks": "az",
    "Atlanta Braves": "atl",
    "Athletics": "ath",
    "Oakland Athletics": "ath",
    "Baltimore Orioles": "bal",
    "Boston Red Sox": "bos",
    "Chicago Cubs": "chc",
    "Chicago White Sox": "cws",
    "Cincinnati Reds": "cin",
    "Cleveland Guardians": "cle",
    "Colorado Rockies": "col",
    "Detroit Tigers": "det",
    "Houston Astros": "hou",
    "Kansas City Royals": "kc",
    "Los Angeles Angels": "laa",
    "Los Angeles Dodgers": "lad",
    "Miami Marlins": "mia",
    "Milwaukee Brewers": "mil",
    "Minnesota Twins": "min",
    "New York Mets": "nym",
    "New York Yankees": "nyy",
    "Philadelphia Phillies": "phi",
    "Pittsburgh Pirates": "pit",
    "San Diego Padres": "sd",
    "San Francisco Giants": "sf",
    "Seattle Mariners": "sea",
    "St. Louis Cardinals": "stl",
    "Tampa Bay Rays": "tb",
    "Texas Rangers": "tex",
    "Toronto Blue Jays": "tor",
    "Washington Nationals": "wsh",
}
TEAM_CODE_ALIASES = {
    "ari": "az",
    "oak": "ath",
    "was": "wsh",
    "chw": "cws",
    "kcr": "kc",
    "sdp": "sd",
    "sfg": "sf",
    "tbr": "tb",
}
SLUG_GAME_RE = re.compile(
    r"(?:^|-)mlb-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})(?:-|$)",
    re.I,
)


def _norm_team(value: Any) -> str:
    code = str(value or "").strip().lower()
    return TEAM_CODE_ALIASES.get(code, code)


def _norm_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower().replace("’", "'"))


def candidate_game(poly_slug: str) -> tuple[str, tuple[str, str]] | None:
    match = SLUG_GAME_RE.search(poly_slug)
    if match is None:
        return None
    teams = tuple(sorted((_norm_team(match.group("a")), _norm_team(match.group("b")))))
    return match.group("date"), teams


def _team_code(team: dict[str, Any]) -> str | None:
    name = str(team.get("name") or "")
    if name in TEAM_NAME_TO_CODE:
        return TEAM_NAME_TO_CODE[name]
    abbreviation = _norm_team(team.get("abbreviation"))
    return abbreviation or None


async def _json(http: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    response = await http.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


async def fetch_game_boxscores(
    http: httpx.AsyncClient,
    dates: Iterable[str],
    *,
    concurrency: int = 6,
) -> dict[tuple[str, tuple[str, str]], dict[str, Any]]:
    """Return MLB boxscores keyed by (date, sorted market team codes)."""
    schedule_rows: list[tuple[str, int, tuple[str, str], str]] = []
    for game_date in sorted(set(dates)):
        body = await _json(
            http,
            f"{MLB_STATS}/v1/schedule",
            params={"sportId": 1, "date": game_date, "hydrate": "team"},
        )
        for date_row in body.get("dates") or []:
            for game in date_row.get("games") or []:
                away = _team_code(((game.get("teams") or {}).get("away") or {}).get("team") or {})
                home = _team_code(((game.get("teams") or {}).get("home") or {}).get("team") or {})
                game_pk = game.get("gamePk")
                if not away or not home or not game_pk:
                    continue
                status = str(((game.get("status") or {}).get("abstractGameState")) or "")
                schedule_rows.append((game_date, int(game_pk), tuple(sorted((away, home))), status))

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(row: tuple[str, int, tuple[str, str], str]):
        game_date, game_pk, teams, status = row
        # Pregame boxscores do not prove either required participation gate.
        if status.lower() not in {"live", "final"}:
            return None
        async with semaphore:
            try:
                box = await _json(http, f"{MLB_STATS}/v1/game/{game_pk}/boxscore")
            except (httpx.HTTPError, ValueError):
                return None
        return (game_date, teams), {"gamePk": game_pk, "status": status, "boxscore": box}

    fetched = await asyncio.gather(*(one(row) for row in schedule_rows))
    return {key: value for item in fetched if item is not None for key, value in [item]}


def participation_proof(
    *,
    family: str,
    player_name: str,
    poly_slug: str,
    games: dict[tuple[str, tuple[str, str]], dict[str, Any]],
) -> ParticipationProof:
    game_key = candidate_game(poly_slug)
    if game_key is None:
        return ParticipationProof()
    game = games.get(game_key)
    if game is None:
        return ParticipationProof()

    target = _norm_name(player_name)
    box = game.get("boxscore") or {}
    teams = box.get("teams") or {}
    for side in ("away", "home"):
        team = teams.get(side) or {}
        players = team.get("players") or {}
        pitchers = [int(x) for x in (team.get("pitchers") or []) if str(x).isdigit()]
        for row in players.values():
            person = row.get("person") or {}
            if _norm_name(person.get("fullName")) != target:
                continue
            stats = row.get("stats") or {}
            game_status = row.get("gameStatus") or {}
            source = f"MLB_STATS_API_GAME_{game.get('gamePk')}"

            if family == "baseball_player_strikeouts":
                player_id = person.get("id")
                starting_pitcher = bool(pitchers and player_id is not None and int(player_id) == pitchers[0])
                pitching = stats.get("pitching") or {}
                pitches = pitching.get("pitchesThrown")
                if pitches is None:
                    pitches = pitching.get("numberOfPitches")
                try:
                    pitch_recorded = int(pitches or 0) >= 1
                except (TypeError, ValueError):
                    pitch_recorded = False
                return ParticipationProof(
                    starting_pitcher=starting_pitcher,
                    pitch_recorded=pitch_recorded,
                    source=source if (starting_pitcher or pitch_recorded) else "",
                )

            batting = stats.get("batting") or {}
            try:
                plate_appearances = int(batting.get("plateAppearances") or 0)
            except (TypeError, ValueError):
                plate_appearances = 0
            batting_order = str(row.get("battingOrder") or "").strip()
            # MLB boxscore explicitly flags substitutes. A batting-order assignment
            # plus isSubstitute=False is the fail-closed starter proof we need.
            started = bool(batting_order) and not bool(game_status.get("isSubstitute", False))
            return ParticipationProof(
                started=started,
                plate_appearance_recorded=plate_appearances >= 1,
                source=source if (started or plate_appearances >= 1) else "",
            )

    return ParticipationProof()


def proof_dict(proof: ParticipationProof) -> dict[str, Any]:
    return asdict(proof)
