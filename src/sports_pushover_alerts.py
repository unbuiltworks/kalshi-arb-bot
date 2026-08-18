"""Read-only qualification, dedupe, and Pushover delivery for sports signals.

This module has no order-entry code. It accepts already-computed cross-venue locks,
rejects stale/immaterial candidates, suppresses noisy repeats, and sends a compact
human-action alert. Credentials come only from environment variables.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import logging
import os
import time
from typing import Any, Iterable, Mapping

import httpx

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_pushover_alerts")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        out = D(str(value))
        return out if out.is_finite() else D(default)
    except Exception:
        return D(default)


def _env_d(name: str, default: str) -> Decimal:
    return _d(os.getenv(name, default), default)


def _env_i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AlertPolicy:
    min_unit_profit: Decimal = D("0.02")
    min_total_profit: Decimal = D("0.05")
    max_quote_skew_ms: int = 70000
    max_quote_age_ms: int = 90000
    realert_profit_abs: Decimal = D("0.02")
    realert_profit_pct: Decimal = D("0.25")
    realert_size_pct: Decimal = D("0.50")

    @classmethod
    def from_env(cls) -> "AlertPolicy":
        return cls(
            min_unit_profit=_env_d("SPORTS_ALERT_MIN_UNIT_PROFIT", "0.02"),
            min_total_profit=_env_d("SPORTS_ALERT_MIN_TOTAL_PROFIT", "0.05"),
            max_quote_skew_ms=_env_i("SPORTS_ALERT_MAX_SKEW_MS", 70000),
            max_quote_age_ms=_env_i("SPORTS_ALERT_MAX_AGE_MS", 90000),
            realert_profit_abs=_env_d("SPORTS_ALERT_REALERT_PROFIT_ABS", "0.02"),
            realert_profit_pct=_env_d("SPORTS_ALERT_REALERT_PROFIT_PCT", "0.25"),
            realert_size_pct=_env_d("SPORTS_ALERT_REALERT_SIZE_PCT", "0.50"),
        )


@dataclass
class SeenAlert:
    profit: Decimal
    quantity: Decimal


class SportsPushoverAlerts:
    def __init__(self, policy: AlertPolicy | None = None) -> None:
        self.policy = policy or AlertPolicy.from_env()
        self._seen: dict[str, SeenAlert] = {}

    @property
    def configured(self) -> bool:
        return bool(os.getenv("PUSHOVER_USER_KEY", "").strip() and os.getenv("PUSHOVER_APP_TOKEN", "").strip())

    def _key(self, lock: Any) -> str:
        return "|".join((
            str(lock.settlement_key), str(lock.yes_venue), str(lock.yes_market_id),
            str(lock.no_venue), str(lock.no_market_id),
        ))

    def qualifies(self, lock: Any, *, max_age_ms: int) -> tuple[bool, str]:
        p = self.policy
        unit_profit = _d(lock.unit_profit)
        total_profit = _d(lock.max_locked_profit)
        qty = _d(lock.max_quantity)
        if unit_profit < p.min_unit_profit:
            return False, "unit_profit_below_floor"
        if total_profit < p.min_total_profit:
            return False, "total_profit_below_floor"
        if qty <= 0:
            return False, "no_executable_size"
        if int(lock.quote_skew_ms) > p.max_quote_skew_ms:
            return False, "quote_skew_too_large"
        if int(max_age_ms) > p.max_quote_age_ms:
            return False, "quote_too_old"
        return True, "qualified"

    def should_realert(self, lock: Any) -> bool:
        key = self._key(lock)
        current_profit = _d(lock.max_locked_profit)
        current_qty = _d(lock.max_quantity)
        prior = self._seen.get(key)
        if prior is None:
            return True
        p = self.policy
        profit_gain = current_profit - prior.profit
        profit_threshold = max(p.realert_profit_abs, prior.profit * p.realert_profit_pct)
        size_threshold = prior.quantity * (D("1") + p.realert_size_pct)
        return profit_gain >= profit_threshold or current_qty >= size_threshold

    def mark_sent(self, lock: Any) -> None:
        self._seen[self._key(lock)] = SeenAlert(_d(lock.max_locked_profit), _d(lock.max_quantity))

    @staticmethod
    def _quote_obj(row: Any) -> Any:
        return getattr(row, "quote", row)

    def _leg(self, lock: Any, rows: Iterable[Any], *, yes: bool, now_ms: int) -> dict[str, Any]:
        venue = str(lock.yes_venue if yes else lock.no_venue)
        market_id = str(lock.yes_market_id if yes else lock.no_market_id)
        match = None
        for row in rows:
            q = self._quote_obj(row)
            if str(getattr(q, "venue", "")) == venue and str(getattr(q, "market_id", "")) == market_id:
                match = row
                break
        q = self._quote_obj(match) if match is not None else None
        side = "YES" if yes else "NO"
        price = _d(getattr(q, "yes_cost" if yes else "no_cost", "0")) if q is not None else D("0")
        fee = _d(getattr(match, "yes_fee" if yes else "no_fee", "0")) if match is not None else D("0")
        received_ms = int(getattr(q, "received_ms", now_ms)) if q is not None else now_ms
        return {
            "venue": venue,
            "market_id": market_id,
            "side": side,
            "price": price,
            "fee": fee,
            "age_ms": max(0, now_ms - received_ms),
        }

    def build_message(self, lock: Any, rows: Iterable[Any], *, rule_status: str, source: str, now_ms: int | None = None) -> tuple[str, str, int]:
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        rows = list(rows)
        yes = self._leg(lock, rows, yes=True, now_ms=now_ms)
        no = self._leg(lock, rows, yes=False, now_ms=now_ms)
        qty = _d(lock.max_quantity)
        cost = _d(lock.max_capital)
        payout = qty
        profit = _d(lock.max_locked_profit)
        total_fees = yes["fee"] + no["fee"]
        manual = yes["venue"].startswith("sportsbook:") or no["venue"].startswith("sportsbook:")
        max_age = max(int(yes["age_ms"]), int(no["age_ms"]))
        title = f"Sports arb +${profit:.2f}"
        message = "\n".join((
            f"{source} | {lock.settlement_key}",
            f"YES {yes['venue']} @ {yes['price']:.4f} | NO {no['venue']} @ {no['price']:.4f}",
            f"Qty {qty} | Cost ${cost:.2f} | Payout ${payout:.2f}",
            f"Fees ${total_fees:.2f} | After-fee P/L +${profit:.2f} ({_d(lock.unit_profit):.4f}/unit)",
            f"Freshness max {max_age}ms | skew {int(lock.quote_skew_ms)}ms",
            f"Settlement {lock.settlement_confidence} | Rules {rule_status}",
            f"Manual sportsbook action: {'YES' if manual else 'NO'}",
        ))
        return title, message, max_age

    async def send(self, *, title: str, message: str) -> str:
        user = os.getenv("PUSHOVER_USER_KEY", "").strip()
        token = os.getenv("PUSHOVER_APP_TOKEN", "").strip()
        if not user or not token:
            raise RuntimeError("Pushover credentials are not configured")
        async with httpx.AsyncClient(timeout=8.0) as http:
            response = await http.post(PUSHOVER_URL, data={"token": token, "user": user, "title": title, "message": message})
            response.raise_for_status()
            body = response.json()
        if int(body.get("status", 0)) != 1:
            raise RuntimeError(f"Pushover rejected notification status={body.get('status')}")
        return str(body.get("request") or "")

    async def maybe_send(self, lock: Any, rows: Iterable[Any], *, rule_status: str, source: str) -> bool:
        if not self.configured:
            return False
        title, message, max_age = self.build_message(lock, rows, rule_status=rule_status, source=source)
        ok, _reason = self.qualifies(lock, max_age_ms=max_age)
        if not ok or not self.should_realert(lock):
            return False
        request_id = await self.send(title=title, message=message)
        self.mark_sent(lock)
        logger.info("SPORTS_PUSHOVER_SENT source=%s request_id=%s", source, request_id)
        return True


async def _test_notification() -> None:
    sink = SportsPushoverAlerts()
    request_id = await sink.send(
        title="Sports alerts connected",
        message="Pushover test succeeded. Sports scanners remain read-only; no order entry was enabled.",
    )
    print(f"PUSHOVER_TEST_OK request_id={request_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    if not args.test:
        raise SystemExit("use --test")
    asyncio.run(_test_notification())


if __name__ == "__main__":
    main()
