"""Read-only qualification, dedupe, and Pushover delivery for sports signals.

This module has no order-entry code. It accepts already-computed cross-venue locks,
rejects stale/immaterial/non-actionable candidates, suppresses noisy repeats, and
sends a compact human-action alert. Credentials come only from environment vars.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import logging
import os
import time
from typing import Any, Iterable

import httpx

D = Decimal
logger = logging.getLogger("kalshi_arb.sports_pushover_alerts")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
ACTIONABLE_CONFIDENCE = "FULL_RULES_AUDITED_EXECUTABLE"


STAT_LABELS = {
    "hits": "Hits",
    "home_runs": "Home Runs",
    "total_bases": "Total Bases",
    "rbis": "RBI",
    "runs": "Runs",
    "strikeouts": "Strikeouts",
    "walks": "Walks",
    "outs_recorded": "Outs Recorded",
}

FAMILY_LABELS = {
    "moneyline": "Moneyline",
    "spread": "Spread",
    "game_total": "Game Total",
    "team_total": "Team Total",
    "player_prop": "Player Prop",
    "team_prop": "Team Prop",
    "game_prop": "Game Prop",
}

VENUE_LABELS = {
    "kalshi": "Kalshi",
    "gemini": "Gemini",
    "polymarket": "Polymarket",
    "fanduel": "FanDuel",
    "draftkings": "DraftKings",
}


@dataclass(frozen=True)
class SportsDisplayIdentity:
    league: str
    matchup: str
    market_family: str
    headline: str
    detail: str


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


def _words(value: str) -> str:
    return " ".join(part for part in str(value).replace("_", " ").replace("-", " ").split() if part)


def _title_words(value: str) -> str:
    return _words(value).title()


def _fmt_line(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _stat_label(stat: str, *, singular: bool = False) -> str:
    normalized = str(stat).strip().lower()
    if singular:
        singulars = {
            "hits": "Hit",
            "home_runs": "Home Run",
            "rbis": "RBI",
            "runs": "Run",
            "strikeouts": "Strikeout",
            "walks": "Walk",
        }
        if normalized in singulars:
            return singulars[normalized]
    return STAT_LABELS.get(normalized, _title_words(normalized))


def _display_venue(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized.startswith("sportsbook:"):
        normalized = normalized.split(":", 1)[1]
    return VENUE_LABELS.get(normalized, _title_words(normalized))


def _format_price_cents(value: Decimal) -> str:
    cents = value * D("100")
    if cents == cents.to_integral_value():
        return f"{int(cents)}¢"
    return f"{cents:.1f}¢"


def _prop_detail(stat: str, comparator: str, line: Decimal | None) -> str:
    stat = str(stat).strip().lower()
    comparator = str(comparator).strip().upper()
    line_text = _fmt_line(line)

    # The most common binary MLB props are clearer to a bettor in their familiar
    # sportsbook wording than as raw GT/GE predicates.
    if stat == "home_runs" and (
        comparator == "YES"
        or (comparator == "GE" and line == D("1"))
        or (comparator == "GT" and line == D("0.5"))
    ):
        return "Home Run YES"
    if stat == "hits" and (
        (comparator == "GE" and line == D("1"))
        or (comparator == "GT" and line == D("0.5"))
    ):
        return "1+ Hit"
    if comparator == "GE" and line is not None and line == line.to_integral_value() and line > 0:
        n = int(line)
        return f"{n}+ {_stat_label(stat, singular=n == 1)}"
    if comparator == "GT":
        return f"Over {line_text} {_stat_label(stat)}".strip()
    if comparator == "LT":
        return f"Under {line_text} {_stat_label(stat)}".strip()
    if comparator == "LE":
        return f"At Most {line_text} {_stat_label(stat)}".strip()
    if comparator == "EQ":
        return f"Exactly {line_text} {_stat_label(stat)}".strip()
    if comparator == "YES":
        return f"{_stat_label(stat, singular=True)} YES".strip()
    return " ".join(part for part in (_stat_label(stat), comparator, line_text) if part)


def parse_sports_display_identity(settlement_key: str) -> SportsDisplayIdentity | None:
    """Turn the canonical sports contract key into a bettor-readable identity.

    Canonical keys are:
    sport|league|participants|family|scope|subject_type|subject|stat|comparator|line
    The parser is deliberately exact and only affects display. A malformed key
    falls back to the raw settlement key and never changes qualification/safety.
    """
    parts = str(settlement_key).split("|")
    if len(parts) < 10:
        return None
    _sport, league, participants, family, _scope, subject_type, subject, stat, comparator, raw_line = parts[:10]
    family = family.strip().lower()
    subject_type = subject_type.strip().lower()
    try:
        line = D(raw_line) if raw_line.strip() else None
        if line is not None and not line.is_finite():
            line = None
    except Exception:
        line = None

    league_label = league.strip().upper() or "SPORTS"
    teams = [p.strip().upper() for p in participants.split(",") if p.strip()]
    matchup = " vs. ".join(teams) if teams else "Matchup unavailable"
    family_label = FAMILY_LABELS.get(family, _title_words(family)) or "Market"
    detail = _prop_detail(stat, comparator, line)

    if family == "player_prop":
        player = _title_words(subject) or "Player"
        headline = f"{player} — {detail}"
    elif family == "team_total":
        team = subject.strip().upper() if subject_type == "team" else _title_words(subject)
        headline = f"{team} Team Total — {detail}" if team else f"Team Total — {detail}"
    elif family == "game_total":
        headline = f"Game Total — {detail}"
    elif family == "spread":
        team = subject.strip().upper() if subject_type == "team" else _title_words(subject)
        headline = f"{team} Spread — {detail}" if team else f"Spread — {detail}"
    elif family == "moneyline":
        team = subject.strip().upper() if subject_type == "team" else _title_words(subject)
        headline = f"{team} Moneyline" if team else "Moneyline"
    else:
        subject_label = subject.strip().upper() if subject_type == "team" else _title_words(subject)
        headline = " — ".join(part for part in (subject_label or family_label, detail) if part)

    return SportsDisplayIdentity(
        league=league_label,
        matchup=matchup,
        market_family=family_label,
        headline=headline,
        detail=detail,
    )


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

    def qualifies(self, lock: Any, *, max_age_ms: int, actionable: bool) -> tuple[bool, str]:
        p = self.policy
        unit_profit = _d(lock.unit_profit)
        total_profit = _d(lock.max_locked_profit)
        qty = _d(lock.max_quantity)
        if not actionable:
            return False, "not_actionable"
        if str(getattr(lock, "settlement_confidence", "")) != ACTIONABLE_CONFIDENCE:
            return False, "settlement_not_execution_audited"
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
        expected_settlement_minutes = getattr(q, "expected_settlement_minutes", None) if q is not None else None
        try:
            expected_settlement_minutes = int(expected_settlement_minutes) if expected_settlement_minutes is not None else None
        except (TypeError, ValueError):
            expected_settlement_minutes = None
        return {
            "venue": venue,
            "market_id": market_id,
            "side": side,
            "price": price,
            "fee": fee,
            "age_ms": max(0, now_ms - received_ms),
            "expected_settlement_minutes": expected_settlement_minutes,
        }

    def build_message(self, lock: Any, rows: Iterable[Any], *, rule_status: str, source: str, now_ms: int | None = None) -> tuple[str, str, int]:
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        rows = list(rows)
        yes = self._leg(lock, rows, yes=True, now_ms=now_ms)
        no = self._leg(lock, rows, yes=False, now_ms=now_ms)
        qty = _d(lock.max_quantity)
        cost = _d(lock.max_capital)
        profit = _d(lock.max_locked_profit)
        total_fees = yes["fee"] + no["fee"]
        roi = (profit / cost * D("100")) if cost > 0 else D("0")
        manual = yes["venue"].startswith("sportsbook:") or no["venue"].startswith("sportsbook:")
        max_age = max(int(yes["age_ms"]), int(no["age_ms"]))
        identity = parse_sports_display_identity(str(lock.settlement_key))

        if identity is not None:
            title = f"{identity.league} ARB — {identity.headline}"
            first_line = identity.matchup
            family_line = f"Market family: {identity.market_family}"
        else:
            title = f"Sports arb +${profit:.2f}"
            first_line = f"{source} | {lock.settlement_key}"
            family_line = "Market family: unavailable"

        lines = [
            first_line,
            f"YES {_display_venue(yes['venue'])} {_format_price_cents(yes['price'])}",
            f"NO {_display_venue(no['venue'])} {_format_price_cents(no['price'])}",
            f"Buy {qty:g} each — Cost ${cost:.2f}",
            f"Locked profit: +${profit:.2f} / +{roi:.1f}%",
            f"Fees included: ${total_fees:.2f}",
            family_line,
            f"Freshness: {max_age}ms max | {int(lock.quote_skew_ms)}ms skew",
            f"Settlement: {lock.settlement_confidence} | Rules: {rule_status}",
            f"Manual sportsbook leg: {'YES' if manual else 'NO'}",
        ]
        settlement_estimates = [
            leg["expected_settlement_minutes"]
            for leg in (yes, no)
            if leg["expected_settlement_minutes"] is not None and leg["expected_settlement_minutes"] >= 0
        ]
        if settlement_estimates:
            lines.insert(7, f"Expected settlement: ~{max(settlement_estimates)} min")
        return title, "\n".join(lines), max_age

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

    async def maybe_send(self, lock: Any, rows: Iterable[Any], *, rule_status: str, source: str, actionable: bool) -> bool:
        if not self.configured:
            return False
        title, message, max_age = self.build_message(lock, rows, rule_status=rule_status, source=source)
        ok, _reason = self.qualifies(lock, max_age_ms=max_age, actionable=actionable)
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
