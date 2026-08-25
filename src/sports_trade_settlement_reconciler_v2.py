"""Settlement supervisor extension for known one-leg Gemini/Kalshi incidents.

The original reconciler intentionally handled only BOTH_FILLED/AWAITING_SETTLEMENT.
That left a known ONE_LEG_FILLED incident permanently outside reconciliation, which
could freeze live sports forever. This module preserves the proven two-leg path and
adds a fail-closed one-leg path:

* only an exact original 1-leg fill with no successful flatten is eligible;
* only the actually filled venue's authoritative resolution is required;
* realized P&L uses recorded capital and recorded fees, never modeled edge;
* terminal status is SETTLED_ONE_LEG so exposure guards can release it only after
  authoritative venue settlement.

No order entry exists in this module.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import logging
from typing import Any, Mapping

import httpx

from src import sports_trade_settlement_reconciler as base

logger = logging.getLogger("kalshi_arb.sports_trade_settlement_reconciler_v2")
D = Decimal


def _d(value: Any) -> D:
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


def is_reconcilable_trade(row: Mapping[str, Any]) -> bool:
    if str(row.get("mode") or "") not in {"LIVE", "LIVE_PROBE"}:
        return False
    if not str(row.get("strategy") or "").startswith("sports_gk_"):
        return False
    if bool(row.get("realized")):
        return False
    status = str(row.get("status") or "")
    legs = int(row.get("legs_filled") or 0)
    return bool(
        (status == "AWAITING_SETTLEMENT" and legs == 2)
        or (status == "ONE_LEG_FILLED" and legs == 1)
    )


async def fetch_open_trades(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await http.get(f"{base.COMMAND_CENTER}/api/trades", params={"limit": 1000})
    response.raise_for_status()
    rows = response.json()
    return [row for row in rows if isinstance(row, dict) and is_reconcilable_trade(row)]


def _flatten_fill_qty(execution: Mapping[str, Any], venue: str) -> D:
    flatten = execution.get("flatten")
    if not isinstance(flatten, Mapping):
        return D("0")
    row: Any = flatten.get(venue)
    # Legacy transport sometimes stored the Gemini flatten directly rather than
    # under a venue key.
    if venue == "gemini" and row is None and "filled_quantity" in flatten:
        row = flatten
    if not isinstance(row, Mapping):
        return D("0")
    return _d(row.get("filled_quantity"))


def one_leg_open_exposure_venue(metadata: Mapping[str, Any]) -> str | None:
    """Return the single still-open original venue, otherwise fail closed."""
    execution = metadata.get("execution") if isinstance(metadata.get("execution"), Mapping) else {}
    g = execution.get("gemini") if isinstance(execution.get("gemini"), Mapping) else {}
    k = execution.get("kalshi") if isinstance(execution.get("kalshi"), Mapping) else {}
    gq = _d(g.get("filled_quantity"))
    kq = _d(k.get("filled_quantity"))
    if gq > 0 and kq == 0:
        return None if _flatten_fill_qty(execution, "gemini") >= gq else "gemini"
    if kq > 0 and gq == 0:
        return None if _flatten_fill_qty(execution, "kalshi") >= kq else "kalshi"
    return None


def one_leg_settlement_payout(
    *,
    venue: str,
    metadata: Mapping[str, Any],
    gemini_result: str | None,
    kalshi_result: str | None,
) -> D | None:
    gemini_bought, kalshi_bought = base.bought_outcomes(dict(metadata))
    if venue == "gemini":
        if gemini_bought not in {"yes", "no"} or gemini_result not in {"yes", "no"}:
            return None
        return D("1") if gemini_bought == gemini_result else D("0")
    if venue == "kalshi":
        if kalshi_bought not in {"yes", "no"} or kalshi_result not in {"yes", "no"}:
            return None
        return D("1") if kalshi_bought == kalshi_result else D("0")
    return None


async def reconcile_one_leg(
    trade: dict[str, Any],
    *,
    gemini_results: Mapping[str, str | None],
    kalshi_results: Mapping[str, str | None],
) -> bool:
    metadata = trade.get("metadata") if isinstance(trade.get("metadata"), Mapping) else {}
    venue = one_leg_open_exposure_venue(metadata)
    if venue is None:
        logger.warning(
            "SPORTS_ONE_LEG_SETTLEMENT_BLOCKED trade_id=%s reason=exposure_not_exact_or_already_flattened",
            trade.get("trade_id"),
        )
        return False
    symbol, ticker = base.exact_market_ids(trade)
    if venue == "gemini" and not symbol:
        logger.warning("SPORTS_ONE_LEG_SETTLEMENT_BLOCKED trade_id=%s reason=missing_gemini_symbol", trade.get("trade_id"))
        return False
    if venue == "kalshi" and not ticker:
        logger.warning("SPORTS_ONE_LEG_SETTLEMENT_BLOCKED trade_id=%s reason=missing_kalshi_ticker", trade.get("trade_id"))
        return False

    gemini_result = gemini_results.get(symbol) if symbol else None
    kalshi_result = kalshi_results.get(ticker) if ticker else None
    payout = one_leg_settlement_payout(
        venue=venue,
        metadata=metadata,
        gemini_result=gemini_result,
        kalshi_result=kalshi_result,
    )
    if payout is None:
        logger.info(
            "SPORTS_ONE_LEG_SETTLEMENT_PENDING trade_id=%s venue=%s gemini=%s kalshi=%s",
            trade.get("trade_id"), venue, gemini_result, kalshi_result,
        )
        return False

    gross, net = base.realized_pnl(
        capital_filled=trade.get("capital_filled"),
        fees=trade.get("fees"),
        payouts=(payout,),
    )
    trade_id = str(trade.get("trade_id") or "")
    bought = base.bought_outcomes(dict(metadata))[0 if venue == "gemini" else 1]
    resolution = gemini_result if venue == "gemini" else kalshi_result
    event = {
        "event_id": base._safe_id(f"{trade_id}:realized-one-leg"),
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": base._now(),
        "strategy": str(trade.get("strategy") or "sports_gk_live_probe"),
        "mode": str(trade.get("mode") or "LIVE_PROBE"),
        "market": trade.get("market"),
        "asset": trade.get("asset"),
        "venues": [venue],
        "side": trade.get("side"),
        "status": "SETTLED_ONE_LEG",
        "quantity": "1",
        "fee": "0",
        "gross_pnl": str(gross),
        "net_pnl": str(net),
        "legs_required": 2,
        "legs_filled": 1,
        "metadata": {
            **dict(metadata),
            "reserved_usd": "0",
            "settlement": {
                "one_leg_incident": True,
                "filled_venue": venue,
                "gemini_instrument_symbol": symbol or None,
                "kalshi_ticker": ticker or None,
                "bought_outcome": bought,
                "authoritative_result": resolution,
                "filled_leg_payout": str(payout),
                "total_payout": str(payout),
                "capital_filled": str(trade.get("capital_filled") or "0"),
                "recorded_fees": str(trade.get("fees") or "0"),
                "realized_from_actual_settlement": True,
            },
        },
    }
    if not await base._post_event(event):
        return False
    player = str(trade.get("asset") or metadata.get("player") or "MLB prop")
    sign = "+" if net >= 0 else ""
    await base._push(
        f"MLB ONE-LEG SETTLED — {sign}${net}",
        "\n".join((
            player,
            f"Filled venue: {venue.title()}",
            f"Bought {str(bought).upper()} → result {str(resolution).upper()}",
            f"Actual payout: ${payout}",
            f"Actual net P&L: {sign}${net}",
            "One-leg incident is now authoritatively realized.",
        )),
    )
    logger.warning(
        "SPORTS_ONE_LEG_REALIZED trade_id=%s venue=%s payout=%s gross=%s net=%s",
        trade_id, venue, payout, gross, net,
    )
    return True


async def reconcile_trade(
    trade: dict[str, Any],
    *,
    gemini_results: Mapping[str, str | None],
    kalshi_results: Mapping[str, str | None],
) -> bool:
    if str(trade.get("status") or "") == "ONE_LEG_FILLED":
        return await reconcile_one_leg(
            trade,
            gemini_results=gemini_results,
            kalshi_results=kalshi_results,
        )
    return await base.reconcile_trade(
        trade,
        gemini_results=gemini_results,
        kalshi_results=kalshi_results,
    )


async def run_forever(interval_seconds: float = 20.0) -> None:
    logger.info(
        "SPORTS_SETTLEMENT_RECONCILER_V2_READY interval_seconds=%s one_leg_reconciliation=true order_entry=false",
        interval_seconds,
    )
    async with httpx.AsyncClient(timeout=20.0) as http:
        while True:
            try:
                trades = await fetch_open_trades(http)
                market_ids = [base.exact_market_ids(trade) for trade in trades]
                symbols = {symbol for symbol, _ in market_ids if symbol}
                tickers = {ticker for _, ticker in market_ids if ticker}
                if trades:
                    gemini_results, kalshi_results = await asyncio.gather(
                        base.fetch_gemini_resolutions(http, symbols),
                        base.fetch_kalshi_resolutions(http, tickers),
                    )
                else:
                    gemini_results, kalshi_results = {}, {}
                realized = 0
                one_leg_open = 0
                for trade in trades:
                    one_leg_open += int(str(trade.get("status") or "") == "ONE_LEG_FILLED")
                    realized += int(await reconcile_trade(
                        trade,
                        gemini_results=gemini_results,
                        kalshi_results=kalshi_results,
                    ))
                logger.info(
                    "SPORTS_SETTLEMENT_HEARTBEAT open_reconcilable=%s one_leg_open=%s realized_this_cycle=%s",
                    len(trades), one_leg_open, realized,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("SPORTS_SETTLEMENT_LOOP_ERROR type=%s", type(exc).__name__)
            await asyncio.sleep(max(5.0, interval_seconds))
