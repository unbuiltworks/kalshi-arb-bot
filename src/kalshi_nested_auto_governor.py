"""Fail-closed risk governor for nested auto-trading supervisors.

This module is transport-agnostic. It owns the controls that must remain true
whether the caller is paper-only today or a separately authorized live executor
later: bounded attempts, event dedupe, cooldown, quantity cap, daily seed-risk
budget, and persistent lockout after an ambiguous/error state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
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
    last_trigger_at: datetime | None = None
    locked_out: bool = False
    lockout_reason: str | None = None

    @staticmethod
    def _day(now: datetime) -> str:
        if now.tzinfo is None:
            now = now.replace(tzinfo=ET)
        return now.astimezone(ET).date().isoformat()

    @staticmethod
    def _aware(now: datetime) -> datetime:
        if now.tzinfo is None:
            return now.replace(tzinfo=ET)
        return now.astimezone(ET)

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
        self.last_trigger_at = None
        # A lockout is intentionally NOT cleared at midnight. Operational
        # ambiguity/error requires explicit human review before reuse.

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

        if self.last_trigger_at is not None:
            elapsed = (self._aware(now) - self._aware(self.last_trigger_at)).total_seconds()
            # Clock rollback is fail-closed instead of bypassing cooldown.
            if elapsed < 0 or elapsed < self.limits.cooldown_seconds:
                return GovernorDecision(False, "COOLDOWN")
        elif self.last_trigger_monotonic is not None:
            elapsed = float(monotonic_now) - self.last_trigger_monotonic
            if elapsed < 0 or elapsed < self.limits.cooldown_seconds:
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
        self.last_trigger_at = self._aware(now)

    def lockout(self, reason: str) -> None:
        self.locked_out = True
        self.lockout_reason = str(reason)[:240] or "unspecified"

    def state(self) -> dict[str, Any]:
        """Full restart-restorable state; safe to serialize as JSON."""
        return {
            "version": 1,
            "day_key": self.day_key,
            "triggers_today": self.triggers_today,
            "seed_cash_risk_today": str(self.seed_cash_risk_today),
            "event_counts": dict(sorted(self.event_counts.items())),
            "pair_keys_seen": sorted(self.pair_keys_seen),
            "last_trigger_at": self.last_trigger_at.isoformat() if self.last_trigger_at else None,
            "locked_out": self.locked_out,
            "lockout_reason": self.lockout_reason,
        }

    @classmethod
    def from_state(
        cls,
        state: dict[str, Any],
        *,
        limits: GovernorLimits,
        now: datetime,
    ) -> "AutoRiskGovernor":
        """Restore persisted state and roll daily counters in ET if needed.

        Malformed values fail closed by raising ValueError; callers should turn a
        corrupt state file into an operational lockout rather than silently
        starting with empty limits.
        """
        if not isinstance(state, dict) or int(state.get("version", -1)) != 1:
            raise ValueError("unsupported governor state")
        event_counts_raw = state.get("event_counts") or {}
        pairs_raw = state.get("pair_keys_seen") or []
        if not isinstance(event_counts_raw, dict) or not isinstance(pairs_raw, list):
            raise ValueError("malformed governor state collections")
        last_raw = state.get("last_trigger_at")
        last_trigger_at = None
        if last_raw:
            last_trigger_at = datetime.fromisoformat(str(last_raw))
            if last_trigger_at.tzinfo is None:
                raise ValueError("persisted last_trigger_at must be timezone-aware")

        governor = cls(
            limits=limits,
            day_key=str(state.get("day_key")) if state.get("day_key") else None,
            triggers_today=int(state.get("triggers_today", 0)),
            seed_cash_risk_today=D(str(state.get("seed_cash_risk_today", "0"))),
            event_counts={str(k): int(v) for k, v in event_counts_raw.items()},
            pair_keys_seen={str(x) for x in pairs_raw},
            last_trigger_monotonic=None,
            last_trigger_at=last_trigger_at,
            locked_out=bool(state.get("locked_out", False)),
            lockout_reason=(str(state.get("lockout_reason"))[:240] if state.get("lockout_reason") else None),
        )
        if governor.triggers_today < 0 or governor.seed_cash_risk_today < 0:
            raise ValueError("negative persisted governor usage")
        if any(v < 0 for v in governor.event_counts.values()):
            raise ValueError("negative persisted event count")
        if governor.triggers_today > limits.max_triggers_per_day:
            raise ValueError("persisted trigger usage exceeds configured limit")
        if governor.seed_cash_risk_today > limits.max_seed_cash_risk_per_day:
            raise ValueError("persisted seed risk exceeds configured limit")
        governor._roll_day_if_needed(now)
        return governor

    def snapshot(self) -> dict[str, object]:
        return {
            "day_key": self.day_key,
            "triggers_today": self.triggers_today,
            "seed_cash_risk_today": str(self.seed_cash_risk_today),
            "events_used": len(self.event_counts),
            "pairs_used": len(self.pair_keys_seen),
            "last_trigger_at": self.last_trigger_at.isoformat() if self.last_trigger_at else None,
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
