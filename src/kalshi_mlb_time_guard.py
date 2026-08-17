"""Correct Kalshi MLB scheduled starts using the Eastern-time event ticker clock."""
from __future__ import annotations
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src.mlb_game_side_normalization import NormalizedSide, normalize_kalshi_mlb
from src.universal_sports_identity import SportsContractIdentity, SportsEventIdentity

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


def normalize_kalshi_mlb_eastern(events: Iterable[Mapping[str, Any]]) -> list[NormalizedSide]:
    events=list(events)
    market_meta: dict[str, tuple[datetime, datetime | None]]={}
    for event in events:
        start=ticker_start_utc(str(event.get("event_ticker") or ""))
        if start is None:
            continue
        for market in event.get("markets") or []:
            if not isinstance(market,Mapping) or not market.get("ticker"):
                continue
            market_meta[str(market["ticker"])]=(start,_parse(market.get("expected_expiration_time")))

    out=[]
    for row in normalize_kalshi_mlb(events):
        meta=market_meta.get(row.venue_market_id)
        if meta is None:
            continue
        start, expected=meta
        # Current MLB contracts expect expiration about three hours after first
        # pitch. Keep a broad 2-6h corroboration window and fail closed outside it.
        if expected is not None:
            hours=(expected-start).total_seconds()/3600
            if not 2 <= hours <= 6:
                continue
        old=row.identity
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
    return out
