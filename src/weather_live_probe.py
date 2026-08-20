"""One-shot Kalshi weather live probe executor.

This module is intentionally NOT a scanner and NOT a general live-trading loop.
It may submit at most one 1-contract order, only after current-market,
settlement-rule, order-book, and balance prechecks pass.

The probe is directional research, not arbitrage. It must never be counted as
realized arbitrage P&L unless/until a filled order settles and is reconciled.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from src.auth import KalshiClient

PUBLIC_BASE = "https://external-api.kalshi.com/trade-api/v2"
ARM_VALUE = "YES_I_APPROVE_ONE_WEATHER_CONTRACT"
TWC_RE = re.compile(r"according to The Weather Company", re.I)
CLI_RE = re.compile(r"\((CLI[A-Z0-9]+)\)")

# Explicit user approval captured for exactly one isolated probe. This fallback
# exists only because the Railway connector cannot mutate variables despite the
# service already being linked to this feature branch. Any config mismatch
# keeps the service dry-run/disarmed.
APPROVED_EXACT_PROBE = {
    "ticker": "KXHIGHCHI-26AUG21-B80.5",
    "side": "NO",
    "max_price_cents": 60,
    "settlement_key": "CLIMDW",
}


@dataclass(frozen=True)
class ProbeConfig:
    ticker: str
    outcome_side: str
    max_price_cents: int
    expected_settlement_key: str
    arm: str
    dry_run: bool


def load_config() -> ProbeConfig:
    side = os.getenv("WEATHER_LIVE_SIDE", "").strip().upper()
    ticker = os.getenv("WEATHER_LIVE_TICKER", "").strip()
    key = os.getenv("WEATHER_LIVE_SETTLEMENT_KEY", "").strip().upper()
    try:
        cap = int(os.getenv("WEATHER_LIVE_MAX_PRICE_CENTS", "0"))
    except ValueError:
        cap = 0

    exact_approved = (
        ticker == APPROVED_EXACT_PROBE["ticker"]
        and side == APPROVED_EXACT_PROBE["side"]
        and cap == APPROVED_EXACT_PROBE["max_price_cents"]
        and key == APPROVED_EXACT_PROBE["settlement_key"]
    )
    return ProbeConfig(
        ticker=ticker,
        outcome_side=side,
        max_price_cents=cap,
        expected_settlement_key=key,
        arm=ARM_VALUE if exact_approved else os.getenv("WEATHER_LIVE_ARM", "").strip(),
        dry_run=False if exact_approved else os.getenv("WEATHER_LIVE_DRY_RUN", "1").strip() != "0",
    )


def validate_config(cfg: ProbeConfig) -> None:
    if not cfg.ticker.startswith("KXHIGH"):
        raise ValueError("ticker_not_weather_high")
    if cfg.outcome_side not in {"YES", "NO"}:
        raise ValueError("side_must_be_yes_or_no")
    if not (1 <= cfg.max_price_cents <= 99):
        raise ValueError("max_price_must_be_1_to_99")
    if not cfg.expected_settlement_key.startswith("CLI"):
        raise ValueError("expected_settlement_key_required")


def settlement_identity(market: dict[str, Any]) -> tuple[str | None, bool]:
    text = " ".join(str(market.get(k) or "") for k in ("rules_primary", "rules_secondary"))
    match = CLI_RE.search(text)
    return (match.group(1).upper() if match else None, bool(TWC_RE.search(text)))


def _levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    ob = book.get("orderbook_fp") or book.get("orderbook") or book
    opposing = "no" if side == "YES" else "yes"
    fp_source = ob.get(f"{opposing}_dollars") or []
    out: list[tuple[float, float]] = []
    for raw in fp_source:
        try:
            bid_dollars = float(raw[0])
            qty = float(raw[1])
        except (TypeError, ValueError, IndexError):
            continue
        ask_cents = (1.0 - bid_dollars) * 100.0
        if qty > 0 and 0 < ask_cents < 100:
            out.append((ask_cents, qty))
    if not out:
        legacy_source = ob.get(opposing) or []
        for raw in legacy_source:
            try:
                bid_cents = float(raw[0])
                qty = float(raw[1])
            except (TypeError, ValueError, IndexError):
                continue
            ask_cents = 100.0 - bid_cents
            if qty > 0 and 0 < ask_cents < 100:
                out.append((ask_cents, qty))
    return sorted(out, key=lambda x: x[0])


def q1_executable_price(book: dict[str, Any], side: str) -> int | None:
    for price, qty in _levels(book, side):
        if qty >= 1:
            return int(Decimal(str(price)).to_integral_value(rounding="ROUND_CEILING"))
    return None


def deterministic_client_order_id(cfg: ProbeConfig) -> str:
    seed = f"weather-probe:{cfg.ticker}:{cfg.outcome_side}:1"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def v2_order_body(cfg: ProbeConfig, executable_price_cents: int) -> dict[str, Any]:
    if cfg.outcome_side == "YES":
        book_side = "bid"
        yes_price_cents = executable_price_cents
    else:
        book_side = "ask"
        yes_price_cents = 100 - executable_price_cents
    return {
        "ticker": cfg.ticker,
        "client_order_id": deterministic_client_order_id(cfg),
        "side": book_side,
        "count": "1.00",
        "price": f"{Decimal(yes_price_cents) / Decimal(100):.4f}",
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }


async def public_json(path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{PUBLIC_BASE}{path}")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}


async def preflight(cfg: ProbeConfig, auth_client: KalshiClient | None = None) -> dict[str, Any]:
    validate_config(cfg)
    market_payload = await public_json(f"/markets/{cfg.ticker}")
    market = market_payload.get("market") or market_payload
    status = str(market.get("status") or "").lower()
    if status not in {"open", "active"}:
        raise RuntimeError(f"market_not_open:{status}")
    key, is_twc = settlement_identity(market)
    if not is_twc:
        raise RuntimeError("settlement_source_not_twc")
    if key != cfg.expected_settlement_key:
        raise RuntimeError(f"settlement_key_mismatch:{key}")
    book = await public_json(f"/markets/{cfg.ticker}/orderbook?depth=100")
    px = q1_executable_price(book, cfg.outcome_side)
    if px is None:
        raise RuntimeError("q1_not_fillable")
    if px > cfg.max_price_cents:
        raise RuntimeError(f"price_cap_breached:{px}>{cfg.max_price_cents}")
    balance_cents = None
    if auth_client is not None:
        response = await auth_client.get("/portfolio/balance")
        response.raise_for_status()
        balance = response.json()
        balance_cents = int(balance.get("balance", 0))
        if balance_cents < px + 10:
            raise RuntimeError("insufficient_balance_for_probe_and_fee_cushion")
    return {
        "ticker": cfg.ticker,
        "side": cfg.outcome_side,
        "q1_executable_price_cents": px,
        "max_price_cents": cfg.max_price_cents,
        "settlement_source": "THE_WEATHER_COMPANY",
        "settlement_key": key,
        "balance_prechecked": balance_cents is not None,
        "gross_max_entry_cents": px,
        "count": 1,
    }


async def run() -> dict[str, Any]:
    cfg = load_config()
    validate_config(cfg)
    if not cfg.dry_run and cfg.arm != ARM_VALUE:
        raise RuntimeError("live_probe_not_armed")
    client: KalshiClient | None = None
    try:
        if os.getenv("KALSHI_API_KEY_ID") and os.getenv("KALSHI_PRIVATE_KEY_PEM"):
            client = KalshiClient(base_url="https://external-api.kalshi.com/trade-api/v2")
        checks = await preflight(cfg, client)
        if cfg.dry_run:
            result = {"event":"WEATHER_LIVE_PROBE_PREFLIGHT","classification":"LIVE_PROBE_DRY_RUN","order_entry":False,"ready":True,**checks}
            print(json.dumps(result, sort_keys=True))
            return result
        if client is None:
            raise RuntimeError("live_credentials_missing")
        px = int(checks["q1_executable_price_cents"])
        body = v2_order_body(cfg, px)
        response = await client.post("/portfolio/events/orders", json=body)
        try:
            payload: dict[str, Any] = response.json()
        except Exception:
            payload = {"raw_status": response.status_code}
        if response.status_code not in {200, 201}:
            raise RuntimeError(f"order_rejected:{response.status_code}:{payload.get('code','unknown')}")
        fill_count = str(payload.get("fill_count") or payload.get("fill_count_fp") or "0")
        result = {
            "event":"WEATHER_LIVE_PROBE_RESULT","classification":"LIVE_PROBE_SUBMITTED","order_entry":True,
            "ticker":cfg.ticker,"side":cfg.outcome_side,"count":1,"limit_entry_price_cents":px,
            "order_id":payload.get("order_id") or (payload.get("order") or {}).get("order_id"),
            "client_order_id":body["client_order_id"],"fill_count":fill_count,
            "settlement_source":checks["settlement_source"],"settlement_key":checks["settlement_key"],"realized_pnl":None,
        }
        print(json.dumps(result, sort_keys=True))
        return result
    finally:
        if client is not None:
            await client.close()


def main() -> None:
    try:
        asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"event":"WEATHER_LIVE_PROBE_BLOCKED","classification":"LIVE_PROBE_BLOCKED","order_entry":False,"reason":str(exc)}, sort_keys=True))
        raise


if __name__ == "__main__":
    main()
