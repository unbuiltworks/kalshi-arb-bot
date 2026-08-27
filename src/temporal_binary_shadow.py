"""Venue-agnostic read-only temporal binary shadow engine.

This module never submits, modifies, or cancels orders. It evaluates a simple
q=1 temporal-pair hypothesis independently for each venue/asset/market.

Every incomplete first leg remains visible as stranded exposure and receives
zero realized-profit credit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Outcome = Literal["UP", "DOWN"]
Venue = Literal["polymarket", "kalshi", "gemini"]

FIRST_TIER_ASSETS = ("BTC", "ETH", "SOL", "XRP")
FIRST_TIER_VENUES = ("polymarket", "kalshi", "gemini")
FIRST_TIER_STREAMS = tuple(
    (venue, asset)
    for venue in FIRST_TIER_VENUES
    for asset in FIRST_TIER_ASSETS
)


def taker_fee_usd(quantity: float, price: float, fee_rate: float) -> float:
    """Dynamic binary-market fee model: q * rate * p * (1-p)."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if not 0.0 <= price <= 1.0:
        raise ValueError("price must be between 0 and 1")
    if fee_rate < 0:
        raise ValueError("fee_rate cannot be negative")
    return quantity * fee_rate * price * (1.0 - price)


@dataclass(frozen=True)
class StreamKey:
    venue: Venue
    asset: str
    horizon_minutes: int

    def __post_init__(self) -> None:
        if self.venue not in FIRST_TIER_VENUES:
            raise ValueError(f"unsupported venue: {self.venue}")
        object.__setattr__(self, "asset", self.asset.upper())
        if self.horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")

    @property
    def name(self) -> str:
        return f"{self.venue}:{self.asset}:{self.horizon_minutes}m"


@dataclass(frozen=True)
class ShadowLeg:
    outcome: Outcome
    price: float
    quantity: float
    fee_usd: float
    timestamp_ms: int

    @property
    def cost_usd(self) -> float:
        return self.price * self.quantity + self.fee_usd


@dataclass(frozen=True)
class ShadowPair:
    first: ShadowLeg
    second: ShadowLeg
    kind: str

    @property
    def quantity(self) -> float:
        return min(self.first.quantity, self.second.quantity)

    @property
    def total_cost_usd(self) -> float:
        return self.first.cost_usd + self.second.cost_usd

    @property
    def locked_profit_usd(self) -> float:
        return self.quantity - self.total_cost_usd

    @property
    def locked_profit_per_pair(self) -> float:
        return self.locked_profit_usd / self.quantity

    @property
    def completion_delay_ms(self) -> int:
        return abs(self.second.timestamp_ms - self.first.timestamp_ms)


class TemporalMarketShadow:
    """One conservative q=1 temporal experiment for one binary market."""

    def __init__(
        self,
        *,
        stream: StreamKey,
        market_id: str,
        quantity: float = 1.0,
        min_net_edge: float = 0.03,
        first_leg_max_price: float = 0.45,
        taker_fee_rate: float = 0.0,
    ) -> None:
        if not market_id:
            raise ValueError("market_id is required")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if not 0 <= min_net_edge < 1:
            raise ValueError("min_net_edge must be in [0, 1)")
        if not 0 <= first_leg_max_price <= 1:
            raise ValueError("first_leg_max_price must be in [0, 1]")
        if taker_fee_rate < 0:
            raise ValueError("taker_fee_rate cannot be negative")

        self.stream = stream
        self.market_id = market_id
        self.quantity = quantity
        self.min_net_edge = min_net_edge
        self.first_leg_max_price = first_leg_max_price
        self.taker_fee_rate = taker_fee_rate
        self.first_leg: ShadowLeg | None = None
        self.completed_pair: ShadowPair | None = None
        self.expired = False
        self.expired_at_ms: int | None = None
        self.last_observed_ms: int | None = None
        self.quote_observations = 0

    def _leg(self, outcome: Outcome, price: float, timestamp_ms: int) -> ShadowLeg:
        return ShadowLeg(
            outcome=outcome,
            price=price,
            quantity=self.quantity,
            fee_usd=taker_fee_usd(self.quantity, price, self.taker_fee_rate),
            timestamp_ms=timestamp_ms,
        )

    @staticmethod
    def _pair_edge(a: ShadowLeg, b: ShadowLeg) -> float:
        return ShadowPair(a, b, "candidate").locked_profit_per_pair

    def observe(
        self,
        *,
        up_ask: float | None,
        down_ask: float | None,
        timestamp_ms: int,
    ) -> list[dict]:
        self.last_observed_ms = timestamp_ms
        self.quote_observations += 1
        events: list[dict] = []

        if self.expired or self.completed_pair is not None:
            return events
        if up_ask is None or down_ask is None:
            return events
        if not 0 <= up_ask <= 1 or not 0 <= down_ask <= 1:
            return events

        up = self._leg("UP", up_ask, timestamp_ms)
        down = self._leg("DOWN", down_ask, timestamp_ms)

        if self.first_leg is None:
            if self._pair_edge(up, down) >= self.min_net_edge:
                self.completed_pair = ShadowPair(up, down, "SIMULTANEOUS")
                events.append(self._pair_event(self.completed_pair))
                return events

            first = up if up.cost_usd <= down.cost_usd else down
            if first.price <= self.first_leg_max_price:
                self.first_leg = first
                events.append({
                    "type": "TEMPORAL_FIRST_LEG_SHADOW",
                    "stream": self.stream.name,
                    "market_id": self.market_id,
                    "outcome": first.outcome,
                    "price": first.price,
                    "fee_usd": first.fee_usd,
                    "cost_usd": first.cost_usd,
                    "timestamp_ms": timestamp_ms,
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
                return events

        if self.first_leg is not None:
            opposite = down if self.first_leg.outcome == "UP" else up
            if self._pair_edge(self.first_leg, opposite) >= self.min_net_edge:
                self.completed_pair = ShadowPair(self.first_leg, opposite, "TEMPORAL")
                events.append(self._pair_event(self.completed_pair))
        return events

    def _pair_event(self, pair: ShadowPair) -> dict:
        return {
            "type": "TEMPORAL_LOCKED_PAIR_SHADOW",
            "stream": self.stream.name,
            "market_id": self.market_id,
            "kind": pair.kind,
            "first_outcome": pair.first.outcome,
            "first_price": pair.first.price,
            "second_outcome": pair.second.outcome,
            "second_price": pair.second.price,
            "quantity": pair.quantity,
            "fees_usd": pair.first.fee_usd + pair.second.fee_usd,
            "total_cost_usd": pair.total_cost_usd,
            "locked_profit_usd": pair.locked_profit_usd,
            "locked_profit_per_pair": pair.locked_profit_per_pair,
            "completion_delay_ms": pair.completion_delay_ms,
            "timestamp_ms": max(pair.first.timestamp_ms, pair.second.timestamp_ms),
            "shadow_only": True,
            "order_entry": False,
            "realized_pnl": 0.0,
        }

    def expire(self, timestamp_ms: int) -> dict | None:
        if self.expired or self.completed_pair is not None:
            return None
        self.expired = True
        self.expired_at_ms = timestamp_ms
        if self.first_leg is None:
            return None
        return {
            "type": "TEMPORAL_STRANDED_FIRST_LEG_SHADOW",
            "stream": self.stream.name,
            "market_id": self.market_id,
            "outcome": self.first_leg.outcome,
            "price": self.first_leg.price,
            "cost_usd": self.first_leg.cost_usd,
            "held_ms": max(0, timestamp_ms - self.first_leg.timestamp_ms),
            "shadow_only": True,
            "order_entry": False,
            "realized_pnl": 0.0,
        }

    def snapshot(self) -> dict:
        pair = self.completed_pair
        return {
            "stream": self.stream.name,
            "market_id": self.market_id,
            "quote_observations": self.quote_observations,
            "state": (
                "COMPLETED" if pair is not None else
                "EXPIRED" if self.expired else
                "ONE_SIDED" if self.first_leg is not None else
                "EMPTY"
            ),
            "first_leg_outcome": self.first_leg.outcome if self.first_leg else None,
            "first_leg_cost_usd": self.first_leg.cost_usd if self.first_leg else None,
            "locked_profit_usd": pair.locked_profit_usd if pair else 0.0,
            "locked_profit_per_pair": pair.locked_profit_per_pair if pair else None,
            "completion_delay_ms": pair.completion_delay_ms if pair else None,
            "shadow_only": True,
            "order_entry": False,
            "realized_pnl": 0.0,
        }
