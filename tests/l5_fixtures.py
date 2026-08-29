"""Synthetic Polymarket API records shaped like the real public responses.

Used by every L5 test. Nothing here talks to the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"


def ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())


BTC5M_START = ts("2026-08-29T20:00:00Z")
BTC5M_END = ts("2026-08-29T20:05:00Z")
ETH15M_START = ts("2026-08-29T21:00:00Z")
ETH15M_END = ts("2026-08-29T21:15:00Z")


def market(
    condition_id: str,
    slug: str,
    question: str,
    start: int | None,
    end: int | None,
    outcomes=("Up", "Down"),
    token_ids=("tok_up", "tok_down"),
    event_slug: str = "",
    event_title: str = "",
    market_id: str = "1",
    closed: bool = True,
) -> dict:
    record: dict = {
        "id": market_id,
        "conditionId": condition_id,
        "slug": slug,
        "question": question,
        "outcomes": json.dumps(list(outcomes)),
        "clobTokenIds": json.dumps(list(token_ids)),
        "closed": closed,
        "events": [{"slug": event_slug or slug, "title": event_title or question}],
    }
    if start is not None:
        record["gameStartTime"] = datetime.fromtimestamp(start, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    if end is not None:
        record["endDate"] = datetime.fromtimestamp(end, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return record


BTC_5M_MARKET = market(
    "0xbtc5m01",
    "bitcoin-up-or-down-5m-2026-08-29-2000",
    "Bitcoin Up or Down - 5 minute - Aug 29 8:00PM ET",
    BTC5M_START,
    BTC5M_END,
    market_id="5001",
)

ETH_15M_MARKET = market(
    "0xeth15m01",
    "ethereum-up-or-down-15m-2026-08-29-2100",
    "Ethereum Up or Down - 15 minute - Aug 29 9:00PM ET",
    ETH15M_START,
    ETH15M_END,
    market_id="15001",
)

# Same asset and slug family as BTC_5M_MARKET but a different window: proves
# grouping keys off the condition ID, not the title.
BTC_5M_MARKET_LATER = market(
    "0xbtc5m02",
    "bitcoin-up-or-down-5m-2026-08-29-2005",
    "Bitcoin Up or Down - 5 minute - Aug 29 8:05PM ET",
    BTC5M_END,
    BTC5M_END + 300,
    market_id="5002",
)

# Out of scope: hourly BTC market.
BTC_HOURLY_MARKET = market(
    "0xbtc1h01",
    "bitcoin-up-or-down-august-29-8pm-et",
    "Bitcoin Up or Down - Aug 29 8PM ET",
    BTC5M_START,
    BTC5M_START + 3600,
    market_id="9001",
)

# Out of scope: right window length, wrong asset.
SOL_5M_MARKET = market(
    "0xsol5m01",
    "solana-up-or-down-5m-2026-08-29-2000",
    "Solana Up or Down - 5 minute",
    BTC5M_START,
    BTC5M_END,
    market_id="7001",
)

# Timestamps say 5m, the title says 15m -- metadata must win, conflict flagged.
CONFLICT_MARKET = market(
    "0xbtc5m03",
    "bitcoin-up-or-down-15m-mislabelled",
    "Bitcoin Up or Down - 15 minute",
    BTC5M_START,
    BTC5M_END,
    market_id="5003",
)

# No usable start field -- forces the text fallback.
NO_START_MARKET = market(
    "0xbtc5m04",
    "bitcoin-up-or-down-5m-no-start",
    "Bitcoin Up or Down - 5 minute",
    None,
    BTC5M_END,
    market_id="5004",
)

# Yes/No outcome labels instead of Up/Down.
YESNO_MARKET = market(
    "0xbtc5m05",
    "bitcoin-up-or-down-5m-yesno",
    "Will Bitcoin be up? - 5 minute",
    BTC5M_START,
    BTC5M_END,
    outcomes=("Yes", "No"),
    token_ids=("tok_yes", "tok_no"),
    market_id="5005",
)

ALL_MARKETS = [
    BTC_5M_MARKET,
    ETH_15M_MARKET,
    BTC_5M_MARKET_LATER,
    BTC_HOURLY_MARKET,
    SOL_5M_MARKET,
    CONFLICT_MARKET,
    NO_START_MARKET,
    YESNO_MARKET,
]


def trade(
    condition_id: str,
    outcome: str,
    side: str,
    price: float,
    size: float,
    timestamp: int,
    tx: str,
    token: str | None = None,
    outcome_index: int | None = None,
    usdc: float | None = None,
    activity_type: str | None = "TRADE",
    slug: str = "",
) -> dict:
    record: dict = {
        "proxyWallet": WALLET,
        "conditionId": condition_id,
        "outcome": outcome,
        "side": side,
        "price": price,
        "size": size,
        "timestamp": timestamp,
        "transactionHash": tx,
        "asset": token if token is not None else ("tok_up" if outcome == "Up" else "tok_down"),
        "slug": slug,
    }
    if activity_type is not None:
        record["type"] = activity_type
    if outcome_index is not None:
        record["outcomeIndex"] = outcome_index
    if usdc is not None:
        record["usdcSize"] = usdc
    return record


def position(
    condition_id: str,
    token: str,
    cash: float,
    size: float = 0.0,
    outcome_index: int = 0,
    settled: bool = True,
) -> dict:
    """A /positions row with the semantics the real endpoint uses: cashPnl
    carries the settlement result, redeemable marks a settled side, and a
    live row instead carries curPrice."""
    record = {
        "proxyWallet": WALLET,
        "conditionId": condition_id,
        "asset": token,
        "size": size,
        "outcomeIndex": outcome_index,
        "realizedPnl": 0.0,
        "cashPnl": cash,
        "redeemable": settled,
    }
    if not settled:
        record["curPrice"] = 0.5
    return record


def simple_window_activity() -> list[dict]:
    """One tidy BTC 5m window: open cheap Up, cross to Down, top up Up."""
    return [
        trade("0xbtc5m01", "Up", "BUY", 0.42, 100, BTC5M_START + 30, "0xa1", usdc=42.0),
        trade("0xbtc5m01", "Down", "BUY", 0.55, 100, BTC5M_START + 45, "0xa2", usdc=55.0),
        trade("0xbtc5m01", "Up", "BUY", 0.72, 200, BTC5M_START + 200, "0xa3", usdc=144.0),
        trade("0xbtc5m01", "Down", "BUY", 0.91, 200, BTC5M_START + 250, "0xa4", usdc=182.0),
    ]
