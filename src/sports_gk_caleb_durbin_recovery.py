"""Idempotent emergency recovery for sports-gk:sup-0824-234316-13.

The live probe bought 1 Gemini NO on Caleb Durbin 1+ hit, while the Kalshi leg
was rejected because the transport used the wrong order endpoint. The legacy
flatten then used an invalid Gemini prediction-market MARKET order.

This recovery does not create a new directional view. It proves the original
Gemini NO fill, proves no prior recovery fill exists, reads the live Gemini YES
book, and buys exactly 1 complementary YES with LIMIT+FOK. Owning one YES and
one NO on the same Gemini contract neutralizes event direction and fixes the
payout at $1 before fees. A deterministic recovery client ID makes restarts
idempotent. Any ambiguous or partial state fails closed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import time
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
ORIGINAL_CLIENT_ID = "mlbgkm-sup-0824-234316-13-g"
RECOVERY_CLIENT_ID = "recover-gk-0824-234316-13-y"
MARKET = "baseball|mlb|bos,mia|player_prop|full_contest|player|calebdurbin|hits|GE|1"
PLAYER = "Caleb Durbin"
ORIGINAL_NO_PRICE = D("0.4200")
ORIGINAL_NO_FEE = D("0.0200")
ORIGINAL_NO_QTY = D("1")
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

async def _order_rows(*, status: str) -> list[Mapping[str, Any]]:
    now_ms = int(time.time() * 1000)
    response = await private_rest_request(
        "POST", "/v1/prediction-markets/orders/history",
        params={"status": status, "symbol": SYMBOL, "limit": 1000,
                "from": now_ms - 4 * 60 * 60 * 1000, "to": now_ms + 60_000},
        timeout=5.0,
    )
    response.raise_for_status()
    body = response.json()
    return [row for row in body.get("orders", []) if isinstance(row, Mapping)]

async def _find_history(client_id: str) -> Mapping[str, Any] | None:
    for status in ("filled", "cancelled"):
        for row in await _order_rows(status=status):
            if str(row.get("clientOrderId") or "") == client_id:
                return row
    return None

async def _prove_original_fill() -> Mapping[str, Any]:
    row = await _find_history(ORIGINAL_CLIENT_ID)
    if row is None:
        raise RuntimeError("original Gemini order not found in authoritative history")
    qty = _d(row.get("filledQuantity"))
    if qty != ORIGINAL_NO_QTY:
        raise RuntimeError(f"original Gemini fill quantity is not exactly 1: {qty}")
    outcome = str(row.get("outcome") or row.get("eventOutcome") or "NO").upper()
    if outcome not in {"NO", ""}:
        raise RuntimeError(f"original Gemini outcome mismatch: {outcome}")
    return row

async def _post_recovery_fill(*, price: D, fee: D, raw: Mapping[str, Any], already_reconciled: bool) -> None:
    amount = price * ORIGINAL_NO_QTY
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-gemini-yes-fill"), "trade_id": TRADE_ID,
        "event_type": "FILL", "event_ts": _now(), "strategy": STRATEGY, "mode": "LIVE_PROBE",
        "market": MARKET, "asset": PLAYER, "venues": ["gemini"], "side": "BUY YES RECOVERY",
        "status": "FILLED", "quantity": "1", "price": str(price), "amount": str(amount),
        "fee": str(fee), "legs_required": 2, "legs_filled": 2,
        "metadata": {"classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
                     "recovery_client_order_id": RECOVERY_CLIENT_ID,
                     "original_client_order_id": ORIGINAL_CLIENT_ID, "original_outcome": "NO",
                     "recovery_outcome": "YES", "same_contract_complement": True,
                     "risk_neutralized": True, "settlement_pending": True,
                     "already_reconciled": already_reconciled, "raw_recovery_execution": dict(raw)},
    })
    locked = D("1") - ORIGINAL_NO_PRICE - ORIGINAL_NO_FEE - price - fee
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-neutralized"), "trade_id": TRADE_ID,
        "event_type": "EXECUTION_RESULT", "event_ts": _now(), "strategy": STRATEGY,
        "mode": "LIVE_PROBE", "market": MARKET, "asset": PLAYER, "venues": ["gemini"],
        "side": "SAME_GEMINI_COMPLEMENTARY", "status": "RISK_NEUTRALIZED_AWAITING_SETTLEMENT",
        "quantity": "1", "legs_required": 2, "legs_filled": 2, "opportunity_net_profit": str(locked),
        "metadata": {"classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
                     "risk_neutralized": True, "guaranteed_payout_at_settlement": "1",
                     "settlement_pending": True, "realized": False, "original_no_cost": str(ORIGINAL_NO_PRICE),
                     "original_no_fee": str(ORIGINAL_NO_FEE), "recovery_yes_cost": str(price),
                     "recovery_yes_fee": str(fee), "locked_net_after_known_entry_fees": str(locked),
                     "execution": {"hard_halt_required": False, "outcome": "RISK_NEUTRALIZED"}},
    })
    await reporting._post_event({
        "event_id": _safe_id(f"{TRADE_ID}:recovery-capital-released"), "trade_id": TRADE_ID,
        "event_type": "CAPITAL_RELEASED", "event_ts": _now(), "strategy": STRATEGY,
        "mode": "LIVE_PROBE", "market": MARKET, "asset": PLAYER, "venues": ["gemini"],
        "side": "SAME_GEMINI_COMPLEMENTARY", "status": "CAPITAL_RELEASED", "amount": "0",
        "legs_required": 2, "legs_filled": 2,
        "metadata": {"reserved_usd": "0", "classification": "ONE_LEG_RECOVERY_SAME_GEMINI_COMPLEMENT",
                     "risk_neutralized": True, "settlement_pending": True, "realized": False,
                     "release_reason": "EXACT_SAME_CONTRACT_YES_PLUS_NO_COMPLEMENT",
                     "locked_net_after_known_entry_fees": str(locked)},
    })
    print(json.dumps({"status": "CALEB_DURBIN_RISK_NEUTRALIZED", "trade_id": TRADE_ID,
                      "symbol": SYMBOL, "original_no_price": str(ORIGINAL_NO_PRICE),
                      "original_no_fee": str(ORIGINAL_NO_FEE), "recovery_yes_price": str(price),
                      "recovery_yes_fee": str(fee), "locked_net_after_known_entry_fees": str(locked),
                      "settlement_pending": True, "realized": False}, separators=(",", ":")), flush=True)

async def _reconcile_existing_recovery() -> bool:
    row = await _find_history(RECOVERY_CLIENT_ID)
    if row is None:
        return False
    qty = _d(row.get("filledQuantity"))
    status = str(row.get("status") or "").lower()
    if qty == D("1") and status == "filled":
        price = _d(row.get("avgExecutionPrice") or row.get("price"))
        fee = gemini_predictions_taker_fee(price, D("1"))
        await _post_recovery_fill(price=price, fee=fee, raw=row, already_reconciled=True)
        return True
    if qty > 0:
        raise RuntimeError(f"recovery order has partial nonzero fill: qty={qty} status={status}")
    return False

async def main() -> None:
    original = await _prove_original_fill()
    print(json.dumps({"status": "ORIGINAL_GEMINI_NO_FILL_CONFIRMED",
                      "client_order_id": ORIGINAL_CLIENT_ID,
                      "filled_quantity": str(original.get("filledQuantity")),
                      "symbol": SYMBOL, "order_entry": False}, separators=(",", ":")), flush=True)
    if await _reconcile_existing_recovery():
        return
    session = GeminiTradingSession()
    await session.connect()
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if await _reconcile_existing_recovery():
                return
            book = await gemini_book_ticker_once(SYMBOL, timeout_s=5.0)
            yes_ask = D(book.yes_ask); yes_depth = D(book.yes_ask_size)
            if not (D("0.01") <= yes_ask <= D("0.99")) or yes_depth < D("1"):
                print(json.dumps({"status": "RECOVERY_WAIT_NO_Q1_YES_ASK", "attempt": attempt,
                                  "yes_ask": str(yes_ask), "yes_depth": str(yes_depth),
                                  "order_entry": False}, separators=(",", ":")), flush=True)
                await asyncio.sleep(0.5); continue
            payload = {"symbol": SYMBOL, "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
                       "price": str(yes_ask), "quantity": "1", "eventOutcome": "YES",
                       "clientOrderId": RECOVERY_CLIENT_ID}
            result = await session.place_fok(payload, terminal_timeout=2.5)
            print(json.dumps({"status": "RECOVERY_FOK_RESULT", "attempt": attempt,
                              "yes_ask": str(yes_ask), "yes_depth": str(yes_depth),
                              "execution": result.as_dict()}, separators=(",", ":"), default=str), flush=True)
            qty = _d(result.filled_quantity)
            if result.status == "FILLED" and qty == D("1"):
                price = _d(result.average_fill_price or yes_ask)
                fee = _d(result.fee_paid) if result.fee_paid is not None else gemini_predictions_taker_fee(price, D("1"))
                await _post_recovery_fill(price=price, fee=fee, raw=result.as_dict(), already_reconciled=False)
                return
            if result.status == "UNKNOWN" or qty > 0:
                raise RuntimeError(f"ambiguous/partial recovery mutation: status={result.status} qty={qty}")
            await asyncio.sleep(0.25)
        raise RuntimeError("no q1 complementary Gemini YES fill after bounded recovery attempts")
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
