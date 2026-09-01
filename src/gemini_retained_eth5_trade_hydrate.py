"""Hydrate a retained Gemini public tape with missing ETH5 public trades.

This is a read-only market-data utility. It loads a retained JSONL capture, finds
ETH 5-minute SHADOW_PLAN markets, fetches each instrument's public Gemini trade
history, normalizes the REST aggressor side into the same ``buyer_maker`` field
used by the WebSocket collector, and writes a NEW chronologically merged JSONL.

It never loads credentials, accesses balances, submits orders, cancels orders, or
mutates the source tape.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterable

import httpx

REST = "https://api.gemini.com"
TARGET_ASSET = "ETH"
TARGET_HORIZON = "5m"


def _parse_iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def _row_time_ms(row: dict[str, Any]) -> int | None:
    value = row.get("observed_at_utc") or row.get("observed_at")
    parsed = _parse_iso_ms(value)
    if parsed is not None:
        return parsed
    raw = row.get("timestampms")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _is_target_plan(row: dict[str, Any]) -> bool:
    return (
        row.get("type") == "SHADOW_PLAN"
        and str(row.get("asset") or "").upper() == TARGET_ASSET
        and str(row.get("horizon") or "").lower() == TARGET_HORIZON
        and bool(str(row.get("ticker") or ""))
        and bool(str(row.get("symbol") or ""))
    )


def target_markets(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_target_plan(row):
            continue
        ticker = str(row["ticker"])
        ts = _row_time_ms(row)
        item = out.setdefault(
            ticker,
            {
                "ticker": ticker,
                "symbol": str(row["symbol"]),
                "first_plan_ms": ts,
                "last_plan_ms": ts,
                "plan_rows": 0,
            },
        )
        item["plan_rows"] += 1
        if ts is not None:
            item["first_plan_ms"] = ts if item["first_plan_ms"] is None else min(item["first_plan_ms"], ts)
            item["last_plan_ms"] = ts if item["last_plan_ms"] is None else max(item["last_plan_ms"], ts)
    return out


def normalize_public_trade(
    *,
    market: dict[str, Any],
    trade: dict[str, Any],
) -> dict[str, Any] | None:
    """Normalize REST trade history to the retained WebSocket PUBLIC_TRADE shape.

    Gemini REST ``type`` is the aggressor: ``sell`` removes the resting bid, so
    the buyer was maker; ``buy`` removes the resting ask, so the seller was maker.
    """
    side = str(trade.get("type") or "").lower()
    if side not in {"buy", "sell"}:
        return None
    try:
        ts = int(trade["timestampms"])
        price = D(str(trade["price"]))
        qty = D(str(trade["amount"]))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    if ts <= 0 or price <= 0 or qty <= 0:
        return None
    return {
        "type": "PUBLIC_TRADE",
        "observed_at": _iso_from_ms(ts),
        "observed_at_utc": _iso_from_ms(ts),
        "timestampms": ts,
        "ticker": market["ticker"],
        "symbol": market["symbol"],
        "asset": TARGET_ASSET,
        "horizon": TARGET_HORIZON,
        "price": str(price),
        "quantity": str(qty),
        "buyer_maker": side == "sell",
        "trade_id": trade.get("tid"),
        "rest_aggressor_side": side,
        "source": "GEMINI_PUBLIC_REST_TRADE_HISTORY",
        "read_only": True,
        "order_entry": False,
        "credentials_loaded": False,
    }


def hydrate_rows(
    rows: list[dict[str, Any]],
    histories: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    markets = target_markets(rows)
    timed = [_row_time_ms(row) for row in rows]
    timed_values = [value for value in timed if value is not None]
    capture_start_ms = min(timed_values) if timed_values else None
    capture_end_ms = max(timed_values) if timed_values else None

    existing_ids: set[tuple[str, str]] = set()
    for row in rows:
        if row.get("type") != "PUBLIC_TRADE":
            continue
        ticker = str(row.get("ticker") or "")
        trade_id = row.get("trade_id")
        if ticker and trade_id is not None:
            existing_ids.add((ticker, str(trade_id)))

    added: list[dict[str, Any]] = []
    fetched_rows = normalized_rows = pre_quote_filtered = post_capture_filtered = duplicates = 0
    per_market: dict[str, dict[str, int]] = {}

    for ticker, market in markets.items():
        history = histories.get(ticker, [])
        fetched_rows += len(history)
        counts = {"fetched": len(history), "added": 0, "pre_quote_filtered": 0, "post_capture_filtered": 0, "duplicates": 0}
        first_plan_ms = market.get("first_plan_ms")
        for trade in history:
            normalized = normalize_public_trade(market=market, trade=trade)
            if normalized is None:
                continue
            normalized_rows += 1
            ts = int(normalized["timestampms"])
            if first_plan_ms is not None and ts < int(first_plan_ms):
                pre_quote_filtered += 1
                counts["pre_quote_filtered"] += 1
                continue
            if capture_end_ms is not None and ts > capture_end_ms:
                post_capture_filtered += 1
                counts["post_capture_filtered"] += 1
                continue
            key = (ticker, str(normalized.get("trade_id")))
            if normalized.get("trade_id") is not None and key in existing_ids:
                duplicates += 1
                counts["duplicates"] += 1
                continue
            if normalized.get("trade_id") is not None:
                existing_ids.add(key)
            added.append(normalized)
            counts["added"] += 1
        per_market[ticker] = counts

    # Preserve untimed source rows in original relative order. Timed rows and
    # hydrated trades are merged by event timestamp with source rows winning ties,
    # which prevents a same-ms backfill from being credited before its quote exists.
    merged_items: list[tuple[int, int, int, dict[str, Any]]] = []
    untimed_base = capture_end_ms + 1 if capture_end_ms is not None else 0
    for index, row in enumerate(rows):
        ts = _row_time_ms(row)
        merged_items.append((ts if ts is not None else untimed_base, 0, index, row))
    for index, row in enumerate(added):
        merged_items.append((int(row["timestampms"]), 1, index, row))
    merged_items.sort(key=lambda item: (item[0], item[1], item[2]))
    merged = [item[3] for item in merged_items]

    summary = {
        "type": "GEMINI_RETAINED_ETH5_TRADE_HYDRATE_V1",
        "classification": "READ_ONLY_PUBLIC_MARKET_DATA_HYDRATION",
        "order_entry": False,
        "credentials_loaded": False,
        "source_rows": len(rows),
        "target_markets": len(markets),
        "target_plan_rows": sum(int(m["plan_rows"]) for m in markets.values()),
        "history_rows_fetched": fetched_rows,
        "history_rows_normalized": normalized_rows,
        "history_rows_added": len(added),
        "history_rows_pre_quote_filtered": pre_quote_filtered,
        "history_rows_post_capture_filtered": post_capture_filtered,
        "history_rows_duplicate_filtered": duplicates,
        "hydrated_rows": len(merged),
        "capture_start_ms": capture_start_ms,
        "capture_end_ms": capture_end_ms,
        "markets": per_market,
        "warnings": [
            "Hydrated REST trades reconstruct missing public flow only; they do not reconstruct queue position.",
            "The source retained tape is never mutated.",
            "No authenticated Gemini endpoint is used.",
        ],
    }
    return merged, summary


def fetch_histories(
    markets: dict[str, dict[str, Any]],
    *,
    client: httpx.Client,
    sleep_s: float = 0.03,
    max_retries: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for ticker, market in markets.items():
        url = f"{REST}/v1/trades/{market['symbol']}"
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = client.get(url, params={"limit_trades": 500})
                if response.status_code == 429 and attempt < max_retries:
                    time.sleep(max(0.1, sleep_s) * (2 ** attempt))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise RuntimeError(f"unexpected trade history payload for {ticker}")
                out[ticker] = [row for row in payload if isinstance(row, dict)]
                last_error = None
                break
            except Exception as exc:  # surfaced after bounded retries
                last_error = exc
                if attempt < max_retries:
                    time.sleep(max(0.1, sleep_s) * (2 ** attempt))
                    continue
        if last_error is not None:
            raise RuntimeError(f"public trade history failed for {ticker}: {type(last_error).__name__}: {last_error}") from last_error
        if sleep_s > 0:
            time.sleep(sleep_s)
    return out


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--sleep-s", type=float, default=0.03)
    args = parser.parse_args()

    rows = load_rows(args.input)
    markets = target_markets(rows)
    with httpx.Client(timeout=12.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-eth5-hydrate/1.0"}) as client:
        histories = fetch_histories(markets, client=client, sleep_s=max(0.0, args.sleep_s))
    hydrated, summary = hydrate_rows(rows, histories)
    write_rows(args.output, hydrated)
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
