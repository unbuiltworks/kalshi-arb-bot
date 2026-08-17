"""Strict read-only adapters for MLB game markets on Polymarket US <-> Kalshi.

The goal is to normalize only contracts that describe the same normal-completion
baseball predicate.  Postponement/cancellation policies differ across venues, so a
match is never labelled risk-free or execution-authorized here.

Supported families:
- game winner / moneyline
- half-run spread / run line
- half-run full-game total

No authentication or order-entry code lives in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping


GAME_FAMILY_SERIES: Mapping[str, str] = {
    "baseball_game_moneyline": "KXMLBGAME",
    "baseball_game_spread": "KXMLBSPREAD",
    "baseball_game_total": "KXMLBTOTAL",
}

# Kalshi has historically used these compact team codes in MLB event tickers.
MLB_CODES = {
    "ARI": "AZ",
    "AZ": "AZ",
    "ATH": "ATH",
    "ATL": "ATL",
    "BAL": "BAL",
    "BOS": "BOS",
    "CHC": "CHC",
    "CHW": "CWS",
    "CWS": "CWS",
    "CIN": "CIN",
    "CLE": "CLE",
    "COL": "COL",
    "DET": "DET",
    "HOU": "HOU",
    "KC": "KC",
    "KCR": "KC",
    "LAA": "LAA",
    "LAD": "LAD",
    "MIA": "MIA",
    "MIL": "MIL",
    "MIN": "MIN",
    "NYM": "NYM",
    "NYY": "NYY",
    "OAK": "ATH",
    "PHI": "PHI",
    "PIT": "PIT",
    "SD": "SD",
    "SDP": "SD",
    "SEA": "SEA",
    "SF": "SF",
    "SFG": "SF",
    "STL": "STL",
    "TB": "TB",
    "TBR": "TB",
    "TEX": "TEX",
    "TOR": "TOR",
    "WSH": "WSH",
    "WAS": "WSH",
}

TEAM_NAME_TO_CODE = {
    "arizona diamondbacks": "AZ",
    "diamondbacks": "AZ",
    "athletics": "ATH",
    "oakland athletics": "ATH",
    "atlanta braves": "ATL",
    "braves": "ATL",
    "baltimore orioles": "BAL",
    "orioles": "BAL",
    "boston red sox": "BOS",
    "red sox": "BOS",
    "chicago cubs": "CHC",
    "cubs": "CHC",
    "chicago white sox": "CWS",
    "white sox": "CWS",
    "cincinnati reds": "CIN",
    "reds": "CIN",
    "cleveland guardians": "CLE",
    "guardians": "CLE",
    "colorado rockies": "COL",
    "rockies": "COL",
    "detroit tigers": "DET",
    "tigers": "DET",
    "houston astros": "HOU",
    "astros": "HOU",
    "kansas city royals": "KC",
    "royals": "KC",
    "los angeles angels": "LAA",
    "angels": "LAA",
    "los angeles dodgers": "LAD",
    "dodgers": "LAD",
    "miami marlins": "MIA",
    "marlins": "MIA",
    "milwaukee brewers": "MIL",
    "brewers": "MIL",
    "minnesota twins": "MIN",
    "twins": "MIN",
    "new york mets": "NYM",
    "mets": "NYM",
    "new york yankees": "NYY",
    "yankees": "NYY",
    "philadelphia phillies": "PHI",
    "phillies": "PHI",
    "pittsburgh pirates": "PIT",
    "pirates": "PIT",
    "san diego padres": "SD",
    "padres": "SD",
    "seattle mariners": "SEA",
    "mariners": "SEA",
    "san francisco giants": "SF",
    "giants": "SF",
    "st. louis cardinals": "STL",
    "st louis cardinals": "STL",
    "cardinals": "STL",
    "tampa bay rays": "TB",
    "rays": "TB",
    "texas rangers": "TEX",
    "rangers": "TEX",
    "toronto blue jays": "TOR",
    "blue jays": "TOR",
    "washington nationals": "WSH",
    "nationals": "WSH",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("’", "'").split())


def _d(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def canonical_team(value: Any) -> str | None:
    text = _norm(value).strip(" .?-_")
    if not text:
        return None
    compact = re.sub(r"[^a-z0-9]+", "", text).upper()
    if compact in MLB_CODES:
        return MLB_CODES[compact]
    if text in TEAM_NAME_TO_CODE:
        return TEAM_NAME_TO_CODE[text]
    # Exact whole-token code fallback for strings such as "PHI wins".
    for raw, canonical in sorted(MLB_CODES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"(?<![A-Za-z]){re.escape(raw)}(?![A-Za-z])", str(value or ""), re.I):
            return canonical
    # Longest team-name match first to avoid e.g. generic "sox" ambiguity.
    for name, code in sorted(TEAM_NAME_TO_CODE.items(), key=lambda x: -len(x[0])):
        if name in text:
            return code
    return None


def _half_line(value: Decimal | None) -> bool:
    if value is None:
        return False
    doubled = value.copy_abs() * Decimal("2")
    return doubled == doubled.to_integral_value() and int(doubled) % 2 == 1


def _v2_type(market: Mapping[str, Any]) -> str:
    value = str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or "").upper()
    return value.removeprefix("SPORTS_MARKET_TYPE_")


def _market_sides(market: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [row for row in (market.get("marketSides") or []) if isinstance(row, Mapping)]


def _side_team(side: Mapping[str, Any]) -> str | None:
    team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
    for candidate in (
        team.get("abbreviation"),
        team.get("displayAbbreviation"),
        team.get("safeName"),
        team.get("name"),
        side.get("description"),
        side.get("identifier"),
    ):
        parsed = canonical_team(candidate)
        if parsed:
            return parsed
    return None


def poly_long_short_teams(market: Mapping[str, Any]) -> tuple[str | None, str | None]:
    long_team: str | None = None
    short_team: str | None = None
    for side in _market_sides(market):
        team = _side_team(side)
        if not team:
            continue
        if bool(side.get("long")):
            long_team = team
        else:
            short_team = team
    return long_team, short_team


def poly_event_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    found: list[str] = []
    for participant in event.get("participants") or []:
        if not isinstance(participant, Mapping):
            continue
        team = participant.get("team") if isinstance(participant.get("team"), Mapping) else {}
        code = canonical_team(team.get("abbreviation") or team.get("name"))
        if code and code not in found:
            found.append(code)
    if len(found) >= 2:
        return found[0], found[1]
    for market in event.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        for team in poly_long_short_teams(market):
            if team and team not in found:
                found.append(team)
        if len(found) >= 2:
            return found[0], found[1]
    text = " | ".join(str(event.get(k) or "") for k in ("title", "subtitle", "slug"))
    for code in sorted(set(MLB_CODES.values()), key=lambda x: -len(x)):
        if re.search(rf"(?<![A-Za-z]){re.escape(code)}(?![A-Za-z])", text, re.I) and code not in found:
            found.append(code)
    return (found[0], found[1]) if len(found) >= 2 else None


def _kalshi_event_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    ticker = str(event.get("event_ticker") or "").upper()
    suffix = ticker.rsplit("-", 1)[-1]
    codes = sorted(set(MLB_CODES.values()), key=lambda x: -len(x))
    for a in codes:
        for b in codes:
            if a != b and suffix.endswith(a + b):
                return a, b
    text = " | ".join(str(event.get(k) or "") for k in ("title", "sub_title"))
    found: list[str] = []
    for name, code in sorted(TEAM_NAME_TO_CODE.items(), key=lambda x: -len(x[0])):
        if name in _norm(text) and code not in found:
            found.append(code)
    return (found[0], found[1]) if len(found) >= 2 else None


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def poly_start_utc(event: Mapping[str, Any], market: Mapping[str, Any]) -> datetime | None:
    for value in (
        market.get("gameStartTime"),
        market.get("eventStartTime"),
        event.get("startTime"),
        event.get("startDate"),
    ):
        parsed = _parse_utc(value)
        if parsed:
            return parsed
    return None


def kalshi_start_utc(event: Mapping[str, Any], market: Mapping[str, Any]) -> datetime | None:
    for value in (
        event.get("strike_date"),
        market.get("expected_expiration_time"),
        market.get("latest_expiration_time"),
        market.get("close_time"),
    ):
        parsed = _parse_utc(value)
        if parsed:
            return parsed
    # Event tickers encode DDHHMM after YYMON for current MLB families, e.g.
    # KXMLBGAME-26AUG171840MIAPHI. Treat this only as a corroborating fallback.
    ticker = str(event.get("event_ticker") or "").upper()
    m = re.search(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})(?P<mm>\d{2})", ticker)
    if m:
        months = {name: i for i, name in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"), 1)}
        mon = months.get(m.group("mon"))
        if mon:
            try:
                return datetime(2000 + int(m.group("yy")), mon, int(m.group("dd")), int(m.group("hh")), int(m.group("mm")), tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _kalshi_text(market: Mapping[str, Any]) -> str:
    return " | ".join(str(market.get(k) or "") for k in ("title", "subtitle", "yes_sub_title", "no_sub_title"))


def kalshi_moneyline_team(market: Mapping[str, Any], teams: tuple[str, str]) -> str | None:
    text = _kalshi_text(market)
    matches = [team for team in teams if canonical_team(team) and re.search(rf"(?<![A-Za-z]){re.escape(team)}(?![A-Za-z])", text, re.I)]
    if len(matches) == 1:
        return matches[0]
    parsed = canonical_team(text)
    return parsed if parsed in teams else None


_SPREAD_RE = re.compile(r"(?P<team>.+?)\s+wins?\s+by\s+(?:more|over)\s+(?:than\s+)?(?P<line>\d+(?:\.\d+)?)\s+runs?", re.I)
_TOTAL_RE = re.compile(r"(?:over|more\s+than)\s+(?P<line>\d+(?:\.\d+)?)\s+(?:total\s+)?runs?", re.I)


def kalshi_spread_predicate(market: Mapping[str, Any], teams: tuple[str, str]) -> tuple[str, Decimal] | None:
    text = _kalshi_text(market)
    m = _SPREAD_RE.search(text)
    if not m:
        return None
    team = canonical_team(m.group("team"))
    line = _d(m.group("line"))
    if team not in teams or not _half_line(line):
        return None
    assert line is not None
    return team, line.copy_abs()


def kalshi_total_line(market: Mapping[str, Any]) -> Decimal | None:
    text = _kalshi_text(market)
    m = _TOTAL_RE.search(text)
    if not m:
        # Common compact form: "Over 8.5 runs scored"
        m = re.search(r"over\s+(?P<line>\d+(?:\.\d+)?)", text, re.I)
    line = _d(m.group("line")) if m else None
    return line.copy_abs() if _half_line(line) and line is not None else None


def _poly_total_yes_is_over(market: Mapping[str, Any]) -> bool | None:
    text = " | ".join(str(market.get(k) or "") for k in ("question", "title", "subtitle", "yes_sub_title"))
    long_descriptions = " | ".join(str(s.get("description") or "") for s in _market_sides(market) if bool(s.get("long")))
    yes_text = _norm(text + " | " + long_descriptions)
    has_over = bool(re.search(r"\bover\b", yes_text))
    has_under = bool(re.search(r"\bunder\b", yes_text))
    if has_over == has_under:
        return None
    return has_over


@dataclass(frozen=True)
class GameCandidate:
    family: str
    poly_slug: str
    kalshi_ticker: str
    teams: tuple[str, str]
    start_utc: str
    predicate: str
    line: Decimal | None
    poly_invert: bool
    rule_status: str = "RULE_EQUIVALENT_NORMAL_COMPLETION_ONLY"
    read_only: bool = True
    order_entry: bool = False

    @property
    def key(self) -> str:
        line = "" if self.line is None else format(self.line.normalize(), "f")
        return "|".join((self.family, self.start_utc, "-".join(sorted(self.teams)), self.predicate, line, self.poly_slug, self.kalshi_ticker))

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "poly_slug": self.poly_slug,
            "kalshi_ticker": self.kalshi_ticker,
            "teams": list(self.teams),
            "start_utc": self.start_utc,
            "predicate": self.predicate,
            "line": str(self.line) if self.line is not None else None,
            "poly_invert": self.poly_invert,
            "rule_status": self.rule_status,
            "read_only": True,
            "order_entry": False,
        }


def _poly_moneyline_alignment(market: Mapping[str, Any], target_team: str, teams: tuple[str, str]) -> bool | None:
    long_team, short_team = poly_long_short_teams(market)
    if long_team == target_team:
        return False
    if short_team == target_team:
        return True
    # If only long team is supplied, infer the other game team as short.
    if long_team in teams:
        other = teams[1] if teams[0] == long_team else teams[0]
        if other == target_team:
            return True
    return None


def _poly_spread_alignment(market: Mapping[str, Any], target_team: str, target_line: Decimal, teams: tuple[str, str]) -> bool | None:
    line = _d(market.get("line"))
    if line is None or not _half_line(line) or line.copy_abs() != target_line.copy_abs():
        return None
    long_team, short_team = poly_long_short_teams(market)
    if long_team not in teams:
        return None
    other = short_team if short_team in teams else (teams[1] if teams[0] == long_team else teams[0])
    # YES on team -1.5 is the same predicate as "team wins by over 1.5".
    if line < 0 and long_team == target_team:
        return False
    # NO on opponent +1.5 is the same predicate as "target wins by over 1.5".
    if line > 0 and other == target_team:
        return True
    return None


def _poly_total_alignment(market: Mapping[str, Any], target_line: Decimal) -> bool | None:
    line = _d(market.get("line"))
    if line is None or line.copy_abs() != target_line.copy_abs() or not _half_line(line):
        return None
    yes_over = _poly_total_yes_is_over(market)
    if yes_over is None:
        return None
    return not yes_over


def discover_game_candidates(
    poly_events: Iterable[Mapping[str, Any]],
    kalshi_events: Iterable[Mapping[str, Any]],
    *,
    max_start_skew_seconds: float = 900.0,
) -> list[GameCandidate]:
    """Strictly match same-game normal-completion predicates across venues."""
    p_rows: list[tuple[Mapping[str, Any], Mapping[str, Any], tuple[str, str], datetime]] = []
    for event in poly_events:
        teams = poly_event_teams(event)
        if teams is None:
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            if _v2_type(market) not in {"MONEYLINE", "SPREAD", "TOTAL"}:
                continue
            start = poly_start_utc(event, market)
            slug = str(market.get("slug") or "")
            if start is None or not slug:
                continue
            p_rows.append((event, market, teams, start))

    out: list[GameCandidate] = []
    seen: set[str] = set()
    for kevent in kalshi_events:
        series = str(kevent.get("series_ticker") or "")
        family = next((f for f, s in GAME_FAMILY_SERIES.items() if s == series), None)
        if family is None:
            continue
        kte = _kalshi_event_teams(kevent)
        if kte is None:
            continue
        kset = set(kte)
        for kmarket in kevent.get("markets") or []:
            if not isinstance(kmarket, Mapping):
                continue
            kticker = str(kmarket.get("ticker") or "")
            kstart = kalshi_start_utc(kevent, kmarket)
            if not kticker or kstart is None:
                continue

            target_team: str | None = None
            target_line: Decimal | None = None
            predicate: str | None = None
            if family == "baseball_game_moneyline":
                target_team = kalshi_moneyline_team(kmarket, kte)
                if target_team:
                    predicate = f"{target_team}_WIN"
            elif family == "baseball_game_spread":
                spread = kalshi_spread_predicate(kmarket, kte)
                if spread:
                    target_team, target_line = spread
                    predicate = f"{target_team}_MARGIN_GT_{target_line}"
            else:
                target_line = kalshi_total_line(kmarket)
                if target_line is not None:
                    predicate = f"TOTAL_RUNS_GT_{target_line}"
            if predicate is None:
                continue

            for _pevent, pmarket, pteams, pstart in p_rows:
                if set(pteams) != kset or abs((pstart - kstart).total_seconds()) > max_start_skew_seconds:
                    continue
                ptype = _v2_type(pmarket)
                invert: bool | None = None
                if family == "baseball_game_moneyline" and ptype == "MONEYLINE" and target_team:
                    invert = _poly_moneyline_alignment(pmarket, target_team, pteams)
                elif family == "baseball_game_spread" and ptype == "SPREAD" and target_team and target_line is not None:
                    invert = _poly_spread_alignment(pmarket, target_team, target_line, pteams)
                elif family == "baseball_game_total" and ptype == "TOTAL" and target_line is not None:
                    invert = _poly_total_alignment(pmarket, target_line)
                if invert is None:
                    continue
                row = GameCandidate(
                    family=family,
                    poly_slug=str(pmarket.get("slug") or ""),
                    kalshi_ticker=kticker,
                    teams=tuple(sorted(kte)),
                    start_utc=pstart.isoformat(),
                    predicate=predicate,
                    line=target_line,
                    poly_invert=invert,
                )
                if row.key not in seen:
                    seen.add(row.key)
                    out.append(row)
    return out


def supported_game_families() -> dict[str, str]:
    return dict(GAME_FAMILY_SERIES)
