"""Read-only, exchange-backed recovery for continuous nested automation.

The live bot must not trust container-local state after a restart.  Kalshi's
recent Orders API is the authoritative durable record for any attempt that
reached the exchange because our executor uses deterministic client_order_ids:

    <attempt-root>-seed
    <attempt-root>-maker

This module reconstructs conservative daily governor usage from those raw order
rows.  It performs no order writes/cancels.  Unexpected/malformed own-prefixed
history fails closed instead of being ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from src.kalshi_nested_auto_governor import AutoRiskGovernor, GovernorLimits
from src.kalshi_nested_probe_transport import KalshiV2RestTransport

D = Decimal
ET = ZoneInfo("America/New_York")
AUTO_PREFIX = "nested-auto-"
CLIENT_ID_RE = re.compile(r"^(nested-auto-[A-Za-z0-9_-]{1,48})-(seed|maker)$")
STRIKE_SUFFIX_RE = re.compile(r"-T-?\d+(?:\.\d+)?$")
TERMINAL_STATUSES = {"executed", "canceled", "cancelled"}
RESTING_STATUSES = {"resting", "open", "pending"}
CONSERVATIVE_SEED_RISK_PER_ATTEMPT = D("0.02")


@dataclass(frozen=True)
class RecoveredAttempt:
    root: str
    event_ticker: str
    seed_order: dict[str, Any] | None
    maker_order: dict[str, Any] | None
    created_at: datetime
    has_resting_order: bool
    seed_fill_count: Decimal
    maker_fill_count: Decimal

    @property
    def order_count(self) -> int:
        return int(self.seed_order is not None) + int(self.maker_order is not None)

    @property
    def seeded_without_full_maker(self) -> bool:
        return self.seed_fill_count > 0 and self.maker_fill_count < D("1")

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "event_ticker": self.event_ticker,
            "created_at": self.created_at.isoformat(),
            "order_count": self.order_count,
            "has_seed": self.seed_order is not None,
            "has_maker": self.maker_order is not None,
            "has_resting_order": self.has_resting_order,
            "seed_fill_count": str(self.seed_fill_count),
            "maker_fill_count": str(self.maker_fill_count),
            "seeded_without_full_maker": self.seeded_without_full_maker,
        }


@dataclass(frozen=True)
class ExchangeRecovery:
    attempts: tuple[RecoveredAttempt, ...]
    governor: AutoRiskGovernor
    lockout_reasons: tuple[str, ...]

    @property
    def safe_to_scan(self) -> bool:
        return not self.governor.locked_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "safe_to_scan": self.safe_to_scan,
            "attempts": [a.as_dict() for a in self.attempts],
            "attempt_count": len(self.attempts),
            "seed_only_exposure_count": sum(a.seeded_without_full_maker for a in self.attempts),
            "lockout_reasons": list(self.lockout_reasons),
            "governor": self.governor.snapshot(),
            "read_only": True,
            "order_entry": False,
        }


def _parse_time(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("own order missing created_time")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("own order created_time must be timezone-aware")
    return parsed.astimezone(ET)


def event_from_market_ticker(ticker: str) -> str:
    clean = str(ticker or "").strip()
    if not clean:
        raise ValueError("own order missing ticker")
    event = STRIKE_SUFFIX_RE.sub("", clean)
    if event == clean or not event:
        raise ValueError(f"cannot derive nested event from ticker {clean!r}")
    return event


def _fill_count(row: dict[str, Any] | None) -> Decimal:
    if row is None:
        return D("0")
    return D(str(row.get("fill_count_fp") or row.get("fill_count") or "0"))


def _status(row: dict[str, Any] | None) -> str:
    if row is None:
        return ""
    return str(row.get("status") or "").strip().lower()


def reconstruct_attempts(
    rows: Iterable[dict[str, Any]],
    *,
    prefix: str = AUTO_PREFIX,
) -> tuple[RecoveredAttempt, ...]:
    """Group deterministic seed/maker client IDs and validate their shape.

    Any row carrying our prefix must match the exact `<root>-seed|maker` schema.
    Duplicate seed/maker rows for one root are rejected because they would make
    restart accounting ambiguous.
    """
    clean_prefix = str(prefix).strip()
    if not clean_prefix:
        raise ValueError("recovery prefix is required")

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for raw_row in rows:
        row = dict(raw_row)
        client_id = str(row.get("client_order_id") or "")
        if not client_id.startswith(clean_prefix):
            continue
        match = CLIENT_ID_RE.fullmatch(client_id)
        if not match:
            raise ValueError(f"malformed own client_order_id {client_id!r}")
        root, leg = match.groups()
        slot = grouped.setdefault(root, {})
        if leg in slot:
            raise ValueError(f"duplicate {leg} order for attempt {root}")
        slot[leg] = row

    attempts: list[RecoveredAttempt] = []
    for root, legs in sorted(grouped.items()):
        seed = legs.get("seed")
        maker = legs.get("maker")
        present = [r for r in (seed, maker) if r is not None]
        if not present:
            continue
        events = {event_from_market_ticker(str(r.get("ticker") or "")) for r in present}
        if len(events) != 1:
            raise ValueError(f"attempt {root} spans multiple events")
        created = min(_parse_time(r.get("created_time")) for r in present)
        statuses = {_status(r) for r in present}
        if "" in statuses:
            raise ValueError(f"attempt {root} has order with missing status")
        # Unknown status is unsafe; explicitly recognize terminal/resting families.
        unknown = statuses - TERMINAL_STATUSES - RESTING_STATUSES
        if unknown:
            raise ValueError(f"attempt {root} has unknown order status {sorted(unknown)}")
        attempts.append(RecoveredAttempt(
            root=root,
            event_ticker=next(iter(events)),
            seed_order=seed,
            maker_order=maker,
            created_at=created,
            has_resting_order=bool(statuses & RESTING_STATUSES),
            seed_fill_count=_fill_count(seed),
            maker_fill_count=_fill_count(maker),
        ))
    attempts.sort(key=lambda a: (a.created_at, a.root))
    return tuple(attempts)


def recover_governor_from_orders(
    rows: Iterable[dict[str, Any]],
    *,
    limits: GovernorLimits,
    now: datetime,
    prefix: str = AUTO_PREFIX,
) -> ExchangeRecovery:
    """Reconstruct today's conservative governor usage from exchange orders."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    now_et = now.astimezone(ET)
    attempts = reconstruct_attempts(rows, prefix=prefix)
    today = now_et.date()
    todays = tuple(a for a in attempts if a.created_at.astimezone(ET).date() == today)

    governor = AutoRiskGovernor(limits)
    governor._roll_day_if_needed(now_et)
    lockouts: list[str] = []

    for attempt in todays:
        # We deliberately count the maximum bounded seed cash risk for every
        # exchange-visible attempt, even if seed was rejected/unfilled. This is
        # conservative and prevents a restart from creating extra daily capacity.
        governor.triggers_today += 1
        governor.seed_cash_risk_today += CONSERVATIVE_SEED_RISK_PER_ATTEMPT
        governor.event_counts[attempt.event_ticker] = governor.event_counts.get(attempt.event_ticker, 0) + 1
        governor.pair_keys_seen.add(attempt.root)
        if governor.last_trigger_at is None or attempt.created_at > governor.last_trigger_at:
            governor.last_trigger_at = attempt.created_at

        if attempt.maker_order is not None and attempt.seed_order is None:
            lockouts.append(f"MAKER_WITHOUT_SEED:{attempt.root}")
        if attempt.has_resting_order:
            lockouts.append(f"RESTING_ORDER_AT_STARTUP:{attempt.root}")

    if governor.triggers_today > limits.max_triggers_per_day:
        lockouts.append("RECOVERED_DAILY_TRIGGER_LIMIT_EXCEEDED")
    if governor.seed_cash_risk_today > limits.max_seed_cash_risk_per_day:
        lockouts.append("RECOVERED_DAILY_SEED_RISK_LIMIT_EXCEEDED")
    if any(count > limits.max_triggers_per_event for count in governor.event_counts.values()):
        lockouts.append("RECOVERED_EVENT_TRIGGER_LIMIT_EXCEEDED")

    if lockouts:
        governor.lockout(";".join(sorted(set(lockouts))))
    return ExchangeRecovery(
        attempts=todays,
        governor=governor,
        lockout_reasons=tuple(sorted(set(lockouts))),
    )


async def recover_from_exchange(
    transport: KalshiV2RestTransport,
    *,
    limits: GovernorLimits,
    now: datetime,
    prefix: str = AUTO_PREFIX,
    lookback_seconds: int = 48 * 3600,
) -> ExchangeRecovery:
    """Read Kalshi recent orders then recover governor state; no write methods."""
    rows = await transport.recent_orders_with_client_prefix(
        prefix,
        lookback_seconds=lookback_seconds,
        max_pages=10,
    )
    return recover_governor_from_orders(rows, limits=limits, now=now, prefix=prefix)
