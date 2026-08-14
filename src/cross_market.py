"""Cross-market sportsbook + Kalshi arb/middle optimizer.

Shadow-only module: it never places sportsbook or Kalshi orders.

The first supported structure is the one seen frequently in live totals:
  * one or more sportsbook OVER bets at half-point totals <= H
  * Kalshi NO on "total > H"

For any final score <= H, Kalshi pays $1/contract. For a score > H,
all sportsbook OVER legs at lines <= H pay. Lower sportsbook lines create
an additional middle payoff without reducing the guaranteed floor.

All money is represented in cents. Kalshi quantities are represented in
hundredths of a contract because the current V2 API exposes fixed-point
contract counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Iterable


def american_to_decimal(odds: int) -> Decimal:
    """Convert American odds to decimal odds exactly enough for cent staking."""
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return Decimal(1) + (Decimal(odds) / Decimal(100))
    return Decimal(1) + (Decimal(100) / Decimal(abs(odds)))


def _is_half_point(line: Decimal) -> bool:
    doubled = line * 2
    return doubled == doubled.to_integral_value() and int(doubled) % 2 == 1


@dataclass(frozen=True)
class SportsbookOverQuote:
    book: str
    event_key: str
    line: Decimal
    american_odds: int
    max_stake_cents: int
    deeplink: str = ""
    observed_ms: int = 0
    source_id: str = ""

    def __post_init__(self) -> None:
        if self.max_stake_cents <= 0:
            raise ValueError("max_stake_cents must be positive")
        if not _is_half_point(self.line):
            raise ValueError("only half-point totals are supported in v1")

    @property
    def decimal_odds(self) -> Decimal:
        return american_to_decimal(self.american_odds)

    @property
    def max_payout_cents(self) -> int:
        return int(Decimal(self.max_stake_cents) * self.decimal_odds)


@dataclass(frozen=True)
class KalshiNoQuote:
    ticker: str
    event_key: str
    line: Decimal
    no_price_cents: int
    no_depth_hundredths: int
    observed_ms: int = 0

    def __post_init__(self) -> None:
        if not (0 < self.no_price_cents < 100):
            raise ValueError("no_price_cents must be between 1 and 99")
        if self.no_depth_hundredths <= 0:
            raise ValueError("no_depth_hundredths must be positive")
        if not _is_half_point(self.line):
            raise ValueError("only half-point totals are supported in v1")


@dataclass(frozen=True)
class PlannedSportsbookLeg:
    quote: SportsbookOverQuote
    stake_cents: int
    payout_cents: int


@dataclass(frozen=True)
class CrossMarketPlan:
    sportsbook_legs: tuple[PlannedSportsbookLeg, ...]
    kalshi: KalshiNoQuote
    kalshi_count_hundredths: int
    kalshi_fee_cents: int
    total_cost_cents: int
    low_payout_cents: int
    high_payout_cents: int
    middle_payout_cents: int
    guaranteed_profit_cents: int
    middle_profit_cents: int
    guaranteed_roi: float
    middle_score: Decimal

    @property
    def kalshi_contracts(self) -> Decimal:
        return Decimal(self.kalshi_count_hundredths) / Decimal(100)

    def as_dict(self) -> dict:
        return {
            "sportsbook_legs": [
                {
                    "book": leg.quote.book,
                    "line": str(leg.quote.line),
                    "american_odds": leg.quote.american_odds,
                    "stake_cents": leg.stake_cents,
                    "payout_cents": leg.payout_cents,
                    "deeplink": leg.quote.deeplink,
                }
                for leg in self.sportsbook_legs
            ],
            "kalshi": {
                "ticker": self.kalshi.ticker,
                "line": str(self.kalshi.line),
                "no_price_cents": self.kalshi.no_price_cents,
                "contracts": str(self.kalshi_contracts),
                "fee_cents": self.kalshi_fee_cents,
            },
            "total_cost_cents": self.total_cost_cents,
            "low_payout_cents": self.low_payout_cents,
            "high_payout_cents": self.high_payout_cents,
            "middle_payout_cents": self.middle_payout_cents,
            "guaranteed_profit_cents": self.guaranteed_profit_cents,
            "middle_profit_cents": self.middle_profit_cents,
            "guaranteed_roi": round(self.guaranteed_roi, 6),
            "middle_score": str(self.middle_score),
        }


def kalshi_taker_fee_cents(count_hundredths: int, price_cents: int, rate: Decimal = Decimal("0.07")) -> int:
    """Kalshi taker fee, rounded up to the next cent.

    Formula: ceil(rate * C * P * (1-P) dollars) to the next cent.
    C may be fractional to 2 decimal places.
    """
    if count_hundredths <= 0:
        return 0
    count = Decimal(count_hundredths) / Decimal(100)
    p = Decimal(price_cents) / Decimal(100)
    raw_fee_dollars = rate * count * p * (Decimal(1) - p)
    raw_fee_cents = raw_fee_dollars * Decimal(100)
    return int(raw_fee_cents.to_integral_value(rounding="ROUND_CEILING"))


def _rank_quotes(quotes: Iterable[SportsbookOverQuote]) -> list[SportsbookOverQuote]:
    return sorted(quotes, key=lambda q: (-q.decimal_odds, q.line, q.book))


def _allocate_high_payout(
    target_payout_cents: int,
    quotes: Iterable[SportsbookOverQuote],
) -> tuple[tuple[PlannedSportsbookLeg, ...], int] | None:
    """Cheapest sportsbook stakes that can produce target payout when total > H."""
    if target_payout_cents <= 0:
        return None

    remaining = target_payout_cents
    legs: list[PlannedSportsbookLeg] = []
    actual_payout = 0

    for quote in _rank_quotes(quotes):
        if remaining <= 0:
            break

        max_payout = quote.max_payout_cents
        wanted = min(remaining, max_payout)
        stake = int(
            (Decimal(wanted) / quote.decimal_odds).to_integral_value(
                rounding="ROUND_CEILING"
            )
        )
        stake = min(stake, quote.max_stake_cents)
        if stake <= 0:
            continue

        payout = int(Decimal(stake) * quote.decimal_odds)
        if payout <= 0:
            continue

        legs.append(PlannedSportsbookLeg(quote=quote, stake_cents=stake, payout_cents=payout))
        actual_payout += payout
        remaining = max(0, target_payout_cents - actual_payout)

    if actual_payout < target_payout_cents:
        return None
    return tuple(legs), actual_payout


def _plan_for_count(
    quotes: tuple[SportsbookOverQuote, ...],
    kalshi: KalshiNoQuote,
    count_hundredths: int,
    bankroll_cents: int,
    taker_fee_rate: Decimal,
) -> CrossMarketPlan | None:
    if count_hundredths <= 0 or count_hundredths > kalshi.no_depth_hundredths:
        return None

    low_payout_cents = count_hundredths
    allocation = _allocate_high_payout(low_payout_cents, quotes)
    if allocation is None:
        return None
    legs, high_payout_cents = allocation

    sportsbook_cost = sum(leg.stake_cents for leg in legs)
    kalshi_cost_cents = math.ceil(
        (count_hundredths * kalshi.no_price_cents) / 100
    )
    kalshi_fee = kalshi_taker_fee_cents(
        count_hundredths, kalshi.no_price_cents, taker_fee_rate
    )
    total_cost = sportsbook_cost + kalshi_cost_cents + kalshi_fee
    if total_cost > bankroll_cents:
        return None

    middle_score = kalshi.line - Decimal("0.5")
    middle_sportsbook_payout = sum(
        leg.payout_cents for leg in legs if middle_score > leg.quote.line
    )
    middle_payout = low_payout_cents + middle_sportsbook_payout

    guaranteed_profit = min(low_payout_cents, high_payout_cents) - total_cost
    middle_profit = middle_payout - total_cost
    guaranteed_roi = guaranteed_profit / total_cost if total_cost else -1.0

    return CrossMarketPlan(
        sportsbook_legs=legs,
        kalshi=kalshi,
        kalshi_count_hundredths=count_hundredths,
        kalshi_fee_cents=kalshi_fee,
        total_cost_cents=total_cost,
        low_payout_cents=low_payout_cents,
        high_payout_cents=high_payout_cents,
        middle_payout_cents=middle_payout,
        guaranteed_profit_cents=guaranteed_profit,
        middle_profit_cents=middle_profit,
        guaranteed_roi=guaranteed_roi,
        middle_score=middle_score,
    )


def optimize_adjacent_total(
    sportsbook_quotes: Iterable[SportsbookOverQuote],
    kalshi: KalshiNoQuote,
    bankroll_cents: int,
    *,
    taker_fee_rate: Decimal = Decimal("0.07"),
    max_quote_age_ms: int | None = None,
    now_ms: int | None = None,
) -> CrossMarketPlan | None:
    """Find the max-dollar guaranteed plan for one event/total."""
    if bankroll_cents <= 0:
        raise ValueError("bankroll_cents must be positive")

    quotes = tuple(
        q for q in sportsbook_quotes
        if q.event_key == kalshi.event_key and q.line <= kalshi.line
    )
    if not quotes:
        return None

    if max_quote_age_ms is not None:
        if now_ms is None:
            raise ValueError("now_ms is required when max_quote_age_ms is set")
        if kalshi.observed_ms and now_ms - kalshi.observed_ms > max_quote_age_ms:
            return None
        quotes = tuple(
            q for q in quotes
            if not q.observed_ms or now_ms - q.observed_ms <= max_quote_age_ms
        )
        if not quotes:
            return None

    ranked = _rank_quotes(quotes)
    candidates: set[int] = {1, kalshi.no_depth_hundredths}
    cumulative_payout = 0
    for q in ranked:
        cumulative_payout += q.max_payout_cents
        candidates.add(min(cumulative_payout, kalshi.no_depth_hundredths))

    def feasible(count_hundredths: int) -> bool:
        return _plan_for_count(
            quotes, kalshi, count_hundredths, bankroll_cents, taker_fee_rate
        ) is not None

    lo, hi = 1, kalshi.no_depth_hundredths
    max_feasible = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if feasible(mid):
            max_feasible = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if max_feasible:
        candidates.add(max_feasible)

    expanded: set[int] = set()
    for c in candidates:
        for delta in range(-5, 6):
            v = c + delta
            if 1 <= v <= kalshi.no_depth_hundredths:
                expanded.add(v)

    best: CrossMarketPlan | None = None
    for count in expanded:
        plan = _plan_for_count(
            quotes, kalshi, count, bankroll_cents, taker_fee_rate
        )
        if plan is None:
            continue
        if best is None:
            best = plan
            continue
        key = (
            plan.guaranteed_profit_cents,
            plan.middle_profit_cents,
            plan.guaranteed_roi,
        )
        best_key = (
            best.guaranteed_profit_cents,
            best.middle_profit_cents,
            best.guaranteed_roi,
        )
        if key > best_key:
            best = plan

    return best
