"""Read-only shadow recorder for two high-performing public Polymarket wallets.

Purpose
-------
This module does NOT place, modify, or cancel orders. It uses Polymarket's
public Data API to observe public wallet activity and the public CLOB book to
measure whether a newly observed trade would still be executable by us after
public-feed/polling delay.

The two lanes are intentionally kept separate:

* swisstony_sports: sports/value-selection behavior.
* l5_crypto: short-duration BTC/ETH microstructure behavior.

Shadow/model observations are never REALIZED P&L. This service only produces
research evidence for later pass/fail gates.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
import time
from typing import Any, Iterable

import httpx

DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


@dataclass(frozen=True)
class Trader:
    key: str
    handle: str
    wallet: str
    lane: str


TRADERS: dict[str, Trader] = {
    "swisstony": Trader(
        key="swisstony",
        handle="swisstony",
        wallet="0x204f72f35326db932158cba6adff0b9a1da95e14",
        lane="swisstony_sports",
    ),
    "l5": Trader(
        key="l5",
        handle="l5Zn1bWoM8eTsK",
        wallet="0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68",
        lane="l5_crypto",
    ),
}

CRYPTO_RE = re.compile(r"\b(bitcoin|btc|ethereum|eth)\b", re.I)
WINDOW_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}\s*(?:AM|PM))\s*-\s*(?P<end>\d{1,2}:\d{2}\s*(?:AM|PM))\s*ET",
    re.I,
)


def _as_float(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def activity_id(row: dict[str, Any]) -> str:
    """Stable-enough public observation id without assuming tx hash uniqueness."""
    parts = [
        row.get("transactionHash"),
        row.get("timestamp"),
        row.get("conditionId"),
        row.get("asset"),
        row.get("side"),
        row.get("outcome"),
        row.get("price"),
        row.get("size"),
    ]
    return "|".join(str(x or "") for x in parts)


def parse_window_minutes(title: str) -> int | None:
    """Parse titles like '... 12:30AM-12:45AM ET' into a duration."""
    match = WINDOW_RE.search(title or "")
    if not match:
        return None
    start = datetime.strptime(match.group("start").replace(" ", "").upper(), "%I:%M%p")
    end = datetime.strptime(match.group("end").replace(" ", "").upper(), "%I:%M%p")
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    if end_min <= start_min:
        end_min += 24 * 60
    duration = end_min - start_min
    return duration if 0 < duration <= 24 * 60 else None


def classify_market(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "")
    slug = str(row.get("slug") or "")
    joined = f"{title} {slug}".lower()

    if CRYPTO_RE.search(joined) and "up or down" in joined:
        minutes = parse_window_minutes(title)
        if minutes is not None:
            return f"crypto_updown_{minutes}m"
        return "crypto_updown"
    if "exact score" in joined:
        return "sports_exact_score"
    if "o/u" in joined or "over/under" in joined:
        return "sports_total"
    if "spread:" in joined or "spread " in joined:
        return "sports_spread"
    if re.search(r"\bwill .+ win\b", joined):
        return "sports_moneyline_binary"
    if any(x in joined for x in (" vs. ", " vs ", " atp", " wta", " mlb", " nfl", " nba", " nhl")):
        return "sports_other"
    if CRYPTO_RE.search(joined):
        return "crypto_other"
    return "other"


def fetch_activity(
    http: httpx.Client,
    trader: Trader,
    *,
    limit: int = 500,
    offset: int = 0,
    start: int | None = None,
    end: int | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "user": trader.wallet,
        "type": "TRADE",
        "limit": max(1, min(int(limit), 500)),
        "offset": max(0, int(offset)),
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC",
    }
    if start is not None:
        params["start"] = int(start)
    if end is not None:
        params["end"] = int(end)
    response = http.get(f"{DATA_API}/activity", params=params)
    response.raise_for_status()
    payload = response.json()
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def fetch_activity_window(
    http: httpx.Client,
    trader: Trader,
    *,
    start: int,
    end: int,
    max_split_depth: int = 12,
) -> list[dict[str, Any]]:
    """Backfill a bounded time window without silently truncating at offset 10k.

    Polymarket's public activity endpoint caps offsets at 10,000. If a window
    fills the entire 10k visible range, recursively split the time window and
    dedupe the resulting rows.
    """

    def _read(lo: int, hi: int, depth: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_size = 500
        for offset in range(0, 10_000, page_size):
            page = fetch_activity(http, trader, limit=page_size, offset=offset, start=lo, end=hi)
            rows.extend(page)
            if len(page) < page_size:
                return rows
        if depth >= max_split_depth or hi - lo <= 1:
            raise RuntimeError(
                f"activity window still saturated after {len(rows)} rows: {lo}-{hi}; refusing silent truncation"
            )
        mid = (lo + hi) // 2
        return _read(lo, mid, depth + 1) + _read(mid + 1, hi, depth + 1)

    deduped: dict[str, dict[str, Any]] = {}
    for row in _read(int(start), int(end), 0):
        deduped[activity_id(row)] = row
    return sorted(deduped.values(), key=lambda r: int(r.get("timestamp") or 0), reverse=True)


def _best_level(levels: Iterable[dict[str, Any]], *, side: str) -> tuple[float | None, float | None]:
    parsed: list[tuple[float, float]] = []
    for row in levels:
        if not isinstance(row, dict):
            continue
        price = _as_float(row.get("price"))
        size = _as_float(row.get("size"))
        if price is not None and size is not None:
            parsed.append((price, size))
    if not parsed:
        return None, None
    return (max(parsed) if side == "bid" else min(parsed),)[0]


def best_book_levels(book: dict[str, Any]) -> dict[str, float | None]:
    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    for raw, out in ((book.get("bids") or [], bids), (book.get("asks") or [], asks)):
        for row in raw:
            if not isinstance(row, dict):
                continue
            p = _as_float(row.get("price"))
            q = _as_float(row.get("size"))
            if p is not None and q is not None:
                out.append((p, q))
    best_bid = max(bids, default=(None, None), key=lambda x: x[0])
    best_ask = min(asks, default=(None, None), key=lambda x: x[0])
    return {
        "best_bid": best_bid[0],
        "best_bid_size": best_bid[1],
        "best_ask": best_ask[0],
        "best_ask_size": best_ask[1],
    }


def fetch_book(http: httpx.Client, token_id: str) -> dict[str, Any]:
    response = http.get(f"{CLOB_API}/book", params={"token_id": token_id})
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def copyability_observation(
    row: dict[str, Any],
    book: dict[str, Any],
    *,
    observed_at: float | None = None,
) -> dict[str, Any]:
    levels = best_book_levels(book)
    side = str(row.get("side") or "").upper()
    target_price = _as_float(row.get("price"))
    target_size = _as_float(row.get("size"))
    trade_ts = _as_float(row.get("timestamp"))
    now = float(observed_at if observed_at is not None else time.time())

    executable_price: float | None
    executable_size: float | None
    if side == "BUY":
        executable_price = levels["best_ask"]
        executable_size = levels["best_ask_size"]
        slippage = None if target_price is None or executable_price is None else executable_price - target_price
        still_at_or_better = slippage is not None and slippage <= 0
    elif side == "SELL":
        executable_price = levels["best_bid"]
        executable_size = levels["best_bid_size"]
        slippage = None if target_price is None or executable_price is None else target_price - executable_price
        still_at_or_better = slippage is not None and slippage <= 0
    else:
        executable_price = None
        executable_size = None
        slippage = None
        still_at_or_better = False

    return {
        "trade_id": activity_id(row),
        "timestamp": row.get("timestamp"),
        "condition_id": row.get("conditionId"),
        "asset": row.get("asset"),
        "title": row.get("title"),
        "outcome": row.get("outcome"),
        "side": side,
        "target_price": target_price,
        "target_size": target_size,
        "executable_price_now": executable_price,
        "best_level_size_now": executable_size,
        "slippage_cost_per_share": slippage,
        "still_executable_at_or_better": still_at_or_better,
        "detected_age_seconds": None if trade_ts is None else max(0.0, now - trade_ts),
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def summarize(trader: Trader, rows: list[dict[str, Any]]) -> dict[str, Any]:
    families = Counter(classify_market(row) for row in rows)
    sides = Counter(str(row.get("side") or "UNKNOWN").upper() for row in rows)
    outcomes = Counter(str(row.get("outcome") or "UNKNOWN") for row in rows)
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[str(row.get("conditionId") or "UNKNOWN")].append(row)

    buy_entries = [row for row in rows if str(row.get("side") or "").upper() == "BUY"]
    buy_prices = [p for p in (_as_float(row.get("price")) for row in buy_entries) if p is not None]
    buys_per_market = [
        sum(1 for row in market_rows if str(row.get("side") or "").upper() == "BUY")
        for market_rows in by_market.values()
    ]
    two_sided_buys = 0
    for market_rows in by_market.values():
        bought_outcomes = {
            str(row.get("outcome") or "")
            for row in market_rows
            if str(row.get("side") or "").upper() == "BUY"
        }
        if len(bought_outcomes) >= 2:
            two_sided_buys += 1

    no_buys = sum(
        1
        for row in buy_entries
        if str(row.get("outcome") or "").strip().lower() == "no"
    )
    short_crypto = sum(
        1
        for row in rows
        if classify_market(row) in {"crypto_updown_5m", "crypto_updown_15m"}
    )

    avg_price = sum(buy_prices) / len(buy_prices) if buy_prices else None
    avg_buys_per_market = sum(buys_per_market) / len(buys_per_market) if buys_per_market else 0.0
    timestamps = [int(row.get("timestamp") or 0) for row in rows if row.get("timestamp") is not None]

    return {
        "type": "wallet_shadow_summary",
        "trader": trader.handle,
        "wallet": trader.wallet,
        "lane": trader.lane,
        "read_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
        "trade_observations": len(rows),
        "unique_markets": len(by_market),
        "side_counts": dict(sides),
        "market_family_counts": dict(families),
        "top_outcomes": outcomes.most_common(12),
        "buy_count": len(buy_entries),
        "buy_avg_price": avg_price,
        "no_share_of_buys": (no_buys / len(buy_entries)) if buy_entries else None,
        "avg_buy_entries_per_market": avg_buys_per_market,
        "two_sided_buy_markets": two_sided_buys,
        "short_crypto_trade_observations": short_crypto,
        "newest_trade_ts": max(timestamps) if timestamps else None,
        "oldest_trade_ts": min(timestamps) if timestamps else None,
    }


def probe_books(
    http: httpx.Client,
    rows: list[dict[str, Any]],
    *,
    max_probes: int,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for row in rows[: max(0, int(max_probes))]:
        token_id = str(row.get("asset") or "").strip()
        if not token_id:
            continue
        try:
            book = fetch_book(http, token_id)
            observations.append(copyability_observation(row, book))
        except httpx.HTTPError as exc:
            observations.append({
                "trade_id": activity_id(row),
                "asset": token_id,
                "title": row.get("title"),
                "type": "copyability_error",
                "error": type(exc).__name__,
                "shadow_only": True,
                "order_entry": False,
                "realized_pnl": 0.0,
            })
    return observations


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def run_once(http: httpx.Client, trader: Trader, *, limit: int, book_probes: int, history_hours: float) -> None:
    if history_hours > 0:
        end = int(time.time())
        start = end - int(history_hours * 3600)
        rows = fetch_activity_window(http, trader, start=start, end=end)
    else:
        rows = fetch_activity(http, trader, limit=limit)
    _emit(summarize(trader, rows))
    for obs in probe_books(http, rows, max_probes=book_probes):
        obs["type"] = obs.get("type", "copyability_observation")
        obs["trader"] = trader.handle
        obs["lane"] = trader.lane
        _emit(obs)


def run_live(
    http: httpx.Client,
    traders: list[Trader],
    *,
    poll_seconds: float,
    limit: int,
    book_probes: int,
    summary_every: float,
) -> None:
    seen: dict[str, set[str]] = {trader.key: set() for trader in traders}
    last_summary = 0.0
    first_pass = True

    while True:
        for trader in traders:
            try:
                rows = fetch_activity(http, trader, limit=limit)
            except httpx.HTTPError as exc:
                _emit({
                    "type": "wallet_shadow_error",
                    "trader": trader.handle,
                    "lane": trader.lane,
                    "error": type(exc).__name__,
                    "read_only": True,
                    "order_entry": False,
                })
                continue

            ids = {activity_id(row) for row in rows}
            if first_pass:
                new_rows = rows[: max(0, int(book_probes))]
            else:
                new_rows = [row for row in rows if activity_id(row) not in seen[trader.key]]
            seen[trader.key].update(ids)

            if new_rows:
                for obs in probe_books(http, new_rows, max_probes=max(book_probes, len(new_rows))):
                    obs["type"] = obs.get("type", "copyability_observation")
                    obs["trader"] = trader.handle
                    obs["lane"] = trader.lane
                    _emit(obs)

            now = time.time()
            if first_pass or now - last_summary >= summary_every:
                _emit(summarize(trader, rows))

        first_pass = False
        if time.time() - last_summary >= summary_every:
            last_summary = time.time()
        time.sleep(max(1.0, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Polymarket whale strategy shadow")
    parser.add_argument("--trader", action="append", choices=sorted(TRADERS), default=[])
    parser.add_argument("--once", action="store_true", help="Run one snapshot and exit")
    parser.add_argument("--limit", type=int, default=500, help="Recent activity rows per poll (max 500)")
    parser.add_argument("--book-probes", type=int, default=12, help="Recent/new trades to compare with live CLOB")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--summary-every", type=float, default=60.0)
    parser.add_argument("--history-hours", type=float, default=0.0, help="Backfill bounded history in --once mode")
    args = parser.parse_args()

    selected = [TRADERS[key] for key in (args.trader or ["swisstony", "l5"])]
    with httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": "arb-wallet-shadow/1.0"}) as http:
        if args.once:
            for trader in selected:
                run_once(
                    http,
                    trader,
                    limit=max(1, min(args.limit, 500)),
                    book_probes=max(0, args.book_probes),
                    history_hours=max(0.0, args.history_hours),
                )
        else:
            run_live(
                http,
                selected,
                poll_seconds=args.poll_seconds,
                limit=max(1, min(args.limit, 500)),
                book_probes=max(0, args.book_probes),
                summary_every=max(10.0, args.summary_every),
            )


if __name__ == "__main__":
    main()
