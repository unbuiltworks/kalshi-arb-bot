"""Read-only historical backtest: Kalshi MLB moneylines vs sharp sportsbook fair value.

Isolation rules:
- Research only. No order transport and no portfolio endpoints.
- Does not import or alter census/live executors.
- Uses one precommitted daily historical snapshot (default 16:00 UTC) to control
  The Odds API historical quota. This is a sampled historical backtest, not an
  exhaustive reconstruction of every 5-minute sportsbook snapshot.
- Sharp source priority is Pinnacle when present, otherwise the mean of
  multiplicatively-devigged FanDuel and DraftKings probabilities.
- At most one signal per physical game/team/snapshot after semantic dedupe.

The script combines Kalshi archived and recent settled KXMLBGAME markets, matches
physical games by canonical teams + <=5 minute scheduled-start skew, reconstructs
Kalshi executable side asks from 1-minute candlesticks, applies the general Kalshi
taker fee formula with cent rounding at a representative ~$10 whole-contract order,
and reports precommitted edge buckets 1/2/3/5/8c+.

Nothing in this module may be counted as realized P&L.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import json
import logging
import math
import os
import statistics
from typing import Any, Iterable, Mapping, Sequence

import httpx

from src.mlb_game_side_normalization import canonical_team, normalize_kalshi_mlb

logger = logging.getLogger("kalshi_arb.kalshi_mlb_sharp_ev_backtest")

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
ODDS = "https://api.the-odds-api.com/v4"
SERIES = "KXMLBGAME"
SHARP_BOOKS = ("pinnacle", "fanduel", "draftkings")
EDGE_BUCKETS = (Decimal("0.01"), Decimal("0.02"), Decimal("0.03"), Decimal("0.05"), Decimal("0.08"))
MAX_START_SKEW_S = 5 * 60
KALSHI_GENERAL_TAKER_RATE = Decimal("0.07")
CENT = Decimal("0.01")
D = Decimal


@dataclass(frozen=True)
class KalshiSide:
    ticker: str
    source_tier: str
    event_key: tuple[str, str]
    start_ts: int
    team: str
    side: str
    won: bool


@dataclass(frozen=True)
class SharpEvent:
    event_id: str
    event_key: tuple[str, str]
    start_ts: int
    fair_by_team: Mapping[str, Decimal]
    source: str


@dataclass(frozen=True)
class Observation:
    snapshot_ts: int
    event_key: tuple[str, str]
    start_ts: int
    ticker: str
    team: str
    side: str
    sharp_source: str
    sharp_p: Decimal
    ask: Decimal
    contracts: int
    total_fee: Decimal
    fee_per_contract: Decimal
    edge: Decimal
    won: bool
    pnl_per_contract: Decimal
    order_pnl: Decimal
    order_stake: Decimal
    quote_age_s: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_ts": self.snapshot_ts,
            "snapshot_time": datetime.fromtimestamp(self.snapshot_ts, tz=timezone.utc).isoformat(),
            "event_key": list(self.event_key),
            "start_time": datetime.fromtimestamp(self.start_ts, tz=timezone.utc).isoformat(),
            "ticker": self.ticker,
            "team": self.team,
            "side": self.side,
            "sharp_source": self.sharp_source,
            "sharp_p": str(self.sharp_p),
            "kalshi_ask": str(self.ask),
            "contracts": self.contracts,
            "total_fee": str(self.total_fee),
            "fee_per_contract": str(self.fee_per_contract),
            "edge": str(self.edge),
            "won": self.won,
            "pnl_per_contract": str(self.pnl_per_contract),
            "order_pnl": str(self.order_pnl),
            "order_stake": str(self.order_stake),
            "quote_age_s": self.quote_age_s,
        }


def _dec(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _iso_ts(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
    except Exception:
        return None


def _ceil_cent(value: Decimal) -> Decimal:
    if value <= 0:
        return D("0")
    return (value / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


def taker_fee(price: Decimal, contracts: int) -> Decimal:
    """General Kalshi taker fee: ceil-cent(0.07 * C * P * (1-P))."""
    c = max(1, int(contracts))
    return _ceil_cent(KALSHI_GENERAL_TAKER_RATE * D(c) * price * (D("1") - price))


def representative_contracts(price: Decimal, target_stake: Decimal) -> int:
    """Conservative whole-contract sizing near the requested stake before fees."""
    if price <= 0:
        return 1
    return max(1, int((target_stake / price).to_integral_value(rounding="ROUND_FLOOR")))


def _american_to_prob(price: Any) -> Decimal | None:
    x = _dec(price)
    if x is None or x == 0:
        return None
    if x > 0:
        return D("100") / (x + D("100"))
    return (-x) / ((-x) + D("100"))


def _decimal_to_prob(price: Any) -> Decimal | None:
    x = _dec(price)
    if x is None or x <= 1:
        return None
    return D("1") / x


def _devig_two_way(raw: Mapping[str, Decimal]) -> dict[str, Decimal]:
    total = sum(raw.values(), D("0"))
    if total <= 0:
        return {}
    return {team: p / total for team, p in raw.items()}


def _book_fair(book: Mapping[str, Any], event_teams: tuple[str, str]) -> dict[str, Decimal]:
    market = next((m for m in book.get("markets") or [] if isinstance(m, Mapping) and m.get("key") == "h2h"), None)
    if not isinstance(market, Mapping):
        return {}
    raw: dict[str, Decimal] = {}
    for outcome in market.get("outcomes") or []:
        if not isinstance(outcome, Mapping):
            continue
        team = canonical_team(outcome.get("name"))
        if team not in event_teams:
            continue
        # Historical endpoint is requested in decimal format to avoid American rounding.
        p = _decimal_to_prob(outcome.get("price"))
        if p is not None:
            raw[team] = p
    if set(raw) != set(event_teams):
        return {}
    return _devig_two_way(raw)


def sharp_event(raw: Mapping[str, Any]) -> SharpEvent | None:
    home = canonical_team(raw.get("home_team")); away = canonical_team(raw.get("away_team"))
    if not home or not away or home == away:
        return None
    event_key = tuple(sorted((home, away)))
    start_ts = _iso_ts(raw.get("commence_time"))
    if start_ts is None:
        return None
    books = {str(b.get("key") or ""): b for b in raw.get("bookmakers") or [] if isinstance(b, Mapping)}
    if "pinnacle" in books:
        fair = _book_fair(books["pinnacle"], event_key)
        if fair:
            return SharpEvent(str(raw.get("id") or ""), event_key, start_ts, fair, "pinnacle")
    fallback: list[dict[str, Decimal]] = []
    used: list[str] = []
    for key in ("fanduel", "draftkings"):
        if key not in books:
            continue
        fair = _book_fair(books[key], event_key)
        if fair:
            fallback.append(fair); used.append(key)
    if not fallback:
        return None
    combined = {team: sum((row[team] for row in fallback), D("0")) / D(len(fallback)) for team in event_key}
    return SharpEvent(str(raw.get("id") or ""), event_key, start_ts, combined, "+".join(used))


async def _get_json(http: httpx.AsyncClient, url: str, *, params: Mapping[str, Any] | None = None,
                    attempts: int = 5) -> tuple[dict[str, Any], httpx.Response]:
    for attempt in range(attempts):
        response = await http.get(url, params=params)
        if response.status_code != 429:
            response.raise_for_status()
            body = response.json()
            return (body if isinstance(body, dict) else {}), response
        retry = response.headers.get("Retry-After")
        try:
            delay = max(float(retry or 0), 0.25 * (2 ** attempt))
        except ValueError:
            delay = 0.25 * (2 ** attempt)
        await asyncio.sleep(min(delay, 5.0))
    response.raise_for_status()
    raise RuntimeError("unreachable")


async def _page_markets(http: httpx.AsyncClient, *, historical: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    endpoint = f"{KALSHI}/historical/markets" if historical else f"{KALSHI}/markets"
    for _ in range(20):
        params: dict[str, Any] = {"limit": 1000, "series_ticker": SERIES, "mve_filter": "exclude"}
        if not historical:
            params["status"] = "settled"
        if cursor:
            params["cursor"] = cursor
        body, _ = await _get_json(http, endpoint, params=params)
        rows = [dict(x) for x in body.get("markets") or [] if isinstance(x, Mapping)]
        for row in rows:
            row["_source_tier"] = "historical" if historical else "live"
            row["series_ticker"] = SERIES
        out.extend(rows)
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
    return out


def _kalshi_sides(markets: Iterable[Mapping[str, Any]], start_date: date, end_date: date) -> list[KalshiSide]:
    out: list[KalshiSide] = []
    for market in markets:
        result = str(market.get("result") or "").lower()
        if result not in {"yes", "no"}:
            continue
        event = {
            "series_ticker": SERIES,
            "event_ticker": market.get("event_ticker"),
            "title": market.get("title"),
            "sub_title": market.get("subtitle") or market.get("sub_title"),
            "markets": [dict(market)],
        }
        normalized = normalize_kalshi_mlb([event])
        for side in normalized:
            start_ts = side.identity.event.scheduled_start_ms // 1000
            start_day = datetime.fromtimestamp(start_ts, tz=timezone.utc).date()
            if not (start_date <= start_day <= end_date):
                continue
            won = (result == "yes") if side.side == "YES" else (result == "no")
            out.append(KalshiSide(
                ticker=side.venue_market_id,
                source_tier=str(market.get("_source_tier") or "live"),
                event_key=tuple(side.identity.event.participants),
                start_ts=start_ts,
                team=side.identity.subject,
                side=side.side,
                won=won,
            ))
    # Exact semantic dedupe only; mirrored Kalshi markets are handled later by keeping cheapest exposure.
    unique: dict[tuple[Any, ...], KalshiSide] = {}
    for row in out:
        unique[(row.ticker, row.team, row.side, row.start_ts)] = row
    return list(unique.values())


async def _historical_odds_snapshot(http: httpx.AsyncClient, api_key: str, snapshot_ts: int) -> tuple[list[SharpEvent], dict[str, Any]]:
    dt = datetime.fromtimestamp(snapshot_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    body, response = await _get_json(
        http,
        f"{ODDS}/historical/sports/baseball_mlb/odds",
        params={
            "apiKey": api_key,
            "bookmakers": ",".join(SHARP_BOOKS),
            "markets": "h2h",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
            "date": dt,
        },
    )
    data = body.get("data") or []
    events = [x for raw in data if isinstance(raw, Mapping) if (x := sharp_event(raw)) is not None]
    quota = {
        "remaining": response.headers.get("x-requests-remaining"),
        "used": response.headers.get("x-requests-used"),
        "last": response.headers.get("x-requests-last"),
        "snapshot": body.get("timestamp"),
    }
    return events, quota


def _candle_price(point: Mapping[str, Any], side: str) -> Decimal | None:
    if side == "YES":
        ask = point.get("yes_ask") if isinstance(point.get("yes_ask"), Mapping) else {}
        return _dec(ask.get("close_dollars") if "close_dollars" in ask else ask.get("close"))
    bid = point.get("yes_bid") if isinstance(point.get("yes_bid"), Mapping) else {}
    yes_bid = _dec(bid.get("close_dollars") if "close_dollars" in bid else bid.get("close"))
    if yes_bid is None:
        return None
    return D("1") - yes_bid


async def _ticker_candles(http: httpx.AsyncClient, row: KalshiSide, snapshot_ts: int,
                          lookback_s: int = 10 * 60) -> list[Mapping[str, Any]]:
    params = {"start_ts": max(0, snapshot_ts - lookback_s), "end_ts": snapshot_ts, "period_interval": 1}
    if row.source_tier == "historical":
        url = f"{KALSHI}/historical/markets/{row.ticker}/candlesticks"
    else:
        url = f"{KALSHI}/series/{SERIES}/markets/{row.ticker}/candlesticks"
    try:
        body, _ = await _get_json(http, url, params=params, attempts=4)
    except httpx.HTTPStatusError as exc:
        # Cutoff can move while the test runs. Fail over across the live/historical partition on 404 only.
        if exc.response.status_code != 404:
            raise
        other = (f"{KALSHI}/series/{SERIES}/markets/{row.ticker}/candlesticks"
                 if row.source_tier == "historical" else f"{KALSHI}/historical/markets/{row.ticker}/candlesticks")
        body, _ = await _get_json(http, other, params=params, attempts=4)
    return [x for x in body.get("candlesticks") or [] if isinstance(x, Mapping)]


async def _observation_for_side(http: httpx.AsyncClient, side: KalshiSide, sharp: SharpEvent,
                                snapshot_ts: int, target_stake: Decimal) -> Observation | None:
    if snapshot_ts >= side.start_ts or snapshot_ts >= sharp.start_ts:
        return None
    if abs(side.start_ts - sharp.start_ts) > MAX_START_SKEW_S:
        return None
    points = await _ticker_candles(http, side, snapshot_ts)
    eligible = [p for p in points if int(p.get("end_period_ts") or 0) <= snapshot_ts]
    if not eligible:
        return None
    point = max(eligible, key=lambda p: int(p.get("end_period_ts") or 0))
    end_ts = int(point.get("end_period_ts") or 0)
    ask = _candle_price(point, side.side)
    if ask is None or ask <= 0 or ask >= 1:
        return None
    sharp_p = sharp.fair_by_team.get(side.team)
    if sharp_p is None:
        return None
    contracts = representative_contracts(ask, target_stake)
    fee = taker_fee(ask, contracts)
    fee_pc = fee / D(contracts)
    edge = sharp_p - ask - fee_pc
    pnl_pc = (D("1") if side.won else D("0")) - ask - fee_pc
    order_pnl = pnl_pc * D(contracts)
    order_stake = ask * D(contracts) + fee
    return Observation(
        snapshot_ts=snapshot_ts, event_key=side.event_key, start_ts=side.start_ts,
        ticker=side.ticker, team=side.team, side=side.side, sharp_source=sharp.source,
        sharp_p=sharp_p, ask=ask, contracts=contracts, total_fee=fee,
        fee_per_contract=fee_pc, edge=edge, won=side.won,
        pnl_per_contract=pnl_pc, order_pnl=order_pnl, order_stake=order_stake,
        quote_age_s=max(0, snapshot_ts - end_ts),
    )


def _max_drawdown(rows: Sequence[Observation]) -> tuple[Decimal, Decimal, Decimal]:
    equity = D("0"); peak = D("0"); max_dd = D("0"); total_stake = D("0")
    for row in sorted(rows, key=lambda r: (r.snapshot_ts, r.start_ts, r.event_key, r.team)):
        total_stake += row.order_stake
        equity += row.order_pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    stake_pct = (max_dd / total_stake) if total_stake > 0 else D("0")
    bankroll_pct = max_dd / D("3400")
    return max_dd, stake_pct, bankroll_pct


def _calibration(rows: Sequence[Observation]) -> dict[str, Any]:
    # One latest sampled forecast per unique physical game/team avoids duplicating outcomes across snapshots.
    latest: dict[tuple[tuple[str, str], int, str], Observation] = {}
    for row in rows:
        key = (row.event_key, row.start_ts, row.team)
        if key not in latest or row.snapshot_ts > latest[key].snapshot_ts:
            latest[key] = row
    points = list(latest.values())
    bins: dict[int, list[Observation]] = defaultdict(list)
    for row in points:
        bucket = min(95, max(5, int((float(row.sharp_p) * 100) // 5 * 5)))
        bins[bucket].append(row)
    table = []
    weighted_abs = D("0")
    for bucket in sorted(bins):
        group = bins[bucket]
        mean_p = sum((r.sharp_p for r in group), D("0")) / D(len(group))
        hit = D(sum(1 for r in group if r.won)) / D(len(group))
        err = abs(hit - mean_p)
        weighted_abs += err * D(len(group))
        table.append({
            "bucket_pct": bucket, "n": len(group), "mean_sharp_p": str(mean_p),
            "hit_rate": str(hit), "abs_error": str(err),
        })
    ece = weighted_abs / D(len(points)) if points else D("0")
    return {"n_unique_team_games": len(points), "ece": str(ece), "bins": table}


def _bucket_summary(rows: Sequence[Observation], threshold: Decimal) -> dict[str, Any]:
    selected = [r for r in rows if r.edge >= threshold]
    if not selected:
        return {"edge": f">={int(threshold * 100)}c", "n": 0}
    wins = sum(1 for r in selected if r.won)
    mean_pc = sum((r.pnl_per_contract for r in selected), D("0")) / D(len(selected))
    total_pnl = sum((r.order_pnl for r in selected), D("0"))
    total_stake = sum((r.order_stake for r in selected), D("0"))
    max_dd, dd_stake_pct, dd_bankroll_pct = _max_drawdown(selected)
    return {
        "edge": f">={int(threshold * 100)}c",
        "n": len(selected),
        "hit_rate": str(D(wins) / D(len(selected))),
        "mean_realized_return_per_contract": str(mean_pc),
        "total_order_pnl": str(total_pnl),
        "total_stake": str(total_stake),
        "roi": str(total_pnl / total_stake if total_stake > 0 else D("0")),
        "max_drawdown_dollars": str(max_dd),
        "max_drawdown_pct_total_stake": str(dd_stake_pct),
        "max_drawdown_pct_3400_bankroll": str(dd_bankroll_pct),
        "mean_edge": str(sum((r.edge for r in selected), D("0")) / D(len(selected))),
        "median_quote_age_s": statistics.median(r.quote_age_s for r in selected),
        "sharp_sources": dict(__import__("collections").Counter(r.sharp_source for r in selected)),
    }


def _go_no_go(summary_3c: Mapping[str, Any], calibration: Mapping[str, Any]) -> dict[str, Any]:
    n = int(summary_3c.get("n") or 0)
    mean_return = _dec(summary_3c.get("mean_realized_return_per_contract")) or D("0")
    dd = _dec(summary_3c.get("max_drawdown_pct_total_stake")) or D("1")
    ece = _dec(calibration.get("ece")) or D("1")
    checks = {
        "n_gte_300": n >= 300,
        "positive_mean_net_return": mean_return > 0,
        "drawdown_under_15pct_of_total_stake": dd < D("0.15"),
        "calibration_error_under_3_points": ece < D("0.03"),
    }
    return {"decision": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "rule": ">=3c bucket only; no threshold selection after results"}


def _snapshot_days(sides: Sequence[KalshiSide], start_date: date, end_date: date) -> list[date]:
    days = {datetime.fromtimestamp(row.start_ts, tz=timezone.utc).date() for row in sides}
    # Snapshot is same UTC calendar day. Include only days with at least one Kalshi game.
    return sorted(d for d in days if start_date <= d <= end_date)


async def run(api_key: str, *, start_date: date, end_date: date, snapshot_hour_utc: int,
              target_stake: Decimal, max_days: int | None = None) -> dict[str, Any]:
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as http:
        archived, recent = await asyncio.gather(_page_markets(http, historical=True), _page_markets(http, historical=False))
        by_ticker: dict[str, dict[str, Any]] = {}
        for market in archived + recent:
            # Prefer live tier if the moving cutoff causes overlap.
            ticker = str(market.get("ticker") or "")
            if ticker and (ticker not in by_ticker or market.get("_source_tier") == "live"):
                by_ticker[ticker] = market
        sides = _kalshi_sides(by_ticker.values(), start_date, end_date)
        sides_by_event: dict[tuple[str, str], list[KalshiSide]] = defaultdict(list)
        for side in sides:
            sides_by_event[side.event_key].append(side)

        days = _snapshot_days(sides, start_date, end_date)
        if max_days is not None:
            days = days[:max(0, max_days)]
        observations: list[Observation] = []
        quota_trace: list[dict[str, Any]] = []
        snapshot_stats: list[dict[str, Any]] = []

        for index, day in enumerate(days, 1):
            snapshot = datetime.combine(day, dtime(hour=snapshot_hour_utc, tzinfo=timezone.utc))
            snapshot_ts = int(snapshot.timestamp())
            sharp_events, quota = await _historical_odds_snapshot(http, api_key, snapshot_ts)
            quota_trace.append({"day": day.isoformat(), **quota})
            matched_games = 0; candle_requests = 0; day_obs: list[Observation] = []
            for sharp in sharp_events:
                if sharp.start_ts <= snapshot_ts:
                    continue
                candidates = [s for s in sides_by_event.get(sharp.event_key, [])
                              if abs(s.start_ts - sharp.start_ts) <= MAX_START_SKEW_S and s.start_ts > snapshot_ts]
                if not candidates:
                    continue
                matched_games += 1
                # One candle fetch per ticker, then calculate whichever normalized side(s) it represents.
                grouped: dict[str, list[KalshiSide]] = defaultdict(list)
                for candidate in candidates:
                    grouped[candidate.ticker].append(candidate)
                ticker_obs: list[Observation] = []
                for ticker_rows in grouped.values():
                    candle_requests += 1
                    base_side = ticker_rows[0]
                    points = await _ticker_candles(http, base_side, snapshot_ts)
                    eligible = [p for p in points if int(p.get("end_period_ts") or 0) <= snapshot_ts]
                    if not eligible:
                        continue
                    point = max(eligible, key=lambda p: int(p.get("end_period_ts") or 0))
                    end_ts = int(point.get("end_period_ts") or 0)
                    for side in ticker_rows:
                        ask = _candle_price(point, side.side)
                        sharp_p = sharp.fair_by_team.get(side.team)
                        if ask is None or sharp_p is None or ask <= 0 or ask >= 1:
                            continue
                        contracts = representative_contracts(ask, target_stake)
                        fee = taker_fee(ask, contracts); fee_pc = fee / D(contracts)
                        edge = sharp_p - ask - fee_pc
                        pnl_pc = (D("1") if side.won else D("0")) - ask - fee_pc
                        ticker_obs.append(Observation(
                            snapshot_ts=snapshot_ts, event_key=side.event_key, start_ts=side.start_ts,
                            ticker=side.ticker, team=side.team, side=side.side, sharp_source=sharp.source,
                            sharp_p=sharp_p, ask=ask, contracts=contracts, total_fee=fee,
                            fee_per_contract=fee_pc, edge=edge, won=side.won,
                            pnl_per_contract=pnl_pc, order_pnl=pnl_pc * D(contracts),
                            order_stake=ask * D(contracts) + fee, quote_age_s=max(0, snapshot_ts - end_ts),
                        ))
                # Semantic dedupe: for each team exposure keep the cheapest/best edge across mirrored Kalshi tickers.
                best: dict[str, Observation] = {}
                for obs in ticker_obs:
                    previous = best.get(obs.team)
                    if previous is None or obs.edge > previous.edge:
                        best[obs.team] = obs
                day_obs.extend(best.values())
            observations.extend(day_obs)
            snapshot_stats.append({
                "day": day.isoformat(), "sharp_events": len(sharp_events), "matched_games": matched_games,
                "candlestick_requests": candle_requests, "observations": len(day_obs),
            })
            logger.info("MLB_SHARP_BACKTEST_PROGRESS day=%s index=%s/%s sharp=%s matched=%s obs=%s quota_remaining=%s",
                        day, index, len(days), len(sharp_events), matched_games, len(day_obs), quota.get("remaining"))

    calibration = _calibration(observations)
    buckets = [_bucket_summary(observations, threshold) for threshold in EDGE_BUCKETS]
    three = next(row for row in buckets if row.get("edge") == ">=3c")
    result = {
        "status": "KALSHI_MLB_SHARP_EV_BACKTEST_READ_ONLY",
        "method": {
            "kalshi_series": SERIES,
            "odds_sport": "baseball_mlb",
            "snapshot_hour_utc": snapshot_hour_utc,
            "sampling": "one_precommitted_daily_snapshot_per_game_day",
            "sharp_priority": ["pinnacle", "fanduel+draftkings multiplicative-devig mean", "single available fanduel/draftkings fallback"],
            "devig": "multiplicative",
            "physical_match": "canonical teams + <=5 minute scheduled-start skew",
            "kalshi_quote": "latest 1-minute candlestick close at/before snapshot; YES ask direct, NO ask=1-YES bid",
            "fee": "ceil_cent(0.07*C*P*(1-P)) at representative whole-contract ~$10 order",
            "historical_depth": "not reconstructable from candlesticks; live capacity logger required before scaling",
            "realized_accounting": False,
        },
        "range": {"start": start_date.isoformat(), "end": end_date.isoformat(), "snapshot_days": len(days)},
        "kalshi": {"markets_discovered": len(by_ticker), "normalized_sides": len(sides)},
        "observations": len(observations),
        "buckets": buckets,
        "calibration": calibration,
        "go_no_go": _go_no_go(three, calibration),
        "quota_last": quota_trace[-1] if quota_trace else {},
        "snapshot_stats": snapshot_stats,
        "sample_observations": [row.as_dict() for row in sorted(observations, key=lambda r: r.edge, reverse=True)[:50]],
        "warning": "BACKTEST/EV ONLY — not arbitrage, not executed, not realized P&L",
    }
    return result


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=_parse_date, default=date(2026, 3, 1))
    parser.add_argument("--end-date", type=_parse_date, default=(datetime.now(timezone.utc) - timedelta(days=1)).date())
    parser.add_argument("--snapshot-hour-utc", type=int, default=16, choices=range(24))
    parser.add_argument("--target-stake", type=Decimal, default=D("10"))
    parser.add_argument("--max-days", type=int, default=None, help="Diagnostic quota-safe limit; omit for full run")
    parser.add_argument("--output", default="/tmp/kalshi_mlb_sharp_ev_backtest.json")
    args = parser.parse_args()
    api_key = os.getenv("THE_ODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is required")
    result = asyncio.run(run(api_key, start_date=args.start_date, end_date=args.end_date,
                             snapshot_hour_utc=args.snapshot_hour_utc,
                             target_stake=args.target_stake, max_days=args.max_days))
    payload = json.dumps(result, indent=2, sort_keys=True, default=str)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print("MLB_SHARP_BACKTEST_RESULT " + json.dumps(result, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
