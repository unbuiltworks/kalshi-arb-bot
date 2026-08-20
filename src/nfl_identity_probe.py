"""Strict read-only NFL normal-event identity overlap probe.

Builds exact game-line identities for Gemini, Kalshi, and The Odds API sportsbooks.
It deliberately does *not* certify rules, price arbitrage, or place orders.

Kalshi NFL event tickers encode the local game date but not kickoff time. Current
market payloads expose ``expected_expiration_time`` three hours after scheduled
kickoff. This module treats ``expected_expiration_time - 3h`` only as a research
hypothesis and measures it against independent Gemini/Odds kickoff timestamps. It
must not be promoted to executable identity provenance until validated/audited.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
from typing import Any, Iterable, Mapping

import httpx

D = Decimal
GEMINI = "https://api.gemini.com/v1/prediction-markets/events"
KALSHI = "https://external-api.kalshi.com/trade-api/v2/events"
ODDS = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
KALSHI_SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL")
ODDS_SPORTS = ("americanfootball_nfl_preseason", "americanfootball_nfl")

NFL_CODES = {
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
    "HOU","IND","JAX","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG","NYJ",
    "PHI","PIT","SEA","SF","TB","TEN","WAS",
}
NFL_ALIASES = {"JAC":"JAX","WSH":"WAS","LA":"LAR"}
NFL_NAMES = {
    "arizona cardinals":"ARI","atlanta falcons":"ATL","baltimore ravens":"BAL","buffalo bills":"BUF",
    "carolina panthers":"CAR","chicago bears":"CHI","cincinnati bengals":"CIN","cleveland browns":"CLE",
    "dallas cowboys":"DAL","denver broncos":"DEN","detroit lions":"DET","green bay packers":"GB",
    "houston texans":"HOU","indianapolis colts":"IND","jacksonville jaguars":"JAX","kansas city chiefs":"KC",
    "las vegas raiders":"LV","los angeles chargers":"LAC","la chargers":"LAC","los angeles rams":"LAR","la rams":"LAR",
    "miami dolphins":"MIA","minnesota vikings":"MIN","new england patriots":"NE","new orleans saints":"NO",
    "new york giants":"NYG","ny giants":"NYG","new york jets":"NYJ","ny jets":"NYJ",
    "philadelphia eagles":"PHI","pittsburgh steelers":"PIT","seattle seahawks":"SEA","san francisco 49ers":"SF",
    "tampa bay buccaneers":"TB","tennessee titans":"TEN","washington commanders":"WAS",
}
TEAM_EVENT_RE = re.compile(r"^(?:GEMI-)?NFL-(?P<date>\d{6})(?P<time>\d{4})-(?P<away>[A-Z]{2,4})-(?P<home>[A-Z]{2,4})-(?P<kind>[MST])$")
KALSHI_EVENT_RE = re.compile(r"^KXNFL(?P<kind>GAME|SPREAD|TOTAL)-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<pair>[A-Z]{4,7})$")
MONTHS = {m:i for i,m in enumerate(("JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"),1)}


@dataclass(frozen=True)
class NflIdentity:
    venue: str
    market_id: str
    family: str
    teams: tuple[str, str]
    subject: str
    comparator: str
    line: Decimal | None
    start_ms: int
    start_provenance: str

    @property
    def proposition_key(self) -> str:
        line = "" if self.line is None else format(self.line.normalize(), "f")
        return "|".join(("football","nfl",",".join(self.teams),self.family,self.subject,self.comparator,line))


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("’", "'").split())


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def canonical_team(value: Any) -> str | None:
    text = _norm(value).strip(" .?-_")
    if not text:
        return None
    raw = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    raw = NFL_ALIASES.get(raw, raw)
    if raw in NFL_CODES:
        return raw
    if text in NFL_NAMES:
        return NFL_NAMES[text]
    for name, code in sorted(NFL_NAMES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", text):
            return code
    return None


def _teams(away: str, home: str) -> tuple[str, str] | None:
    a, h = canonical_team(away), canonical_team(home)
    if not a or not h or a == h:
        return None
    return tuple(sorted((a,h)))


def _parse_iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _gemini_start(ticker: str) -> tuple[tuple[str,str], datetime, str] | None:
    m = TEAM_EVENT_RE.fullmatch(str(ticker or "").upper())
    if not m:
        return None
    teams = _teams(m.group("away"), m.group("home"))
    if teams is None:
        return None
    try:
        start = datetime.strptime(m.group("date") + m.group("time"), "%y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return teams, start, m.group("kind")


def _gemini_contract_team(contract: Mapping[str, Any]) -> str | None:
    for raw in (contract.get("ticker"), str(contract.get("instrumentSymbol") or "").rsplit("-",1)[-1]):
        token = re.match(r"^(?P<team>[A-Z]{2,4})(?=\d|$)", str(raw or "").upper())
        if token:
            team = canonical_team(token.group("team"))
            if team:
                return team
    return None


def normalize_gemini(events: Iterable[Mapping[str, Any]]) -> list[NflIdentity]:
    out: list[NflIdentity] = []
    spread_re = re.compile(r"(?P<team>.+?)\s+wins?\s+by\s+more\s+than\s+(?P<line>\d+(?:\.\d+)?)\s+points?", re.I)
    total_re = re.compile(r"more\s+than\s+(?P<line>\d+(?:\.\d+)?)\s+points?\s+scored", re.I)
    for event in events:
        parsed = _gemini_start(str(event.get("ticker") or ""))
        if parsed is None:
            continue
        teams, start, kind = parsed
        family = {"M":"moneyline","S":"spread","T":"game_total"}[kind]
        for contract in event.get("contracts") or []:
            if not isinstance(contract, Mapping):
                continue
            symbol = str(contract.get("instrumentSymbol") or "")
            label = str(contract.get("label") or contract.get("abbreviatedName") or "")
            if not symbol:
                continue
            if family == "moneyline":
                team = _gemini_contract_team(contract)
                if team not in teams:
                    continue
                out.append(NflIdentity("gemini",symbol,family,teams,team,"YES",None,int(start.timestamp()*1000),"gemini_ticker_utc"))
            elif family == "spread":
                m = spread_re.search(label)
                team = canonical_team(m.group("team")) if m else None
                line = _d(m.group("line")) if m else None
                if team not in teams or line is None:
                    continue
                out.append(NflIdentity("gemini",symbol,family,teams,team,"GT",abs(line),int(start.timestamp()*1000),"gemini_ticker_utc"))
            else:
                m = total_re.search(label); line = _d(m.group("line")) if m else None
                if line is None:
                    continue
                out.append(NflIdentity("gemini",symbol,family,teams,"contest","GT",abs(line),int(start.timestamp()*1000),"gemini_ticker_utc"))
    return out


def _split_pair(pair: str) -> tuple[str,str] | None:
    raw = str(pair or "").upper()
    candidates = sorted(NFL_CODES | set(NFL_ALIASES), key=lambda x: -len(x))
    matches: list[tuple[str,str]] = []
    for left in candidates:
        if not raw.startswith(left):
            continue
        right = raw[len(left):]
        if right not in candidates:
            continue
        teams = _teams(left,right)
        if teams:
            matches.append(teams)
    unique = sorted(set(matches))
    return unique[0] if len(unique) == 1 else None


def _kalshi_event(event: Mapping[str, Any]) -> tuple[tuple[str,str],str] | None:
    m = KALSHI_EVENT_RE.fullmatch(str(event.get("event_ticker") or "").upper())
    if not m:
        return None
    teams = _split_pair(m.group("pair"))
    if teams is None:
        return None
    return teams, {"GAME":"moneyline","SPREAD":"spread","TOTAL":"game_total"}[m.group("kind")]


def _kalshi_inferred_start(market: Mapping[str, Any]) -> datetime | None:
    expiry = _parse_iso(market.get("expected_expiration_time"))
    return expiry - timedelta(hours=3) if expiry else None


def _kalshi_text(market: Mapping[str, Any]) -> str:
    return " | ".join(str(market.get(k) or "") for k in ("title","subtitle","yes_sub_title","no_sub_title"))


def normalize_kalshi(events: Iterable[Mapping[str, Any]]) -> list[NflIdentity]:
    out: list[NflIdentity] = []
    spread_re = re.compile(r"(?P<team>.+?)\s+wins?\s+by\s+(?:more|over)\s+(?:than\s+)?(?P<line>\d+(?:\.\d+)?)\s+points?", re.I)
    total_re = re.compile(r"(?:over|more\s+than)\s+(?P<line>\d+(?:\.\d+)?)\s+points?", re.I)
    for event in events:
        parsed = _kalshi_event(event)
        if parsed is None:
            continue
        teams, family = parsed
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ticker = str(market.get("ticker") or "")
            start = _kalshi_inferred_start(market)
            text = _kalshi_text(market)
            if not ticker or start is None:
                continue
            if family == "moneyline":
                team = canonical_team(market.get("yes_sub_title") or text)
                if team not in teams:
                    continue
                out.append(NflIdentity("kalshi",ticker,family,teams,team,"YES",None,int(start.timestamp()*1000),"expected_expiration_minus_3h_RESEARCH_ONLY"))
            elif family == "spread":
                m = spread_re.search(text)
                team = canonical_team(m.group("team")) if m else None
                line = _d(m.group("line")) if m else None
                if team not in teams or line is None:
                    continue
                out.append(NflIdentity("kalshi",ticker,family,teams,team,"GT",abs(line),int(start.timestamp()*1000),"expected_expiration_minus_3h_RESEARCH_ONLY"))
            else:
                m = total_re.search(text); line = _d(m.group("line")) if m else None
                if line is None:
                    continue
                out.append(NflIdentity("kalshi",ticker,family,teams,"contest","GT",abs(line),int(start.timestamp()*1000),"expected_expiration_minus_3h_RESEARCH_ONLY"))
    return out


def normalize_sportsbooks(events: Iterable[Mapping[str, Any]]) -> list[NflIdentity]:
    out: list[NflIdentity] = []
    for event in events:
        teams = _teams(str(event.get("away_team") or ""), str(event.get("home_team") or ""))
        start = _parse_iso(event.get("commence_time"))
        event_id = str(event.get("id") or "")
        if teams is None or start is None or not event_id:
            continue
        start_ms = int(start.timestamp()*1000)
        for book in event.get("bookmakers") or []:
            if not isinstance(book, Mapping):
                continue
            b = str(book.get("key") or book.get("title") or "").strip().lower()
            for market in book.get("markets") or []:
                if not isinstance(market, Mapping):
                    continue
                typ = str(market.get("key") or "")
                for idx,row in enumerate(market.get("outcomes") or []):
                    if not isinstance(row, Mapping):
                        continue
                    if typ == "h2h":
                        team = canonical_team(row.get("name"))
                        if team in teams:
                            out.append(NflIdentity(f"sportsbook:{b}",f"{event_id}:h2h:{idx}","moneyline",teams,team,"YES",None,start_ms,"odds_api_commence_time"))
                    elif typ == "spreads":
                        team = canonical_team(row.get("name")); point = _d(row.get("point"))
                        if team in teams and point is not None:
                            # Team +P means team margin > -P on half-point lines.
                            out.append(NflIdentity(f"sportsbook:{b}",f"{event_id}:spread:{idx}","spread",teams,team,"GT",-point,start_ms,"odds_api_commence_time"))
                    elif typ == "totals" and str(row.get("name") or "").lower() == "over":
                        point = _d(row.get("point"))
                        if point is not None:
                            out.append(NflIdentity(f"sportsbook:{b}",f"{event_id}:total:{idx}","game_total",teams,"contest","GT",point,start_ms,"odds_api_commence_time"))
    return out


async def fetch_gemini(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await http.get(GEMINI, params={"status":"active","sport":"american_football","limit":500,"offset":0})
    response.raise_for_status(); body = response.json()
    rows = [dict(x) for x in body.get("data") or body.get("events") or [] if isinstance(x, Mapping)]
    return [x for x in rows if "NFL" in str(x.get("ticker") or "").upper()]


async def fetch_kalshi(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    for series in KALSHI_SERIES:
        for attempt in range(5):
            response = await http.get(KALSHI, params={"limit":200,"status":"open","series_ticker":series,"with_nested_markets":"true"})
            if response.status_code != 429:
                response.raise_for_status(); body = response.json()
                all_rows.extend(dict(x) for x in body.get("events") or [] if isinstance(x, Mapping))
                break
            await asyncio.sleep(min(4.0,0.5*(2**attempt)))
        else:
            response.raise_for_status()
        await asyncio.sleep(0.12)
    return all_rows


async def fetch_sportsbooks(http: httpx.AsyncClient, api_key: str) -> tuple[list[dict[str, Any]],dict[str,Any]]:
    rows: list[dict[str, Any]] = []
    quota: dict[str,Any] = {}
    for sport in ODDS_SPORTS:
        response = await http.get(ODDS.format(sport=sport), params={"apiKey":api_key,"regions":"us","markets":"h2h,spreads,totals","oddsFormat":"decimal","dateFormat":"iso"})
        if response.status_code == 404:
            continue
        response.raise_for_status(); body = response.json()
        rows.extend(dict(x) for x in body if isinstance(x, Mapping))
        quota = {"remaining":response.headers.get("x-requests-remaining",""),"used":response.headers.get("x-requests-used",""),"last_cost":response.headers.get("x-requests-last","")}
    return rows, quota


def summarize(g: list[NflIdentity], k: list[NflIdentity], s: list[NflIdentity], quota: dict[str,Any]) -> dict[str,Any]:
    rows = g+k+s
    groups: dict[str,list[NflIdentity]] = defaultdict(list)
    for row in rows:
        groups[row.proposition_key].append(row)
    clusters=[]; kalshi_cross_checks=[]
    for key,members in groups.items():
        venue_families={"gemini" if x.venue=="gemini" else "kalshi" if x.venue=="kalshi" else "sportsbook" for x in members}
        if len(venue_families)<2:
            continue
        starts=[x.start_ms for x in members]
        skew=max(starts)-min(starts)
        cluster={
            "key":key,"family":members[0].family,"venues":sorted({x.venue for x in members}),"start_skew_ms":skew,
            "members":[{"venue":x.venue,"market_id":x.market_id,"start_ms":x.start_ms,"start_provenance":x.start_provenance} for x in members[:30]],
        }
        clusters.append(cluster)
        if any(x.venue=="kalshi" for x in members) and any(x.venue!="kalshi" for x in members):
            kalshi_cross_checks.append(skew)
    clusters.sort(key=lambda x:(x["start_skew_ms"],x["family"],x["key"]))
    exact=[x for x in clusters if x["start_skew_ms"]<=5*60*1000]
    return {
        "status":"NFL_IDENTITY_PROBE",
        "normalized":{"gemini":len(g),"kalshi":len(k),"sportsbook":len(s)},
        "clusters_any_start":len(clusters),"clusters_within_5m":len(exact),
        "exact_by_family":dict(Counter(x["family"] for x in exact)),
        "kalshi_start_hypothesis":{"cross_checks":len(kalshi_cross_checks),"zero_skew":sum(x==0 for x in kalshi_cross_checks),"within_5m":sum(x<=300000 for x in kalshi_cross_checks),"max_skew_ms":max(kalshi_cross_checks,default=None),"provenance":"expected_expiration_minus_3h_RESEARCH_ONLY"},
        "top_exact":exact[:60],"quota":quota,
        "rule_gate":"NFL_RULE_FINGERPRINT_REQUIRED_BEFORE_ALERT_OR_EXECUTION",
        "read_only":True,"order_entry":False,
    }


async def run() -> dict[str,Any]:
    key=os.getenv("THE_ODDS_API_KEY","").strip()
    if not key: raise RuntimeError("THE_ODDS_API_KEY required")
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini,kalshi,sportsbook_result=await asyncio.gather(fetch_gemini(http),fetch_kalshi(http),fetch_sportsbooks(http,key))
    sportsbook,quota=sportsbook_result
    return summarize(normalize_gemini(gemini),normalize_kalshi(kalshi),normalize_sportsbooks(sportsbook),quota)


def main()->None:
    print(json.dumps(asyncio.run(run()),separators=(",",":"),sort_keys=True,default=str),flush=True)


if __name__=="__main__":
    main()
