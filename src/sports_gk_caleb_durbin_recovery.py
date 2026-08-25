"""Idempotent emergency recovery for sports-gk:sup-0824-234316-13.

A live probe bought 1 Gemini NO on Caleb Durbin 1+ hit while the Kalshi hedge
was rejected. Recovery is allowed only from the authoritative exact position
NO=1, YES=0. Prefer a direct SELL NO LIMIT+FOK using Gemini's own current
position exit price. If that is unavailable, a complementary BUY YES may be used
only with q1 displayed depth. Success is accepted only from authoritative
post-trade positions: direct flatten => NO=0, YES=0; complement => NO=1, YES=1.
Any partial/ambiguous state fails closed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any, Mapping

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_live_shadow import gemini_book_ticker_once
from src.gemini_kalshi_probe_transport import GeminiTradingSession
from src.gemini_prediction_auth import private_rest_request
from src import sports_trade_reporting as reporting

D = Decimal
TRADE_ID = "sports-gk:sup-0824-234316-13"
STRATEGY = "sports_gk_autonomous_rule_risk"
SYMBOL = "gemi-mlb-2608242240-bos-mia-pphits-cdurbino0"
SELL_CLIENT_ID = "recover-gk-0824-234316-13-sno"
YES_CLIENT_ID = "recover-gk-0824-234316-13-y"
MARKET = "baseball|mlb|bos,mia|player_prop|full_contest|player|calebdurbin|hits|GE|1"
PLAYER = "Caleb Durbin"
ORIGINAL_NO_PRICE = D("0.4200")
ORIGINAL_NO_FEE = D("0.0200")
MAX_ATTEMPTS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(value: Any) -> D:
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def _nested_price(row: Mapping[str, Any], action: str, outcome: str) -> D:
    prices = row.get("prices") if isinstance(row.get("prices"), Mapping) else {}
    action_row = prices.get(action) if isinstance(prices.get(action), Mapping) else {}
    return _d(action_row.get(outcome))


async def _position_state() -> dict[str, Any]:
    response = await private_rest_request(
        "POST", "/v1/prediction-markets/positions", params={}, timeout=5.0
    )
    response.raise_for_status()
    body = response.json()
    rows = body.get("positions") or [] if isinstance(body, Mapping) else []
    yes_qty = D("0")
    no_qty = D("0")
    yes_avg: D | None = None
    no_avg: D | None = None
    sell_no_price = D("0")
    is_live = False
    matched: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("symbol") or "").lower() != SYMBOL.lower():
            continue
        matched.append(dict(row))
        is_live = is_live or bool(row.get("isLive"))
        outcome = str(row.get("outcome") or "").lower()
        qty = _d(row.get("totalQuantity"))
        if outcome == "yes":
            yes_qty += qty
            if row.get("avgPrice") is not None:
                yes_avg = _d(row.get("avgPrice"))
        elif outcome == "no":
            no_qty += qty
            if row.get("avgPrice") is not None:
                no_avg = _d(row.get("avgPrice"))
            candidate = _nested_price(row, "sell", "no")
            if candidate > 0:
                sell_no_price = candidate
    return {
        "yes_qty": yes_qty,
        "no_qty": no_qty,
        "yes_avg": yes_avg,
        "no_avg": no_avg,
        "sell_no_price": sell_no_price,
        "is_live": is_live,
        "rows": matched,
    }


async def _release_capital(*, status: str, realized: bool, net_pnl: D | None = None) -> None:
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-capital-released"),
        "trade_id": TRADE_ID,
        "event_type": "CAPITAL_RELEASED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": MARKET,
        "asset": PLAYER,
        "venues": ["gemini"],
        "side": "RECOVERY",
        "status": "CAPITAL_RELEASED",
        "amount": "0",
        "legs_required": 2,
        "legs_filled": 2 if not realized else 1,
        "metadata": {
            "reserved_usd": "0",
            "classification": status,
            "risk_neutralized": True,
            "realized": realized,
            "net_pnl": None if net_pnl is None else str(net_pnl),
            "release_reason": status,
        },
    })


async def _report_direct_flatten(*, price: D, fee: D, raw: Mapping[str, Any], reconciled: bool) -> None:
    realized_net = price - fee - ORIGINAL_NO_PRICE - ORIGINAL_NO_FEE
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-sell-no-fill"),
        "trade_id": TRADE_ID,
        "event_type": "FILL",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": MARKET,
        "asset": PLAYER,
        "venues": ["gemini"],
        "side": "SELL NO RECOVERY",
        "status": "FILLED",
        "quantity": "1",
        "price": str(price),
        "amount": str(price),
        "fee": str(fee),
        "legs_required": 1,
        "legs_filled": 1,
        "metadata": {
            "classification": "ONE_LEG_RECOVERY_DIRECT_FLATTEN",
            "recovery_client_order_id": SELL_CLIENT_ID,
            "risk_neutralized": True,
            "position_closed": True,
            "reconciled_by_position": reconciled,
            "raw_recovery_execution": dict(raw),
        },
    })
    await _release_capital(
        status="ONE_LEG_RECOVERY_DIRECT_FLATTEN",
        realized=True,
        net_pnl=realized_net,
    )
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-trade-realized"),
        "trade_id": TRADE_ID,
        "event_type": "TRADE_REALIZED",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": MARKET,
        "asset": PLAYER,
        "venues": ["gemini"],
        "side": "FLAT",
        "status": "TRADE_REALIZED",
        "quantity": "1",
        "legs_required": 1,
        "legs_filled": 1,
        "fee": str(ORIGINAL_NO_FEE + fee),
        "capital_wagered": str(ORIGINAL_NO_PRICE + ORIGINAL_NO_FEE),
        "gross_pnl": str(price - ORIGINAL_NO_PRICE),
        "net_pnl": str(realized_net),
        "realized_pnl": str(realized_net),
        "realized": True,
        "metadata": {
            "classification": "ONE_LEG_RECOVERY_DIRECT_FLATTEN",
            "original_no_price": str(ORIGINAL_NO_PRICE),
            "original_no_fee": str(ORIGINAL_NO_FEE),
            "exit_no_price": str(price),
            "exit_no_fee": str(fee),
            "risk_neutralized": True,
            "position_closed": True,
        },
    })
    print(json.dumps({
        "status": "CALEB_DURBIN_EXPOSURE_FLATTENED",
        "trade_id": TRADE_ID,
        "symbol": SYMBOL,
        "exit_no_price": str(price),
        "exit_no_fee": str(fee),
        "realized_net_pnl": str(realized_net),
        "realized": True,
    }, separators=(",", ":")), flush=True)


async def _report_complement(*, price: D, fee: D, raw: Mapping[str, Any], reconciled: bool) -> None:
    locked = D("1") - ORIGINAL_NO_PRICE - ORIGINAL_NO_FEE - price - fee
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-gemini-yes-fill"),
        "trade_id": TRADE_ID,
        "event_type": "FILL",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": MARKET,
        "asset": PLAYER,
        "venues": ["gemini"],
        "side": "BUY YES RECOVERY",
        "status": "FILLED",
        "quantity": "1",
        "price": str(price),
        "amount": str(price),
        "fee": str(fee),
        "legs_required": 2,
        "legs_filled": 2,
        "metadata": {
            "classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
            "recovery_client_order_id": YES_CLIENT_ID,
            "same_contract_complement": True,
            "risk_neutralized": True,
            "settlement_pending": True,
            "already_reconciled": reconciled,
            "raw_recovery_execution": dict(raw),
        },
    })
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-neutralized"),
        "trade_id": TRADE_ID,
        "event_type": "EXECUTION_RESULT",
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": MARKET,
        "asset": PLAYER,
        "venues": ["gemini"],
        "side": "SAME_GEMINI_COMPLEMENTARY",
        "status": "RISK_NEUTRALIZED_AWAITING_SETTLEMENT",
        "quantity": "1",
        "legs_required": 2,
        "legs_filled": 2,
        "opportunity_net_profit": str(locked),
        "metadata": {
            "classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
            "risk_neutralized": True,
            "guaranteed_payout_at_settlement": "1",
            "settlement_pending": True,
            "realized": False,
            "locked_net_after_known_entry_fees": str(locked),
            "execution": {"hard_halt_required": False, "outcome": "RISK_NEUTRALIZED"},
        },
    })
    await _release_capital(
        status="EXACT_SAME_CONTRACT_YES_PLUS_NO_COMPLEMENT",
        realized=False,
        net_pnl=None,
    )
    print(json.dumps({
        "status": "CALEB_DURBIN_RISK_NEUTRALIZED",
        "trade_id": TRADE_ID,
        "symbol": SYMBOL,
        "recovery_yes_price": str(price),
        "recovery_yes_fee": str(fee),
        "locked_net_after_known_entry_fees": str(locked),
        "settlement_pending": True,
        "realized": False,
    }, separators=(",", ":")), flush=True)


async def main() -> None:
    state = await _position_state()
    print(json.dumps({
        "status": "RECOVERY_POSITION_PREFLIGHT",
        "symbol": SYMBOL,
        "yes_qty": str(state["yes_qty"]),
        "no_qty": str(state["no_qty"]),
        "sell_no_price": str(state["sell_no_price"]),
        "is_live": bool(state["is_live"]),
        "order_entry": False,
    }, separators=(",", ":")), flush=True)

    if state["no_qty"] == D("0") and state["yes_qty"] == D("0"):
        raise RuntimeError("position already flat but no authoritative recovery fill is available in this one-off")
    if state["no_qty"] != D("1") or state["yes_qty"] not in {D("0"), D("1")}:
        raise RuntimeError(f"unexpected starting position YES={state['yes_qty']} NO={state['no_qty']}")
    if state["yes_qty"] == D("1"):
        price = state["yes_avg"] if isinstance(state["yes_avg"], D) else D("0")
        if price <= 0:
            raise RuntimeError("already-complementary position lacks YES average price")
        fee = gemini_predictions_taker_fee(price, D("1"))
        await _report_complement(price=price, fee=fee, raw={"position_state": "already_complementary"}, reconciled=True)
        return

    session = GeminiTradingSession()
    await session.connect()
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            current = await _position_state()
            if current["no_qty"] == D("0") and current["yes_qty"] == D("0"):
                raise RuntimeError("position became flat between attempts without a locally attributable recovery fill")
            if current["no_qty"] == D("1") and current["yes_qty"] == D("1"):
                price = current["yes_avg"] if isinstance(current["yes_avg"], D) else D("0")
                if price <= 0:
                    raise RuntimeError("complementary YES position lacks average price")
                fee = gemini_predictions_taker_fee(price, D("1"))
                await _report_complement(price=price, fee=fee, raw={"position_state": "reconciled_complement"}, reconciled=True)
                return
            if current["no_qty"] != D("1") or current["yes_qty"] != D("0"):
                raise RuntimeError(f"ambiguous current position YES={current['yes_qty']} NO={current['no_qty']}")

            sell_no = D(current["sell_no_price"])
            if D("0.01") <= sell_no <= D("0.99"):
                payload = {
                    "symbol": SYMBOL,
                    "side": "SELL",
                    "type": "LIMIT",
                    "timeInForce": "FOK",
                    "price": str(sell_no),
                    "quantity": "1",
                    "eventOutcome": "NO",
                    "clientOrderId": SELL_CLIENT_ID,
                }
                result = await session.place_fok(payload, terminal_timeout=2.5)
                post = await _position_state()
                print(json.dumps({
                    "status": "RECOVERY_SELL_NO_FOK_RESULT",
                    "attempt": attempt,
                    "sell_no_price": str(sell_no),
                    "execution": result.as_dict(),
                    "post_yes_qty": str(post["yes_qty"]),
                    "post_no_qty": str(post["no_qty"]),
                }, separators=(",", ":"), default=str), flush=True)
                if post["no_qty"] == D("0") and post["yes_qty"] == D("0"):
                    price = _d(result.average_fill_price) if result.average_fill_price is not None else sell_no
                    fee = _d(result.fee_paid) if result.fee_paid is not None else gemini_predictions_taker_fee(price, D("1"))
                    await _report_direct_flatten(price=price, fee=fee, raw=result.as_dict(), reconciled=result.status != "FILLED")
                    return
                if post["no_qty"] != D("1") or post["yes_qty"] != D("0"):
                    raise RuntimeError(f"partial/ambiguous direct flatten YES={post['yes_qty']} NO={post['no_qty']}")
                if result.status == "UNKNOWN" or _d(result.filled_quantity) > 0:
                    raise RuntimeError(f"ambiguous direct flatten result status={result.status} qty={result.filled_quantity}")

            book = await gemini_book_ticker_once(SYMBOL, timeout_s=5.0)
            yes_ask = D(book.yes_ask)
            yes_depth = D(book.yes_ask_size)
            if D("0.01") <= yes_ask <= D("0.99") and yes_depth >= D("1"):
                payload = {
                    "symbol": SYMBOL,
                    "side": "BUY",
                    "type": "LIMIT",
                    "timeInForce": "FOK",
                    "price": str(yes_ask),
                    "quantity": "1",
                    "eventOutcome": "YES",
                    "clientOrderId": YES_CLIENT_ID,
                }
                result = await session.place_fok(payload, terminal_timeout=2.5)
                post = await _position_state()
                print(json.dumps({
                    "status": "RECOVERY_BUY_YES_FOK_RESULT",
                    "attempt": attempt,
                    "yes_ask": str(yes_ask),
                    "yes_depth": str(yes_depth),
                    "execution": result.as_dict(),
                    "post_yes_qty": str(post["yes_qty"]),
                    "post_no_qty": str(post["no_qty"]),
                }, separators=(",", ":"), default=str), flush=True)
                if post["no_qty"] == D("1") and post["yes_qty"] == D("1"):
                    price = _d(result.average_fill_price) if result.average_fill_price is not None else yes_ask
                    fee = _d(result.fee_paid) if result.fee_paid is not None else gemini_predictions_taker_fee(price, D("1"))
                    await _report_complement(price=price, fee=fee, raw=result.as_dict(), reconciled=result.status != "FILLED")
                    return
                if post["no_qty"] != D("1") or post["yes_qty"] != D("0"):
                    raise RuntimeError(f"partial/ambiguous complement YES={post['yes_qty']} NO={post['no_qty']}")
                if result.status == "UNKNOWN" or _d(result.filled_quantity) > 0:
                    raise RuntimeError(f"ambiguous complement result status={result.status} qty={result.filled_quantity}")

            print(json.dumps({
                "status": "RECOVERY_NO_EXECUTABLE_EXIT",
                "attempt": attempt,
                "sell_no_price": str(sell_no),
                "yes_ask": str(yes_ask),
                "yes_depth": str(yes_depth),
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
            await asyncio.sleep(0.5)
        raise RuntimeError("no executable q1 recovery path after bounded attempts")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
