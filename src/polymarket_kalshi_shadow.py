"""Live, read-only Polymarket US <-> Kalshi sports-total shadow scanner.

Phase 1 deliberately places NO orders. It:
  * discovers equivalent MLB full-game total markets on both exchanges,
  * requires same teams, local game date, threshold, and full-game total type,
  * streams both order books,
  * calculates two-leg after-fee arbitrage in both directions,
  * sizes each candidate to a hard total-capital ceiling (default $50), and
  * logs OPEN / MOVE / CLOSE including opportunity lifetime.

The output is evidence for whether the edge is frequent, large, liquid, and slow
enough to execute before an order-entry layer is considered.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
import json
import logging
import os
import re
import signal
import time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from polymarket_us import PolymarketUS

from src.kalshi_cross_book import KalshiCrossBooks, TwoSidedQuote

logger = logging.getLogger("kalshi_arb.poly_kalshi_shadow")

POLY_GATEWAY = "https://gateway.polymarket.us"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
MLB_SERIES = "KXMLBTOTAL"
ET = ZoneInfo("America/New_York")

_POLY_TOTAL_RE = re.compile(
    r"^tsc-mlb-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})-(.+)$",
    re.I,
)
_OVER_RE = re.compile(r"\bover\s+([0-9]+(?:\.[0-9]+)?)", re.I)
_MORE_RE = re.compile(r"\bmore\s+than\s+([0-9]+(?:\.[0-9]+)?)", re.I)


@dataclass(frozen=True)
class PolyMarket:
    slug: str
    event_title: str
    teams: frozenset[str]
    game_date: str
    line: Decimal
    question: str


@dataclass(frozen=True)
class KalshiMarket:
    ticker: str
    event_ticker: str
    event_title: str
    teams: frozenset[str]
    game_date: str
    line: Decimal
    strike_type: str
    rules_primary: str


@dataclass(frozen=True)
class Match:
    poly: PolyMarket
    kalshi: KalshiMarket

    @property
    def key(self) -> str:
        return f"{self.poly.slug}|{self.kalshi.ticker}"


@dataclass(frozen=True)
class PolyQuote:
    slug: str
    yes_ask: Decimal
    yes_depth: Decimal
    no_ask: Decimal
    no_depth: Decimal
    state: str
    received_ms: int


@dataclass(frozen=True)
class ShadowPlan:
    direction: str
    quantity: int
    poly_side: str
    poly_price: Decimal
    kalshi_side: str
    kalshi_price: Decimal
    poly_fee: Decimal
    kalshi_fee: Decimal
    total_cost: Decimal
    payout: Decimal
    profit: Decimal
    roi: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "quantity": self.quantity,
            "poly_side": self.poly_side,
            "poly_price": str(self.poly_price),
            "kalshi_side": self.kalshi_side,
            "kalshi_price": str(self.kalshi_price),
            "poly_fee": str(self.poly_fee),
            "kalshi_fee": str(self.kalshi_fee),
            "total_cost": str(self.total_cost),
            "payout": str(self.payout),
            "profit": str(self.profit),
            "roi": str(self.roi),
        }


class PolyBookStore:
    """Top-of-book state populated by Polymarket US's authenticated market WS."""

    def __init__(self) -> None:
        self._quotes: dict[str, PolyQuote] = {}
        self.connected = False
        self.last_message_ms = 0

    @staticmethod
    def _value(obj: Any) -> Decimal | None:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get("value")
        try:
            return Decimal(str(obj))
        except Exception:
            return None

    @staticmethod
    def _qty(level: Any) -> Decimal | None:
        if not isinstance(level, dict):
            return None
        raw = level.get("qty", level.get("quantity"))
        try:
            return Decimal(str(raw))
        except Exception:
            return None

    def on_market_data(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        # SDK callback may expose either camelCase or snake_case keys.
        slug = str(data.get("marketSlug") or data.get("market_slug") or "")
        if not slug:
            return
        bids = data.get("bids") or []
        offers = data.get("offers") or data.get("asks") or []
        if not bids or not offers:
            return

        best_bid = bids[0]
        best_offer = offers[0]
        bid_px = self._value(best_bid.get("px") if isinstance(best_bid, dict) else None)
        ask_px = self._value(best_offer.get("px") if isinstance(best_offer, dict) else None)
        bid_qty = self._qty(best_bid)
        ask_qty = self._qty(best_offer)
        if None in (bid_px, ask_px, bid_qty, ask_qty):
            return
        assert bid_px is not None and ask_px is not None
        assert bid_qty is not None and ask_qty is not None

        now = int(time.time() * 1000)
        state = str(data.get("state") or data.get("market_state") or "")
        self._quotes[slug] = PolyQuote(
            slug=slug,
            yes_ask=ask_px,
            yes_depth=ask_qty,
            no_ask=Decimal("1") - bid_px,
            no_depth=bid_qty,
            state=state,
            received_ms=now,
        )
        self.last_message_ms = now

    def quote(self, slug: str, *, max_age_ms: int = 30000) -> PolyQuote | None:
        quote = self._quotes.get(slug)
        if quote is None:
            return None
        if int(time.time() * 1000) - quote.received_ms > max_age_ms:
            return None
        if quote.state and quote.state not in {"MARKET_STATE_OPEN", "OPEN"}:
            return None
        return quote


class OpportunityTracker:
    def __init__(self) -> None:
        self._open: dict[str, tuple[int, ShadowPlan]] = {}

    def update(self, match: Match, plan: ShadowPlan | None) -> None:
        key = f"{match.key}|{plan.direction if plan else 'none'}"
        now = int(time.time() * 1000)

        # Close any direction for this market pair that is no longer current.
        prefix = f"{match.key}|"
        live_direction = plan.direction if plan else None
        for existing in list(self._open):
            if not existing.startswith(prefix):
                continue
            direction = existing[len(prefix):]
            if live_direction == direction:
                continue
            opened_ms, old_plan = self._open.pop(existing)
            _emit(
                "CLOSE",
                match,
                old_plan,
                lifetime_ms=now - opened_ms,
            )

        if plan is None:
            return

        current = self._open.get(key)
        if current is None:
            self._open[key] = (now, plan)
            _emit("OPEN", match, plan, lifetime_ms=0)
            return

        opened_ms, old = current
        moved = (
            old.quantity != plan.quantity
            or abs(old.profit - plan.profit) >= Decimal("0.02")
            or old.poly_price != plan.poly_price
            or old.kalshi_price != plan.kalshi_price
        )
        if moved:
            self._open[key] = (opened_ms, plan)
            _emit("MOVE", match, plan, lifetime_ms=now - opened_ms)

    def close_all(self, matches_by_key: dict[str, Match]) -> None:
        now = int(time.time() * 1000)
        for key, (opened_ms, plan) in list(self._open.items()):
            pair_key = key.rsplit("|", 1)[0]
            match = matches_by_key.get(pair_key)
            if match is not None:
                _emit("CLOSE", match, plan, lifetime_ms=now - opened_ms)
            self._open.pop(key, None)


def _emit(kind: str, match: Match, plan: ShadowPlan, *, lifetime_ms: int) -> None:
    payload = {
        "kind": kind,
        "ts_ms": int(time.time() * 1000),
        "lifetime_ms": lifetime_ms,
        "event": match.poly.event_title,
        "game_date": match.poly.game_date,
        "line": str(match.poly.line),
        "poly_slug": match.poly.slug,
        "kalshi_ticker": match.kalshi.ticker,
        **plan.as_dict(),
    }
    logger.info("SHADOW %s", json.dumps(payload, separators=(",", ":")))


def polymarket_taker_fee(quantity: int, price: Decimal, rate: Decimal) -> Decimal:
    raw = rate * Decimal(quantity) * price * (Decimal("1") - price)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def kalshi_taker_fee(quantity: int, price: Decimal, rate: Decimal) -> Decimal:
    raw = rate * Decimal(quantity) * price * (Decimal("1") - price)
    # Kalshi's per-order fee is rounded up to the next cent.
    return (raw * Decimal("100")).to_integral_value(rounding=ROUND_CEILING) / Decimal("100")


def best_plan(
    poly: PolyQuote,
    kalshi: TwoSidedQuote,
    *,
    bankroll: Decimal,
    poly_fee_rate: Decimal,
    kalshi_fee_rate: Decimal,
) -> ShadowPlan | None:
    candidates: list[ShadowPlan] = []
    directions = (
        (
            "POLY_OVER_KALSHI_UNDER",
            "OVER",
            poly.yes_ask,
            poly.yes_depth,
            "UNDER",
            kalshi.no_ask,
            kalshi.no_depth,
        ),
        (
            "KALSHI_OVER_POLY_UNDER",
            "UNDER",
            poly.no_ask,
            poly.no_depth,
            "OVER",
            kalshi.yes_ask,
            kalshi.yes_depth,
        ),
    )

    for direction, poly_side, poly_px, poly_depth, kal_side, kal_px, kal_depth in directions:
        max_q = int(min(poly_depth, kal_depth))
        if max_q <= 0:
            continue
        # A $50 cap implies only a small integer search. Keep a defensive ceiling.
        max_q = min(max_q, 500)
        best: ShadowPlan | None = None
        for quantity in range(1, max_q + 1):
            pm_fee = polymarket_taker_fee(quantity, poly_px, poly_fee_rate)
            k_fee = kalshi_taker_fee(quantity, kal_px, kalshi_fee_rate)
            cost = Decimal(quantity) * (poly_px + kal_px) + pm_fee + k_fee
            if cost > bankroll:
                break
            payout = Decimal(quantity)
            profit = payout - cost
            roi = profit / cost if cost > 0 else Decimal("0")
            plan = ShadowPlan(
                direction=direction,
                quantity=quantity,
                poly_side=poly_side,
                poly_price=poly_px,
                kalshi_side=kal_side,
                kalshi_price=kal_px,
                poly_fee=pm_fee,
                kalshi_fee=k_fee,
                total_cost=cost,
                payout=payout,
                profit=profit,
                roi=roi,
            )
            if best is None or plan.profit > best.profit:
                best = plan
        if best is not None and best.profit > 0:
            candidates.append(best)

    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.profit, p.roi))


def _poly_market(event_title: str, market: dict[str, Any]) -> PolyMarket | None:
    if market.get("marketType") != "totals":
        return None
    if market.get("sportsMarketType") != "baseball_team_full_game_total":
        return None
    slug = str(market.get("slug") or "")
    match = _POLY_TOTAL_RE.match(slug)
    if not match:
        return None
    team_a, team_b, game_date, _ = match.groups()
    try:
        line = Decimal(str(market.get("line")))
    except Exception:
        return None
    question = str(market.get("question") or market.get("title") or "")
    found = _MORE_RE.search(question) or _OVER_RE.search(question)
    if found and Decimal(found.group(1)) != line:
        return None
    return PolyMarket(
        slug=slug,
        event_title=event_title,
        teams=frozenset((team_a.lower(), team_b.lower())),
        game_date=game_date,
        line=line,
        question=question,
    )


def _kalshi_line(market: dict[str, Any]) -> Decimal | None:
    for field in ("yes_sub_title", "subtitle", "title"):
        text = str(market.get(field) or "")
        found = _OVER_RE.search(text)
        if found:
            return Decimal(found.group(1))
    raw = market.get("floor_strike")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        return None


def _kalshi_date(market: dict[str, Any], event_ticker: str) -> str | None:
    raw = market.get("occurrence_datetime")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.astimezone(ET).date().isoformat()
        except Exception:
            pass
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
    if not m:
        return None
    year, mon, day = m.groups()
    months = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    month = months.get(mon)
    if month is None:
        return None
    return f"20{year}-{month:02d}-{int(day):02d}"


def _ticker_teams(event_ticker: str, abbreviations: set[str]) -> frozenset[str] | None:
    upper = event_ticker.upper()
    matches: set[tuple[str, str]] = set()
    abbrs = sorted({a.upper() for a in abbreviations}, key=len, reverse=True)
    for a in abbrs:
        if not upper.endswith(a):
            continue
        before = upper[: -len(a)]
        for b in abbrs:
            if a != b and before.endswith(b):
                matches.add(tuple(sorted((a.lower(), b.lower()))))
    if len(matches) != 1:
        return None
    pair = next(iter(matches))
    return frozenset(pair)


async def discover_matches(http: httpx.AsyncClient) -> list[Match]:
    teams_req = http.get(
        f"{POLY_GATEWAY}/v1/sports/teams",
        params={"limit": 500, "filters.league": "mlb"},
    )
    poly_req = http.get(
        f"{POLY_GATEWAY}/v2/leagues/mlb/events",
        params={"limit": 1000, "active": "true", "closed": "false"},
    )
    kal_req = http.get(
        f"{KALSHI_REST}/events",
        params={
            "limit": 200,
            "status": "open",
            "series_ticker": MLB_SERIES,
            "with_nested_markets": "true",
        },
    )
    teams_resp, poly_resp, kal_resp = await asyncio.gather(teams_req, poly_req, kal_req)
    teams_resp.raise_for_status()
    poly_resp.raise_for_status()
    kal_resp.raise_for_status()

    team_rows = teams_resp.json().get("teams", [])
    abbreviations = {
        str(t.get("abbreviation", "")).lower()
        for t in team_rows
        if t.get("abbreviation")
    }

    poly_markets: list[PolyMarket] = []
    poly_body = poly_resp.json()
    for event in poly_body.get("events", []):
        title = str(event.get("title") or "")
        for market in event.get("markets", []):
            parsed = _poly_market(title, market)
            if parsed is not None:
                poly_markets.append(parsed)

    kalshi_markets: list[KalshiMarket] = []
    for event in kal_resp.json().get("events", []):
        event_ticker = str(event.get("event_ticker") or "")
        pair = _ticker_teams(event_ticker, abbreviations)
        if pair is None:
            continue
        event_title = str(event.get("title") or event.get("sub_title") or "")
        for market in event.get("markets", []):
            line = _kalshi_line(market)
            game_date = _kalshi_date(market, event_ticker)
            if line is None or game_date is None:
                continue
            strike_type = str(market.get("strike_type") or "").lower()
            if strike_type and strike_type not in {"greater", "greater_than"}:
                continue
            kalshi_markets.append(
                KalshiMarket(
                    ticker=str(market.get("ticker") or ""),
                    event_ticker=event_ticker,
                    event_title=event_title,
                    teams=pair,
                    game_date=game_date,
                    line=line,
                    strike_type=strike_type,
                    rules_primary=str(market.get("rules_primary") or ""),
                )
            )

    poly_index: dict[tuple[frozenset[str], str, Decimal], list[PolyMarket]] = {}
    kal_index: dict[tuple[frozenset[str], str, Decimal], list[KalshiMarket]] = {}
    for market in poly_markets:
        poly_index.setdefault((market.teams, market.game_date, market.line), []).append(market)
    for market in kalshi_markets:
        kal_index.setdefault((market.teams, market.game_date, market.line), []).append(market)

    matches: list[Match] = []
    for key, p_rows in poly_index.items():
        k_rows = kal_index.get(key, [])
        # Fail closed on ambiguous game/line mappings (e.g. doubleheaders).
        if len(p_rows) == 1 and len(k_rows) == 1 and k_rows[0].ticker:
            matches.append(Match(poly=p_rows[0], kalshi=k_rows[0]))

    logger.info(
        "DISCOVERY %s",
        json.dumps(
            {
                "poly_full_game_totals": len(poly_markets),
                "kalshi_full_game_totals": len(kalshi_markets),
                "matched": len(matches),
                "sample": [
                    {
                        "event": m.poly.event_title,
                        "date": m.poly.game_date,
                        "line": str(m.poly.line),
                        "poly": m.poly.slug,
                        "kalshi": m.kalshi.ticker,
                    }
                    for m in matches[:12]
                ],
            },
            separators=(",", ":"),
        ),
    )
    return matches


async def run_poly_ws(client: PolymarketUS, store: PolyBookStore, slugs: list[str]) -> None:
    ws = client.ws.markets()
    ws.on("market_data", store.on_market_data)
    ws.on("error", lambda e: logger.warning("Polymarket WS error: %s", e))
    await ws.connect()
    store.connected = True
    try:
        for i in range(0, len(slugs), 100):
            chunk = slugs[i : i + 100]
            await ws.subscribe(
                f"arb-books-{i // 100}",
                "SUBSCRIPTION_TYPE_MARKET_DATA",
                chunk,
            )
        logger.info("Polymarket WS subscribed to %d matched market(s)", len(slugs))
        while True:
            await asyncio.sleep(3600)
    finally:
        store.connected = False
        await ws.close()


async def scan_loop(
    matches: list[Match],
    poly_store: PolyBookStore,
    kalshi_books: KalshiCrossBooks,
    *,
    bankroll: Decimal,
    poly_fee_rate: Decimal,
    kalshi_fee_rate: Decimal,
    scan_ms: int,
) -> None:
    tracker = OpportunityTracker()
    by_key = {m.key: m for m in matches}
    last_summary = 0.0
    comparisons = 0
    positive = 0
    try:
        while True:
            now = time.monotonic()
            best_near: tuple[Decimal, Match, str] | None = None
            for match in matches:
                p = poly_store.quote(match.poly.slug)
                if p is None:
                    continue
                k = await kalshi_books.quote(match.kalshi.ticker, max_age_ms=30000)
                if k is None:
                    continue
                comparisons += 1
                plan = best_plan(
                    p,
                    k,
                    bankroll=bankroll,
                    poly_fee_rate=poly_fee_rate,
                    kalshi_fee_rate=kalshi_fee_rate,
                )
                if plan is not None:
                    positive += 1
                tracker.update(match, plan)

                # Track gross one-contract gap for diagnostics even when fees erase it.
                gross_a = Decimal("1") - (p.yes_ask + k.no_ask)
                gross_b = Decimal("1") - (k.yes_ask + p.no_ask)
                gross = max(gross_a, gross_b)
                direction = (
                    "POLY_OVER_KALSHI_UNDER" if gross_a >= gross_b
                    else "KALSHI_OVER_POLY_UNDER"
                )
                if best_near is None or gross > best_near[0]:
                    best_near = (gross, match, direction)

            if now - last_summary >= 30:
                summary: dict[str, Any] = {
                    "matched": len(matches),
                    "comparisons_since_start": comparisons,
                    "positive_observations_since_start": positive,
                    "poly_connected": poly_store.connected,
                }
                if best_near is not None:
                    summary["best_gross_gap"] = str(best_near[0])
                    summary["best_event"] = best_near[1].poly.event_title
                    summary["best_line"] = str(best_near[1].poly.line)
                    summary["best_direction"] = best_near[2]
                logger.info("SUMMARY %s", json.dumps(summary, separators=(",", ":")))
                last_summary = now
            await asyncio.sleep(scan_ms / 1000)
    finally:
        tracker.close_all(by_key)


async def main_async(args: argparse.Namespace) -> None:
    bankroll = Decimal(str(args.bankroll))
    poly_fee_rate = Decimal(os.getenv("POLYMARKET_TAKER_FEE_RATE", "0.05"))
    # Kept explicit/overrideable because Kalshi series fee schedules can vary.
    kalshi_fee_rate = Decimal(os.getenv("KALSHI_TAKER_FEE_RATE", "0.07"))

    logger.info(
        "START shadow_only=true bankroll=%s poly_taker_rate=%s kalshi_taker_rate=%s",
        bankroll,
        poly_fee_rate,
        kalshi_fee_rate,
    )

    async with httpx.AsyncClient(timeout=10.0) as http:
        matches = await discover_matches(http)
    if not matches:
        raise SystemExit("No unambiguous Polymarket/Kalshi MLB full-game total matches found")

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise SystemExit("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required")

    poly_client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
    poly_store = PolyBookStore()
    kalshi_books = KalshiCrossBooks([m.kalshi.ticker for m in matches])

    poly_task = asyncio.create_task(
        run_poly_ws(poly_client, poly_store, [m.poly.slug for m in matches])
    )
    kalshi_task = asyncio.create_task(kalshi_books.run())
    scan_task = asyncio.create_task(
        scan_loop(
            matches,
            poly_store,
            kalshi_books,
            bankroll=bankroll,
            poly_fee_rate=poly_fee_rate,
            kalshi_fee_rate=kalshi_fee_rate,
            scan_ms=args.scan_ms,
        )
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    done, _ = await asyncio.wait(
        {poly_task, kalshi_task, scan_task, asyncio.create_task(stop.wait())},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in done:
        if task is not None and task.done() and not task.cancelled():
            exc = task.exception()
            if exc:
                raise exc

    for task in (poly_task, kalshi_task, scan_task):
        task.cancel()
    await kalshi_books.close()
    try:
        poly_client.close()
    except Exception:
        pass
    await asyncio.gather(poly_task, kalshi_task, scan_task, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket US <-> Kalshi read-only shadow scanner")
    parser.add_argument("--bankroll", type=str, default="50", help="hard total capital cap per shadow trade")
    parser.add_argument("--scan-ms", type=int, default=50, help="local comparison interval in milliseconds")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
