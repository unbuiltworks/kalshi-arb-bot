"""Read-only MLB EV backtest: ESPN archived DraftKings close vs Kalshi pregame ask.

This is an isolated research fallback for the precommitted sharp-fair-value study.
It consumes zero The Odds API credits and has no order-entry imports.

Locked methodology before seeing results:
- sportsbook source: ESPN archived DraftKings closing moneyline only;
- devig: multiplicative two-way normalization of DK closing decimal prices;
- physical match: canonical teams + <=5 minute scheduled-start skew;
- Kalshi quote: latest 1-minute bid/ask candle ending at least 60 seconds before
  ESPN scheduled start, and no more than 120 seconds old at that cutoff;
- fee/sizing/buckets/go-no-go: reuse the already precommitted backtest module;
- mirrored Kalshi markets: keep the cheapest/best edge per physical team exposure;
- historical depth is unavailable and is not inferred from candlesticks.

Nothing produced here is executed or realized P&L.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import httpx

import src.kalshi_mlb_sharp_ev_backtest as bt
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern
from src.mlb_game_side_normalization import canonical_team

logger = logging.getLogger("kalshi_arb.espn_mlb_closing_ev_backtest")

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
CORE = "https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb"
PREGAME_BUFFER_S = 60
MAX_QUOTE_AGE_S = 120
ESPN_CONCURRENCY = 16
KALSHI_CONCURRENCY = 12
D = Decimal


async def _page_markets_compat(http: httpx.AsyncClient, *, historical: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = ""
    endpoint = f"{bt.KALSHI}/historical/markets" if historical else f"{bt.KALSHI}/markets"
    for _ in range(20):
        params: dict[str, Any] = {"limit": 1000, "series_ticker": bt.SERIES}
        if not historical:
            params["status"] = "settled"
            params["mve_filter"] = "exclude"
        if cursor:
            params["cursor"] = cursor
        body, _ = await bt._get_json(http, endpoint, params=params)
        rows = [dict(x) for x in body.get("markets") or [] if isinstance(x, Mapping)]
        for row in rows:
            row["_source_tier"] = "historical" if historical else "live"
            row["series_ticker"] = bt.SERIES
        out.extend(rows)
        cursor = str(body.get("cursor") or "")
        if not cursor:
            break
    return out


def _parse_ts(value: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
    except Exception:
        return None


def _american_to_decimal(value: Any) -> Decimal | None:
    try:
        x = D(str(value).replace("+", ""))
    except Exception:
        return None
    if x == 0:
        return None
    if x > 0:
        return D("1") + x / D("100")
    return D("1") + D("100") / (-x)


def _closing_decimal(team_odds: Mapping[str, Any]) -> Decimal | None:
    close = team_odds.get("close") if isinstance(team_odds.get("close"), Mapping) else {}
    moneyline = close.get("moneyLine") if isinstance(close.get("moneyLine"), Mapping) else {}
    raw_decimal = moneyline.get("decimal") if "decimal" in moneyline else moneyline.get("value")
    try:
        dec = D(str(raw_decimal))
        if dec > 1:
            return dec
    except Exception:
        pass
    return _american_to_decimal(moneyline.get("american") or moneyline.get("alternateDisplayValue"))


def _event_teams(event: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    competitions = event.get("competitions") or []
    comp = competitions[0] if competitions and isinstance(competitions[0], Mapping) else {}
    competitors = comp.get("competitors") or []
    home_name = away_name = ""
    home = away = ""
    for competitor in competitors:
        if not isinstance(competitor, Mapping):
            continue
        team = competitor.get("team") if isinstance(competitor.get("team"), Mapping) else {}
        display = str(team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "")
        canon = canonical_team(display)
        where = str(competitor.get("homeAway") or "").lower()
        if where == "home":
            home_name, home = display, canon
        elif where == "away":
            away_name, away = display, canon
    if not home or not away or home == away:
        return None
    return home_name, home, away_name, away


async def _fetch_json_status(http: httpx.AsyncClient, url: str, *, params: Mapping[str, Any] | None = None) -> tuple[Any, int]:
    response = await http.get(url, params=params)
    if response.status_code != 200:
        return None, response.status_code
    try:
        return response.json(), response.status_code
    except Exception:
        return None, response.status_code


async def _espn_day_events(http: httpx.AsyncClient, day: date) -> list[dict[str, Any]]:
    payload, status = await _fetch_json_status(http, SCOREBOARD, params={"dates": day.strftime("%Y%m%d"), "limit": 100})
    if status != 200 or not isinstance(payload, Mapping):
        logger.warning("ESPN_SCOREBOARD_FAIL day=%s status=%s", day, status)
        return []
    return [dict(x) for x in payload.get("events") or [] if isinstance(x, Mapping)]


async def _espn_closing_event(http: httpx.AsyncClient, event: Mapping[str, Any]) -> bt.SharpEvent | None:
    event_id = str(event.get("id") or "")
    competitions = event.get("competitions") or []
    comp = competitions[0] if competitions and isinstance(competitions[0], Mapping) else {}
    comp_id = str(comp.get("id") or event_id)
    start_ts = _parse_ts(event.get("date") or comp.get("date"))
    teams = _event_teams(event)
    if not event_id or not comp_id or start_ts is None or teams is None:
        return None
    _, home, _, away = teams
    key = tuple(sorted((home, away)))

    payload, status = await _fetch_json_status(
        http, f"{CORE}/events/{event_id}/competitions/{comp_id}/odds", params={"limit": 100}
    )
    if status != 200:
        return None
    items = payload.get("items") if isinstance(payload, Mapping) else payload if isinstance(payload, list) else []
    dk: Mapping[str, Any] | None = None
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        provider = item.get("provider") if isinstance(item.get("provider"), Mapping) else {}
        provider_name = str(provider.get("name") or "").lower()
        provider_id = str(provider.get("id") or "")
        if provider_name == "draftkings" or provider_id == "100":
            dk = item
            break
    if dk is None:
        return None
    home_odds = dk.get("homeTeamOdds") if isinstance(dk.get("homeTeamOdds"), Mapping) else {}
    away_odds = dk.get("awayTeamOdds") if isinstance(dk.get("awayTeamOdds"), Mapping) else {}
    home_dec = _closing_decimal(home_odds)
    away_dec = _closing_decimal(away_odds)
    if home_dec is None or away_dec is None:
        return None
    raw = {home: D("1") / home_dec, away: D("1") / away_dec}
    fair = bt._devig_two_way(raw)
    if set(fair) != set(key):
        return None
    return bt.SharpEvent(event_id=event_id, event_key=key, start_ts=start_ts, fair_by_team=fair,
                         source="draftkings_close_espn")


async def _bounded(sem: asyncio.Semaphore, coro):
    async with sem:
        return await coro


async def _load_espn_closes(http: httpx.AsyncClient, days: Sequence[date]) -> tuple[list[bt.SharpEvent], dict[str, Any]]:
    day_sem = asyncio.Semaphore(8)
    day_rows = await asyncio.gather(*[_bounded(day_sem, _espn_day_events(http, d)) for d in days])
    raw_events = [e for rows in day_rows for e in rows]
    sem = asyncio.Semaphore(ESPN_CONCURRENCY)
    sharp_rows = await asyncio.gather(*[_bounded(sem, _espn_closing_event(http, e)) for e in raw_events])
    sharp = [x for x in sharp_rows if x is not None]
    return sharp, {"days": len(days), "scoreboard_events": len(raw_events), "closing_events": len(sharp)}


async def _observation_at_close(http: httpx.AsyncClient, side: bt.KalshiSide, sharp: bt.SharpEvent,
                                target_stake: Decimal) -> bt.Observation | None:
    cutoff_ts = sharp.start_ts - PREGAME_BUFFER_S
    if cutoff_ts <= 0 or abs(side.start_ts - sharp.start_ts) > bt.MAX_START_SKEW_S:
        return None
    points = await bt._ticker_candles(http, side, cutoff_ts, lookback_s=10 * 60)
    eligible = [p for p in points if int(p.get("end_period_ts") or 0) <= cutoff_ts]
    if not eligible:
        return None
    point = max(eligible, key=lambda p: int(p.get("end_period_ts") or 0))
    end_ts = int(point.get("end_period_ts") or 0)
    quote_age = max(0, cutoff_ts - end_ts)
    if quote_age > MAX_QUOTE_AGE_S:
        return None
    ask = bt._candle_price(point, side.side)
    sharp_p = sharp.fair_by_team.get(side.team)
    if ask is None or sharp_p is None or ask <= 0 or ask >= 1:
        return None
    contracts = bt.representative_contracts(ask, target_stake)
    fee = bt.taker_fee(ask, contracts)
    fee_pc = fee / D(contracts)
    edge = sharp_p - ask - fee_pc
    pnl_pc = (D("1") if side.won else D("0")) - ask - fee_pc
    return bt.Observation(
        snapshot_ts=cutoff_ts, event_key=side.event_key, start_ts=side.start_ts,
        ticker=side.ticker, team=side.team, side=side.side,
        sharp_source=sharp.source, sharp_p=sharp_p, ask=ask,
        contracts=contracts, total_fee=fee, fee_per_contract=fee_pc,
        edge=edge, won=side.won, pnl_per_contract=pnl_pc,
        order_pnl=pnl_pc * D(contracts), order_stake=ask * D(contracts) + fee,
        quote_age_s=quote_age,
    )


async def run(*, start_date: date, end_date: date, target_stake: Decimal,
              max_days: int | None = None) -> dict[str, Any]:
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as http:
        archived, recent = await asyncio.gather(
            _page_markets_compat(http, historical=True), _page_markets_compat(http, historical=False)
        )
        by_ticker: dict[str, dict[str, Any]] = {}
        for market in archived + recent:
            ticker = str(market.get("ticker") or "")
            if ticker and (ticker not in by_ticker or market.get("_source_tier") == "live"):
                by_ticker[ticker] = market

        original_normalizer = bt.normalize_kalshi_mlb
        bt.normalize_kalshi_mlb = normalize_kalshi_mlb_eastern
        try:
            sides = bt._kalshi_sides(by_ticker.values(), start_date, end_date)
        finally:
            bt.normalize_kalshi_mlb = original_normalizer

        days = sorted({datetime.fromtimestamp(s.start_ts, tz=timezone.utc).date() for s in sides
                       if start_date <= datetime.fromtimestamp(s.start_ts, tz=timezone.utc).date() <= end_date})
        if max_days is not None:
            days = days[:max(0, max_days)]
        allowed_days = set(days)
        sides = [s for s in sides if datetime.fromtimestamp(s.start_ts, tz=timezone.utc).date() in allowed_days]
        by_event: dict[tuple[str, str], list[bt.KalshiSide]] = defaultdict(list)
        for side in sides:
            by_event[side.event_key].append(side)

        sharp_events, espn_stats = await _load_espn_closes(http, days)
        matched = 0
        tasks = []
        task_meta: list[tuple[bt.SharpEvent, bt.KalshiSide]] = []
        sem = asyncio.Semaphore(KALSHI_CONCURRENCY)
        for sharp in sharp_events:
            candidates = [s for s in by_event.get(sharp.event_key, [])
                          if abs(s.start_ts - sharp.start_ts) <= bt.MAX_START_SKEW_S]
            if not candidates:
                continue
            matched += 1
            for side in candidates:
                tasks.append(_bounded(sem, _observation_at_close(http, side, sharp, target_stake)))
                task_meta.append((sharp, side))
        raw_obs = await asyncio.gather(*tasks) if tasks else []
        observations = [x for x in raw_obs if x is not None]

    # Mirrored Kalshi markets can represent the same physical team exposure.
    best: dict[tuple[tuple[str, str], int, str], bt.Observation] = {}
    for obs in observations:
        key = (obs.event_key, obs.start_ts, obs.team)
        previous = best.get(key)
        if previous is None or obs.edge > previous.edge:
            best[key] = obs
    observations = sorted(best.values(), key=lambda r: (r.start_ts, r.event_key, r.team))

    calibration = bt._calibration(observations)
    buckets = [bt._bucket_summary(observations, threshold) for threshold in bt.EDGE_BUCKETS]
    three = next(row for row in buckets if row.get("edge") == ">=3c")
    return {
        "status": "KALSHI_MLB_ESPN_DK_CLOSE_EV_BACKTEST_READ_ONLY",
        "method": {
            "sportsbook": "ESPN archived DraftKings closing moneyline",
            "devig": "multiplicative two-way",
            "kalshi_cutoff_seconds_before_scheduled_start": PREGAME_BUFFER_S,
            "max_kalshi_quote_age_seconds": MAX_QUOTE_AGE_S,
            "physical_match": "canonical teams + <=5 minute scheduled-start skew",
            "fee": "ceil_cent(0.07*C*P*(1-P)) at representative whole-contract ~$10 order",
            "historical_depth": "not reconstructable from candlesticks; not assumed",
            "realized_accounting": False,
        },
        "range": {"start": start_date.isoformat(), "end": end_date.isoformat(), "game_days": len(days)},
        "kalshi": {"markets_discovered": len(by_ticker), "normalized_sides_in_range": len(sides)},
        "espn": espn_stats,
        "matched_physical_games": matched,
        "observations": len(observations),
        "buckets": buckets,
        "calibration": calibration,
        "go_no_go": bt._go_no_go(three, calibration),
        "sample_observations": [x.as_dict() for x in sorted(observations, key=lambda r: r.edge, reverse=True)[:50]],
        "warning": "BACKTEST/EV ONLY — DraftKings closing-line fallback, not arbitrage, not executed, not realized P&L",
    }


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=_parse_date, default=date(2026, 3, 25))
    parser.add_argument("--end-date", type=_parse_date, default=(datetime.now(timezone.utc) - timedelta(days=1)).date())
    parser.add_argument("--target-stake", type=Decimal, default=D("10"))
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--output", default="research/results/espn_mlb_closing_ev_backtest.json")
    args = parser.parse_args()
    result = asyncio.run(run(start_date=args.start_date, end_date=args.end_date,
                             target_stake=args.target_stake, max_days=args.max_days))
    payload = json.dumps(result, indent=2, sort_keys=True, default=str)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload + "\n", encoding="utf-8")
    print("ESPN_MLB_CLOSE_BACKTEST_RESULT " + json.dumps(result, separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
