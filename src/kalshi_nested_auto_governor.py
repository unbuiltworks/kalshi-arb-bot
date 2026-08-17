"""Fail-closed risk governor for nested auto-trading supervisors.

This module is transport-agnostic. It owns the controls that must remain true
whether the caller is paper-only today or a separately authorized live executor
later: bounded attempts, event dedupe, cooldown, quantity cap, daily seed-risk
budget, and permanent lockout after an ambiguous/error state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
D = Decimal


@dataclass(frozen=True)
class GovernorLimits:
    max_quantity: Decimal = D("1")
    max_triggers_per_day: int = 5
    max_triggers_per_event: int = 1
    cooldown_seconds: float = 60.0
    max_seed_cash_risk_per_attempt: Decimal = D("0.02")
    max_seed_cash_risk_per_day: Decimal = D("0.10")

    def __post_init__(self) -> None:
        if self.max_quantity != D("1"):
            raise ValueError("initial autonomous governor must remain exactly one contract")
        if self.max_triggers_per_day < 1:
            raise ValueError("max_triggers_per_day must be positive")
        if self.max_triggers_per_event < 1:
            raise ValueError("max_triggers_per_event must be positive")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        if self.max_seed_cash_risk_per_attempt <= 0:
            raise ValueError("max seed risk per attempt must be positive")
        if self.max_seed_cash_risk_per_day < self.max_seed_cash_risk_per_attempt:
            raise ValueError("daily seed-risk budget cannot be below one-attempt budget")


@dataclass
class GovernorDecision:
    allowed: bool
    reason: str


@dataclass
class AutoRiskGovernor:
    limits: GovernorLimits = field(default_factory=GovernorLimits)
    day_key: str | None = None
    triggers_today: int = 0
    seed_cash_risk_today: Decimal = D("0")
    event_counts: dict[str, int] = field(default_factory=dict)
    pair_keys_seen: set[str] = field(default_factory=set)
    last_trigger_monotonic: float | None = None
    locked_out: bool = False
    lockout_reason: str | None = None

    @staticmethod
    def _day(now: datetime) -> str:
        if now.tzinfo is None:
            now = now.replace(tzinfo=ET)
        return now.astimezone(ET).date().isoformat()

    def _roll_day_if_needed(self, now: datetime) -> None:
        key = self._day(now)
        if self.day_key == key:
            return
        self.day_key = key
        self.triggers_today = 0
        self.seed_cash_risk_today = D("0")
        self.event_counts.clear()
        self.pair_keys_seen.clear()
        self.last_trigger_monotonic = None

    def decision(
        self,
        *,
        event_ticker: str,
        pair_key: str,
        quantity: Decimal,
        seed_cash_risk: Decimal,
        now: datetime,
        monotonic_now: float,
    ) -> GovernorDecision:
        self._roll_day_if_needed(now)
        if self.locked_out:
            return GovernorDecision(False, f"LOCKED_OUT:{self.lockout_reason or 'unknown'}")
        if D(quantity) != self.limits.max_quantity:
            return GovernorDecision(False, "QUANTITY_NOT_ONE")
        risk = D(seed_cash_risk)
        if risk < 0 or risk > self.limits.max_seed_cash_risk_per_attempt:
            return GovernorDecision(False, "SEED_RISK_OVER_ATTEMPT_LIMIT")
        if self.triggers_today >= self.limits.max_triggers_per_day:
            return GovernorDecision(False, "DAILY_TRIGGER_LIMIT")
        if self.seed_cash_risk_today + risk > self.limits.max_seed_cash_risk_per_day:
            return GovernorDecision(False, "DAILY_SEED_RISK_LIMIT")
        if self.event_counts.get(str(event_ticker), 0) >= self.limits.max_triggers_per_event:
            return GovernorDecision(False, "EVENT_TRIGGER_LIMIT")
        if str(pair_key) in self.pair_keys_seen:
            return GovernorDecision(False, "PAIR_ALREADY_USED")
        if self.last_trigger_monotonic is not None:
            elapsed = float(monotonic_now) - self.last_trigger_monotonic
            if elapsed < self.limits.cooldown_seconds:
                return GovernorDecision(False, "COOLDOWN")
        return GovernorDecision(True, "ALLOW")

    def record_trigger(
        self,
        *,
        event_ticker: str,
        pair_key: str,
        seed_cash_risk: Decimal,
        now: datetime,
        monotonic_now: float,
    ) -> None:
        self._roll_day_if_needed(now)
        self.triggers_today += 1
        self.seed_cash_risk_today += D(seed_cash_risk)
        event = str(event_ticker)
        self.event_counts[event] = self.event_counts.get(event, 0) + 1
        self.pair_keys_seen.add(str(pair_key))
        self.last_trigger_monotonic = float(monotonic_now)

    def lockout(self, reason: str) -> None:
        self.locked_out = True
        self.lockout_reason = str(reason)[:240] or "unspecified"

    def snapshot(self) -> dict[str, object]:
        return {
            "day_key": self.day_key,
            "triggers_today": self.triggers_today,
            "seed_cash_risk_today": str(self.seed_cash_risk_today),
            "events_used": len(self.event_counts),
            "pairs_used": len(self.pair_keys_seen),
            "locked_out": self.locked_out,
            "lockout_reason": self.lockout_reason,
            "limits": {
                "max_quantity": str(self.limits.max_quantity),
                "max_triggers_per_day": self.limits.max_triggers_per_day,
                "max_triggers_per_event": self.limits.max_triggers_per_event,
                "cooldown_seconds": self.limits.cooldown_seconds,
                "max_seed_cash_risk_per_attempt": str(self.limits.max_seed_cash_risk_per_attempt),
                "max_seed_cash_risk_per_day": str(self.limits.max_seed_cash_risk_per_day),
            },
        }
