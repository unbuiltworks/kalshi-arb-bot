"""One-shot q1 Gemini Sports L5-style live mechanics canary.

Reuses the already-proven Gemini inventory-maker child for order placement,
fill/cancel handling, durable reservation, and queue telemetry.  Only event
discovery and ledger identity are substituted.  This is a mechanics probe, not
an assertion that Sports receives rebates/LR or that the strategy is profitable.

Live bounds:
- one MLB moneyline contract only, expiring within six hours;
- exactly q=1 maximum YES and q=1 maximum NO on the SAME Gemini contract;
- both quotes maker-only and inside 20c-80c;
- current fee-adjusted matched-pair edge >=1c at entry;
- maximum 180 seconds resting, inherited from the proven child;
- no normal sell/rescue/chase; residual inventory is held to Gemini settlement;
- no automatic restart;
- dual-key arming required before any venue/account call in this wrapper.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
import re
import time
from typing import Any, Mapping

import httpx

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_local_budget as local_base
from src import gemini_sports_l5_inventory_local_budget as sports_budget

LIVE_ENV = "GEMINI_SPORTS_L5_LIVE_Q1"
PROBE_ID_ENV = "GEMINI_SPORTS_L5_PROBE_ID"
PROBE_ARM_ENV = "GEMINI_SPORTS_L5_PROBE_ARM"

ONE = D("1")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
MAX_EXPIRY_S = 6 * 60 * 60
MLB_MONEYLINE_RE = re.compile(
    r"^(?:GEMI-)?MLB-\d{10}-[A-Z]{2,4}-[A-Z]{2,4}-M$", re.I
)


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_SPORTS_L5_Q1 " + json.dumps({
            "stage": stage,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            **fields,
        }, separators=(",", ":"), default=str),
        flush=True,
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    pid = os.getenv(PROBE_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and pid and os.getenv(PROBE_ARM_ENV, "").strip() == pid)


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "sports"


def _q1(contract: Mapping[str, Any]) -> bool:
    try:
        qmin = D(str(contract.get("quantityMinimum") or "1"))
        qinc = D(str(contract.get("quantityIncrement") or "1"))
    except Exception:
        return False
    return qmin <= ONE and qinc > 0 and ONE / qinc == (ONE / qinc).to_integral_value()


def _sports_pair_model(original):
    def model(bid: D, ask: D) -> dict[str, D]:
        out = dict(original(D(str(bid)), D(str(ask))))
        y = D(str(out["yes_quote"])); n = D(str(out["no_quote"]))
        if not (MIN_PRICE <= y <= MAX_PRICE and MIN_PRICE <= n <= MAX_PRICE):
            out["modeled_edge"] = D("-1")
        return out
    return model


async def discover_sports_moneyline() -> base.Event | None:
    """Choose a liquid, short-dated MLB moneyline contract that already passes
    the live child's fresh q1/band/edge shape.  Public-data only."""
    now_ms = int(time.time() * 1000)
    candidates: list[tuple[float, int, base.Event]] = []
    async with httpx.AsyncClient(
        timeout=8.0, follow_redirects=True,
        headers={"User-Agent": "arb-gemini-sports-l5-q1/1.0"},
    ) as http:
        offset = 0
        while offset < 5000:
            response = await http.get(
                f"{base.REST}/v1/prediction-markets/events",
                params=[("status", "active"), ("category", "Sports"),
                        ("limit", "500"), ("offset", str(offset))],
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                if not MLB_MONEYLINE_RE.fullmatch(ticker):
                    continue
                active = [
                    c for c in (row.get("contracts") or [])
                    if isinstance(c, Mapping)
                    and str(c.get("marketState") or c.get("status") or "").lower()
                    in {"open", "active"}
                ]
                # MLB moneyline event must expose exactly the two team outcomes.
                if len(active) != 2:
                    continue
                event_vol = float(row.get("volume24h") or 0.0)
                for contract in active:
                    symbol = str(contract.get("instrumentSymbol") or "").strip()
                    if not symbol or not _q1(contract):
                        continue
                    expiry = base._iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                    if expiry is None:
                        continue
                    tte = (expiry - now_ms) / 1000.0
                    if tte < inv.MIN_SECONDS_TO_EXPIRY or tte > MAX_EXPIRY_S:
                        continue
                    try:
                        qmin = D(str(contract.get("quantityMinimum") or "1"))
                        qinc = D(str(contract.get("quantityIncrement") or "1"))
                        pinc = D(str(contract.get("priceIncrement") or "0.01"))
                    except Exception:
                        continue
                    candidates.append((event_vol, expiry, base.Event(
                        ticker=ticker, symbol=symbol, expiry_ms=expiry,
                        quantity_minimum=qmin, quantity_increment=qinc,
                        price_increment=pinc,
                    )))
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total = int((pagination or {}).get("total") or len(rows))
            offset += len(rows)
            if not rows or offset >= total:
                break

    # Highest recent event activity first, then shortest capital tie-up.
    candidates.sort(key=lambda row: (-row[0], row[1], row[2].symbol))
    patched = _sports_pair_model(base.pair_model)
    for _, _, event in candidates[:12]:
        try:
            bid, _, ask, _ = await base.fresh_top(event.symbol, timeout=2.5)
            model = patched(bid, ask)
        except Exception:
            continue
        if model["modeled_edge"] >= inv.ENTRY_PAIR_EDGE:
            emit(
                "CANDIDATE_SELECTED", event_ticker=event.ticker,
                symbol=event.symbol, seconds_to_expiry=round(event.seconds_to_expiry, 1),
                yes_bid=str(bid), yes_ask=str(ask),
                yes_quote=str(model["yes_quote"]), no_quote=str(model["no_quote"]),
                modeled_pair_edge=str(model["modeled_edge"]),
                market_family="MLB_MONEYLINE", order_entry=False,
            )
            return event
    return None


def _sports_trade_id(event_ticker: str, canary_id: str) -> str:
    return f"sports-gem-l5q1-{_clean(canary_id, 16)}"[:64]


async def run() -> None:
    # No venue/account calls before this wrapper's dual-key arm.
    if not live_armed():
        emit(
            "DISARMED", q_per_outcome=1, max_campaigns=1,
            target="MLB_MONEYLINE_LT6H", order_entry=False,
            realized_pnl=None, classification="DISARMED_NO_VENUE_CALLS",
        )
        return
    if local_base.local_open_state() is not None:
        raise RuntimeError("Sports q1 start blocked by unresolved local Gemini reservation")

    pid = _clean(os.environ[PROBE_ID_ENV])
    original_discover = base.discover_btc5m
    original_pair_model = base.pair_model
    original_budget = inv.budget
    original_trade_id = inv.inventory_trade_id
    original_floor = inv.ENTRY_PAIR_EDGE
    child_env_names = (inv.LIVE_ENV, inv.CANARY_ID_ENV, inv.CANARY_ARM_ENV)
    saved_env = {name: os.environ.get(name) for name in child_env_names}

    emit(
        "START", probe_id=pid, q_per_outcome=1, max_campaigns=1,
        target="MLB_MONEYLINE_LT6H", price_band="0.20-0.80",
        min_fee_adjusted_pair_edge=str(original_floor),
        maker_rewards_counted_as_realized=False,
        classification="LIVE_MECHANICS_PROBE_NOT_PROVEN_ECONOMIC_EDGE",
        order_entry=True,
    )
    try:
        base.discover_btc5m = discover_sports_moneyline
        base.pair_model = _sports_pair_model(original_pair_model)
        inv.budget = sports_budget
        inv.inventory_trade_id = _sports_trade_id
        inv.ENTRY_PAIR_EDGE = D("0.01")
        child_id = f"sportq1-{pid}"[:36]
        os.environ[inv.LIVE_ENV] = "YES"
        os.environ[inv.CANARY_ID_ENV] = child_id
        os.environ[inv.CANARY_ARM_ENV] = child_id
        await inv.run()
    finally:
        base.discover_btc5m = original_discover
        base.pair_model = original_pair_model
        inv.budget = original_budget
        inv.inventory_trade_id = original_trade_id
        inv.ENTRY_PAIR_EDGE = original_floor
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    local = local_base.local_open_state()
    if local is None:
        emit(
            "COMPLETE_NO_OPEN_RESERVATION", probe_id=pid,
            order_entry=False, realized_pnl=None,
            classification="LIVE_PROBE_ENDED_WITHOUT_UNRESOLVED_INVENTORY",
        )
        return
    if str(local.get("strategy") or "") != sports_budget.STRATEGY:
        raise RuntimeError("unexpected local reservation after Sports q1 child")
    emit(
        "INVENTORY_PENDING_AUTHORITATIVE_SETTLEMENT",
        probe_id=pid, trade_id=local.get("trade_id"),
        symbol=local.get("symbol"), event_ticker=local.get("event_ticker"),
        yes_fill=local.get("yes_fill"), no_fill=local.get("no_fill"),
        order_entry=False, realized_pnl=None,
        classification="LIVE_FILL_NOT_REALIZED_UNTIL_GEMINI_SETTLEMENT",
    )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
