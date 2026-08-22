"""Quantity-aware execution and realized-P&L accounting for q1-q5 live canaries.

The original campaign accounting intentionally assumes q=1.  This module leaves that
path untouched and provides a separate scale path so q=5 costs, fills and settlement
payouts cannot be silently recorded as one contract.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from src import gemini_kalshi_crypto_live_campaign as base

D = Decimal


def scaled_costs(record: dict[str, Any], *, required_quantity: Decimal) -> dict[str, D]:
    q = D(required_quantity)
    result = record["result"]
    gemini = result["gemini"]
    kalshi = result["kalshi"]
    kalshi_payload = record["kalshi_payload"]

    g_qty = D(str(gemini["filled_quantity"]))
    k_qty = D(str(kalshi["filled_quantity"]))
    if g_qty != q or k_qty != q:
        raise RuntimeError(f"unexpected scaled live quantities required={q} Gemini={g_qty} Kalshi={k_qty}")

    g_price = D(str(gemini["average_fill_price"]))
    g_fee = D(str(gemini.get("fee_paid") or "0"))
    k_yes_price = D(str(kalshi["average_fill_price"]))
    k_fee = D(str(kalshi.get("fee_paid") or "0"))
    k_side = base._purchased_kalshi_side(kalshi_payload)
    k_cost_price = k_yes_price if k_side == "yes" else D("1") - k_yes_price

    g_raw = g_price * g_qty
    k_raw = k_cost_price * k_qty
    return {
        "quantity": q,
        "gemini_price": g_price,
        "gemini_fee": g_fee,
        "gemini_amount": g_raw + g_fee,
        "kalshi_yes_price": k_yes_price,
        "kalshi_cost_price": k_cost_price,
        "kalshi_fee": k_fee,
        "kalshi_amount": k_raw + k_fee,
        "raw_cost": g_raw + k_raw,
        "fees": g_fee + k_fee,
        "capital": g_raw + k_raw + g_fee + k_fee,
    }


def scaled_realized_pnl(
    record: dict[str, Any],
    costs: dict[str, D],
    settlement: dict[str, Any],
    *,
    required_quantity: Decimal,
) -> dict[str, D]:
    q = D(required_quantity)
    gp = record["gemini_payload"]
    kp = record["kalshi_payload"]
    g_side = str(gp.get("eventOutcome") or "").lower()
    k_side = base._purchased_kalshi_side(kp)
    g_out = str(settlement["gemini_outcome"]).lower()
    k_out = str(settlement["kalshi_outcome"]).lower()
    payout = q * D(int(g_side == g_out)) + q * D(int(k_side == k_out))
    return {
        "payout": payout,
        "gross_pnl": payout - D(costs["raw_cost"]),
        "net_pnl": payout - D(costs["capital"]),
    }


async def emit_scaled_execution_events(
    http: httpx.AsyncClient,
    run_id: str,
    record: dict[str, Any],
    *,
    required_quantity: Decimal,
) -> dict[str, D]:
    q = D(required_quantity)
    result = record["result"]
    gp = record.get("gemini_payload") or {}
    kp = record.get("kalshi_payload") or {}
    symbol = str(gp.get("symbol") or "")
    event_ticker = base._event_ticker_from_symbol(symbol)
    asset = base._asset_from_event_ticker(event_ticker)
    kalshi_ticker = str(kp.get("ticker") or "")
    trade_id = f"gk15m-scale-{run_id}"
    completed = str(record.get("completed_at_utc") or base._iso())
    legs_filled = sum(
        int(D(str((result.get(venue) or {}).get("filled_quantity") or "0")) > 0)
        for venue in ("gemini", "kalshi")
    )
    common = {
        "trade_id": trade_id,
        "event_ts": completed,
        "strategy": "gemini_kalshi_15m_crypto_scale",
        "mode": "LIVE_SCALE_CANARY",
        "market": f"{event_ticker}|{kalshi_ticker}",
        "asset": asset,
        "venues": ["Gemini", "Kalshi"],
        "quantity": str(q),
        "legs_required": 2,
        "legs_filled": legs_filled,
    }
    await base._post_event(http, {
        **common,
        "event_id": f"{trade_id}-submitted",
        "event_type": "ORDER_SUBMITTED",
        "status": str(result.get("outcome") or "SUBMITTED"),
        "opportunity_net_profit": str(record.get("modeled_locked_profit") or "") or None,
        "metadata": {
            "run_id": run_id,
            "gemini_symbol": symbol,
            "kalshi_ticker": kalshi_ticker,
            "hedge_cushion": result.get("hedge_cushion"),
        },
    })

    if str(result.get("outcome")) != "BOTH_FILLED":
        for venue in ("gemini", "kalshi"):
            leg = result.get(venue) or {}
            filled = D(str(leg.get("filled_quantity") or "0"))
            if filled <= 0:
                continue
            await base._post_event(http, {
                **common,
                "event_id": f"{trade_id}-fill-{venue}",
                "event_type": "FILL",
                "status": str(leg.get("status") or "FILLED"),
                "price": str(leg.get("average_fill_price")) if leg.get("average_fill_price") is not None else None,
                "amount": None,
                "fee": str(leg.get("fee_paid") or "0"),
                "side": venue,
                "metadata": {"client_order_id": leg.get("client_order_id"), "filled_quantity": str(filled)},
            })
        raise RuntimeError(f"scaled execution did not finish BOTH_FILLED: {result.get('outcome')}")

    costs = scaled_costs(record, required_quantity=q)
    g = result["gemini"]
    k = result["kalshi"]
    gemini_side = str(gp.get("eventOutcome") or "").lower()
    kalshi_side = base._purchased_kalshi_side(kp)
    await base._post_event(http, {
        **common,
        "event_id": f"{trade_id}-fill-gemini",
        "event_type": "FILL",
        "status": "FILLED",
        "price": str(costs["gemini_price"]),
        "amount": str(costs["gemini_amount"]),
        "fee": str(costs["gemini_fee"]),
        "side": f"Gemini {gemini_side.upper()}",
        "metadata": {"client_order_id": g.get("client_order_id"), "instrument_symbol": symbol},
    })
    await base._post_event(http, {
        **common,
        "event_id": f"{trade_id}-fill-kalshi",
        "event_type": "FILL",
        "status": "FILLED",
        "price": str(costs["kalshi_cost_price"]),
        "amount": str(costs["kalshi_amount"]),
        "fee": str(costs["kalshi_fee"]),
        "side": f"Kalshi {kalshi_side.upper()}",
        "metadata": {
            "client_order_id": k.get("client_order_id"),
            "ticker": kalshi_ticker,
            "yes_space_average_fill_price": str(costs["kalshi_yes_price"]),
        },
    })
    return costs


async def emit_scaled_realized_event(
    http: httpx.AsyncClient,
    *,
    run_id: str,
    record: dict[str, Any],
    costs: dict[str, D],
    settlement: dict[str, Any],
    required_quantity: Decimal,
) -> D:
    q = D(required_quantity)
    pnl = scaled_realized_pnl(record, costs, settlement, required_quantity=q)
    gp = record["gemini_payload"]
    kp = record["kalshi_payload"]
    trade_id = f"gk15m-scale-{run_id}"
    asset = base._asset_from_event_ticker(settlement["event_ticker"])
    status = "REALIZED" if settlement["venue_outcomes_agree"] else "REALIZED_INDEX_MISMATCH"
    await base._post_event(http, {
        "event_id": f"{trade_id}-realized",
        "trade_id": trade_id,
        "event_type": "TRADE_REALIZED",
        "event_ts": base._iso(),
        "strategy": "gemini_kalshi_15m_crypto_scale",
        "mode": "LIVE_SCALE_CANARY",
        "market": f"{settlement['event_ticker']}|{settlement['kalshi_ticker']}",
        "asset": asset,
        "venues": ["Gemini", "Kalshi"],
        "quantity": str(q),
        "legs_required": 2,
        "legs_filled": 2,
        "status": status,
        "amount": str(costs["capital"]),
        "fee": str(costs["fees"]),
        "gross_pnl": str(pnl["gross_pnl"]),
        "net_pnl": str(pnl["net_pnl"]),
        "metadata": {
            "run_id": run_id,
            "gemini_symbol": gp.get("symbol"),
            "kalshi_ticker": kp.get("ticker"),
            "gemini_outcome": settlement["gemini_outcome"],
            "kalshi_outcome": settlement["kalshi_outcome"],
            "venue_outcomes_agree": settlement["venue_outcomes_agree"],
            "payout": str(pnl["payout"]),
            "hedge_cushion": (record.get("result") or {}).get("hedge_cushion"),
        },
    })
    return pnl["net_pnl"]
