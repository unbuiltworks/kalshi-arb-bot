"""Pure pricing helper. Caller must independently match event and settlement rules.
No network, orders, or production integration."""
from decimal import Decimal as D
from src.pmus_sports_sharp import devig, norm, _ts, MAX_ODDS_AGE_S

def line_fair(book, *, kind, selection, point, now, opponent=None):
    """Return fair probability for an exact two-way half-point line.
    Integer lines can push and are deliberately unsupported."""
    try:
        p = D(str(point))
        if not p.is_finite() or abs(p) % 1 != D("0.5"):
            return None
        if kind not in ("spreads", "totals"):
            return None
        name = norm(selection)
        other = norm(opponent) if kind == "spreads" else (
            "under" if name == "over" else "over")
        if not name or not other or name == other:
            return None
        if kind == "totals" and name not in ("over", "under"):
            return None
        matches = []
        for market in book.get("markets", []):
            if market.get("key") != kind:
                continue
            stamp = _ts(market.get("last_update") or book.get("last_update"))
            if stamp is None or not 0 <= now - stamp <= MAX_ODDS_AGE_S:
                continue
            outcomes = market.get("outcomes", [])
            a = [o for o in outcomes if norm(o.get("name")) == name
                 and D(str(o.get("point"))) == p]
            b = [o for o in outcomes if norm(o.get("name")) == other
                 and D(str(o.get("point"))) == (-p if kind == "spreads" else p)]
            if len(a) == len(b) == 1:
                probs = devig([a[0].get("price"), b[0].get("price")])
                if probs is not None:
                    matches.append(probs[0])
        return matches[0] if len(matches) == 1 else None
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return None


def prop_fair(book, *, market_key, player, selection, point, now):
    """Price an already event-matched player prop. Exact provider stat key,
    player and half-point threshold required. Settlement equivalence is
    the caller's responsibility; no production integration."""
    try:
        p = D(str(point))
        name = norm(player)
        side = norm(selection)
        if (not isinstance(market_key, str) or not market_key.startswith("player_")
                or not name or side not in ("over", "under")
                or not p.is_finite() or p < 0 or p % 1 != D("0.5")):
            return None
        other = "under" if side == "over" else "over"
        matches = []
        for market in book.get("markets", []):
            if market.get("key") != market_key:
                continue
            stamp = _ts(market.get("last_update") or book.get("last_update"))
            if stamp is None or not 0 <= now - stamp <= MAX_ODDS_AGE_S:
                continue
            outcomes = [o for o in market.get("outcomes", [])
                        if norm(o.get("description")) == name]
            a = [o for o in outcomes if norm(o.get("name")) == side
                 and D(str(o.get("point"))) == p]
            b = [o for o in outcomes if norm(o.get("name")) == other
                 and D(str(o.get("point"))) == p]
            if len(a) == len(b) == 1:
                probs = devig([a[0].get("price"), b[0].get("price")])
                if probs is not None and all(x.is_finite() for x in probs):
                    matches.append(probs[0])
        return matches[0] if len(matches) == 1 else None
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return None


# ------------------------------------------------- venue market adapter
# INTEGRATION (28 Nov, PR #253 review). Connects line_fair to the venue's
# own market records. Only FULL-GAME spreads and GAME totals are mapped:
# their contract text (catalogue 28 Nov) says "Overtime is included if
# played" (NHL: a shootout counts as one goal for the winner), which is the
# sportsbook full-game convention for the Odds API `spreads` / `totals`
# keys. Everything else -- halves, quarters, periods, TEAM totals (labelled
# SPORTS_MARKET_TYPE_TOTAL by the venue's V2 field), props, drawable soccer
# -- is refused with a reason; the V2 field is never used to map.
import re as _re
from typing import Any as _Any, Dict as _Dict, Mapping as _Mapping, \
    Optional as _Opt, Tuple as _Tuple

LINE_TYPES = {
    "basketball_team_full_game_spread": "spreads",
    "football_team_full_game_spread": "spreads",
    "hockey_team_full_game_spread": "spreads",
    "basketball_team_full_game_total": "totals",
    "football_team_full_game_total": "totals",
    "hockey_team_full_game_total": "totals",
}
_SLUG = _re.compile(
    r"^(asc|tsc)-([a-z0-9]+-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})-(.+)$")


def _slug_point(suffix: str) -> _Opt[D]:
    m = _re.search(r"(?:^|-)(neg|pos)?-?(\d+)pt(\d+)$", suffix)
    if not m:
        return None
    v = D("%s.%s" % (m.group(2), m.group(3)))
    return -v if m.group(1) == "neg" else v


def line_spec(market: _Mapping[str, _Any]) -> _Tuple[_Opt[_Dict[str, _Any]],
                                                     str]:
    """({kind, event, sibling, team, point, sport}, "ok") for a supported
    full-game spread / game total, else (None, reason)."""
    from src import pmus_sports_sharp as SH
    try:
        t = str(market.get("sportsMarketType") or "").strip().lower()
        kind = LINE_TYPES.get(t)
        if kind is None:
            return None, "unsupported_type:%s" % (t or "none")
        m = _SLUG.match(str(market.get("slug") or ""))
        if not m or (m.group(1) == "asc") != (kind == "spreads"):
            return None, "slug_unparsed"
        event, suffix = m.group(2), m.group(3)
        sport = event.split("-")[0]
        if sport not in SH.SPORT_KEYS:
            return None, "sport_not_configured:%s" % sport
        sp = _slug_point(suffix)
        sides = [x for x in market.get("marketSides") or []
                 if isinstance(x, _Mapping)]
        longs = [x for x in sides if x.get("long") is True]
        if sp is None or len(longs) != 1:
            return None, "line_or_side_unparsed"
        lg = longs[0]
        if kind == "spreads":
            team = (lg.get("team") or {}).get("name") \
                if isinstance(lg.get("team"), _Mapping) else None
            try:
                dp = D(str(lg.get("description")).strip())
            except Exception:                            # noqa: BLE001
                return None, "line_or_side_unparsed"
            if not team or dp != sp:
                return None, "line_mismatch"
            return {"kind": kind, "event": event, "sibling": "aec-" + event,
                    "team": team, "point": sp, "sport": sport}, "ok"
        q = _re.search(r"more than (\d+(?:\.\d+)?)\?",
                       str(market.get("question") or ""))
        if not q or D(q.group(1)) != sp or \
                SH.norm(lg.get("description")) != "over":
            return None, "line_mismatch"
        return {"kind": kind, "event": event, "sibling": "aec-" + event,
                "team": None, "point": sp, "sport": sport}, "ok"
    except Exception as exc:                             # noqa: BLE001
        return None, "spec_error:%s" % type(exc).__name__


def line_market_fair(market: _Mapping[str, _Any],
                     sibling: _Mapping[str, _Any],
                     events, *, now: float,
                     prefer: str = "pinnacle") -> _Tuple[
                         _Opt[_Dict[str, _Any]], str]:
    """({fair_yes, source, kind, point, event_id}, "ok") or (None, reason).
    `sibling` is the event's moneyline market (exact event identity: its
    two teams and start time must match ONE feed event). Only the preferred
    book is used (no cross-book blend for an exact line)."""
    from src import pmus_sports_sharp as SH
    spec, why = line_spec(market)
    if spec is None:
        return None, why
    if str(sibling.get("slug") or "") != spec["sibling"] or \
            not SH.is_moneyline(sibling):
        return None, "sibling_mismatch"
    ev = SH.match_event(sibling, events)
    if ev is None:
        return None, "no_event_match"
    teams = SH.market_teams(sibling)
    pair = SH.teams_match(teams, ev.get("home_team"), ev.get("away_team"))
    if not pair:
        return None, "team_pairing_failed"
    books = [b for b in ev.get("bookmakers") or []
             if isinstance(b, _Mapping) and b.get("key") == prefer]
    if len(books) != 1:
        return None, "no_%s" % prefer
    if spec["kind"] == "spreads":
        mine = [k for k in pair if SH.norm(k) == SH.norm(spec["team"])]
        if len(mine) != 1:
            return None, "spread_team_not_in_event"
        other = [k for k in pair if k != mine[0]][0]
        p = line_fair(books[0], kind="spreads", selection=pair[mine[0]],
                      opponent=pair[other], point=spec["point"], now=now)
    else:
        p = line_fair(books[0], kind="totals", selection="Over",
                      point=spec["point"], now=now)
    if p is None:
        return None, "line_not_offered_or_stale"
    age = None
    for mk in books[0].get("markets") or []:
        if isinstance(mk, _Mapping) and mk.get("key") == spec["kind"]:
            ts = _ts(mk.get("last_update") or books[0].get("last_update"))
            age = None if ts is None else round(now - ts, 1)
    return {"fair_yes": p, "source": prefer, "kind": spec["kind"],
            "point": str(spec["point"]), "event_id": ev.get("id"),
            "age_s": age}, "ok"
