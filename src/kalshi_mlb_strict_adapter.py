"""Strict Kalshi MLB normalization for the three-venue sports router.

The legacy research normalizer used combined event/title text to infer a moneyline
team. Live Kalshi payloads make that unsafe because the title names both teams. For
moneylines this adapter keys the YES proposition from ``yes_sub_title`` (with the
market-ticker suffix only as corroborating fallback), then derives the NO predicate
as the opponent. Spread/total rows reuse the Eastern-time-corrected normalizer.

All rows remain rule-unverified and research-only. No authentication or order entry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern, ticker_start_utc
from src.mlb_game_side_normalization import (
    NormalizedSide,
    _kalshi_teams,
    canonical_team,
)
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _moneyline_rule() -> RuleAudit:
    return RuleAudit(
        official_source="kalshi_rules_unverified",
        overtime="not_applicable_baseball_extra_innings_unverified",
        postponement="unverified",
        stat_corrections="unverified",
        push_tie="unverified",
        complete=False,
    )


def _moneyline_team(market: Mapping[str, Any], teams: tuple[str, str]) -> str | None:
    # Primary semantic field observed on live KXMLBGAME payloads.
    team = canonical_team(market.get("yes_sub_title"))
    if team in teams:
        return team

    # Corroborating fallback: KXMLBGAME-...-BAL / ...-TB. Never use the event title,
    # which contains both teams and caused a dangerous side flip in research.
    ticker = str(market.get("ticker") or "")
    suffix = ticker.rsplit("-", 1)[-1] if ticker else ""
    team = canonical_team(suffix)
    if team in teams:
        return team
    return None


def _other(team: str, teams: tuple[str, str]) -> str:
    return teams[1] if teams[0] == team else teams[0]


def _moneyline_rows(event: Mapping[str, Any]) -> list[NormalizedSide]:
    if str(event.get("series_ticker") or "") != "KXMLBGAME":
        return []
    teams = _kalshi_teams(event)
    if teams is None or len(set(teams)) != 2:
        return []
    teams = tuple(sorted(teams))
    start = ticker_start_utc(str(event.get("event_ticker") or ""))
    if start is None:
        return []

    out: list[NormalizedSide] = []
    for market in event.get("markets") or []:
        if not isinstance(market, Mapping):
            continue
        ticker = str(market.get("ticker") or "")
        if not ticker:
            continue
        yes_team = _moneyline_team(market, teams)
        if yes_team is None:
            continue

        expected = _parse(market.get("expected_expiration_time"))
        if expected is not None:
            hours = (expected - start).total_seconds() / 3600
            if not 2 <= hours <= 6:
                continue

        no_team = _other(yes_team, teams)
        event_id = SportsEventIdentity(
            sport="baseball",
            league="mlb",
            participants=teams,
            scheduled_start_ms=int(start.timestamp() * 1000),
        )
        audit = _moneyline_rule()
        try:
            yes_identity = SportsContractIdentity(
                venue="kalshi",
                market_id=f"{ticker}#YES",
                event=event_id,
                market_family="moneyline",
                scope="full_contest",
                subject_type="team",
                subject=yes_team,
                stat="",
                comparator="YES",
                line=None,
                rule_audit=audit,
            )
            no_identity = SportsContractIdentity(
                venue="kalshi",
                market_id=f"{ticker}#NO",
                event=event_id,
                market_family="moneyline",
                scope="full_contest",
                subject_type="team",
                subject=no_team,
                stat="",
                comparator="YES",
                line=None,
                rule_audit=audit,
            )
        except ValueError:
            continue

        label = str(market.get("yes_sub_title") or ticker)
        out.extend((
            NormalizedSide(yes_identity, ticker, "YES", label),
            NormalizedSide(no_identity, ticker, "NO", label),
        ))
    return out


def normalize_kalshi_mlb_strict(events: Iterable[Mapping[str, Any]]) -> list[NormalizedSide]:
    events = list(events)
    # Reuse the existing corrected spread/total path but discard every legacy
    # moneyline row so none of its broad-title team inference can leak through.
    non_moneyline = [
        row for row in normalize_kalshi_mlb_eastern(events)
        if row.identity.market_family != "moneyline"
    ]
    moneyline: list[NormalizedSide] = []
    for event in events:
        moneyline.extend(_moneyline_rows(event))
    return non_moneyline + moneyline
