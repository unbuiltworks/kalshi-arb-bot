"""Idempotent emergency recovery for sports-gk:sup-0824-234316-13.

The live probe bought 1 Gemini NO on Caleb Durbin 1+ hit while its Kalshi hedge
was rejected. Recover directional risk by buying exactly 1 complementary Gemini
YES on the same contract. Current Gemini positions are authoritative: mutation is
allowed only from exact NO=1, YES=0, and success is accepted only after exact
NO=1, YES=1 is observed. Any other state fails closed.
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
RECOVERY_CLIENT_ID = "recover-gk-0824-234316-13-y"
MARKET = "baseball|mlb|bos,mia|player_prop|full_contest|player|calebdurbin|hits|GE|1"
PLAYER = "Caleb Durbin"
ORIGINAL_NO_PRICE = D("0.4200")
ORIGINAL_NO_FEE = D("0.0200")
MAX_ATTEMPTS = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(value: Any) -> D:
    try:
        return D(str(value or "0"))
    except Exception:
        return D("0")


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


async def _position_state() -> dict[str, Any]:
    response = await private_rest_request(
        "POST", "/v1/prediction-markets/positions", params={}, timeout=5.0
    )
    response.raise_for_status()
    body = response.json()
    rows = body.get("positions") or [] if isinstance(body, Mapping) else []
    yes_qty = D("0")
    no_qty = D("0")
    yes_avg = None
    no_avg = None
    matched = []
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("symbol") or "").lower() != SYMBOL.lower():
            continue
        matched.append(dict(row))
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
    return {"yes_qty": yes_qty, "no_qty": no_qty, "yes_avg": yes_avg, "no_avg": no_avg, "rows": matched}


async def _report_neutralized(*, price: D, fee: D, raw: Mapping[str, Any], reconciled: bool) -> None:
    locked = D("1") - ORIGINAL_NO_PRICE - ORIGINAL_NO_FEE - price - fee
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-gemini-yes-fill"),
        "trade_id": TRADE_ID, "event_type": "FILL", "event_ts": _now(),
        "strategy": STRATEGY, "mode": "LIVE_PROBE", "market": MARKET,
        "asset": PLAYER, "venues": ["gemini"], "side": "BUY YES RECOVERY",
        "status": "FILLED", "quantity": "1", "price": str(price),
        "amount": str(price), "fee": str(fee), "legs_required": 2, "legs_filled": 2,
        "metadata": {"classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
                     "recovery_client_order_id": RECOVERY_CLIENT_ID,
                     "same_contract_complement": True, "risk_neutralized": True,
                     "settlement_pending": True, "already_reconciled": reconciled,
                     "raw_recovery_execution": dict(raw)},
    })
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-neutralized"),
        "trade_id": TRADE_ID, "event_type": "EXECUTION_RESULT", "event_ts": _now(),
        "strategy": STRATEGY, "mode": "LIVE_PROBE", "market": MARKET,
        "asset": PLAYER, "venues": ["gemini"], "side": "SAME_GEMINI_COMPLEMENTARY",
        "status": "RISK_NEUTRALIZED_AWAITING_SETTLEMENT", "quantity": "1",
        "legs_required": 2, "legs_filled": 2, "opportunity_net_profit": str(locked),
        "metadata": {"classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
                     "risk_neutralized": True, "guaranteed_payout_at_settlement": "1",
                     "settlement_pending": True, "realized": False,
                     "original_no_cost": str(ORIGINAL_NO_PRICE), "original_no_fee": str(ORIGINAL_NO_FEE),
                     "recovery_yes_cost": str(price), "recovery_yes_fee": str(fee),
                     "locked_net_after_known_entry_fees": str(locked),
                     "execution": {"hard_halt_required": False, "outcome": "RISK_NEUTRALIZED"}},
    })
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-capital-released"),
        "trade_id": TRADE_ID, "event_type": "CAPITAL_RELEASED", "event_ts": _now(),
        "strategy": STRATEGY, "mode": "LIVE_PROBE", "market": MARKET,
        "asset": PLAYER, "venues": ["gemini"], "side": "SAME_GEMINI_COMPLEMENTARY",
        "status": "CAPITAL_RELEASED", "amount": "0", "legs_required": 2, "legs_filled": 2,
        "metadata": {"reserved_usd": "0", "classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
                     "risk_neutralized": True, "settlement_pending": True, "realized": False,
                     "release_reason": "EXACT_SAME_CONTRACT_YES_PLUS_NO_COMPLEMENT",
                     "locked_net_after_known_entry_fees": str(locked)},
    })
    print(json.dumps({"status": "CALEB_DURBIN_RISK_NEUTRALIZED", "trade_id": TRADE_ID,
                      "symbol": SYMBOL, "recovery_yes_price": str(price), "recovery_yes_fee": str(fee),
                      "locked_net_after_known_entry_fees": str(locked),
                      "settlement_pending": True, "realized": False}, separators=(",", ":")), flush=True)


async def main() -> None:
    state = await _position_state()
    print(json.dumps({"status": "RECOVERY_POSITION_PREFLIGHT", "symbol": SYMBOL,
                      "yes_qty": str(state["yes_qty"]), "no_qty": str(state["no_qty"]),
                      "order_entry": False}, separators=(",", ":")), flush=True)
    if state["no_qty"] != D("1"):
        raise RuntimeError(f"expected exact Gemini NO=1 before recovery; found {state['no_qty']}")
    if state["yes_qty"] == D("1"):
        price = state["yes_avg"] if isinstance(state["yes_avg"], D) else D("0")
        if price <= 0:
            raise RuntimeError("already-neutralized YES position lacks authoritative avg price")
        fee = gemini_predictions_taker_fee(price, D("1"))
        await _report_neutralized(price=price, fee=fee, raw={"position_state": "already_complementary"}, reconciled=True)
        return
    if state["yes_qty"] != D("0"):
        raise RuntimeError(f"unexpected Gemini YES quantity before recovery: {state['yes_qty']}")

    session = GeminiTradingSession()
    await session.connect()
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            current = await _position_state()
            if current["no_qty"] != D("1"):
                raise RuntimeError(f"Gemini NO quantity changed during recovery: {current['no_qty']}")
            if current["yes_qty"] == D("1"):
                price = current["yes_avg"] if isinstance(current["yes_avg"], D) else D("0")
                if price <= 0:
                    raise RuntimeError("reconciled YES position lacks avg price")
                fee = gemini_predictions_taker_fee(price, D("1"))
                await _report_neutralized(price=price, fee=fee, raw={"position_state": "reconciled_after_send"}, reconciled=True)
                return
            if current["yes_qty"] != D("0"):
                raise RuntimeError(f"unexpected Gemini YES quantity during recovery: {current['yes_qty']}")

            book = await gemini_book_ticker_once(SYMBOL, timeout_s=5.0)
            yes_ask = D(book.yes_ask)
            yes_depth = D(book.yes_ask_size)
            if not (D("0.01") <= yes_ask <= D("0.99")) or yes_depth < D("1"):
                print(json.dumps({"status": "RECOVERY_WAIT_NO_Q1_YES_ASK", "attempt": attempt,
                                  "yes_ask": str(yes_ask), "yes_depth": str(yes_depth),
                                  "order_entry": False}, separators=(",", ":")), flush=True)
                await asyncio.sleep(0.5)
                continue

            payload = {"symbol": SYMBOL, "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
                       "price": str(yes_ask), "quantity": "1", "eventOutcome": "YES",
                       "clientOrderId": RECOVERY_CLIENT_ID}
            result = await session.place_fok(payload, terminal_timeout=2.5)
            print(json.dumps({"status": "RECOVERY_FOK_RESULT", "attempt": attempt,
                              "yes_ask": str(yes_ask), "yes_depth": str(yes_depth),
                              "execution": result.as_dict()}, separators=(",", ":"), default=str), flush=True)

            post = await _position_state()
            if post["no_qty"] == D("1") and post["yes_qty"] == D("1"):
                price = _d(result.average_fill_price) if result.average_fill_price is not None else post["yes_avg"]
                if not isinstance(price, D) or price <= 0:
                    price = yes_ask
                fee = _d(result.fee_paid) if result.fee_paid is not None else gemini_predictions_taker_fee(price, D("1"))
                await _report_neutralized(price=price, fee=fee, raw=result.as_dict(), reconciled=result.status != "FILLED")
                return
            if post["no_qty"] != D("1") or post["yes_qty"] != D("0"):
                raise RuntimeError(f"ambiguous post-recovery position: YES={post['yes_qty']} NO={post['no_qty']}")
            if result.status == "UNKNOWN" or _d(result.filled_quantity) > 0:
                raise RuntimeError(f"ambiguous recovery result without complementary position: {result.status} qty={result.filled_quantity}")
            await asyncio.sleep(0.25)
        raise RuntimeError("no q1 complementary Gemini YES fill after bounded recovery attempts")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
