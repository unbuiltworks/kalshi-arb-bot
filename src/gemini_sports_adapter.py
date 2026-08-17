"""Strict read-only Gemini sports normalization for the universal arb router.

Gemini live sports events currently expose deterministic tickers, contract strikes,
labels, and prices even when the optional ``sportsMarket`` object is absent. This
adapter accepts both documented structured metadata and that live shape, failing
closed whenever the ticker/contract semantics are ambiguous. No auth or order entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable, Mapping

from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity

TEAM_EVENT_RE = re.compile(
    r"^(?:GEMI-)?(?P<league>[A-Z]{2,14})-(?P<date>\d{6})(?P<time>\d{4})-"
    r"(?P<away>[A-Z]{2,4})-(?P<home>[A-Z]{2,4})-(?P<code>[A-Z0-9]{1,8})$"
)
SPORT_MAP = {
    "baseball": "baseball", "american_football": "football", "basketball": "basketball",
    "hockey": "hockey", "ice_hockey": "hockey", "soccer": "soccer",
    "tennis": "tennis", "mma": "mma", "golf": "golf",
}
TEAM_ALIASES = {
    "ARI": "AZ", "OAK": "ATH", "CHW": "CWS", "KCR": "KC", "SDP": "SD",
    "SFG": "SF", "TBR": "TB", "WSH": "WSH", "WAS": "WSH",
}
METRIC_ALIASES = {
    "runs_batted_in": "rbis", "pitching_outs_recorded": "outs_recorded",
    "pass_completions": "completions", "passing_touchdowns": "passing_tds",
    "interceptions_thrown": "interceptions", "three_pointers_made": "three_pointers",
    "saves": "goalie_saves",
}
MLB_CODE_META = {
    "M": ("moneyline", "contest", ""),
    "S": ("spread", "team", "runs"),
    "T": ("total", "contest", "runs"),
    "TT": ("total", "team", "runs"),
    "PPSO": ("prop", "player", "strikeouts"),
    "PPHITS": ("prop", "player", "hits"),
    "PPHR": ("prop", "player", "home_runs"),
    "PPRBI": ("prop", "player", "rbis"),
    "PPTB": ("prop", "player", "total_bases"),
    "PPRUNS": ("prop", "player", "runs"),
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("’", "'").split())


def _d(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def canonical_team(value: Any) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return TEAM_ALIASES.get(raw, raw)


def parse_team_event_ticker(ticker: str) -> dict[str, Any] | None:
    m = TEAM_EVENT_RE.fullmatch(str(ticker or "").upper())
    if not m:
        return None
    row = m.groupdict()
    try:
        start = datetime.strptime(row["date"] + row["time"], "%y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return {**row, "away": canonical_team(row["away"]), "home": canonical_team(row["home"]), "start": start}


def _scope_key(scope: Mapping[str, Any]) -> str | None:
    typ = _norm(scope.get("type"))
    if not typ:
        return None
    if typ == "full_contest": return "full_contest"
    ordinal, start, end = scope.get("ordinal"), scope.get("start"), scope.get("end")
    if ordinal is not None: return f"{typ}:{ordinal}"
    if start is not None or end is not None: return f"{typ}:{start or ''}-{end or ''}"
    return typ


def _market_family(market_type: str, subject: str) -> str | None:
    if market_type == "moneyline": return "moneyline"
    if market_type == "spread": return "spread"
    if market_type == "total": return "team_total" if subject == "team" else "game_total"
    if market_type == "prop":
        if subject == "player": return "player_prop"
        if subject == "team": return "team_prop"
        if subject == "contest": return "game_prop"
    return None


def _metric(value: Any) -> str:
    metric = _norm(value).replace(" ", "_")
    return METRIC_ALIASES.get(metric, metric)


def _contract_text(contract: Mapping[str, Any]) -> str:
    return " | ".join(str(contract.get(k) or "") for k in ("ticker", "contractTicker", "instrumentSymbol", "label", "abbreviatedName", "title", "name"))


def _contract_team(contract: Mapping[str, Any], teams: tuple[str, str]) -> str | None:
    # Event portion contains both teams; contract suffix/ticker identifies the side.
    for key in ("ticker", "contractTicker", "instrumentSymbol"):
        raw = str(contract.get(key) or "").upper().strip()
        if not raw:
            continue
        tail = raw.rsplit("-", 1)[-1]
        for team in teams:
            if tail == team or tail.startswith(team):
                return team
    text = _contract_text(contract).upper()
    hits = [team for team in teams if re.search(rf"(?<![A-Z]){re.escape(team)}(?![A-Z])", text)]
    return hits[0] if len(hits) == 1 else None


def _player_subject(contract: Mapping[str, Any]) -> str | None:
    for key in ("subject", "participant", "player", "playerName", "participantName"):
        value = contract.get(key)
        if isinstance(value, Mapping): value = value.get("name") or value.get("label")
        if str(value or "").strip(): return _norm(value)
    label = str(contract.get("label") or contract.get("title") or "").strip()
    if not label: return None
    cleaned = re.sub(r"\s+(?:over|under)\s+[-+]?\d+(?:\.\d+)?(?:\s+.*)?$", "", label, flags=re.I).strip()
    return _norm(cleaned) or None


def _strike(contract: Mapping[str, Any]) -> tuple[str, Decimal | None] | None:
    strike = contract.get("strike") if isinstance(contract.get("strike"), Mapping) else {}
    typ, value = _norm(strike.get("type")), _d(strike.get("value"))
    return (typ, value) if typ else None


def _comparator(strike_type: str) -> str | None:
    return {"over": "GT", "over_or_equal": "GE", "under": "LT", "under_or_equal": "LE"}.get(strike_type)


def _metadata(event: Mapping[str, Any], parsed: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    sports = event.get("sportsMarket") if isinstance(event.get("sportsMarket"), Mapping) else None
    if sports:
        sport = SPORT_MAP.get(_norm(sports.get("sport")))
        market_type, subject = _norm(sports.get("type")), _norm(sports.get("subject"))
        scope = sports.get("scope") if isinstance(sports.get("scope"), Mapping) else {}
        scope_key = _scope_key(scope)
        metric = _metric(sports.get("metric"))
        if sport and market_type and subject and scope_key:
            return sport, market_type, subject, metric
    if str(parsed.get("league") or "").upper() == "MLB":
        meta = MLB_CODE_META.get(str(parsed.get("code") or "").upper())
        if meta:
            market_type, subject, metric = meta
            return "baseball", market_type, subject, metric
    return None


@dataclass(frozen=True)
class GeminiNormalizedContract:
    identity: SportsContractIdentity
    instrument_symbol: str
    contract_label: str
    yes_semantics: str
    strike_type: str | None
    strike_value: Decimal | None


def normalize_event(event: Mapping[str, Any]) -> list[GeminiNormalizedContract]:
    parsed = parse_team_event_ticker(str(event.get("ticker") or event.get("eventTicker") or ""))
    if parsed is None: return []
    meta = _metadata(event, parsed)
    if meta is None: return []
    sport, market_type, subject_kind, metric = meta
    family = _market_family(market_type, subject_kind)
    if family is None: return []
    scope_key = "full_contest"
    sports = event.get("sportsMarket") if isinstance(event.get("sportsMarket"), Mapping) else None
    if sports:
        scope = sports.get("scope") if isinstance(sports.get("scope"), Mapping) else {}
        scope_key = _scope_key(scope) or "full_contest"
    teams = tuple(sorted((parsed["away"], parsed["home"])))
    if len(set(teams)) != 2: return []
    event_id = SportsEventIdentity(
        sport=sport, league=str(parsed["league"]).lower(), participants=teams,
        scheduled_start_ms=int(parsed["start"].timestamp() * 1000),
    )
    audit = RuleAudit(
        official_source="gemini_contract_rules_unverified", overtime="unverified",
        postponement="unverified", stat_corrections="unverified", push_tie="unverified", complete=False,
    )
    out: list[GeminiNormalizedContract] = []
    for contract in event.get("contracts") or []:
        if not isinstance(contract, Mapping): continue
        symbol = str(contract.get("instrumentSymbol") or contract.get("ticker") or contract.get("contractTicker") or "")
        if not symbol: continue
        st = _strike(contract); strike_type, strike_value = st if st else (None, None)
        subject_type, subject, stat, comparator = subject_kind, "", metric, "YES"
        line: Decimal | None = None
        label = str(contract.get("label") or contract.get("abbreviatedName") or "")
        yes_semantics = ""
        if family == "moneyline":
            team = _contract_team(contract, teams)
            if team is None: continue
            subject_type, subject, stat, comparator = "team", team, "", "YES"
            yes_semantics = f"{team}_WIN"
        elif family == "spread":
            team = _contract_team(contract, teams)
            if team is None or strike_type != "spread" or strike_value is None: continue
            subject_type, subject, stat, comparator = "team", team, metric or "runs", "GT"
            # Current live Gemini labels express the strike directly as a margin
            # threshold ("wins by more than 1.5"). Older/documented handicap-like
            # fixtures may encode -1.5, so only invert when label is not explicit.
            if re.search(r"wins?\s+by\s+(?:more|over)", label, re.I):
                line = abs(strike_value)
            else:
                line = -strike_value
            yes_semantics = f"{team}_MARGIN_GT_{format(line, 'f')}"
        elif family in {"game_total", "team_total", "player_prop", "team_prop", "game_prop"}:
            comp = _comparator(strike_type or "")
            if comp is None or strike_value is None: continue
            comparator, line = comp, strike_value
            if family in {"game_total", "game_prop"}: subject_type, subject = "contest", "contest"
            elif family in {"team_total", "team_prop"}:
                team = _contract_team(contract, teams)
                if team is None: continue
                subject_type, subject = "team", team
            else:
                player = _player_subject(contract)
                if not player or not stat: continue
                subject_type, subject = "player", player
            yes_semantics = f"{subject}:{stat}:{comparator}:{format(line, 'f')}"
        else:
            continue
        try:
            identity = SportsContractIdentity(
                venue="gemini", market_id=symbol, event=event_id, market_family=family,
                scope=scope_key, subject_type=subject_type, subject=subject, stat=stat,
                comparator=comparator, line=line, rule_audit=audit,
            )
        except ValueError:
            continue
        out.append(GeminiNormalizedContract(
            identity=identity, instrument_symbol=symbol, contract_label=label,
            yes_semantics=yes_semantics, strike_type=strike_type, strike_value=strike_value,
        ))
    return out


def normalize_events(events: Iterable[Mapping[str, Any]]) -> list[GeminiNormalizedContract]:
    out: list[GeminiNormalizedContract] = []
    for event in events: out.extend(normalize_event(event))
    return out
