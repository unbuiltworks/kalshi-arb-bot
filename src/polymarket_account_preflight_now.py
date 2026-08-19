"""One-shot read-only Polymarket account readiness check.

Uses only the authenticated account balance endpoint. No market/order mutation calls,
no arm token, and no order-entry path.
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
from typing import Any

from polymarket_us import PolymarketUS

D = Decimal
MIN_READY_CASH = D("2.00")


def _d(value: Any) -> Decimal:
    try:
        out = D(str(value))
        return out if out.is_finite() else D("0")
    except Exception:
        return D("0")


def _available(body: Any) -> Decimal:
    candidates: list[Any] = []
    if isinstance(body, dict):
        candidates.extend([body.get("available"), body.get("availableBalance"), body.get("buyingPower")])
        balances = body.get("balances") or []
        if isinstance(balances, list):
            for row in balances:
                if not isinstance(row, dict):
                    continue
                currency = str(row.get("currency") or "USD").upper()
                if currency in {"USD", "USDC"}:
                    candidates.extend([row.get("available"), row.get("availableBalance"), row.get("balance")])
    return max((_d(v) for v in candidates if v is not None), default=D("0"))


def main() -> None:
    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=5.0,
    )
    try:
        body = client.account.balances()
        available = _available(body)
        print(json.dumps({
            "status": "POLYMARKET_ACCOUNT_PREFLIGHT_NOW",
            "reachable": True,
            "minimum_ready_cash": str(MIN_READY_CASH),
            "minimum_cash_ready": available >= MIN_READY_CASH,
            "available_usd_equivalent": str(available),
            "read_only": True,
            "order_entry": False,
            "arm_token_created": False,
        }, separators=(",", ":")), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
