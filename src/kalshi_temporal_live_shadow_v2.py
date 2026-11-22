"""Read-only Kalshi 15m temporal shadow with metadata-based series certification.

Wraps `kalshi_temporal_live_shadow` but replaces the brittle title check with
venue metadata checks: known series ticker, Crypto category, fifteen_min
frequency, CRYPTO15M terms, quadratic fee, and CF Benchmarks settlement.
No credentials, signing, orders, cancellations, or portfolio access.
"""
from __future__ import annotations

import argparse

import src.kalshi_temporal_live_shadow as base


def fetch_series_certified(http, asset: str) -> base.KalshiSeries:
    asset = asset.upper()
    ticker = base.SERIES_BY_ASSET[asset]
    response = http.get(f"{base.KALSHI_API}/series/{ticker}")
    response.raise_for_status()
    payload = response.json()
    row = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(row, dict):
        raise ValueError(f"unexpected series payload for {ticker}")

    if str(row.get("ticker") or "").upper() != ticker:
        raise ValueError(f"series ticker mismatch for {ticker}")
    if str(row.get("category") or "").lower() != "crypto":
        raise ValueError(f"series {ticker} is not Crypto")
    if str(row.get("frequency") or "").lower() != "fifteen_min":
        raise ValueError(f"series {ticker} is not fifteen_min")

    fee_type = str(row.get("fee_type") or "")
    if not fee_type.startswith("quadratic"):
        raise ValueError(f"unsupported fee type for {ticker}: {fee_type}")

    terms = str(row.get("contract_terms_url") or "")
    certification = str(row.get("contract_url") or "")
    if "CRYPTO15M" not in (terms + " " + certification).upper():
        raise ValueError(f"series {ticker} missing CRYPTO15M contract metadata")

    sources = []
    for source in row.get("settlement_sources") or []:
        if isinstance(source, dict):
            sources.append((str(source.get("name") or ""), str(source.get("url") or "")))
    if not any("cf benchmarks" in name.lower() for name, _ in sources):
        raise ValueError(f"series {ticker} missing CF Benchmarks settlement source")

    return base.KalshiSeries(
        asset=asset,
        ticker=ticker,
        fee_type=fee_type,
        fee_multiplier=float(row.get("fee_multiplier") or 1.0),
        settlement_sources=tuple(sources),
        title=str(row.get("title") or ""),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Kalshi certified 15m crypto temporal shadow")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--discovery-seconds", type=float, default=15.0)
    parser.add_argument("--summary-seconds", type=float, default=10.0)
    parser.add_argument("--min-net-edge", type=float, default=0.03)
    parser.add_argument("--first-leg-max-price", type=float, default=0.45)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    base.fetch_series = fetch_series_certified
    base.run(
        poll_seconds=args.poll_seconds,
        discovery_seconds=args.discovery_seconds,
        summary_seconds=args.summary_seconds,
        min_net_edge=args.min_net_edge,
        first_leg_max_price=args.first_leg_max_price,
        once=args.once,
    )


if __name__ == "__main__":
    main()
