"""Live shadow scanner: OpticOdds sportsbook stream + Kalshi WebSocket orderbook.

This process never places orders. It measures whether cross-market total arbs
exist, how large they are, and how long they survive.

Pairing is explicit by configuration because a bad event/settlement match can
turn an apparent arb into an unhedged bet. Automatic fuzzy matching is not used
as an execution gate. Kalshi fee rate is also explicit per configured pair;
markets can have different fee schedules, so the scanner refuses to silently
assume one universal rate.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from src.cross_market import KalshiNoQuote, optimize_adjacent_total
from src.kalshi_live_book import LiveKalshiBooks
from src.opticodds_stream import OpticOddsStream, SportsbookQuoteStore

logger = logging.getLogger("kalshi_arb.cross_shadow")


@dataclass(frozen=True)
class PairConfig:
    event_key: str
    fixture_id: str
    kalshi_ticker: str
    kalshi_line: Decimal
    kalshi_taker_fee_rate: Decimal
    sport: str
    league: str
    market: str
    enabled: bool = True


def load_pairs(path: str) -> tuple[PairConfig, ...]:
    raw = json.loads(Path(path).read_text())
    items = raw.get("pairs", raw) if isinstance(raw, dict) else raw
    result: list[PairConfig] = []
    for item in items:
        if not item.get("enabled", True):
            continue
        if "kalshi_taker_fee_rate" not in item:
            raise ValueError(
                f"{item.get('event_key', '<unknown>')}: kalshi_taker_fee_rate is required"
            )
        fee_rate = Decimal(str(item["kalshi_taker_fee_rate"]))
        if fee_rate < 0 or fee_rate > Decimal("0.50"):
            raise ValueError(
                f"{item.get('event_key', '<unknown>')}: implausible Kalshi fee rate {fee_rate}"
            )
        result.append(
            PairConfig(
                event_key=str(item["event_key"]),
                fixture_id=str(item["fixture_id"]),
                kalshi_ticker=str(item["kalshi_ticker"]),
                kalshi_line=Decimal(str(item["kalshi_line"])),
                kalshi_taker_fee_rate=fee_rate,
                sport=str(item["sport"]).lower(),
                league=str(item["league"]),
                market=str(item["market"]),
                enabled=True,
            )
        )
    return tuple(result)


def live_book_to_quote(pair: PairConfig, price_dollars: Decimal, count: Decimal) -> KalshiNoQuote | None:
    """Normalize current executable Kalshi NO ask into optimizer units.

    V1 of the optimizer deliberately skips sub-cent Kalshi prices. That is safer
    than rounding an executable 36.1c ask down to 36c and inventing edge.
    """
    cents = price_dollars * 100
    if cents != cents.to_integral_value():
        return None
    price_cents = int(cents)
    if price_cents <= 0 or price_cents >= 100:
        return None

    count_hundredths = int(count * 100)
    if count_hundredths <= 0:
        return None

    return KalshiNoQuote(
        ticker=pair.kalshi_ticker,
        event_key=pair.event_key,
        line=pair.kalshi_line,
        no_price_cents=price_cents,
        no_depth_hundredths=count_hundredths,
        # The locally maintained book is current as of the live WS stream; use
        # retrieval time for cross-feed freshness rather than age of last change.
        observed_ms=int(time.time() * 1000),
    )


async def stream_group(
    api_key: str,
    pairs: tuple[PairConfig, ...],
    sportsbooks: tuple[str, ...],
    store: SportsbookQuoteStore,
    stake_cap_cents: int,
) -> None:
    fixture_to_event = {p.fixture_id: p.event_key for p in pairs}
    stream = OpticOddsStream(
        api_key,
        sport=pairs[0].sport,
        sportsbooks=sportsbooks,
        leagues=sorted({p.league for p in pairs}),
        markets=sorted({p.market for p in pairs}),
        fixture_ids=sorted({p.fixture_id for p in pairs}),
        live_only=True,
        include_deep_link=True,
    )

    async for event in stream.events():
        if event.event not in {"odds", "locked-odds"}:
            continue
        try:
            payload = json.loads(event.data)
        except json.JSONDecodeError:
            continue
        if event.event == "odds":
            store.apply_odds_payload(
                payload,
                fixture_to_event=fixture_to_event,
                configured_stake_cap_cents=stake_cap_cents,
            )
        else:
            store.apply_locked_payload(payload)


class OpportunityTracker:
    """Deduplicate opportunity logs and measure edge lifetime."""

    def __init__(self) -> None:
        self._open: dict[str, tuple[int, int]] = {}

    def observe(self, pair: PairConfig, plan: Any | None, min_profit: int, min_roi: float) -> None:
        now = int(time.time() * 1000)
        qualifies = (
            plan is not None
            and plan.guaranteed_profit_cents >= min_profit
            and plan.guaranteed_roi >= min_roi
        )
        current = self._open.get(pair.event_key)

        if qualifies:
            fingerprint = hash(
                (
                    plan.kalshi.no_price_cents,
                    plan.kalshi_count_hundredths,
                    tuple(
                        (
                            leg.quote.book,
                            str(leg.quote.line),
                            leg.quote.american_odds,
                            leg.stake_cents,
                        )
                        for leg in plan.sportsbook_legs
                    ),
                )
            )
            if current is None:
                self._open[pair.event_key] = (now, fingerprint)
                logger.warning(
                    "OPEN %s floor=$%.2f roi=%.2f%% middle=$%.2f cost=$%.2f fee_rate=%s plan=%s",
                    pair.event_key,
                    plan.guaranteed_profit_cents / 100,
                    plan.guaranteed_roi * 100,
                    plan.middle_profit_cents / 100,
                    plan.total_cost_cents / 100,
                    pair.kalshi_taker_fee_rate,
                    json.dumps(plan.as_dict(), separators=(",", ":")),
                )
            elif current[1] != fingerprint:
                self._open[pair.event_key] = (current[0], fingerprint)
                logger.info(
                    "MOVE %s floor=$%.2f roi=%.2f%% age_ms=%d",
                    pair.event_key,
                    plan.guaranteed_profit_cents / 100,
                    plan.guaranteed_roi * 100,
                    now - current[0],
                )
            return

        if current is not None:
            opened_ms, _ = self._open.pop(pair.event_key)
            logger.warning("CLOSE %s lifetime_ms=%d", pair.event_key, now - opened_ms)


async def run(args: argparse.Namespace) -> None:
    pairs = load_pairs(args.pairs)
    if not pairs:
        raise SystemExit("No enabled cross-market pairs in config")

    api_key = os.getenv("OPTICODDS_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPTICODDS_API_KEY is required")

    sportsbooks = tuple(
        x.strip()
        for x in os.getenv(
            "CROSS_MARKET_SPORTSBOOKS",
            "Caesars,DraftKings,BetMGM,Fanatics",
        ).split(",")
        if x.strip()
    )
    if not sportsbooks:
        raise SystemExit("CROSS_MARKET_SPORTSBOOKS is empty")

    store = SportsbookQuoteStore()
    tracker = OpportunityTracker()
    kalshi_books = LiveKalshiBooks([p.kalshi_ticker for p in pairs])

    by_sport: dict[str, list[PairConfig]] = {}
    for pair in pairs:
        by_sport.setdefault(pair.sport, []).append(pair)

    tasks = [
        asyncio.create_task(
            stream_group(
                api_key,
                tuple(group),
                sportsbooks,
                store,
                args.sportsbook_stake_cap_cents,
            )
        )
        for group in by_sport.values()
    ]
    tasks.append(asyncio.create_task(kalshi_books.run()))

    logger.info(
        "SHADOW ONLY: %d pair(s), books=%s, bankroll=$%.2f, scan=%dms",
        len(pairs),
        ",".join(sportsbooks),
        args.bankroll_cents / 100,
        args.scan_interval_ms,
    )

    try:
        while True:
            cycle_started = time.monotonic()
            now_ms = int(time.time() * 1000)
            for pair in pairs:
                try:
                    live = await kalshi_books.best_no_ask(pair.kalshi_ticker)
                    kalshi_quote = None
                    if live is not None:
                        kalshi_quote = live_book_to_quote(
                            pair, live.price_dollars, live.count
                        )
                    sportsbook_quotes = store.for_event(pair.event_key)
                    plan = None
                    if kalshi_quote and sportsbook_quotes:
                        plan = optimize_adjacent_total(
                            sportsbook_quotes,
                            kalshi_quote,
                            args.bankroll_cents,
                            taker_fee_rate=pair.kalshi_taker_fee_rate,
                            max_quote_age_ms=args.max_quote_age_ms,
                            now_ms=now_ms,
                        )
                    tracker.observe(
                        pair,
                        plan,
                        min_profit=args.min_profit_cents,
                        min_roi=args.min_roi,
                    )
                except Exception:
                    logger.exception("scan failed for %s", pair.event_key)

            elapsed = (time.monotonic() - cycle_started) * 1000
            await asyncio.sleep(max(0.005, (args.scan_interval_ms - elapsed) / 1000))
    finally:
        for task in tasks:
            task.cancel()
        await kalshi_books.close()
        await asyncio.gather(*tasks, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sportsbook + Kalshi live shadow scanner")
    parser.add_argument("--pairs", default="config/cross_market_pairs.json")
    parser.add_argument("--bankroll-cents", type=int, default=100000)
    parser.add_argument("--sportsbook-stake-cap-cents", type=int, default=30000)
    parser.add_argument("--scan-interval-ms", type=int, default=50)
    parser.add_argument("--max-quote-age-ms", type=int, default=1500)
    parser.add_argument("--min-profit-cents", type=int, default=2500)
    parser.add_argument("--min-roi", type=float, default=0.03)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-8s %(message)s",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
