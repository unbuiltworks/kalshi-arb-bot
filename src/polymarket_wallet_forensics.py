"""Public-data wallet forensics for Polymarket reference traders.

No credentials, signing, order entry, cancellation, or mutation endpoints.
The adapter only reads the public Data API and normalizes fills for offline
research. It supports multiple reference wallets so mechanisms are not mixed.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

import httpx

from src.polymarket_temporal_arb_shadow import Fill, MarketInventory

DATA_API = "https://data-api.polymarket.com"

REFERENCE_WALLETS = {
    "l5zn1bwom8etsk": {
        "address": "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68",
        "lane": "short_crypto_temporal_pairing",
    },
    "swisstony": {
        "address": "0x204f72f35326db932158cba6adff0b9a1da95e14",
        "lane": "sports_forensics",
    },
}

_CRYPTO_ASSET_RE = re.compile(r"\b(bitcoin|btc|ethereum|eth)\b", re.IGNORECASE)
_WINDOW_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})(?P<ampm1>AM|PM)?\s*-\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2})(?P<ampm2>AM|PM)\s*ET",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WalletTrade:
    proxy_wallet: str
    side: str
    condition_id: str
    size: float
    price: float
    timestamp_s: int
    title: str
    slug: str
    event_slug: str
    outcome: str
    outcome_index: int | None
    transaction_hash: str

    @property
    def notional_usd(self) -> float:
        return self.size * self.price


def _clean_address(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"0x[a-f0-9]{40}", value):
        raise ValueError("wallet must be a 0x-prefixed 40-hex address")
    return value


def resolve_wallet(value: str) -> tuple[str, str | None]:
    key = value.strip().lower().lstrip("@")
    if key in REFERENCE_WALLETS:
        return REFERENCE_WALLETS[key]["address"], key
    return _clean_address(value), None


def normalize_trade(row: dict) -> WalletTrade:
    return WalletTrade(
        proxy_wallet=_clean_address(str(row.get("proxyWallet") or "")),
        side=str(row.get("side") or "").upper(),
        condition_id=str(row.get("conditionId") or ""),
        size=float(row.get("size") or 0.0),
        price=float(row.get("price") or 0.0),
        timestamp_s=int(row.get("timestamp") or 0),
        title=str(row.get("title") or ""),
        slug=str(row.get("slug") or ""),
        event_slug=str(row.get("eventSlug") or ""),
        outcome=str(row.get("outcome") or ""),
        outcome_index=(
            int(row["outcomeIndex"])
            if row.get("outcomeIndex") is not None
            else None
        ),
        transaction_hash=str(row.get("transactionHash") or ""),
    )


def fetch_public_trades(
    wallet: str,
    *,
    limit_per_page: int = 1000,
    max_trades: int = 10_000,
    timeout_s: float = 20.0,
    client: httpx.Client | None = None,
) -> list[WalletTrade]:
    """Fetch public trades for a wallet, including maker and taker activity."""
    wallet = _clean_address(wallet)
    if not (1 <= limit_per_page <= 10_000):
        raise ValueError("limit_per_page must be between 1 and 10000")
    if max_trades <= 0:
        raise ValueError("max_trades must be positive")

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout_s)
    rows: list[WalletTrade] = []
    offset = 0
    try:
        while len(rows) < max_trades and offset <= 10_000:
            page_limit = min(limit_per_page, max_trades - len(rows), 10_000)
            response = http.get(
                f"{DATA_API}/trades",
                params={
                    "user": wallet,
                    "limit": page_limit,
                    "offset": offset,
                    # API default is true; forensics needs maker + taker activity.
                    "takerOnly": "false",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("unexpected Polymarket trades payload")
            if not payload:
                break

            rows.extend(normalize_trade(row) for row in payload)
            if len(payload) < page_limit:
                break
            offset += len(payload)
            # Public Data API offset is capped at 10k. Do not silently claim
            # completeness beyond that boundary.
            if offset > 10_000:
                break
    finally:
        if owns_client:
            http.close()

    return rows[:max_trades]


def _asset_from_title(title: str) -> str | None:
    match = _CRYPTO_ASSET_RE.search(title)
    if not match:
        return None
    token = match.group(1).lower()
    return "BTC" if token in {"bitcoin", "btc"} else "ETH"


def _to_minutes(hour: int, minute: int, ampm: str) -> int:
    hour %= 12
    if ampm.upper() == "PM":
        hour += 12
    return hour * 60 + minute


def infer_window_minutes(title: str) -> int | None:
    """Infer 5m/15m horizon from titles such as '12:30AM-12:45AM ET'."""
    match = _WINDOW_RE.search(title.replace("–", "-"))
    if not match:
        return None

    ampm2 = match.group("ampm2").upper()
    ampm1 = (match.group("ampm1") or ampm2).upper()
    start = _to_minutes(int(match.group("h1")), int(match.group("m1")), ampm1)
    end = _to_minutes(int(match.group("h2")), int(match.group("m2")), ampm2)
    duration = (end - start) % (24 * 60)
    return duration or None


def classify_trade(trade: WalletTrade) -> dict[str, str | int | None]:
    title = trade.title
    asset = _asset_from_title(title)
    horizon = infer_window_minutes(title)
    outcome = trade.outcome.strip().upper()

    is_up_down = outcome in {"UP", "DOWN"} and "up or down" in title.lower()
    if asset and is_up_down and horizon in {5, 15}:
        family = "SHORT_CRYPTO_UP_DOWN"
    elif asset and is_up_down:
        family = "CRYPTO_UP_DOWN_OTHER"
    else:
        family = "OTHER"

    return {
        "family": family,
        "asset": asset,
        "horizon_minutes": horizon,
    }


def reconstruct_short_crypto(
    trades: Iterable[WalletTrade],
    *,
    min_locked_edge_per_pair: float = 0.03,
) -> dict:
    """Reconstruct BUY inventory for BTC/ETH 5m/15m Up/Down markets.

    SELL-bearing markets are reported separately and excluded from locked-edge
    attribution because the simple inventory engine intentionally has no
    realized-sale accounting yet.
    """
    by_market: dict[str, list[WalletTrade]] = defaultdict(list)
    ignored = Counter()

    for trade in trades:
        cls = classify_trade(trade)
        if cls["family"] != "SHORT_CRYPTO_UP_DOWN":
            ignored["non_short_crypto"] += 1
            continue
        by_market[trade.condition_id].append(trade)

    market_rows = []
    valid_locked_profit = 0.0
    valid_matched_qty = 0.0

    for condition_id, market_trades in by_market.items():
        market_trades = sorted(
            market_trades,
            key=lambda t: (t.timestamp_s, t.transaction_hash, t.outcome),
        )
        sell_count = sum(t.side == "SELL" for t in market_trades)
        sample = market_trades[0]
        cls = classify_trade(sample)

        inv = MarketInventory(
            condition_id,
            min_locked_edge_per_pair=min_locked_edge_per_pair,
        )
        for trade in market_trades:
            if trade.side != "BUY":
                continue
            outcome = trade.outcome.strip().upper()
            if outcome not in {"UP", "DOWN"}:
                continue
            inv.add_fill(
                Fill(
                    side=outcome,
                    price=trade.price,
                    quantity=trade.size,
                    # Public Data API does not expose a per-fill fee field.
                    fee_usd=0.0,
                    timestamp_ms=trade.timestamp_s * 1000,
                )
            )

        snapshot = inv.snapshot()
        attribution_eligible = sell_count == 0
        if attribution_eligible:
            valid_locked_profit += float(snapshot["locked_profit_total"] or 0.0)
            valid_matched_qty += float(snapshot["matched_qty"] or 0.0)

        first_up = next(
            (
                t.timestamp_s
                for t in market_trades
                if t.side == "BUY" and t.outcome.upper() == "UP"
            ),
            None,
        )
        first_down = next(
            (
                t.timestamp_s
                for t in market_trades
                if t.side == "BUY" and t.outcome.upper() == "DOWN"
            ),
            None,
        )
        first_pair_gap_s = (
            abs(first_up - first_down)
            if first_up is not None and first_down is not None
            else None
        )

        market_rows.append(
            {
                **snapshot,
                "title": sample.title,
                "asset": cls["asset"],
                "horizon_minutes": cls["horizon_minutes"],
                "trade_count": len(market_trades),
                "sell_count": sell_count,
                "attribution_eligible": attribution_eligible,
                "first_pair_gap_s": first_pair_gap_s,
            }
        )

    market_rows.sort(
        key=lambda row: (
            row["asset"] or "",
            row["horizon_minutes"] or 0,
            row["market_id"],
        )
    )

    return {
        "markets": market_rows,
        "summary": {
            "short_crypto_markets": len(market_rows),
            "attribution_eligible_markets": sum(
                bool(row["attribution_eligible"]) for row in market_rows
            ),
            "markets_with_sells": sum(row["sell_count"] > 0 for row in market_rows),
            "locked_positive_markets": sum(
                row["attribution_eligible"] and row["state"] == "LOCKED_POSITIVE"
                for row in market_rows
            ),
            "matched_qty_attribution_eligible": valid_matched_qty,
            "locked_profit_usd_before_unobserved_fees": valid_locked_profit,
            "ignored_trades": dict(ignored),
            "fee_status": "UNKNOWN_PUBLIC_API_DOES_NOT_EXPOSE_PER_FILL_FEE",
        },
    }


def summarize_wallet(trades: Iterable[WalletTrade]) -> dict:
    trades = list(trades)
    class_counts = Counter()
    side_counts = Counter()
    notional = 0.0
    for trade in trades:
        class_counts[classify_trade(trade)["family"]] += 1
        side_counts[trade.side] += 1
        notional += trade.notional_usd

    return {
        "trade_count": len(trades),
        "side_counts": dict(side_counts),
        "family_counts": dict(class_counts),
        "observed_notional_usd": notional,
        "unique_markets": len({t.condition_id for t in trades if t.condition_id}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only public Polymarket wallet-history forensics"
    )
    parser.add_argument(
        "wallet",
        help="reference alias (l5zn1bwom8etsk/swisstony) or 0x wallet",
    )
    parser.add_argument("--max-trades", type=int, default=10_000)
    parser.add_argument("--min-locked-edge", type=float, default=0.03)
    parser.add_argument(
        "--dump-trades",
        action="store_true",
        help="include normalized trades in JSON output",
    )
    args = parser.parse_args()

    address, alias = resolve_wallet(args.wallet)
    trades = fetch_public_trades(address, max_trades=args.max_trades)

    output = {
        "alias": alias,
        "wallet": address,
        "read_only": True,
        "source": "https://data-api.polymarket.com/trades",
        "wallet_summary": summarize_wallet(trades),
        "short_crypto_reconstruction": reconstruct_short_crypto(
            trades,
            min_locked_edge_per_pair=args.min_locked_edge,
        ),
    }
    if args.dump_trades:
        output["trades"] = [asdict(trade) for trade in trades]

    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
