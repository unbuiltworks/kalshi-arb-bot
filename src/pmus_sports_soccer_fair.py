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
    feed event whose two teams pair one-to-one with the venue's (exact club
    keys via the explicit alias table, `club_pair`) and whose commence time
    is within MATCH_TOL_S of the market's gameStartTime."""
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
        pair = club_pair(teams, ev.get("home_team"), ev.get("away_team"))
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


# ------------------------------------------------ deterministic club keys
#: Explicit alias table: normalised name (after the strip rules below) ->
#: canonical club key. Venue full names are from the public catalogue
#: (28 Nov slate + settled 22 Nov Serie A); feed-side short names are the
#: usual sportsbook spellings and are UNVERIFIED against The Odds API until
#: a coverage read confirms them. Only an entry here, or an exact strip
#: rule, can make two different spellings equal; nothing is fuzzy.
CLUB_ALIASES: Dict[str, str] = {
    # La Liga
    "real betis balompie": "real betis",
    "betis": "real betis",
    # Serie A
    "internazionale milano": "inter", "internazionale": "inter",
    "inter milan": "inter", "inter": "inter",
    "bologna fc 1909": "bologna",
    "parma calcio 1913": "parma",
    # Eredivisie
    "psv eindhoven": "psv",
    "sbv excelsior": "excelsior",
    "telstar 1963": "telstar",
    "cambuur leeuwarden": "cambuur", "cambuur": "cambuur",
    # Bundesliga
    "1 fc koln": "koln", "koln": "koln", "cologne": "koln",
    "paderborn 07": "paderborn",
    "schalke 04": "schalke",
    "07 elversberg": "elversberg",
}
#: Whole words removed from the START or END of a name (never the middle,
#: never the last remaining word): legal-form / club-type tokens only.
STRIP_WORDS = ("fc", "cf", "ac", "sc", "afc", "cfc", "ss", "us", "sv",
               "calcio")
#: Exact trailing phrases removed (explicit rule list).
STRIP_SUFFIXES = ("de madrid",)


def club_key(name: Any) -> str:
    """Canonical club key: normalise, apply the explicit alias table, else
    the strip rules then the table again. Applied identically to venue and
    feed names. "" for an empty name."""
    n = SH.norm(name)
    if not n:
        return ""
    if n in CLUB_ALIASES:
        return CLUB_ALIASES[n]
    w = n.split()
    changed = True
    while changed and len(w) > 1:
        changed = False
        if w[0] in STRIP_WORDS:
            w, changed = w[1:], True
        elif w[-1] in STRIP_WORDS:
            w, changed = w[:-1], True
        else:
            for suf in STRIP_SUFFIXES:
                sw = suf.split()
                if len(w) > len(sw) and w[-len(sw):] == sw:
                    w, changed = w[:-len(sw)], True
                    break
    k = " ".join(w)
    return CLUB_ALIASES.get(k, k)


def club_pair(teams: Sequence[str], home: Any, away: Any
              ) -> Optional[Dict[str, str]]:
    """{venue team -> feed team} when the two venue teams and the two feed
    teams have the same two DISTINCT club keys, one-to-one; else None."""
    if len(teams) != 2:
        return None
    vk = [club_key(t) for t in teams]
    fk = [club_key(home), club_key(away)]
    if not all(vk) or not all(fk) or vk[0] == vk[1] or fk[0] == fk[1] or \
            DRAW in vk or DRAW in fk:
        return None
    if vk == fk:
        return {teams[0]: str(home), teams[1]: str(away)}
    if vk == fk[::-1]:
        return {teams[0]: str(away), teams[1]: str(home)}
    return None


# ------------------------------------------------- one match, one risk
_MATCH = re.compile(
    r"^atc-([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})-([a-z0-9]+)$")


def match_of(slug: Any) -> Optional[Dict[str, Any]]:
    """{"match", "league", "outcome", "outcomes", "siblings"} for a soccer
    full-time-winner outcome slug atc-<lg>-<home>-<away>-<date>-<outcome>
    (outcome = home code, away code or "draw"), else None. Pure slug
    arithmetic; the market TYPE is the caller's to check."""
    m = _MATCH.match(str(slug or ""))
    if not m:
        return None
    lg, h, a, date, o = m.groups()
    if h == a or DRAW in (h, a) or o not in (h, a, DRAW):
        return None
    base = "atc-%s-%s-%s-%s-" % (lg, h, a, date)
    outs = (h, DRAW, a)
    return {"match": "%s-%s-%s-%s" % (lg, h, a, date), "league": lg,
            "outcome": o, "outcomes": outs,
            "siblings": tuple(base + x for x in outs)}


def _payoff(outcome: str, side: str, result: str) -> D:
    yes = outcome == result
    return D(1) if (yes if side == "YES" else not yes) else D(0)


def outcome_pnl(holdings: Mapping[str, Mapping[str, Any]],
                outcomes: Sequence[str],
                resting: Iterable[Tuple[str, str, Any, Any]] = ()
                ) -> Dict[str, D]:
    """Worst P&L of ONE match for each result (H, D, A), exactly.

    holdings  {outcome: {"pos": {YES, NO}, "cost": {YES, NO}}}: the filled
              inventory of each outcome's binary market (missing = flat).
    resting   (outcome, side, px, qty) bids that may fill.
    For a fixed result the P&L is linear in each bid's fill, so its worst
    case is each bid filling fully exactly when that loses money for that
    result (and not at all otherwise): base(r) + sum min(0, q*(pay - px)).
    No outcome is assumed to offset another except through this arithmetic.
    Raises ValueError on an unknown outcome or unreadable figure."""
    outs = tuple(outcomes)
    if len(outs) != 3 or len(set(outs)) != 3:
        raise ValueError("outcomes")
    rest = list(resting)
    for o in list(holdings) + [r[0] for r in rest]:
        if o not in outs:
            raise ValueError("unknown_outcome:%s" % o)
    res: Dict[str, D] = {}
    for r in outs:
        v = D(0)
        for o, h in holdings.items():
            pos, cost = h.get("pos") or {}, h.get("cost") or {}
            for sd in ("YES", "NO"):
                q, c = D(str(pos.get(sd) or 0)), D(str(cost.get(sd) or 0))
                if not (q.is_finite() and c.is_finite()) or q < 0 or c < 0:
                    raise ValueError("figure")
                v += q * _payoff(o, sd, r) - c
        for o, sd, px, qty in rest:
            p, q = D(str(px)), D(str(qty))
            if not (p.is_finite() and q.is_finite()) or q < 0:
                raise ValueError("figure")
            side = "YES" if str(sd).upper() == "YES" else "NO"
            v += min(D(0), q * (_payoff(o, side, r) - p))
        res[r] = v
    return res


def match_worst(holdings, outcomes, resting=()) -> D:
    """The match's worst P&L over results and adverse fills."""
    return min(outcome_pnl(holdings, outcomes, resting).values())


def match_loss(holdings, outcomes, resting=()) -> D:
    """max(0, -worst): the most this match can lose."""
    return max(D(0), -match_worst(holdings, outcomes, resting))


def dutch_lock(holdings: Mapping[str, Mapping[str, Any]],
               outcomes: Sequence[str]) -> Dict[str, D]:
    """YES held on ALL three outcomes is a locked set: exactly one pays $1.
    {"sets": min YES qty, "set_cost": sum of the three YES average costs,
    "locked_pnl": sets * (1 - set_cost)}; sets 0 when any is missing."""
    qs, avgs = [], []
    for o in outcomes:
        h = holdings.get(o) or {}
        q = D(str((h.get("pos") or {}).get("YES") or 0))
        c = D(str((h.get("cost") or {}).get("YES") or 0))
        qs.append(q)
        avgs.append(c / q if q > 0 else D(0))
    n = min(qs) if qs else D(0)
    if n <= 0:
        return {"sets": D(0), "set_cost": D(0), "locked_pnl": D(0)}
    sc = sum(avgs)
    return {"sets": n, "set_cost": sc, "locked_pnl": n * (D(1) - sc)}


def three_way_consistent(mids: Sequence[Any], tol: Any = "0.04"
                         ) -> Tuple[bool, Optional[D]]:
    """(ok, sum): the three outcome mids of one match must all be present,
    each in (0, 1), and sum to 1 within `tol`."""
    try:
        ms = [D(str(m)) for m in mids]
        if len(ms) != 3 or not all(m.is_finite() and D(0) < m < D(1)
                                   for m in ms):
            return False, None
        s = sum(ms)
        return abs(s - D(1)) <= D(str(tol)), s
    except Exception:                                    # noqa: BLE001
        return False, None
