"""Strict read-only Gemini <-> Kalshi NFL q=1 counterpart and price scan.

Safety properties:
- exact NFL team-code parsing only; no fuzzy matching;
- exact local game date + participant pair + market family matching;
- Kalshi expected-expiration must sit 2-6 hours after Gemini kickoff;
- only full-contest Gemini contracts and half-point Kalshi spread/total lines;
- q=1 fees included on both venues;
- no authentication beyond public data, no order entry, and rule audit remains open.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import re
import time
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import httpx

from src.gemini_sports_adapter import canonical_team, normalize_events as normalize_gemini_events
from src.mlb_game_side_normalization import NormalizedSide
from src.strict_mlb_prop_snapshot import KALSHI, kalshi_top_book
from src.three_venue_pricing_core import gemini_contract_book, make_priced_quote
from src.universal_prediction_quant import find_cross_venue_locks
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity

D = Decimal
ET = ZoneInfo("America/New_York")
logger = logging.getLogger("kalshi_arb.sports_nfl_gk_price_scan")
GEMINI = "https://api.gemini.com"
SERIES_FAMILY = {
    "KXNFLGAME": "moneyline",
    "KXNFLSPREAD": "spread",
    "KXNFLTOTAL": "game_total",
}
RAW_NFL_CODES = frozenset({
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
    "HOU","IND","JAX","KC","LAC","LAR","LV","MIA","MIN","NE","NO","NYG","NYJ",
    "PHI","PIT","SEA","SF","TB","TEN","WAS",
})
EVENT_RE = re.compile(r"^KXNFL(?:GAME|SPREAD|TOTAL)-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<teams>[A-Z]+)$")
SPREAD_RE = re.compile(r"^(?P<team>.+?) wins by over (?P<line>\d+(?:\.5)) points\?$", re.I)
TOTAL_RE = re.compile(r"^Will there be over (?P<line>\d+(?:\.5)) points scored\?$", re.I)
MIN_EXPIRY_DELTA_S = 2 * 3600
MAX_EXPIRY_DELTA_S = 6 * 3600
MAX_QUOTE_SKEW_MS = 30_000
MIN_FLOOR = D("0.03")


@dataclass(frozen=True)
class KalshiCandidate:
    side: NormalizedSide
    series: str
    expected_expiration: datetime
    matched_by: str


def _d(value: Any) -> D | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _half(value: D) -> bool:
    twice = abs(value) * D("2")
    return twice == twice.to_integral_value() and int(twice) % 2 == 1


def _split_teams(compact: str) -> tuple[str, str] | None:
    matches: set[tuple[str, str]] = set()
    for left in sorted(RAW_NFL_CODES, key=lambda x: (-len(x), x)):
        if not compact.startswith(left):
            continue
        right = compact[len(left):]
        if right not in RAW_NFL_CODES:
            continue
        a, b = canonical_team(left), canonical_team(right)
        if not a or not b or a == b:
            continue
        matches.add(tuple(sorted((a, b))))
    return next(iter(matches)) if len(matches) == 1 else None


def _kalshi_event_shape(event: Mapping[str, Any]) -> tuple[tuple[str, str], datetime.date] | None:
    ticker = str(event.get("event_ticker") or "").upper()
    m = EVENT_RE.fullmatch(ticker)
    if m is None:
        return None
    teams = _split_teams(m.group("teams"))
    if teams is None:
        return None
    try:
        date = datetime.strptime(m.group("date"), "%y%b%d").date()
    except ValueError:
        return None
    return teams, date


def _rule() -> RuleAudit:
    return RuleAudit(
        official_source="kalshi_public_nfl_rules_captured_unverified",
        overtime="unverified",
        postponement="unverified",
        stat_corrections="unverified",
        push_tie="unverified",
        complete=False,
    )


def _gemini_rows(events: Iterable[Mapping[str, Any]]) -> tuple[list[NormalizedSide], dict[str, Mapping[str, Any]]]:
    normalized: list[NormalizedSide] = []
    contracts: dict[str, Mapping[str, Any]] = {}
    for event in events:
        ticker = str(event.get("ticker") or event.get("eventTicker") or "").upper()
        if not ticker.startswith("NFL-"):
            continue
        for contract in event.get("contracts") or []:
            if isinstance(contract, Mapping):
                symbol = str(contract.get("instrumentSymbol") or contract.get("ticker") or "")
                if symbol:
                    contracts[symbol] = contract
        for row in normalize_gemini_events([event]):
            ident = row.identity
            if ident.event.sport != "football" or ident.event.league != "nfl" or ident.scope != "full_contest":
                continue
            if ident.market_family not in {"moneyline", "spread", "game_total"}:
                continue
            if ident.market_family in {"spread", "game_total"} and (ident.line is None or not _half(ident.line)):
                continue
            normalized.append(NormalizedSide(ident, row.instrument_symbol, "YES", row.contract_label))
    return normalized, contracts


def _gemini_match_index(rows: Iterable[NormalizedSide]) -> dict[tuple[tuple[str, str], object, str], set[int]]:
    out: dict[tuple[tuple[str, str], object, str], set[int]] = defaultdict(set)
    for row in rows:
        ident = row.identity
        local_date = datetime.fromtimestamp(ident.event.scheduled_start_ms / 1000, tz=timezone.utc).astimezone(ET).date()
        out[(tuple(ident.event.participants), local_date, ident.market_family)].add(ident.event.scheduled_start_ms)
    return out


def _market_team_from_ticker(ticker: str, teams: tuple[str, str]) -> str | None:
    tail = ticker.rsplit("-", 1)[-1]
    m = re.match(r"^(?P<team>[A-Z]{2,3})(?=\d|$)", tail)
    if not m:
        return None
    team = canonical_team(m.group("team"))
    return team if team in teams else None


def _kalshi_rows(events_by_series: Mapping[str, list[dict[str, Any]]], gemini_index: Mapping[tuple[tuple[str, str], object, str], set[int]]) -> tuple[list[KalshiCandidate], Counter[str]]:
    out: list[KalshiCandidate] = []
    stats: Counter[str] = Counter()
    for series, events in events_by_series.items():
        family = SERIES_FAMILY[series]
        for event in events:
            shape = _kalshi_event_shape(event)
            if shape is None:
                stats["event_parse_reject"] += 1
                continue
            teams, local_date = shape
            starts = gemini_index.get((teams, local_date, family), set())
            if len(starts) != 1:
                stats["gemini_unique_event_reject"] += 1
                continue
            start_ms = next(iter(starts))
            event_id = SportsEventIdentity(
                sport="football", league="nfl", participants=teams, scheduled_start_ms=start_ms,
            )
            for market in event.get("markets") or []:
                if not isinstance(market, Mapping) or str(market.get("status") or "").lower() != "active":
                    continue
                ticker = str(market.get("ticker") or "")
                expiry = _parse_dt(market.get("expected_expiration_time"))
                if not ticker or expiry is None:
                    stats["missing_ticker_or_expiry"] += 1
                    continue
                delta_s = expiry.timestamp() - start_ms / 1000
                if not MIN_EXPIRY_DELTA_S <= delta_s <= MAX_EXPIRY_DELTA_S:
                    stats["expiry_plausibility_reject"] += 1
                    continue
                subject_type = "contest"; subject = "contest"; stat = "points"; comparator = "GT"; line: D | None = None
                if family == "moneyline":
                    team = _market_team_from_ticker(ticker, teams)
                    if team is None:
                        stats["moneyline_team_reject"] += 1
                        continue
                    subject_type, subject, stat, comparator = "team", team, "", "YES"
                elif family == "spread":
                    team = _market_team_from_ticker(ticker, teams)
                    m = SPREAD_RE.fullmatch(str(market.get("title") or ""))
                    if team is None or m is None:
                        stats["spread_parse_reject"] += 1
                        continue
                    line = _d(m.group("line"))
                    if line is None or not _half(line):
                        stats["spread_line_reject"] += 1
                        continue
                    subject_type, subject = "team", team
                elif family == "game_total":
                    m = TOTAL_RE.fullmatch(str(market.get("title") or ""))
                    if m is None:
                        stats["total_parse_reject"] += 1
                        continue
                    line = _d(m.group("line"))
                    if line is None or not _half(line):
                        stats["total_line_reject"] += 1
                        continue
                try:
                    ident = SportsContractIdentity(
                        venue="kalshi", market_id=ticker, event=event_id, market_family=family,
                        scope="full_contest", subject_type=subject_type, subject=subject, stat=stat,
                        comparator=comparator, line=line, rule_audit=_rule(),
                    )
                except ValueError:
                    stats["identity_reject"] += 1
                    continue
                out.append(KalshiCandidate(
                    side=NormalizedSide(ident, ticker, "YES", str(market.get("title") or ticker)),
                    series=series, expected_expiration=expiry,
                    matched_by="EXACT_TEAMS_LOCAL_DATE_FAMILY_UNIQUE_GEMINI_START_PLUS_EXPIRY_PLAUSIBILITY",
                ))
                stats[f"accepted_{family}"] += 1
    return out, stats


async def _get(http: httpx.AsyncClient, url: str, stats: Counter[str], **kwargs: Any) -> tuple[dict[str, Any] | None, int]:
    last: httpx.Response | None = None
    for attempt in range(5):
        stats["requests"] += 1
        try:
            r = await http.get(url, **kwargs); last = r
            received = int(time.time() * 1000)
            if r.status_code == 429:
                stats["http_429"] += 1
                retry_after = r.headers.get("retry-after", "").strip()
                try: header = float(retry_after) if retry_after else 0.0
                except ValueError: header = 0.0
                if attempt + 1 < 5:
                    stats["retries"] += 1
                    await asyncio.sleep(max(header, min(0.35 * (2 ** attempt), 3.0)))
                    continue
            r.raise_for_status(); body = r.json() if r.content else {}
            stats["success"] += 1
            return (body if isinstance(body, dict) else {}), received
        except Exception:
            stats["failures"] += 1
            if attempt + 1 < 5:
                stats["retries"] += 1; await asyncio.sleep(0.20 * (attempt + 1)); continue
            return None, int(time.time() * 1000)
    if last is not None:
        stats["failures"] += 1
    return None, int(time.time() * 1000)


async def run_once() -> dict[str, Any]:
    fetch_health: Counter[str] = Counter()
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=12.0) as http:
        gbody, gemini_received = await _get(http, f"{GEMINI}/v1/prediction-markets/events", fetch_health, params={
            "status": "active", "sport": "american_football", "limit": 500, "offset": 0,
        })
        gemini_events = [dict(x) for x in ((gbody or {}).get("data") or (gbody or {}).get("events") or []) if isinstance(x, Mapping)]
        gemini_rows, gcontracts = _gemini_rows(gemini_events)
        gindex = _gemini_match_index(gemini_rows)

        events_by_series: dict[str, list[dict[str, Any]]] = {}
        for series in SERIES_FAMILY:
            body, _ = await _get(http, f"{KALSHI}/events", fetch_health, params={
                "limit": 200, "status": "open", "series_ticker": series, "with_nested_markets": "true",
            })
            events_by_series[series] = [dict(x) for x in ((body or {}).get("events") or []) if isinstance(x, Mapping)]
            await asyncio.sleep(0.30)
        kalshi_rows, match_stats = _kalshi_rows(events_by_series, gindex)

        gemini_by_key: dict[str, list[NormalizedSide]] = defaultdict(list)
        kalshi_by_key: dict[str, list[KalshiCandidate]] = defaultdict(list)
        for row in gemini_rows: gemini_by_key[row.contract_key].append(row)
        for row in kalshi_rows: kalshi_by_key[row.side.contract_key].append(row)
        keys = sorted(set(gemini_by_key) & set(kalshi_by_key))
        wanted_g = [row for key in keys for row in gemini_by_key[key]]
        wanted_k = [row for key in keys for row in kalshi_by_key[key]]

        fee_multiplier: dict[str, D] = {}
        for series in SERIES_FAMILY:
            body, _ = await _get(http, f"{KALSHI}/series/{series}", fetch_health)
            meta = (body or {}).get("series") if isinstance((body or {}).get("series"), Mapping) else {}
            multiplier = _d(meta.get("fee_multiplier"))
            fee_type = str(meta.get("fee_type") or "")
            if multiplier is not None and D("0") <= multiplier <= D("100") and fee_type in {"quadratic", "quadratic_with_maker_fees"}:
                fee_multiplier[series] = multiplier

        sem = asyncio.Semaphore(3)
        book_health: Counter[str] = Counter()
        async def one(candidate: KalshiCandidate):
            async with sem:
                body, received = await _get(http, f"{KALSHI}/markets/{candidate.side.venue_market_id}/orderbook", book_health, params={"depth": 1})
                return candidate, body, received
        book_results = await asyncio.gather(*(one(row) for row in wanted_k))

    priced = []
    for row in wanted_g:
        book = gemini_contract_book(gcontracts.get(row.venue_market_id, {}))
        if book is None:
            continue
        priced_row = make_priced_quote(
            row, book, received_ms=gemini_received,
            depth_source="gemini_active_event_best_bid_ask_q1", category="sports_nfl_game",
        )
        if priced_row is not None: priced.append(priced_row)
    kalshi_priced = 0
    for candidate, body, received in book_results:
        book = kalshi_top_book(body or {}) if body is not None else None
        if book is None:
            book_health["unparseable_book"] += 1
            continue
        priced_row = make_priced_quote(
            candidate.side, book, received_ms=received,
            kalshi_fee_multiplier=fee_multiplier.get(candidate.series),
            depth_source="kalshi_public_depth1", category="sports_nfl_game",
        )
        if priced_row is not None:
            priced.append(priced_row); kalshi_priced += 1

    positive = find_cross_venue_locks(
        [row.quote for row in priced], min_unit_profit=D("0"), max_quote_skew_ms=MAX_QUOTE_SKEW_MS,
        min_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
    )
    floor = [row for row in positive if row.unit_profit >= MIN_FLOOR]
    by_family: Counter[str] = Counter()
    key_identity: dict[str, SportsContractIdentity] = {}
    for row in wanted_g: key_identity.setdefault(row.contract_key, row.identity)
    for key in keys:
        ident = key_identity.get(key)
        if ident is not None: by_family[ident.market_family] += 1

    result = {
        "status": "SPORTS_NFL_GK_Q1_PRICE_SCAN",
        "gemini_football_events": len(gemini_events),
        "gemini_nfl_normalized_contracts": len(gemini_rows),
        "kalshi_events": {k: len(v) for k, v in events_by_series.items()},
        "kalshi_normalized_candidates": len(kalshi_rows),
        "match_stats": dict(match_stats),
        "exact_counterpart_keys": len(keys),
        "exact_counterpart_keys_by_family": dict(by_family),
        "priced_quotes": len(priced),
        "priced_quotes_by_venue": dict(Counter(row.quote.venue for row in priced)),
        "kalshi_books_requested": len(wanted_k),
        "kalshi_books_priced": kalshi_priced,
        "fetch_health": dict(fetch_health),
        "book_health": dict(book_health),
        "positive_q1_locks": len(positive),
        "floor_3c_q1_locks": len(floor),
        "top_positive_locks": [row.as_dict() for row in positive[:30]],
        "top_floor_3c_locks": [row.as_dict() for row in floor[:30]],
        "quote_skew_limit_ms": MAX_QUOTE_SKEW_MS,
        "identity_confidence": "NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        "rule_gate": "NFL_GEMINI_KALSHI_FULL_RULE_FINGERPRINT_REQUIRED_BEFORE_EXECUTION",
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
        "cycle_ms": round((time.monotonic() - started) * 1000, 3),
    }
    logger.warning("SPORTS_NFL_GK_Q1_PRICE_SCAN %s", json.dumps(result, separators=(",", ":"), default=str))
    return result
