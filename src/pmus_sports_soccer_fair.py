"""THREE-WAY soccer fair value for Polymarket US "full time winner" markets
(28 Nov). PURE: no network, no orders, not wired into execution. The only
runtime surface is `soccer_shadow_rows`, which returns [] unless
PMUS_SHARP_SOCCER=1 (default OFF) and is not called by any trading loop.

VENUE STRUCTURE (public catalogue, event lal-bet-ray-2026-11-28, 28 Nov):
  one YES/NO market per outcome, all with sportsMarketType
  "soccer_team_full_time_winner" / V2 "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME":
    atc-lal-bet-ray-2026-11-28-bet   "Will Real Betis Balompie win against
                                      Rayo Vallecano de Madrid ...?"
    atc-lal-bet-ray-2026-11-28-ray   (the other team)
    atc-lal-bet-ray-2026-11-28-draw  "... end in a draw?"
  A team market names the SAME team on both its YES (long=true) and NO
  (long=false) sides, so `pmus_sports_sharp.market_teams` cannot read the
  opponent from it: the two teams come from the event's two team markets
  (or are passed in). The draw market has no team on either side. The
  team "ordering" (home/away) flag is NOT reliable (both team markets say
  "home" on YES) and the event `teams` list carried an unrelated team, so
  neither is used.

CONTRACT TEXT (identical on all three markets):
  "This market will settle to the winner at the end of 90 minutes plus
  stoppage time ... If the match is tied following 90 minutes plus stoppage
  time, the market will settle to Tie."
  That is regulation-time settlement, which is what sportsbook soccer h2h
  (1X2: home / Draw / away) settles on, so P(YES team market) = P(team wins
  in regulation) and P(YES draw market) = P(draw). Any market whose text
  does not carry the 90-minutes-plus-stoppage phrase, or that mentions
  extra time or penalties (a cup "to qualify / advance" contract), is
  REFUSED: a 1X2 price is not its fair value.
  OPEN: on a team market "settle to Tie" is read as NO (YES = that team wins);
  the venue text does not spell that out for a YES/NO instrument.

Odds API soccer keys are from provider docs and UNVERIFIED in production.
"""
from __future__ import annotations

import os
import re
from decimal import Decimal as D
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, \
    Tuple

from src import pmus_sports_sharp as SH

#: venue league code -> The Odds API soccer key (h2h is 3-way). SEPARATE from
#: SH.SPORT_KEYS on purpose: callers iterate SPORT_KEYS for 2-way sports.
#: UNVERIFIED in production. UEFA Champions League (soccer_uefa_champs_league)
#: is not mapped: its venue league code has not been observed.
SOCCER_KEYS: Dict[str, str] = {
    "epl": "soccer_epl",
    "lal": "soccer_spain_la_liga",
    "bun": "soccer_germany_bundesliga",
    "sea": "soccer_italy_serie_a",
    "lg1": "soccer_france_ligue_one",
    "mls": "soccer_usa_mls",
    "eflch": "soccer_efl_champ",
    "ere": "soccer_netherlands_eredivisie",
    "ligpor": "soccer_portugal_primeira_liga",
    "lmx": "soccer_mexico_ligamx",
}

SOCCER_TYPE = "soccer_team_full_time_winner"
SOCCER_TYPE_V2 = "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"
DRAW = "draw"
_SLUG = re.compile(
    r"^atc-([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})-([a-z0-9]+)$")
_REG = re.compile(r"90 minutes plus stoppage time", re.I)
_NOT_REG = re.compile(r"extra[- ]time|penalt|overtime|shoot-?out", re.I)


def enabled() -> bool:
    return os.getenv("PMUS_SHARP_SOCCER", "0").strip() == "1"


# ------------------------------------------------------------ fair value
def h2h3(book: Mapping[str, Any], *, now: float
         ) -> Optional[Tuple[Dict[str, D], float]]:
    """({norm(home), norm(away), "draw": prob}, last_update) from ONE book's
    h2h, or None. Exactly 3 distinct non-empty names, one of them "draw";
    every price finite and > 1; last_update present and within
    MAX_ODDS_AGE_S of `now` (not in the future)."""
    try:
        mks = [mk for mk in book.get("markets") or []
               if isinstance(mk, Mapping) and mk.get("key") == "h2h"]
        if len(mks) != 1:
            return None
        mk = mks[0]
        outs = [o for o in mk.get("outcomes") or [] if isinstance(o, Mapping)]
        if len(outs) != 3:
            return None
        names = [SH.norm(o.get("name")) for o in outs]
        if not all(names) or len(set(names)) != 3 or \
                names.count(DRAW) != 1:
            return None
        prices = [o.get("price") for o in outs]
        if any(isinstance(p, bool) or p is None for p in prices):
            return None
        probs = SH.devig(prices)
        if probs is None or len(probs) != 3 or \
                not all(p.is_finite() and 0 < p < 1 for p in probs):
            return None
        upd = SH._ts(mk.get("last_update") or book.get("last_update"))
        if upd is None or not 0 <= now - upd <= SH.MAX_ODDS_AGE_S:
            return None
        return dict(zip(names, probs)), upd
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return None


def fair3_from_event(event: Mapping[str, Any], *, now: float,
                     prefer: str = "pinnacle") -> Optional[Dict[str, Any]]:
    """{probs: {home, away, "draw"}, source, age_s} for an Odds API soccer
    event: the preferred book if it has a fresh 3-way h2h, else the median of
    every fresh 3-way book (all must name the same three outcomes, and the
    two team names must be the event's home/away), renormalised."""
    teams = {SH.norm(event.get("home_team")), SH.norm(event.get("away_team"))}
    if "" in teams or DRAW in teams or len(teams) != 2:
        return None
    want = teams | {DRAW}
    books = [b for b in event.get("bookmakers") or [] if isinstance(b, Mapping)]
    for b in books:
        if b.get("key") != prefer:
            continue
        got = h2h3(b, now=now)
        if got and set(got[0]) == want:
            return {"probs": got[0], "source": prefer,
                    "age_s": round(now - got[1], 1)}
    fresh = [g for g in (h2h3(b, now=now) for b in books) if g]
    if not fresh or any(set(f[0]) != want for f in fresh):
        return None
    probs = {t: D(str(median(f[0][t] for f in fresh))) for t in want}
    s = sum(probs.values())
    return {"probs": {t: p / s for t, p in probs.items()},
            "source": "median_of_%d" % len(fresh),
            "age_s": round(now - min(f[1] for f in fresh), 1)}


# ------------------------------------------------- venue market adapter
def _long_team(market: Mapping[str, Any]) -> Tuple[Optional[Mapping], int]:
    sides = [s for s in market.get("marketSides") or []
             if isinstance(s, Mapping)]
    longs = [s for s in sides if s.get("long") is True]
    if len(longs) != 1:
        return None, -1
    tm = longs[0].get("team")
    teamed = [s for s in sides if isinstance(s.get("team"), Mapping)]
    return (tm if isinstance(tm, Mapping) else None), len(teamed)


def soccer_market_spec(market: Mapping[str, Any]
                       ) -> Tuple[Optional[Dict[str, Any]], str]:
    """({outcome: "team"|"draw", team, abbr, event, league, sport_key}, "ok")
    for a regulation-time soccer full-time-winner market, else (None,
    reason). Anything ambiguous is refused."""
    try:
        t = str(market.get("sportsMarketType") or "").strip().lower()
        v2 = str(market.get("sportsMarketTypeV2") or "").strip().upper()
        if t != SOCCER_TYPE:
            return None, "unsupported_type:%s" % (t or "none")
        if v2 and v2 != SOCCER_TYPE_V2:
            return None, "unsupported_type_v2:%s" % v2
        m = _SLUG.match(str(market.get("slug") or ""))
        if not m:
            return None, "slug_unparsed"
        league, a, b, date, suffix = m.groups()
        if a == b or DRAW in (a, b):
            return None, "slug_unparsed"
        event = "%s-%s-%s-%s" % (league, a, b, date)
        key = SOCCER_KEYS.get(league)
        if key is None:
            return None, "league_not_configured:%s" % league
        text = str(market.get("description") or "")
        if not _REG.search(text):
            return None, "settlement_not_regulation"
        if _NOT_REG.search(text):
            return None, "settlement_includes_extra_time"
        tm, n_teamed = _long_team(market)
        if n_teamed < 0:
            return None, "side_unparsed"
        if suffix == DRAW:
            if tm is not None or n_teamed:
                return None, "draw_market_has_team"
            if DRAW not in SH.norm(market.get("question")).split():
                return None, "draw_question_mismatch"
            return {"outcome": DRAW, "team": None, "abbr": None,
                    "event": event, "league": league, "sport_key": key}, "ok"
        if suffix not in (a, b):
            return None, "suffix_not_in_event"
        if tm is None or not tm.get("name"):
            return None, "team_unreadable"
        if str(tm.get("abbreviation") or "").strip().lower() != suffix:
            return None, "team_abbr_mismatch"
        for s in market.get("marketSides") or []:
            ot = s.get("team") if isinstance(s, Mapping) else None
            if isinstance(ot, Mapping) and \
                    SH.norm(ot.get("name")) != SH.norm(tm.get("name")):
                return None, "sides_name_different_teams"
        return {"outcome": "team", "team": str(tm["name"]), "abbr": suffix,
                "event": event, "league": league, "sport_key": key}, "ok"
    except Exception as exc:                             # noqa: BLE001
        return None, "spec_error:%s" % type(exc).__name__


def event_teams(market: Mapping[str, Any],
                event_markets_or_teams: Any) -> Tuple[Optional[Tuple[str, str]],
                                                      str]:
    """The event's two team names. Either an explicit (team, team) pair or
    the event's markets: exactly two team full-time-winner markets of the
    SAME event, with the event slug's two abbreviations."""
    spec, why = soccer_market_spec(market)
    if spec is None:
        return None, why
    x = event_markets_or_teams
    if isinstance(x, (tuple, list)) and len(x) == 2 and \
            all(isinstance(t, str) for t in x):
        a, b = x
        if not SH.norm(a) or not SH.norm(b) or SH.norm(a) == SH.norm(b):
            return None, "teams_invalid"
        return (a, b), "ok"
    if not isinstance(x, Iterable) or isinstance(x, (str, bytes, Mapping)):
        return None, "teams_invalid"
    by_abbr: Dict[str, List[str]] = {}
    for mk in x:
        if not isinstance(mk, Mapping):
            continue
        s, _ = soccer_market_spec(mk)
        if s is None or s["event"] != spec["event"] or s["outcome"] != "team":
            continue
        by_abbr.setdefault(s["abbr"], []).append(s["team"])
    _, a, b = spec["event"].split("-")[:3]
    if set(by_abbr) != {a, b} or any(len(v) != 1 for v in by_abbr.values()):
        return None, "event_teams_unresolved"
    ta, tb = by_abbr[a][0], by_abbr[b][0]
    if SH.norm(ta) == SH.norm(tb):
        return None, "teams_invalid"
    return (ta, tb), "ok"


def soccer_market_fair(market: Mapping[str, Any], event_markets_or_teams: Any,
                       odds_events: Iterable[Mapping[str, Any]], *,
                       now: float, prefer: str = "pinnacle"
                       ) -> Tuple[Optional[Dict[str, Any]], str]:
    """({fair_yes, outcome, team, source, age_s, event_id}, "ok") or (None,
    reason). P(YES) = P(team wins in regulation) or P(draw), from the ONE
    feed event whose two teams pair one-to-one with the venue's (teams_match,
    PMUS_SHARP_NAME_MATCH) and whose commence time is within MATCH_TOL_S of
    the market's gameStartTime."""
    spec, why = soccer_market_spec(market)
    if spec is None:
        return None, why
    teams, why = event_teams(market, event_markets_or_teams)
    if teams is None:
        return None, why
    if spec["outcome"] == "team" and \
            SH.norm(spec["team"]) not in {SH.norm(t) for t in teams}:
        return None, "team_not_in_event"
    start = SH._ts(market.get("gameStartTime"))
    if start is None:
        return None, "no_start_time"
    hits = []
    for ev in odds_events or []:
        if not isinstance(ev, Mapping):
            continue
        ct = SH._ts(ev.get("commence_time"))
        pair = SH.teams_match(teams, ev.get("home_team"), ev.get("away_team"))
        if pair and ct is not None and abs(ct - start) <= SH.MATCH_TOL_S:
            hits.append((ev, pair))
    if len(hits) != 1:
        return None, "no_event_match" if not hits else "ambiguous_event"
    ev, pair = hits[0]
    f = fair3_from_event(ev, now=now, prefer=prefer)
    if f is None:
        return None, "no_fresh_3way_h2h"
    if spec["outcome"] == DRAW:
        p = f["probs"].get(DRAW)
    else:
        mine = [k for k in pair if SH.norm(k) == SH.norm(spec["team"])]
        if len(mine) != 1:
            return None, "team_pairing_failed"
        p = f["probs"].get(SH.norm(pair[mine[0]]))
    if p is None or not (D(0) < p < D(1)):
        return None, "outcome_not_priced"
    return {"fair_yes": p, "outcome": spec["outcome"], "team": spec["team"],
            "source": f["source"], "age_s": f["age_s"],
            "event_id": ev.get("id"), "sport_key": spec["sport_key"]}, "ok"


def soccer_shadow_rows(event_markets: Sequence[Mapping[str, Any]],
                       odds_events: Iterable[Mapping[str, Any]], *,
                       now: float) -> List[Dict[str, Any]]:
    """SHADOW-ONLY rows (one per soccer full-time-winner market of the
    event, priced or refused with a reason). [] unless PMUS_SHARP_SOCCER=1.
    Places nothing and is not read by any execution gate."""
    if not enabled():
        return []
    evs = list(odds_events or [])
    rows = []
    for mk in event_markets or []:
        if not isinstance(mk, Mapping) or str(
                mk.get("sportsMarketType") or "") != SOCCER_TYPE:
            continue
        f, why = soccer_market_fair(mk, event_markets, evs, now=now)
        rows.append({"slug": mk.get("slug"), "why": why,
                     "fair_yes": None if f is None else str(f["fair_yes"]),
                     "source": None if f is None else f["source"],
                     "label": "SHADOW: soccer 3-way fair; nothing placed"})
    return rows
