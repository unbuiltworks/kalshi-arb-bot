"""One-shot read-only Polymarket US pregame NFL schema diagnostic."""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping

from src.sports_kp_mve_poly_pregame_shadow import _pregame_events, _poly_type, _event_teams, _long_team
from src.sports_kp_mve_poly_nfl_aliases import ALIASES


def _looks_nfl(event: Mapping[str, Any]) -> bool:
    fields = [event.get("seriesSlug"), event.get("subcategory"), event.get("league"), event.get("title"), event.get("slug")]
    text = " ".join(str(x or "") for x in fields).upper()
    if "NFL" in text or "FOOTBALL" in text:
        return True
    padded = " " + re.sub(r"[^A-Z0-9]+", " ", text) + " "
    return any((" " + name + " ") in padded for name in ALIASES if len(name) > 4)


async def run_diagnostic(horizon_hours: int = 24) -> dict[str, Any]:
    events = await _pregame_events(horizon_hours)
    likely = [e for e in events if _looks_nfl(e)]
    series = Counter(str(e.get("seriesSlug") or e.get("subcategory") or e.get("league") or "") for e in likely)
    market_types: Counter[str] = Counter()
    parsed_team_events = 0
    parsed_markets = 0
    samples: list[dict[str, Any]] = []
    for event in likely:
        teams = _event_teams(event)
        if teams:
            parsed_team_events += 1
        m_samples = []
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            typ = _poly_type(market)
            raw_typ = str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or market.get("marketType") or market.get("type") or "")
            market_types[raw_typ] += 1
            lt = _long_team(market, teams) if teams else None
            if typ and teams and lt:
                parsed_markets += 1
            if len(m_samples) < 5:
                m_samples.append({
                    "slug": market.get("slug"),
                    "title": market.get("title") or market.get("question"),
                    "raw_type": raw_typ,
                    "normalized_type": typ,
                    "line": market.get("line"),
                    "teams": teams,
                    "long_team": lt,
                    "marketSides": market.get("marketSides"),
                })
        if len(samples) < 8:
            samples.append({
                "title": event.get("title"), "slug": event.get("slug"),
                "seriesSlug": event.get("seriesSlug"), "subcategory": event.get("subcategory"),
                "startTime": event.get("startTime") or event.get("startDate"),
                "participants": event.get("participants"),
                "teams": teams, "markets": m_samples,
            })
    return {
        "status": "POLY_PREGAME_NFL_DIAGNOSTIC",
        "events_scanned": len(events),
        "likely_nfl_events": len(likely),
        "parsed_team_events": parsed_team_events,
        "parsed_moneyline_spread_markets": parsed_markets,
        "likely_nfl_series": dict(series.most_common(20)),
        "market_type_counts": dict(market_types.most_common(30)),
        "samples": samples,
        "read_only": True,
        "order_entry": False,
    }
