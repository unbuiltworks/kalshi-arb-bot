"""Credential-free live shadow for Gemini 5m 30-49c rebate buying.

Uses Gemini's public strike/book/rebate-rate APIs plus Coinbase Exchange public
spot/candles as an *uncalibrated* settlement proxy. It never places orders and
never labels projected rebates or model EV as realized P&L.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any

import httpx

from src.gemini_l5_fair_value import estimate_up_probability
from src.gemini_l5_threshold_shadow import discover, book
from src.gemini_rebate_buyer_30_49 import policy_matrix

GEMINI = "https://api.gemini.com"
COINBASE = "https://api.exchange.coinbase.com"
BASIS_RESERVES_BPS = (D("2"), D("5"), D("10"))


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


async def current_crypto_rebate_rate(http: httpx.AsyncClient) -> tuple[D, dict[str, Any]]:
    r = await http.get(
        f"{GEMINI}/v1/prediction-markets/maker-rebate/rates",
        params={"category": "Crypto"}, headers={"Cache-Control": "no-cache"},
    )
    r.raise_for_status()
    payload = r.json()
    now = datetime.now(timezone.utc)
    eligible = []
    for rule in payload.get("rate_rules", []):
        start = _dt(rule.get("effective_from"))
        end = _dt(rule.get("effective_to"))
        category = rule.get("category")
        if start and start <= now and (end is None or now < end) and category in {None, "Crypto"}:
            eligible.append((start, rule))
    if not eligible:
        raise RuntimeError("no current public Crypto maker-rebate rule")
    _, rule = max(eligible, key=lambda item: item[0])
    return D(str(rule["rebate_multiplier_bps"])) / D("10000"), rule


async def strike(http: httpx.AsyncClient, ticker: str) -> D:
    r = await http.get(
        f"{GEMINI}/v1/prediction-markets/events/{ticker}/strike",
        headers={"Cache-Control": "no-cache"},
    )
    r.raise_for_status()
    value = r.json().get("value")
    if value is None:
        raise RuntimeError("strike not populated")
    return D(str(value))


async def proxy_snapshot(http: httpx.AsyncClient, asset: str) -> tuple[D, D, float]:
    product = f"{asset}-USD"
    ticker_r, candles_r = await asyncio.gather(
        http.get(f"{COINBASE}/products/{product}/ticker", headers={"Cache-Control": "no-cache"}),
        http.get(f"{COINBASE}/products/{product}/candles", params={"granularity": 60}),
    )
    ticker_r.raise_for_status(); candles_r.raise_for_status()
    ticker = ticker_r.json()
    spot = D(str(ticker["price"]))
    stamp = _dt(ticker.get("time"))
    age_s = max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds()) if stamp else 999.0
    candles = candles_r.json()
    ordered = sorted(
        (row for row in candles if isinstance(row, list) and len(row) >= 5),
        key=lambda row: row[0],
    )[-31:]
    closes = [float(row[4]) for row in ordered if float(row[4]) > 0]
    if len(closes) < 10:
        raise RuntimeError("insufficient Coinbase 1m candles for volatility")
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(returns) < 8:
        raise RuntimeError("insufficient log returns for volatility")
    sigma_1m = statistics.pstdev(returns)
    sigma_per_sqrt_second = D(str(max(sigma_1m / math.sqrt(60.0), 1e-9)))
    return spot, sigma_per_sqrt_second, age_s


def _decision_dict(d) -> dict[str, Any]:
    return {
        "policy": d.policy,
        "actionable": d.actionable,
        "side": d.side,
        "quote_price": str(d.quote_price) if d.quote_price is not None else None,
        "fair_probability": str(d.fair_probability) if d.fair_probability is not None else None,
        "quantity": str(d.quantity),
        "maker_fee_usd": str(d.maker_fee_usd),
        "fair_value_edge_before_rebate_usd": str(d.fair_value_edge_before_rebate_usd),
        "projected_rebate_usd_not_realized": str(d.projected_rebate_usd),
        "projected_combined_ev_usd_not_realized": str(d.projected_combined_ev_usd),
        "reason": d.reason,
    }


async def capture(*, seconds: float, poll_s: float, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    counts: dict[str, int] = {}
    samples = errors = 0
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent":"arb-rebate-buyer-shadow/1.0"}) as http:
        rebate_rate, rebate_rule = await current_crypto_rebate_rate(http)
        with output.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                started = time.monotonic()
                try:
                    events = await discover(http)
                except Exception as exc:
                    errors += 1
                    print(json.dumps({"type":"GEMINI_REBATE_BUYER_DISCOVERY_ERROR","error":str(exc)[:240]}), flush=True)
                    await asyncio.sleep(max(0.2, poll_s)); continue
                for event in events.values():
                    if event.horizon != "5m" or event.asset not in {"BTC", "ETH"}:
                        continue
                    if not (20 < event.seconds_to_expiry <= 300):
                        continue
                    try:
                        (yes_bid, _, yes_ask, _), k, (spot, sigma, spot_age_s) = await asyncio.gather(
                            book(http, event.symbol), strike(http, event.ticker), proxy_snapshot(http, event.asset)
                        )
                        sensitivity = {}
                        for reserve in BASIS_RESERVES_BPS:
                            estimate = estimate_up_probability(
                                settlement_proxy_price=spot, strike_price=k,
                                seconds_to_expiry=max(0.0, event.seconds_to_expiry),
                                log_sigma_per_sqrt_second=sigma,
                                settlement_basis_sigma_bps=reserve,
                            )
                            matrix = policy_matrix(
                                estimate=estimate, yes_bid=yes_bid, yes_ask=yes_ask,
                                rebate_rate=rebate_rate,
                            )
                            sensitivity[str(reserve)] = {
                                "probability_up": str(estimate.probability_up),
                                "probability_down": str(estimate.probability_down),
                                "policies": {name: _decision_dict(d) for name, d in matrix.items()},
                            }
                            for name, d in matrix.items():
                                if d.actionable:
                                    key = f"{event.asset}:basis{reserve}:{name}"
                                    counts[key] = counts.get(key, 0) + 1
                        row = {
                            "type": "GEMINI_REBATE_BUYER_30_49_SHADOW",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "asset": event.asset, "ticker": event.ticker, "symbol": event.symbol,
                            "seconds_to_expiry": round(event.seconds_to_expiry, 3),
                            "yes_bid": str(yes_bid), "yes_ask": str(yes_ask),
                            "no_bid": str(D("1") - yes_ask),
                            "strike": str(k), "coinbase_spot_proxy": str(spot),
                            "coinbase_spot_age_s": round(spot_age_s, 3),
                            "realized_log_sigma_per_sqrt_second": str(sigma),
                            "maker_rebate_rate_from_public_api": str(rebate_rate),
                            "maker_rebate_rule": rebate_rule,
                            "basis_reserve_sensitivity_bps": sensitivity,
                            "quantity": "10", "order_entry": False, "credentials_loaded": False,
                            "fair_value_calibration_status": "UNCALIBRATED_COINBASE_PROXY_VS_GEMINI_KAIKO_SETTLEMENT",
                            "pnl_status": "SHADOW_MODEL_NOT_REALIZED",
                            "liquidity_rewards_counted": False,
                        }
                        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n"); fh.flush()
                        print(json.dumps(row, separators=(",", ":"), default=str), flush=True)
                        samples += 1
                    except Exception as exc:
                        errors += 1
                        print(json.dumps({
                            "type":"GEMINI_REBATE_BUYER_SAMPLE_ERROR", "asset":event.asset,
                            "ticker":event.ticker, "error_type":type(exc).__name__, "error":str(exc)[:240],
                            "order_entry":False,
                        }, separators=(",", ":")), flush=True)
                await asyncio.sleep(max(0.05, poll_s - (time.monotonic() - started)))
    summary = {
        "type":"GEMINI_REBATE_BUYER_30_49_SUMMARY", "samples":samples, "errors":errors,
        "actionable_sample_counts":counts, "order_entry":False, "credentials_loaded":False,
        "realized_pnl":None,
        "warning":"Projected rebates and fair-value EV are shadow estimates. Coinbase-vs-Kaiko basis is not calibrated for live use.",
    }
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True), flush=True)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--poll-s", type=float, default=5.0)
    ap.add_argument("--output", type=Path, default=Path("artifacts/gemini-rebate-buyer-30-49.jsonl"))
    args = ap.parse_args()
    asyncio.run(capture(seconds=args.seconds, poll_s=args.poll_s, output=args.output))


if __name__ == "__main__":
    main()
