"""Fail-closed q=1 Gemini Prediction Markets canary transport.

This module is PREPARED but intentionally DISARMED by default.  It contains
only bounded one-order-at-a-time primitives for a future L5 live probe after
shadow evidence clears the predeclared gate.  It must not be imported by the
shadow collector execution path.

Safety invariants:
* q == 1 contract only
* buy-only prediction-market orders
* maker-or-cancel required
* one active market at a time
* max residual dollars and daily realized-loss stops
* minimum fee-adjusted pair edge gate
* BTC 5m / 15m symbols only for this first canary
* exact explicit arm token required
* no credentials are stored in source
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any, Callable, Mapping, Optional

API = "https://api.gemini.com"
ORDER_PATH = "/v1/prediction-markets/order"
CANCEL_PATH = "/v1/prediction-markets/order/cancel"
ACTIVE_PATH = "/v1/prediction-markets/orders/active"
TERMS_STATUS_PATH = "/v1/prediction-markets/terms/status"
ARM_TOKEN = "YES_I_APPROVE_LIVE_Q1"


class CanaryBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class CanaryConfig:
    armed: bool = False
    quantity: D = D("1")
    min_edge_after_fees: D = D("0.01")
    max_residual_usd: D = D("2.00")
    daily_realized_loss_limit_usd: D = D("2.00")

    @classmethod
    def from_env(cls) -> "CanaryConfig":
        return cls(
            armed=os.getenv("GEMINI_L5_CANARY_ARMED", "") == ARM_TOKEN,
            quantity=D(os.getenv("GEMINI_L5_CANARY_QTY", "1")),
            min_edge_after_fees=D(os.getenv("GEMINI_L5_CANARY_MIN_EDGE", "0.01")),
            max_residual_usd=D(os.getenv("GEMINI_L5_CANARY_MAX_RESIDUAL_USD", "2.00")),
            daily_realized_loss_limit_usd=D(os.getenv("GEMINI_L5_CANARY_DAILY_LOSS_USD", "2.00")),
        )


def _is_short_btc_symbol(symbol: str) -> bool:
    s = str(symbol or "")
    return s.startswith("GEMI-BTC05M") or s.startswith("GEMI-BTC15M")


def validate_probe(*, cfg: CanaryConfig, symbol: str, quantity: D, side: str,
                   outcome: str, price: D, edge_after_fees: D,
                   active_market_count: int, residual_usd: D,
                   daily_realized_pnl_usd: D) -> None:
    if not cfg.armed:
        raise CanaryBlocked("canary_disarmed")
    if cfg.quantity != D("1") or quantity != D("1"):
        raise CanaryBlocked("q1_only")
    if not _is_short_btc_symbol(symbol):
        raise CanaryBlocked("symbol_not_btc_5m_or_15m")
    if side != "buy":
        raise CanaryBlocked("buy_only")
    if outcome not in {"yes", "no"}:
        raise CanaryBlocked("bad_outcome")
    if price <= 0 or price >= 1:
        raise CanaryBlocked("bad_price")
    if edge_after_fees < cfg.min_edge_after_fees:
        raise CanaryBlocked("edge_below_floor")
    if active_market_count != 0:
        raise CanaryBlocked("one_market_at_a_time")
    if residual_usd + price * quantity > cfg.max_residual_usd:
        raise CanaryBlocked("residual_cap")
    if daily_realized_pnl_usd <= -cfg.daily_realized_loss_limit_usd:
        raise CanaryBlocked("daily_realized_loss_stop")


def build_maker_order(*, symbol: str, outcome: str, price: D,
                      quantity: D = D("1")) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "orderType": "limit",
        "side": "buy",
        "quantity": str(quantity),
        "price": str(price),
        "outcome": outcome,
        "timeInForce": "good-til-cancel",
        "makerOrCancel": True,
    }


def signed_headers(path: str, logical_payload: Mapping[str, Any], *,
                   api_key: str, api_secret: str, nonce: Optional[int] = None) -> tuple[dict[str, str], bytes]:
    """Return Gemini private-REST headers and an EMPTY HTTP body.

    Gemini private REST signs the logical JSON request in X-GEMINI-PAYLOAD.
    """
    if not api_key or not api_secret:
        raise CanaryBlocked("missing_credentials")
    n = int(time.time() * 1000) if nonce is None else int(nonce)
    payload = {"request": path, "nonce": n, **dict(logical_payload)}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.b64encode(raw)
    sig = hmac.new(api_secret.encode("utf-8"), encoded, hashlib.sha384).hexdigest()
    headers = {
        "X-GEMINI-APIKEY": api_key,
        "X-GEMINI-PAYLOAD": encoded.decode("ascii"),
        "X-GEMINI-SIGNATURE": sig,
        "Content-Type": "text/plain",
        "Content-Length": "0",
        "Cache-Control": "no-cache",
    }
    return headers, b""


def require_terms_accepted(status: Mapping[str, Any]) -> None:
    if status.get("hasAcceptedLatest") is not True:
        raise CanaryBlocked("prediction_market_terms_not_current")


def submit_one_probe(*, cfg: CanaryConfig, symbol: str, outcome: str, price: D,
                     edge_after_fees: D, active_market_count: int,
                     residual_usd: D, daily_realized_pnl_usd: D,
                     terms_status: Mapping[str, Any], api_key: str, api_secret: str,
                     post: Callable[[str, Mapping[str, str], bytes], Mapping[str, Any]]) -> Mapping[str, Any]:
    """Submit exactly one maker-only q=1 canary after every hard gate passes.

    `post` is injected so tests cannot accidentally reach the network.
    """
    validate_probe(
        cfg=cfg, symbol=symbol, quantity=D("1"), side="buy", outcome=outcome,
        price=price, edge_after_fees=edge_after_fees,
        active_market_count=active_market_count, residual_usd=residual_usd,
        daily_realized_pnl_usd=daily_realized_pnl_usd,
    )
    require_terms_accepted(terms_status)
    order = build_maker_order(symbol=symbol, outcome=outcome, price=price)
    headers, body = signed_headers(ORDER_PATH, order, api_key=api_key, api_secret=api_secret)
    return post(API + ORDER_PATH, headers, body)
