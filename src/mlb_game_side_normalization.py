"""Strict normal-event MLB game-side normalization across Gemini, Polymarket US, Kalshi.

Creates one canonical ``SportsContractIdentity`` per executable outcome side for
moneylines, half-run spreads, and half-run totals. This is intentionally a
normal-event identity layer only: venue postponement/cancellation rules remain
unverified, so every RuleAudit is incomplete and nothing here is live-eligible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.gemini_sports_adapter import normalize_events as normalize_gemini_events
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity

D = Decimal
MLB_TICKER_TZ = ZoneInfo("America/New_York")

MLB_ALIASES = {
    "ARI": "AZ", "AZ": "AZ", "OAK": "ATH", "ATH": "ATH", "ATL": "ATL", "BAL": "BAL",
    "BOS": "BOS", "CHC": "CHC", "CHW": "CWS", "CWS": "CWS", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC": "KC", "KCR": "KC", "LAA": "LAA",
    "LAD": "LAD", "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NYM": "NYM", "NYY": "NYY",
    "PHI": "PHI", "PIT": "PIT", "SD": "SD", "SDP": "SD", "SEA": "SEA", "SF": "SF",
    "SFG": "SF", "STL": "STL", "TB": "TB", "TBR": "TB", "TEX": "TEX", "TOR": "TOR",
    "WSH": "WSH", "WAS": "WSH",
}

MLB_NAMES = {
    "arizona diamondbacks": "AZ", "diamondbacks": "AZ",
    "athletics": "ATH", "oakland athletics": "ATH",
    "atlanta braves": "ATL", "braves": "ATL",
    "baltimore orioles": "BAL", "orioles": "BAL",
    "boston red sox": "BOS", "red sox": "BOS", "boston": "BOS",
    "chicago cubs": "CHC", "cubs": "CHC",
    "chicago white sox": "CWS", "white sox": "CWS",
    "cincinnati reds": "CIN", "reds": "CIN",
    "cleveland guardians": "CLE", "guardians": "CLE", "cleveland": "CLE",
    "colorado rockies": "COL", "rockies": "COL", "colorado": "COL",
    "detroit tigers": "DET", "tigers": "DET", "detroit": "DET",
    "houston astros": "HOU", "astros": "HOU", "houston": "HOU",
    "kansas city royals": "KC", "royals": "KC", "kansas city": "KC",
    "los angeles angels": "LAA", "angels": "LAA",
    "los angeles dodgers": "LAD", "dodgers": "LAD",
    "miami marlins": "MIA", "marlins": "MIA", "miami": "MIA",
    "milwaukee brewers": "MIL", "brewers": "MIL", "milwaukee": "MIL",
    "minnesota twins": "MIN", "twins": "MIN", "minnesota": "MIN",
    "new york mets": "NYM", "mets": "NYM",
    "new york yankees": "NYY", "yankees": "NYY",
    "philadelphia phillies": "PHI", "phillies": "PHI", "philadelphia": "PHI",
    "pittsburgh pirates": "PIT", "pirates": "PIT", "pittsburgh": "PIT",
    "san diego padres": "SD", "padres": "SD", "san diego": "SD",
    "seattle mariners": "SEA", "mariners": "SEA", "seattle": "SEA",
    "san francisco giants": "SF", "giants": "SF", "san francisco": "SF",
    "st. louis cardinals": "STL", "st louis cardinals": "STL", "cardinals": "STL", "st. louis": "STL", "st louis": "STL",
    "tampa bay rays": "TB", "rays": "TB", "tampa bay": "TB",
    "texas rangers": "TEX", "rangers": "TEX", "texas": "TEX",
    "toronto blue jays": "TOR", "blue jays": "TOR", "toronto": "TOR",
    "washington nationals": "WSH", "nationals": "WSH", "washington": "WSH",
}

KALSHI_SERIES = {
    "KXMLBGAME": "moneyline",
    "KXMLBSPREAD": "spread",
    "KXMLBTOTAL": "game_total",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("’", "'").split())


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _half(value: Decimal | None) -> bool:
    if value is None:
        return False
    twice = abs(value) * D("2")
    return twice == twice.to_integral_value() and int(twice) % 2 == 1


def canonical_team(value: Any) -> str | None:
    text = _norm(value).strip(" .?-_")
    if not text:
        return None
    compact = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    if compact in MLB_ALIASES:
        return MLB_ALIASES[compact]
    if text in MLB_NAMES:
        return MLB_NAMES[text]
    for name, code in sorted(MLB_NAMES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", text):
            return code
    for raw, code in sorted(MLB_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<![A-Za-z]){re.escape(raw)}(?![A-Za-z])", str(value or ""), re.I):
            return code
    return None


def _rule(venue: str) -> RuleAudit:
    return RuleAudit(
        official_source=f"{venue}_rules_unverified",
        overtime="not_applicable_baseball_extra_innings_unverified",
        postponement="unverified",
        stat_corrections="unverified",
        push_tie="unverified",
        complete=False,
    )


def _event(venue: str, teams: tuple[str, str], start: datetime) -> SportsEventIdentity:
    return SportsEventIdentity(
        sport="baseball", league="mlb", participants=tuple(sorted(teams)),
        scheduled_start_ms=int(start.timestamp() * 1000),
    )


def _identity(*, venue: str, market_id: str, event: SportsEventIdentity, family: str,
              subject_type: str, subject: str, stat: str, comparator: str,
              line: Decimal | None) -> SportsContractIdentity:
    return SportsContractIdentity(
        venue=venue, market_id=market_id, event=event, market_family=family,
        scope="full_contest", subject_type=subject_type, subject=subject,
        stat=stat, comparator=comparator, line=line, rule_audit=_rule(venue),
    )


@dataclass(frozen=True)
class NormalizedSide:
    identity: SportsContractIdentity
    venue_market_id: str
    side: str
    source_label: str

    @property
    def contract_key(self) -> str:
        return self.identity.contract_key()


def _other(team: str, teams: tuple[str, str]) -> str:
    return teams[1] if teams[0] == team else teams[0]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _poly_type(market: Mapping[str, Any]) -> str:
    return str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or "").upper().removeprefix("SPORTS_MARKET_TYPE_")


def _poly_side_teams(market: Mapping[str, Any]) -> tuple[str | None, str | None]:
    long_team = short_team = None
    for side in market.get("marketSides") or []:
        if not isinstance(side, Mapping):
            continue
        team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
        code = None
        for candidate in (team.get("abbreviation"), team.get("displayAbbreviation"), team.get("name"), side.get("description"), side.get("identifier")):
            code = canonical_team(candidate)
            if code:
                break
        if not code:
            continue
        if bool(side.get("long")):
            long_team = code
        else:
            short_team = code
    return long_team, short_team


def _poly_event_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    found: list[str] = []
    for participant in event.get("participants") or []:
        if not isinstance(participant, Mapping):
            continue
        team = participant.get("team") if isinstance(participant.get("team"), Mapping) else {}
        code = canonical_team(team.get("abbreviation") or team.get("name"))
        if code and code not in found:
            found.append(code)
    for market in event.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        for code in _poly_side_teams(market):
            if code and code not in found:
                found.append(code)
        if len(found) >= 2:
            break
    return tuple(found[:2]) if len(found) >= 2 else None


def _poly_start(event: Mapping[str, Any], market: Mapping[str, Any]) -> datetime | None:
    for value in (market.get("gameStartTime"), event.get("startTime"), event.get("startDate"), market.get("startDate")):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _poly_total_over(market: Mapping[str, Any]) -> bool | None:
    text = " | ".join(str(market.get(k) or "") for k in ("question", "title", "subtitle"))
    long_text = " | ".join(str(side.get("description") or "") for side in market.get("marketSides") or [] if isinstance(side, Mapping) and bool(side.get("long")))
    text = _norm(text + " | " + long_text)
    over = bool(re.search(r"\bover\b", text)); under = bool(re.search(r"\bunder\b", text))
    if over == under:
        return None
    return over


def normalize_polymarket_mlb(events: Iterable[Mapping[str, Any]]) -> list[NormalizedSide]:
    out: list[NormalizedSide] = []
    for event in events:
        teams = _poly_event_teams(event)
        if teams is None or len(set(teams)) != 2:
            continue
        teams = tuple(sorted(teams))
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            typ = _poly_type(market)
            if typ not in {"MONEYLINE", "SPREAD", "TOTAL"}:
                continue
            slug = str(market.get("slug") or market.get("id") or "")
            start = _poly_start(event, market)
            if not slug or start is None:
                continue
            eid = _event("polymarket", teams, start)
            long_team, short_team = _poly_side_teams(market)
            if long_team and not short_team and long_team in teams:
                short_team = _other(long_team, teams)
            label = str(market.get("question") or market.get("title") or slug)
            try:
                if typ == "MONEYLINE" and long_team in teams and short_team in teams:
                    out.extend([
                        NormalizedSide(_identity(venue="polymarket", market_id=f"{slug}#YES", event=eid, family="moneyline", subject_type="team", subject=long_team, stat="", comparator="YES", line=None), slug, "YES", label),
                        NormalizedSide(_identity(venue="polymarket", market_id=f"{slug}#NO", event=eid, family="moneyline", subject_type="team", subject=short_team, stat="", comparator="YES", line=None), slug, "NO", label),
                    ])
                elif typ == "SPREAD" and long_team in teams:
                    handicap = _d(market.get("line"))
                    if not _half(handicap):
                        continue
                    assert handicap is not None
                    other = short_team if short_team in teams else _other(long_team, teams)
                    threshold = -handicap
                    out.extend([
                        NormalizedSide(_identity(venue="polymarket", market_id=f"{slug}#YES", event=eid, family="spread", subject_type="team", subject=long_team, stat="runs", comparator="GT", line=threshold), slug, "YES", label),
                        NormalizedSide(_identity(venue="polymarket", market_id=f"{slug}#NO", event=eid, family="spread", subject_type="team", subject=other, stat="runs", comparator="GT", line=-threshold), slug, "NO", label),
                    ])
                elif typ == "TOTAL":
                    line = _d(market.get("line")); yes_over = _poly_total_over(market)
                    if not _half(line) or yes_over is None:
                        continue
                    assert line is not None
                    yes_comp, no_comp = ("GT", "LT") if yes_over else ("LT", "GT")
                    out.extend([
                        NormalizedSide(_identity(venue="polymarket", market_id=f"{slug}#YES", event=eid, family="game_total", subject_type="contest", subject="contest", stat="runs", comparator=yes_comp, line=abs(line)), slug, "YES", label),
                        NormalizedSide(_identity(venue="polymarket", market_id=f"{slug}#NO", event=eid, family="game_total", subject_type="contest", subject="contest", stat="runs", comparator=no_comp, line=abs(line)), slug, "NO", label),
                    ])
            except ValueError:
                continue
    return out


def _kalshi_teams(event: Mapping[str, Any]) -> tuple[str, str] | None:
    ticker = str(event.get("event_ticker") or "").upper()
    suffix = ticker.rsplit("-", 1)[-1]
    codes = sorted(set(MLB_ALIASES.values()), key=lambda x: -len(x))
    for a in codes:
        for b in codes:
            if a != b and suffix.endswith(a + b):
                return tuple(sorted((a, b)))
    text = " | ".join(str(event.get(k) or "") for k in ("title", "sub_title"))
    found: list[str] = []
    for name, code in sorted(MLB_NAMES.items(), key=lambda item: -len(item[0])):
        if name in _norm(text) and code not in found:
            found.append(code)
    return tuple(sorted(found[:2])) if len(found) >= 2 else None


def _kalshi_start(event: Mapping[str, Any], market: Mapping[str, Any]) -> datetime | None:
    ticker = str(event.get("event_ticker") or "").upper()
    m = re.search(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})(?P<mm>\d{2})", ticker)
    months = {name: idx for idx, name in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"), 1)}
    if m and m.group("mon") in months:
        try:
            # Kalshi MLB event-ticker HHMM is Eastern wall-clock time. Convert that
            # local scheduled start to UTC before cross-venue identity comparison.
            local_start = datetime(
                2000 + int(m.group("yy")),
                months[m.group("mon")],
                int(m.group("dd")),
                int(m.group("hh")),
                int(m.group("mm")),
                tzinfo=MLB_TICKER_TZ,
            )
            return local_start.astimezone(timezone.utc)
        except ValueError:
            pass
    for value in (event.get("strike_date"), market.get("expected_expiration_time"), market.get("close_time")):
        parsed = _parse_dt(value)
        if parsed:
            return parsed
    return None


def _kalshi_text(market: Mapping[str, Any]) -> str:
    return " | ".join(str(market.get(k) or "") for k in ("title", "subtitle", "yes_sub_title", "no_sub_title"))


def normalize_kalshi_mlb(events: Iterable[Mapping[str, Any]]) -> list[NormalizedSide]:
    out: list[NormalizedSide] = []
    spread_re = re.compile(r"(?P<team>.+?)\s+wins?\s+by\s+(?:more|over)\s+(?:than\s+)?(?P<line>\d+(?:\.\d+)?)\s+runs?", re.I)
    total_re = re.compile(r"(?:over|more\s+than)\s+(?P<line>\d+(?:\.\d+)?)", re.I)
    for event in events:
        family = KALSHI_SERIES.get(str(event.get("series_ticker") or ""))
        teams = _kalshi_teams(event)
        if family is None or teams is None:
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ticker = str(market.get("ticker") or "")
            start = _kalshi_start(event, market)
            if not ticker or start is None:
                continue
            eid = _event("kalshi", teams, start)
            text = _kalshi_text(market); label = text or ticker
            try:
                if family == "moneyline":
                    team = canonical_team(text)
                    if team not in teams:
                        continue
                    other = _other(team, teams)
                    out.extend([
                        NormalizedSide(_identity(venue="kalshi", market_id=f"{ticker}#YES", event=eid, family="moneyline", subject_type="team", subject=team, stat="", comparator="YES", line=None), ticker, "YES", label),
                        NormalizedSide(_identity(venue="kalshi", market_id=f"{ticker}#NO", event=eid, family="moneyline", subject_type="team", subject=other, stat="", comparator="YES", line=None), ticker, "NO", label),
                    ])
                elif family == "spread":
                    m = spread_re.search(text)
                    team = canonical_team(m.group("team")) if m else None
                    line = _d(m.group("line")) if m else None
                    if team not in teams or not _half(line):
                        continue
                    assert line is not None
                    other = _other(team, teams); line = abs(line)
                    out.extend([
                        NormalizedSide(_identity(venue="kalshi", market_id=f"{ticker}#YES", event=eid, family="spread", subject_type="team", subject=team, stat="runs", comparator="GT", line=line), ticker, "YES", label),
                        NormalizedSide(_identity(venue="kalshi", market_id=f"{ticker}#NO", event=eid, family="spread", subject_type="team", subject=other, stat="runs", comparator="GT", line=-line), ticker, "NO", label),
                    ])
                elif family == "game_total":
                    m = total_re.search(text); line = _d(m.group("line")) if m else None
                    if not _half(line):
                        continue
                    assert line is not None; line = abs(line)
                    out.extend([
                        NormalizedSide(_identity(venue="kalshi", market_id=f"{ticker}#YES", event=eid, family="game_total", subject_type="contest", subject="contest", stat="runs", comparator="GT", line=line), ticker, "YES", label),
                        NormalizedSide(_identity(venue="kalshi", market_id=f"{ticker}#NO", event=eid, family="game_total", subject_type="contest", subject="contest", stat="runs", comparator="LT", line=line), ticker, "NO", label),
                    ])
            except ValueError:
                continue
    return out


def normalize_gemini_mlb(events: Iterable[Mapping[str, Any]]) -> list[NormalizedSide]:
    out: list[NormalizedSide] = []
    for row in normalize_gemini_events(events):
        if row.identity.event.sport != "baseball" or row.identity.event.league != "mlb":
            continue
        out.append(NormalizedSide(
            identity=row.identity, venue_market_id=row.instrument_symbol,
            side="CONTRACT", source_label=row.contract_label,
        ))
    return out
