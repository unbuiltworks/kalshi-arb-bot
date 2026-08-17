"""Correct Kalshi MLB scheduled starts and fail-closed moneyline side identity."""
from __future__ import annotations
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.mlb_game_side_normalization import NormalizedSide, _kalshi_teams, canonical_team, normalize_kalshi_mlb
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity

_MONTHS={name:i for i,name in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"),1)}
_RE=re.compile(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})(?P<mm>\d{2})")
_ET=ZoneInfo("America/New_York")


def ticker_start_utc(ticker: str) -> datetime | None:
    m=_RE.search(str(ticker or "").upper())
    if not m or m.group("mon") not in _MONTHS:
        return None
    try:
        local=datetime(2000+int(m.group("yy")),_MONTHS[m.group("mon")],int(m.group("dd")),int(m.group("hh")),int(m.group("mm")),tzinfo=_ET)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except (ValueError,TypeError):
        return None


def _valid_expiry(start: datetime, expected: datetime | None) -> bool:
    if expected is None:
        return True
    hours=(expected-start).total_seconds()/3600
    return 2 <= hours <= 6


def _audit() -> RuleAudit:
    return RuleAudit(
        official_source="kalshi_rules_unverified",
        overtime="not_applicable_baseball_extra_innings_unverified",
        postponement="unverified",
        stat_corrections="unverified",
        push_tie="unverified",
        complete=False,
    )


def _strict_moneylines(events: list[Mapping[str, Any]]) -> list[NormalizedSide]:
    out: list[NormalizedSide]=[]
    for event in events:
        if str(event.get("series_ticker") or "")!="KXMLBGAME":
            continue
        teams=_kalshi_teams(event)
        start=ticker_start_utc(str(event.get("event_ticker") or ""))
        if teams is None or start is None or len(set(teams))!=2:
            continue
        teams=tuple(sorted(teams))
        event_id=SportsEventIdentity(
            sport="baseball",league="mlb",participants=teams,
            scheduled_start_ms=int(start.timestamp()*1000),
        )
        for market in event.get("markets") or []:
            if not isinstance(market,Mapping):
                continue
            ticker=str(market.get("ticker") or "")
            if not ticker or not _valid_expiry(start,_parse(market.get("expected_expiration_time"))):
                continue
            yes_team=canonical_team(market.get("yes_sub_title"))
            if yes_team not in teams:
                yes_team=canonical_team(ticker.rsplit("-",1)[-1])
            if yes_team not in teams:
                continue
            no_team=teams[1] if teams[0]==yes_team else teams[0]
            label=str(market.get("yes_sub_title") or ticker)
            try:
                yes_identity=SportsContractIdentity(
                    venue="kalshi",market_id=f"{ticker}#YES",event=event_id,
                    market_family="moneyline",scope="full_contest",subject_type="team",
                    subject=yes_team,stat="",comparator="YES",line=None,rule_audit=_audit(),
                )
                no_identity=SportsContractIdentity(
                    venue="kalshi",market_id=f"{ticker}#NO",event=event_id,
                    market_family="moneyline",scope="full_contest",subject_type="team",
                    subject=no_team,stat="",comparator="YES",line=None,rule_audit=_audit(),
                )
            except ValueError:
                continue
            out.extend((
                NormalizedSide(yes_identity,ticker,"YES",label),
                NormalizedSide(no_identity,ticker,"NO",label),
            ))
    return out


def normalize_kalshi_mlb_eastern(events: Iterable[Mapping[str, Any]]) -> list[NormalizedSide]:
    events=list(events)
    market_meta: dict[str, tuple[datetime, datetime | None]]={}
    for event in events:
        start=ticker_start_utc(str(event.get("event_ticker") or ""))
        if start is None:
            continue
        for market in event.get("markets") or []:
            if isinstance(market,Mapping) and market.get("ticker"):
                market_meta[str(market["ticker"])]=(start,_parse(market.get("expected_expiration_time")))

    out: list[NormalizedSide]=[]
    for row in normalize_kalshi_mlb(events):
        old=row.identity
        # Moneylines are rebuilt independently below so broad event-title parsing
        # can never choose the wrong team.
        if old.market_family=="moneyline":
            continue
        meta=market_meta.get(row.venue_market_id)
        if meta is None:
            continue
        start,expected=meta
        if not _valid_expiry(start,expected):
            continue
        event_id=SportsEventIdentity(
            sport=old.event.sport,league=old.event.league,participants=old.event.participants,
            scheduled_start_ms=int(start.timestamp()*1000),
        )
        identity=SportsContractIdentity(
            venue=old.venue,market_id=old.market_id,event=event_id,
            market_family=old.market_family,scope=old.scope,subject_type=old.subject_type,
            subject=old.subject,stat=old.stat,comparator=old.comparator,line=old.line,
            rule_audit=old.rule_audit,
        )
        out.append(NormalizedSide(identity,row.venue_market_id,row.side,row.source_label))
    out.extend(_strict_moneylines(events))
    return out
