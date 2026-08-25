"""Durable shared exposure gate for the Polymarket MLB maker q1 canary.

This lane is rule-risk, not guaranteed arbitrage. Before any maker mutation it must
prove the Command Center is reachable and the shared sports risk ceiling has room.

A truly ambiguous sports mutation still freezes every sports mutation lane because
we do not know the account exposure. A *known* one-leg Gemini<->Kalshi fill is
different: its exact capital remains reserved against the shared $5 ceiling and the
G<->K strategy remains quarantined, but it must not freeze an unrelated Polymarket
market forever. Polymarket's own unresolved exposure still blocks another
Polymarket maker.

Confirmed zero-fill outcomes explicitly release their reservation. Filled/ambiguous
outcomes retain risk until authoritative reconciliation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
from typing import Any, Mapping

import httpx

D = Decimal
ONE = D("1")
COMMAND_CENTER = os.getenv(
    "ARB_COMMAND_CENTER_URL",
    "https://arb-command-center-production.up.railway.app",
).rstrip("/")
LEDGER_TOKEN = os.getenv("LEDGER_INGEST_TOKEN", "").strip()
STRATEGY = "sports_poly_mlb_maker_q1"
MAX_REALIZED_LOSS_BUDGET = D("5")  # Existing approved research-loss ceiling; never auto-increase.
TERMINAL = {
    "REALIZED", "TRADE_REALIZED", "SETTLED", "SETTLED_COMPATIBLE",
    "SETTLED_DIVERGENT", "SETTLED_ONE_LEG", "CAPITAL_RELEASED",
    "NO_FILL_RELEASED",
}
GLOBAL_AMBIGUOUS = {"AMBIGUOUS_SUBMISSION", "INVALID_FILL_QUANTITY"}


def _d(value: Any) -> D:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


async def _trades() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.get(f"{COMMAND_CENTER}/api/trades", params={"limit": 1000})
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, list):
        raise RuntimeError("command center trades response is not a list")
    return [row for row in body if isinstance(row, dict)]


def _is_live_sports(row: Mapping[str, Any]) -> bool:
    mode = str(row.get("mode") or "").upper()
    strategy = str(row.get("strategy") or "").lower()
    return mode in {"LIVE", "LIVE_PROBE"} and strategy.startswith("sports_")


def _status(row: Mapping[str, Any]) -> str:
    return str(row.get("status") or "").upper()


def _risk_amount(row: Mapping[str, Any]) -> D:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    reserved = _d(metadata.get("reserved_usd"))
    filled = _d(row.get("capital_filled") or row.get("capital_wagered"))
    return max(D("0"), reserved, filled)


def _is_known_gk_one_leg(row: Mapping[str, Any]) -> bool:
    """True only for exact known G<->K one-leg incidents, never ambiguity."""
    if not _is_live_sports(row) or _status(row) != "ONE_LEG_FILLED":
        return False
    strategy = str(row.get("strategy") or "").lower()
    return bool(
        strategy.startswith("sports_gk_")
        and int(_d(row.get("legs_filled"))) == 1
        and _d(row.get("capital_filled") or row.get("capital_wagered")) > 0
    )


def _blocking_unresolved_for_poly(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return exposure that must prevent a new Polymarket mutation.

    Unknown exposure is global. Polymarket's own unresolved mutation is local and
    blocks another maker. A known G<->K one-leg incident is quarantined to G<->K
    and accounted against the shared risk ceiling instead of globally freezing the
    independent Polymarket venue.
    """
    for row in rows:
        if not _is_live_sports(row) or _status(row) in TERMINAL:
            continue
        risk = _risk_amount(row)
        legs_filled = _d(row.get("legs_filled"))
        if risk <= 0 and legs_filled <= 0:
            continue
        status = _status(row)
        strategy = str(row.get("strategy") or "").lower()
        if status in GLOBAL_AMBIGUOUS:
            return row
        if strategy == STRATEGY:
            return row
        if _is_known_gk_one_leg(row):
            continue
        # Any other unresolved mutation is not proven safe to scope locally.
        return row
    return None


def _unresolved_sports(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Backward-compatible name used by startup recovery, with scoped semantics."""
    return _blocking_unresolved_for_poly(rows)


def _unresolved_risk_used(rows: list[dict[str, Any]]) -> D:
    """Capital at risk from every nonterminal live-sports trade, deduped by trade."""
    by_trade: dict[str, D] = {}
    anonymous = D("0")
    for row in rows:
        if not _is_live_sports(row) or _status(row) in TERMINAL:
            continue
        risk = _risk_amount(row)
        if risk <= 0:
            continue
        trade_id = str(row.get("trade_id") or "")
        if trade_id:
            by_trade[trade_id] = max(by_trade.get(trade_id, D("0")), risk)
        else:
            anonymous += risk
    return sum(by_trade.values(), anonymous)


def _rule_risk_loss_used(rows: list[dict[str, Any]]) -> D:
    """Conservatively total negative realized P&L carrying a RULE_RISK classification."""
    total = D("0")
    seen: set[str] = set()
    for row in rows:
        if not _is_live_sports(row):
            continue
        trade_id = str(row.get("trade_id") or "")
        if trade_id and trade_id in seen:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        classification = str(metadata.get("classification") or row.get("classification") or "").upper()
        status = _status(row)
        if "RULE_RISK" not in classification or status not in TERMINAL:
            continue
        net = _d(row.get("net_pnl") or row.get("pnl_net") or row.get("realized_pnl"))
        if net < 0:
            total += -net
        if trade_id:
            seen.add(trade_id)
    return total


def _remaining_risk_capacity(rows: list[dict[str, Any]]) -> tuple[D, D, D]:
    """Return (realized_loss_used, unresolved_risk_used, remaining) under hard $5."""
    loss_used = _rule_risk_loss_used(rows)
    unresolved_used = _unresolved_risk_used(rows)
    remaining = MAX_REALIZED_LOSS_BUDGET - loss_used - unresolved_used
    return loss_used, unresolved_used, max(D("0"), remaining)


async def _post(event: dict[str, Any]) -> None:
    if not LEDGER_TOKEN:
        raise RuntimeError("LEDGER_INGEST_TOKEN missing; MLB maker live execution denied")
    async with httpx.AsyncClient(timeout=4.0) as http:
        response = await http.post(
            f"{COMMAND_CENTER}/api/events",
            headers={"Authorization": f"Bearer {LEDGER_TOKEN}"},
            json=event,
        )
        response.raise_for_status()


async def reserve(*, row: Mapping[str, Any], certification: Mapping[str, Any], trade_id: str) -> dict[str, Any]:
    rows = await _trades()
    blocked = _blocking_unresolved_for_poly(rows)
    if blocked is not None:
        raise RuntimeError(
            "unresolved LIVE sports exposure blocks MLB maker: "
            f"trade_id={blocked.get('trade_id')} status={blocked.get('status')}"
        )
    loss_used, unresolved_used, remaining = _remaining_risk_capacity(rows)
    if remaining < ONE:
        raise RuntimeError(
            "sports shared risk budget lacks room for q1 MLB maker: "
            f"realized_loss={loss_used} unresolved_risk={unresolved_used} "
            f"remaining={remaining} required=1 ceiling={MAX_REALIZED_LOSS_BUDGET}"
        )

    market = f"{row.get('low_slug')}|{row.get('high_slug')}"
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": market,
        "asset": str(row.get("player") or row.get("family") or "MLB_PROP"),
        "venues": ["polymarket"],
        "side": "COMPLEMENTARY_MAKER_HEDGE",
        "status": "CAPITAL_RESERVED",
        "amount": "1",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "1",
            "q": 1,
            "classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
            "guaranteed_arbitrage": False,
            "max_realized_loss_budget_usd": str(MAX_REALIZED_LOSS_BUDGET),
            "realized_rule_risk_loss_used_usd": str(loss_used),
            "unresolved_live_sports_risk_used_usd": str(unresolved_used),
            "remaining_shared_risk_before_reservation_usd": str(remaining),
            "maker_slug": row.get("maker_slug"),
            "hedge_slug": row.get("hedge_slug"),
            "certification": dict(certification),
        },
    })
    return {
        "reserved": True,
        "trade_id": trade_id,
        "loss_used": str(loss_used),
        "unresolved_risk_used": str(unresolved_used),
        "remaining_before": str(remaining),
        "loss_ceiling": str(MAX_REALIZED_LOSS_BUDGET),
    }


async def release(*, row: Mapping[str, Any], trade_id: str, reason: str) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-released"),
        "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": f"{row.get('low_slug')}|{row.get('high_slug')}",
        "asset": str(row.get("player") or row.get("family") or "MLB_PROP"),
        "venues": ["polymarket"],
        "side": "COMPLEMENTARY_MAKER_HEDGE",
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 2,
        "legs_filled": 0,
        "metadata": {
            "reserved_usd": "0",
            "q": 1,
            "classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
            "guaranteed_arbitrage": False,
            "release_reason": reason,
        },
    })
