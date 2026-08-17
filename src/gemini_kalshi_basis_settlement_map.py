"""Persistent read-only settlement-basis map for Gemini <-> Kalshi 15m crypto.

Gemini and Kalshi use different official settlement indexes.  This module measures
that difference *after settlement* using the venues' published settlement values and
stores one durable row per matched 15-minute window.  It is research infrastructure,
not an order authorization layer.

Ground-truth rows are append-only.  A compact JSON summary is atomically replaced on
each refresh so a restart can resume from the existing /data journal without losing
history.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import statistics
import tempfile
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import ASSET_ALIASES, _gemini_meta
from src.gemini_kalshi_settlement_agreement import (
    _gemini_outcome,
    _gemini_settled,
    _kalshi_outcome,
)
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced
from src.gemini_kalshi_window_identity import kalshi_15m_asset, kalshi_window_end

D = Decimal
CLASSIFICATION = "BASIS_RESEARCH_ONLY"


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def kalshi_expiration_value(market: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    """Return Kalshi's published settlement-index value and the field used.

    ``expiration_value`` is the canonical field for current Kalshi market payloads.
    The aliases are accepted only to keep historical captures readable; unknown or
    malformed values fail closed as ``None``.
    """
    for field in (
        "expiration_value",
        "expiration_value_dollars",
        "settlement_value",
        "settlement_value_dollars",
    ):
        value = _dec(market.get(field))
        if value is not None:
            return value, field
    return None, None


def _basis_bps(kalshi_value: Decimal, gemini_value: Decimal) -> Decimal | None:
    if gemini_value == 0:
        return None
    return (kalshi_value - gemini_value) / gemini_value * D("10000")


def _window_key(asset: str, expiry: datetime, gemini_ticker: str, kalshi_ticker: str) -> str:
    return "|".join((asset, expiry.isoformat(), gemini_ticker, kalshi_ticker))


@dataclass(frozen=True)
class BasisRow:
    key: str
    asset: str
    expiry_utc: str
    gemini_ticker: str
    kalshi_ticker: str
    gemini_outcome: str
    kalshi_outcome: str
    outcomes_agree: bool
    gemini_strike: str | None
    gemini_settlement_value: str
    kalshi_expiration_value: str
    kalshi_value_field: str
    signed_basis: str
    signed_basis_bps: str | None
    abs_basis_bps: str | None
    classification: str = CLASSIFICATION
    read_only: bool = True
    trade_authorized: bool = False
    order_entry: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def match_settlement_values(
    gemini_events: list[dict[str, Any]],
    kalshi_markets: list[dict[str, Any]],
    *,
    max_window_skew_s: float = 30.0,
) -> list[BasisRow]:
    """Match true 15m window endpoints and retain only rows with both index values."""
    gemini_rows: list[tuple[dict[str, Any], str, datetime, str, Decimal | None, Decimal]] = []
    for event in gemini_events:
        meta = _gemini_meta(event)
        if meta is None:
            continue
        asset, duration, expiry = meta
        if duration != 15 or asset not in ASSET_ALIASES:
            continue
        outcome, strike, settlement, _margin_bps = _gemini_outcome(event)
        settlement_d = _dec(settlement)
        if outcome is None or settlement_d is None:
            continue
        gemini_rows.append((event, asset, expiry, outcome, _dec(strike), settlement_d))

    kalshi_rows: list[tuple[dict[str, Any], str, datetime, str, Decimal, str]] = []
    for market in kalshi_markets:
        asset = kalshi_15m_asset(market)
        expiry = kalshi_window_end(market)
        outcome = _kalshi_outcome(market)
        value, value_field = kalshi_expiration_value(market)
        if asset and expiry and outcome and value is not None and value_field is not None:
            kalshi_rows.append((market, asset, expiry, outcome, value, value_field))

    out: list[BasisRow] = []
    used_kalshi: set[str] = set()
    for event, asset, g_expiry, g_outcome, g_strike, g_settlement in gemini_rows:
        candidates: list[tuple[float, dict[str, Any], str, Decimal, str]] = []
        for market, k_asset, k_expiry, k_outcome, k_value, k_field in kalshi_rows:
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker in used_kalshi or k_asset != asset:
                continue
            skew = abs((g_expiry - k_expiry).total_seconds())
            if skew <= max_window_skew_s:
                candidates.append((skew, market, k_outcome, k_value, k_field))
        if not candidates:
            continue
        candidates.sort(key=lambda row: row[0])
        _skew, market, k_outcome, k_value, k_field = candidates[0]
        kalshi_ticker = str(market.get("ticker") or "")
        used_kalshi.add(kalshi_ticker)
        gemini_ticker = str(event.get("ticker") or "")
        signed = k_value - g_settlement
        bps = _basis_bps(k_value, g_settlement)
        out.append(BasisRow(
            key=_window_key(asset, g_expiry, gemini_ticker, kalshi_ticker),
            asset=asset,
            expiry_utc=g_expiry.isoformat(),
            gemini_ticker=gemini_ticker,
            kalshi_ticker=kalshi_ticker,
            gemini_outcome=g_outcome,
            kalshi_outcome=k_outcome,
            outcomes_agree=(g_outcome == k_outcome),
            gemini_strike=str(g_strike) if g_strike is not None else None,
            gemini_settlement_value=str(g_settlement),
            kalshi_expiration_value=str(k_value),
            kalshi_value_field=k_field,
            signed_basis=str(signed),
            signed_basis_bps=str(bps) if bps is not None else None,
            abs_basis_bps=str(abs(bps)) if bps is not None else None,
        ))
    out.sort(key=lambda row: (row.expiry_utc, row.asset))
    return out


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("key"):
                rows.append(row)
    return rows


def append_new_rows(path: Path, rows: list[BasisRow]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {str(row.get("key")) for row in load_rows(path)}
    fresh = [row for row in rows if row.key not in existing]
    if not fresh:
        return 0
    with path.open("a", encoding="utf-8") as fh:
        for row in fresh:
            fh.write(json.dumps(row.as_dict(), separators=(",", ":")) + "\n")
        fh.flush()
    return len(fresh)


def _percentile(values: list[Decimal], q: float) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * q))
    return ordered[max(0, min(len(ordered) - 1, idx))]


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bps = [_dec(row.get("signed_basis_bps")) for row in rows]
    bps = [value for value in bps if value is not None]
    abs_bps = [abs(value) for value in bps]
    disagreements = sum(1 for row in rows if row.get("outcomes_agree") is False)
    positive = [value for value in bps if value > 0]
    negative = [value for value in bps if value < 0]
    median_abs = statistics.median(abs_bps) if abs_bps else None
    return {
        "windows": len(rows),
        "outcome_disagreements": disagreements,
        "outcome_disagreement_rate": disagreements / len(rows) if rows else None,
        "median_abs_basis_bps": str(median_abs) if median_abs is not None else None,
        "p95_abs_basis_bps": str(_percentile(abs_bps, 0.95)) if abs_bps else None,
        "p99_abs_basis_bps": str(_percentile(abs_bps, 0.99)) if abs_bps else None,
        "max_abs_basis_bps": str(max(abs_bps)) if abs_bps else None,
        "max_positive_basis_bps": str(max(positive)) if positive else None,
        "max_negative_basis_bps": str(min(negative)) if negative else None,
        "classification": CLASSIFICATION,
        "read_only": True,
        "trade_authorized": False,
    }


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_asset: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_asset.setdefault(str(row.get("asset") or "UNKNOWN"), []).append(row)
    return {
        "overall": _stats(rows),
        "by_asset": {asset: _stats(bucket) for asset, bucket in sorted(by_asset.items())},
        "classification": CLASSIFICATION,
        "basis_definition": "KALSHI_EXPIRATION_VALUE_MINUS_GEMINI_SETTLEMENT_VALUE",
        "read_only": True,
        "trade_authorized": False,
        "order_entry": False,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, separators=(",", ":"))
        tmp.write("\n")
        tmp.flush()
        temp_path = Path(tmp.name)
    temp_path.replace(path)


async def refresh(
    *,
    journal_path: Path,
    summary_path: Path,
    hours: int,
    gemini_limit: int,
    max_window_skew_s: float = 30.0,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )
    matched = match_settlement_values(gemini, kalshi, max_window_skew_s=max_window_skew_s)
    appended = append_new_rows(journal_path, matched)
    all_rows = load_rows(journal_path)
    summary = build_summary(all_rows)
    summary.update({
        "refresh_matched_windows": len(matched),
        "refresh_new_windows": appended,
        "history_hours_requested": hours,
        "gemini_limit": gemini_limit,
    })
    atomic_write_json(summary_path, summary)
    return summary


async def run_forever(
    *,
    journal_path: Path,
    summary_path: Path,
    refresh_seconds: float,
    initial_hours: int,
    refresh_hours: int,
    gemini_limit: int,
) -> None:
    first = True
    while True:
        hours = initial_hours if first else refresh_hours
        first = False
        try:
            summary = await refresh(
                journal_path=journal_path,
                summary_path=summary_path,
                hours=hours,
                gemini_limit=gemini_limit,
            )
            print("CRYPTO_BASIS_SETTLEMENT_SUMMARY " + json.dumps(summary, separators=(",", ":")), flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                "CRYPTO_BASIS_SETTLEMENT_REFRESH_ERROR "
                + json.dumps({
                    "type": type(exc).__name__,
                    "error": str(exc)[:300],
                    "classification": CLASSIFICATION,
                    "read_only": True,
                    "trade_authorized": False,
                }, separators=(",", ":")),
                flush=True,
            )
        await asyncio.sleep(max(60.0, refresh_seconds))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal-path", type=Path, default=Path("/data/crypto-basis-settlements.jsonl"))
    parser.add_argument("--summary-path", type=Path, default=Path("/data/crypto-basis-summary.json"))
    parser.add_argument("--refresh-seconds", type=float, default=300.0)
    parser.add_argument("--initial-hours", type=int, default=168)
    parser.add_argument("--refresh-hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=5000)
    args = parser.parse_args()
    asyncio.run(run_forever(
        journal_path=args.journal_path,
        summary_path=args.summary_path,
        refresh_seconds=args.refresh_seconds,
        initial_hours=args.initial_hours,
        refresh_hours=args.refresh_hours,
        gemini_limit=args.gemini_limit,
    ))


if __name__ == "__main__":
    main()
