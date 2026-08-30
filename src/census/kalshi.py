"""Kalshi flow census: public market data -> venue-flow census report.

Read-only. Uses only Kalshi's public trade-api endpoints (no auth headers, no
credentials read, nothing that can place or cancel orders):

    GET /trade-api/v2/markets            ?series_ticker=&status=&limit=&cursor=
    GET /trade-api/v2/markets/trades     ?ticker=&limit=&cursor=

Every raw response is cached verbatim to JSONL before any parsing, so the
census can be recomputed offline and the parsing audited against the bytes
actually served.

Side mapping (see core.py): Kalshi reports the TAKER side per trade. The
passive two-sided accumulator acquires the opposite: taker_side=yes means a
maker's (economic) NO bid was filled -> maker acquires DOWN; taker_side=no ->
maker acquires UP.

Field names are extracted tolerantly with fallbacks, and any trade this
module cannot map is counted and reported, never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable

from src.census.core import DOWN, UP, TapeTrade, aggregate, census_window

BASE = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "kalshi-arb-research/flow-census (read-only)"
DEFAULT_SERIES = ("KXBTC15M", "KXETH15M", "KXBTC", "KXETH")
PAGE_LIMIT = 1000


# --- HTTP (GET only) ---------------------------------------------------------


def _get(path: str, params: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{BASE}{path}?{query}" if query else f"{BASE}{path}"
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    last: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(2**attempt)
    raise RuntimeError(f"GET {url} failed after retries: {last}")


class RawWriter:
    def __init__(self, directory: str) -> None:
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def append(self, name: str, endpoint: str, params: dict, payload: Any) -> None:
        with open(os.path.join(self.directory, name), "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "endpoint": endpoint,
                        "params": params,
                        "fetched_at": int(time.time()),
                        "payload": payload,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )


# --- tolerant field extraction ----------------------------------------------


def _ts(value: Any) -> int | None:
    if isinstance(value, (int, float)) and value > 0:
        return int(value if value < 1e11 else value / 1000)
    if isinstance(value, str) and value:
        try:
            return int(
                datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            if value.isdigit():
                return _ts(int(value))
    return None


def _price_dollars(record: dict, *keys: str) -> float | None:
    """Kalshi prices arrive as integer cents (yes_price) or dollar strings
    (yes_price_dollars / *_fp). Normalize to 0..1 dollars."""
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if key.endswith(("_dollars", "_fp")) or number <= 1.0:
            return number
        return number / 100.0
    return None


def parse_trade(record: dict) -> TapeTrade | None:
    ts = _ts(record.get("created_time") or record.get("created_ts") or record.get("ts"))
    taker = str(record.get("taker_side") or "").lower()
    count = record.get("count")
    if count is None:
        count = record.get("count_fp") or record.get("contracts")
    try:
        count = float(count)
    except (TypeError, ValueError):
        return None
    yes = _price_dollars(record, "yes_price_dollars", "yes_price_fp", "yes_price")
    no = _price_dollars(record, "no_price_dollars", "no_price_fp", "no_price")
    if yes is None and no is not None:
        yes = 1.0 - no
    if no is None and yes is not None:
        no = 1.0 - yes
    if ts is None or count <= 0 or yes is None or no is None or taker not in ("yes", "no"):
        return None
    maker_side = DOWN if taker == "yes" else UP
    return TapeTrade(ts=ts, maker_side=maker_side, count=count, up_price=yes, down_price=no)


# --- fetch -------------------------------------------------------------------


def fetch_markets(
    raw: RawWriter, series: str, statuses: Iterable[str], max_pages: int = 20
) -> list[dict]:
    markets: list[dict] = []
    for status in statuses:
        cursor = None
        for _ in range(max_pages):
            params = {
                "series_ticker": series,
                "status": status,
                "limit": 200,
                "cursor": cursor,
            }
            payload = _get("/markets", params)
            raw.append("raw_markets.jsonl", "/markets", params, payload)
            page = payload.get("markets") or []
            markets.extend(page)
            cursor = payload.get("cursor")
            if not cursor or not page:
                break
    return markets


def fetch_trades(raw: RawWriter, ticker: str, max_pages: int = 30) -> list[dict]:
    trades: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {"ticker": ticker, "limit": PAGE_LIMIT, "cursor": cursor}
        payload = _get("/markets/trades", params)
        raw.append("raw_trades.jsonl", "/markets/trades", params, payload)
        page = payload.get("trades") or []
        trades.extend(page)
        cursor = payload.get("cursor")
        if not cursor or not page:
            break
    return trades


# --- census ------------------------------------------------------------------


def run_census(
    out_dir: str,
    series: Iterable[str] = DEFAULT_SERIES,
    statuses: Iterable[str] = ("settled", "closed", "open"),
    since_hours: float | None = 48.0,
    max_markets_per_series: int = 200,
) -> dict[str, Any]:
    raw = RawWriter(out_dir)
    cutoff = time.time() - since_hours * 3600 if since_hours else None

    windows = []
    unparsed_trades = 0
    series_found: dict[str, int] = {}
    for series_ticker in series:
        try:
            markets = fetch_markets(raw, series_ticker, statuses)
        except Exception as exc:  # noqa: BLE001 - a missing series is a finding
            series_found[series_ticker] = -1
            print(f"[census] series {series_ticker}: {exc}", file=sys.stderr)
            continue
        series_found[series_ticker] = len(markets)
        seen: set[str] = set()
        kept = 0
        for market in markets:
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            close_ts = _ts(market.get("close_time") or market.get("close_ts"))
            open_ts = _ts(market.get("open_time") or market.get("open_ts"))
            if cutoff and close_ts and close_ts < cutoff:
                continue
            if kept >= max_markets_per_series:
                break
            kept += 1
            trade_records = fetch_trades(raw, ticker)
            tape = []
            for record in trade_records:
                trade = parse_trade(record)
                if trade is None:
                    unparsed_trades += 1
                else:
                    tape.append(trade)
            windows.append(
                census_window(
                    window_id=ticker,
                    label=f"{series_ticker}:{market.get('title') or ticker}",
                    tape=tape,
                    open_ts=open_ts,
                    close_ts=close_ts,
                )
            )

    report = {
        "venue": "kalshi",
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now(tz=timezone.utc).isoformat(),
        "read_only": True,
        "series_market_counts": series_found,
        "since_hours": since_hours,
        "unparsed_trades": unparsed_trades,
        "aggregate": aggregate(windows),
        "windows": [w.as_dict() for w in windows],
    }
    with open(os.path.join(out_dir, "census_kalshi.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return report


def print_summary(report: dict) -> None:
    agg = report["aggregate"]
    print(f"Kalshi flow census — generated {report['generated_at_iso']}")
    print(f"series found: {report['series_market_counts']}")
    print(
        f"windows with trades: {agg['windows_with_trades']}/{agg['windows_seen']}   "
        f"trades: {agg['total_trades']}   contracts: {agg['total_contracts']}"
    )
    print(
        f"trades/window: {agg['mean_trades_per_traded_window']}   "
        f"both-sided: {agg['windows_both_sides']}/{agg['windows_with_trades']}   "
        f"alternation: {agg['mean_alternation_rate']}   "
        f"median clip: {agg['clip_median_overall']}"
    )
    print(f"{'config':16s} {'matched':>9s} {'residual':>9s} {'ratio':>7s} "
          f"{'gross$':>8s} {'EV@6c':>8s} {'EV@12c':>8s} {'EV@24c':>8s}")
    for name, c in agg["by_config"].items():
        ev = c["adjusted_ev_usd"]
        ratio = c["matched_to_residual"]
        print(f"{name:16s} {c['matched']:>9.1f} {c['residual']:>9.1f} "
              f"{(f'{ratio:.2f}' if ratio is not None else 'inf'):>7s} "
              f"{c['gross_locked_usd']:>8.2f} {ev['lambda_6c']:>8.2f} "
              f"{ev['lambda_12c']:>8.2f} {ev['lambda_24c']:>8.2f}")
    print("\nBenchmarks: L5@polymarket realized 4.38:1; Gemini first tape ceiling 0.29:1.")
    print("Gate: ratio >= ~1:1 at q=10 with a meaningful $/day gross, else no-go for accumulation.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.census.kalshi",
        description="Read-only Kalshi venue-flow census (public market data only).",
    )
    parser.add_argument("--series", nargs="+", default=list(DEFAULT_SERIES))
    parser.add_argument("--hours", type=float, default=48.0,
                        help="only markets closing within the last N hours (0 = no filter)")
    parser.add_argument("--out-dir", default="data/census/kalshi")
    parser.add_argument("--max-markets", type=int, default=200)
    args = parser.parse_args(argv)
    report = run_census(
        args.out_dir,
        series=args.series,
        since_hours=args.hours or None,
        max_markets_per_series=args.max_markets,
    )
    print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
