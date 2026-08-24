"""Fail-closed startup recovery for ambiguous Polymarket MLB maker attempts.

An MLB attempt reserves $1 before mutation. If the process loses exact order identity,
the reservation must remain until exchange state proves zero fill. Recovery uses the
authenticated open-order endpoint plus portfolio trade activity on the exact maker and
hedge slugs. It releases only when both slugs have no open order and no trade activity
at or after the reservation timestamp. Any fill/activity or incomplete evidence keeps
the reservation and blocks new live order entry.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Mapping

from polymarket_us import PolymarketUS

import src.sports_poly_mlb_rule_risk_budget as budget


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        out = datetime.fromisoformat(text)
    except Exception:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def _trade_ts(row: Mapping[str, Any]) -> datetime | None:
    for key in (
        "event_ts",
        "last_event_ts",
        "updated_at",
        "updatedAt",
        "created_at",
        "createdAt",
        "timestamp",
    ):
        parsed = _parse_ts(row.get(key))
        if parsed is not None:
            return parsed
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    for key in ("event_ts", "reserved_at", "created_at", "createdAt"):
        parsed = _parse_ts(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _activity_trade(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    trade = row.get("trade")
    return trade if isinstance(trade, Mapping) else None


def _activity_ts(trade: Mapping[str, Any]) -> datetime | None:
    return _parse_ts(trade.get("createTime") or trade.get("create_time") or trade.get("updateTime") or trade.get("update_time"))


async def _activities(client: PolymarketUS, slug: str) -> list[Mapping[str, Any]]:
    response = await asyncio.to_thread(
        client.portfolio.activities,
        {
            "limit": 100,
            "types": ["ACTIVITY_TYPE_TRADE"],
            "marketSlug": slug,
            "sortOrder": "SORT_ORDER_DESCENDING",
        },
    )
    rows = response.get("activities") or [] if isinstance(response, Mapping) else []
    return [row for row in rows if isinstance(row, Mapping)]


async def recover_unresolved_mlb_reservation() -> dict[str, Any]:
    rows = await budget._trades()
    blocked = budget._unresolved_sports(rows)
    if blocked is None:
        return {"status": "MLB_STARTUP_RECOVERY_CLEAR", "clear": True, "unresolved": False}

    strategy = str(blocked.get("strategy") or "")
    trade_id = str(blocked.get("trade_id") or "")
    if strategy != budget.STRATEGY:
        return {
            "status": "MLB_STARTUP_RECOVERY_OTHER_SPORTS_UNRESOLVED",
            "clear": False,
            "trade_id": trade_id,
            "strategy": strategy,
        }

    metadata = blocked.get("metadata") if isinstance(blocked.get("metadata"), Mapping) else {}
    maker_slug = str(metadata.get("maker_slug") or "")
    hedge_slug = str(metadata.get("hedge_slug") or "")
    market = str(blocked.get("market") or "")
    low_slug, sep, high_slug = market.partition("|")
    reserved_at = _trade_ts(blocked)
    if not maker_slug or not hedge_slug or not sep or reserved_at is None:
        return {
            "status": "MLB_STARTUP_RECOVERY_INSUFFICIENT_LEDGER_EVIDENCE",
            "clear": False,
            "trade_id": trade_id,
            "has_maker_slug": bool(maker_slug),
            "has_hedge_slug": bool(hedge_slug),
            "has_market_pair": bool(sep),
            "has_reservation_timestamp": reserved_at is not None,
            "ledger_keys": sorted(str(k) for k in blocked.keys()),
        }

    client = PolymarketUS(
        key_id=__import__("os").environ["POLYMARKET_KEY_ID"],
        secret_key=__import__("os").environ["POLYMARKET_SECRET_KEY"],
        timeout=8.0,
    )
    try:
        # Cancellation is safe even if the ambiguous create never reached the exchange.
        # Do not infer zero fill from cancellation; activity history is authoritative.
        for slug in (maker_slug, hedge_slug):
            try:
                await asyncio.to_thread(client.orders.cancel_all, {"marketSlug": slug})
            except Exception:
                # A cancel failure is not itself proof of exposure; the verification
                # reads below decide whether startup may proceed.
                pass

        await asyncio.sleep(0.75)
        open_response = await asyncio.to_thread(client.orders.list, {"slugs": [maker_slug, hedge_slug]})
        open_rows = open_response.get("orders") or [] if isinstance(open_response, Mapping) else []
        open_orders = [
            row for row in open_rows
            if isinstance(row, Mapping)
            and str(row.get("marketSlug") or row.get("market_slug") or "") in {maker_slug, hedge_slug}
        ]
        if open_orders:
            return {
                "status": "MLB_STARTUP_RECOVERY_OPEN_ORDER_REMAINS",
                "clear": False,
                "trade_id": trade_id,
                "open_order_count": len(open_orders),
            }

        post_reservation_trades: list[dict[str, Any]] = []
        for slug in (maker_slug, hedge_slug):
            for activity in await _activities(client, slug):
                trade = _activity_trade(activity)
                if trade is None:
                    continue
                when = _activity_ts(trade)
                if when is None or when < reserved_at:
                    continue
                qty = str(trade.get("qty") or trade.get("quantity") or "0")
                post_reservation_trades.append(
                    {
                        "market_slug": str(trade.get("marketSlug") or trade.get("market_slug") or slug),
                        "trade_id": str(trade.get("id") or ""),
                        "qty": qty,
                        "create_time": when.isoformat(),
                    }
                )

        if post_reservation_trades:
            return {
                "status": "MLB_STARTUP_RECOVERY_TRADE_ACTIVITY_FOUND_REVIEW_REQUIRED",
                "clear": False,
                "trade_id": trade_id,
                "reserved_at": reserved_at.isoformat(),
                "trade_activity": post_reservation_trades[:8],
            }

        release_row = {
            "low_slug": low_slug,
            "high_slug": high_slug,
            "maker_slug": maker_slug,
            "hedge_slug": hedge_slug,
            "player": blocked.get("asset") or "MLB_PROP",
        }
        await budget.release(
            row=release_row,
            trade_id=trade_id,
            reason="STARTUP_AUTHORITATIVE_OPEN_ORDERS_CLEAR_AND_NO_POST_RESERVATION_TRADE_ACTIVITY",
        )
        return {
            "status": "MLB_STARTUP_RECOVERY_ZERO_FILL_RELEASED",
            "clear": True,
            "trade_id": trade_id,
            "reserved_at": reserved_at.isoformat(),
            "maker_slug": maker_slug,
            "hedge_slug": hedge_slug,
        }
    finally:
        client.close()
