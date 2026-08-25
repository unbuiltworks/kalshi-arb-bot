"""Read-only authoritative MLB outcome diagnostic for the quarantined Caleb Durbin exposure."""
from __future__ import annotations
import asyncio, json
import httpx

GAME_PK = 823828
PLAYER = "Caleb Durbin"

async def main() -> None:
    async with httpx.AsyncClient(timeout=5.0) as http:
        schedule, box = await asyncio.gather(
            http.get("https://statsapi.mlb.com/api/v1/schedule", params={"sportId": 1, "date": "2026-08-24"}),
            http.get(f"https://statsapi.mlb.com/api/v1/game/{GAME_PK}/boxscore"),
        )
        schedule.raise_for_status(); box.raise_for_status()
    body = schedule.json(); status = None
    for date in body.get("dates") or []:
        for game in date.get("games") or []:
            if int(game.get("gamePk") or 0) == GAME_PK:
                status = ((game.get("status") or {}).get("detailedState"))
    hits = None; at_bats = None; plate_appearances = None
    for team_side in ("away", "home"):
        players = (((box.json().get("teams") or {}).get(team_side) or {}).get("players") or {})
        for row in players.values():
            person = row.get("person") or {}
            if str(person.get("fullName") or "").strip().lower() != PLAYER.lower():
                continue
            batting = ((row.get("stats") or {}).get("batting") or {})
            hits = batting.get("hits"); at_bats = batting.get("atBats"); plate_appearances = batting.get("plateAppearances")
    print(json.dumps({"game_pk": GAME_PK, "game_status": status, "player": PLAYER,
                      "hits": hits, "at_bats": at_bats, "plate_appearances": plate_appearances,
                      "known_yes_1plus_hit": bool(isinstance(hits, int) and hits >= 1),
                      "read_only": True, "order_entry": False}, separators=(",", ":")), flush=True)

if __name__ == "__main__": asyncio.run(main())
