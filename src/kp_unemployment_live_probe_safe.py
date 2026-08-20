"""Safety wrapper for the isolated August U3 K/P q=1 LIVE_PROBE.

Before the live-probe state machine starts, this wrapper:
* re-fetches the exact three venue contracts,
* derives current taker fee coefficients from live market metadata,
* verifies at least $2 available cash/buying power on both venues, and
* fails closed on unknown fee schemas or unavailable balances.
It creates no arm state and places no orders itself.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src.auth import KalshiClient
from src.kp_unemployment_certification import APPROVALS, validate_kalshi_market, validate_poly_market
from src.kp_unemployment_live_probe import KALSHI_BASE, POLY_BASE, run as run_probe

D = Decimal
MIN_READY_CASH = D("2.00")
logger = logging.getLogger("kalshi_arb.kp_unemployment_live_probe_safe")


def _d(value: Any) -> D:
    try:
        out = D(str(value))
        return out if out.is_finite() else D("0")
    except Exception:
        return D("0")


def _poly_available(body: Any) -> D:
    candidates: list[Any] = []
    if isinstance(body, dict):
        candidates.extend((body.get("available"), body.get("availableBalance"), body.get("buyingPower")))
        rows = body.get("balances") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("currency") or "USD").upper() in {"USD", "USDC"}:
                    candidates.extend((row.get("available"), row.get("availableBalance"), row.get("balance")))
    return max((_d(x) for x in candidates if x is not None), default=D("0"))


async def _fee_preflight() -> tuple[D, D]:
    poly_rates: list[D] = []
    kalshi_rates: list[D] = []
    async with httpx.AsyncClient(timeout=12.0) as http:
        for approval in APPROVALS:
            p_resp, k_resp = await asyncio.gather(
                http.get(f"{POLY_BASE}/v1/market/slug/{approval.polymarket_slug}"),
                http.get(f"{KALSHI_BASE}/markets/{approval.kalshi_ticker}"),
            )
            p_resp.raise_for_status(); k_resp.raise_for_status()
            p_market = (p_resp.json().get("market") or {})
            k_market = (k_resp.json().get("market") or {})
            validate_poly_market(approval, p_market)
            validate_kalshi_market(approval, k_market)

            p_rate = _d(p_market.get("feeCoefficient"))
            if not D("0") <= p_rate <= D("0.25"):
                raise PermissionError(f"unknown Polymarket feeCoefficient for {approval.polymarket_slug}")
            poly_rates.append(p_rate)

            fee_type = str(k_market.get("fee_type") or "quadratic").strip().lower()
            if fee_type not in {"quadratic", "quadratic_with_maker_fees"}:
                raise PermissionError(f"unknown Kalshi fee_type {fee_type!r}")
            multiplier_raw = k_market.get("fee_multiplier")
            multiplier = D("1") if multiplier_raw in (None, "") else _d(multiplier_raw)
            if multiplier <= 0 or multiplier > D("10"):
                raise PermissionError("invalid Kalshi fee multiplier")
            kalshi_rates.append(D("0.07") * multiplier)

    # Use the highest current coefficient across the three contracts. This is
    # conservative if a venue differentiates fees by threshold.
    return max(poly_rates), max(kalshi_rates)


async def _cash_preflight() -> dict[str, Any]:
    pclient = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=8.0,
    )
    kclient = KalshiClient(base_url=KALSHI_BASE)
    try:
        p_body, k_resp = await asyncio.gather(
            asyncio.to_thread(pclient.account.balances),
            kclient.get("/portfolio/balance"),
        )
        k_resp.raise_for_status()
        p_available = _poly_available(p_body)
        k_available = _d(k_resp.json().get("balance")) / D("100")
        if p_available < MIN_READY_CASH or k_available < MIN_READY_CASH:
            raise PermissionError("insufficient available cash for bounded q=1 recovery reserve")
        return {
            "polymarket_minimum_cash_ready": True,
            "kalshi_minimum_cash_ready": True,
            "minimum_ready_cash": str(MIN_READY_CASH),
        }
    finally:
        try: pclient.close()
        except Exception: pass
        await kclient.close()


async def run() -> int:
    poly_rate, kalshi_rate = await _fee_preflight()
    cash = await _cash_preflight()
    os.environ["POLYMARKET_TAKER_FEE_RATE"] = str(poly_rate)
    os.environ["KALSHI_TAKER_FEE_RATE"] = str(kalshi_rate)
    logger.info("KP_U3_SAFETY_PREFLIGHT %s", json.dumps({
        "mode": "LIVE_PROBE",
        "poly_taker_fee_coefficient": str(poly_rate),
        "kalshi_taker_fee_coefficient": str(kalshi_rate),
        **cash,
        "order_entry": False,
    }, separators=(",", ":")))
    try:
        return await run_probe()
    except asyncio.TimeoutError:
        # No market update arrived before the short-lived arm expired. The base
        # service uses /tmp arm state and Railway restartPolicy=NEVER, so exit cleanly.
        logger.info("KP_U3_ARM_EXPIRED_NO_TRADE timeout=true order_entry=false")
        return 0


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
