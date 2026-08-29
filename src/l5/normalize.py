"""Turn raw Polymarket API records into a deduplicated, classified trade list."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from src.l5.classify import MarketClassification, classify_market, iso

TRADE_ACTIVITY_TYPE = "TRADE"

# Activity rows that are not fills but do move inventory or cash. Kept
# separately so nothing is silently dropped.
NON_TRADE_TYPES = ("SPLIT", "MERGE", "REDEEM", "REWARD", "CONVERSION")

UP = "UP"
DOWN = "DOWN"

_OUTCOME_ALIASES = {
    "up": UP,
    "down": DOWN,
    "yes": UP,   # only used when the market's own outcomes list says so
    "no": DOWN,
}


@dataclass(frozen=True)
class Trade:
    timestamp: int
    timestamp_iso: str
    asset: str                  # BTC | ETH
    window_minutes: int
    condition_id: str           # canonical market / window ID
    market_id: str
    market_slug: str
    question: str
    event_slug: str
    event_title: str
    outcome: str                # UP | DOWN
    outcome_index: int | None
    side: str                   # BUY | SELL
    price: float
    shares: float
    dollars: float
    dollars_derived: bool       # True when usdcSize was absent and we used price*size
    token_id: str               # ERC-1155 token id (the API's own `asset` field)
    transaction_hash: str
    window_start_ts: int | None
    window_end_ts: int | None
    seconds_from_window_start: int | None
    seconds_to_expiry: int | None
    window_source: str
    window_conflict: bool
    bounds_reconstructed: bool
    source: str                 # activity | trades | screenshot
    # Screenshot-derived provenance; None for API rows.
    feed_seq: int | None
    timestamp_uncertainty_s: int | None
    price_cents_displayed: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizeStats:
    raw_records: int = 0
    parsed: int = 0
    dropped_non_trade: int = 0
    dropped_no_market: int = 0
    dropped_out_of_scope: int = 0
    dropped_unresolved_outcome: int = 0
    dropped_bad_numbers: int = 0
    deduped_within_source: int = 0
    deduped_across_sources: int = 0
    window_conflicts: int = 0
    bounds_reconstructed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- market index ------------------------------------------------------------


def build_market_index(market_records: Iterable[dict]) -> dict[str, MarketClassification]:
    """condition_id -> classification, keeping the richest record per market."""
    index: dict[str, MarketClassification] = {}
    for record in market_records:
        classification = classify_market(record)
        if not classification.condition_id:
            continue
        existing = index.get(classification.condition_id)
        if existing is None or (not existing.in_scope and classification.in_scope):
            index[classification.condition_id] = classification
    return index


def condition_ids_in(records: Iterable[dict]) -> set[str]:
    out: set[str] = set()
    for record in records:
        cid = record.get("conditionId") or record.get("condition_id")
        if cid:
            out.add(str(cid))
    return out


# --- field parsing -----------------------------------------------------------


def _opt_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ordering_key(trade: "Trade") -> tuple:
    """Chronological order.

    `feed_seq` breaks ties before any value-derived field: screenshot rows all
    land on the same minute, and the activity feed's own ordering is exact
    where the timestamps are not.
    """
    return (
        trade.timestamp,
        trade.feed_seq if trade.feed_seq is not None else 0,
        trade.condition_id,
        trade.outcome,
        trade.side,
        trade.price,
        trade.shares,
    )


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return out


def _int_ts(value: Any) -> int | None:
    n = _num(value)
    if n is None or n <= 0:
        return None
    if n > 1e11:
        n /= 1000.0
    return int(n)


def resolve_outcome(record: dict, market: MarketClassification) -> str | None:
    """Map a fill onto UP/DOWN.

    Explicit "Up"/"Down" text wins. A Yes/No market is only mapped through the
    market's own `outcomes` list and the row's outcomeIndex -- never guessed
    from the question text.
    """
    raw = record.get("outcome")
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key in ("up", "down"):
            return _OUTCOME_ALIASES[key]

    index = record.get("outcomeIndex")
    if index is None:
        index = record.get("outcome_index")
    idx = None
    if isinstance(index, (int, float)) and not isinstance(index, bool):
        idx = int(index)
    elif isinstance(index, str) and index.strip().lstrip("-").isdigit():
        idx = int(index)

    if idx is not None and 0 <= idx < len(market.outcomes):
        key = str(market.outcomes[idx]).strip().lower()
        if key in _OUTCOME_ALIASES:
            return _OUTCOME_ALIASES[key]

    # Last resort: the row's token id against the market's token list.
    token = str(record.get("asset") or record.get("tokenId") or "")
    if token and token in market.token_ids:
        position = market.token_ids.index(token)
        if position < len(market.outcomes):
            key = str(market.outcomes[position]).strip().lower()
            if key in _OUTCOME_ALIASES:
                return _OUTCOME_ALIASES[key]
    return None


def _identity_key(record: dict) -> tuple:
    """Fields that identify a fill regardless of which endpoint served it."""
    return (
        str(record.get("transactionHash") or ""),
        str(record.get("asset") or record.get("tokenId") or ""),
        str(record.get("conditionId") or ""),
        str(record.get("side") or "").upper(),
        _int_ts(record.get("timestamp")),
        round(_num(record.get("price")) or 0.0, 8),
        round(_num(record.get("size")) or 0.0, 8),
    )


def _exact_key(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def dedupe_records(by_source: dict[str, list[dict]], stats: NormalizeStats) -> list[tuple[str, dict]]:
    """Deduplicate within each source, then reconcile across sources.

    Within a source, byte-identical records are the overlap our backwards time
    cursor deliberately creates, so they collapse. Across sources we keep, for
    each identity key, whichever source reported that fill the most times --
    so a genuinely repeated fill survives, but the same fill served by both
    /activity and /trades is counted once.
    """
    per_source_unique: dict[str, list[dict]] = {}
    for source, records in by_source.items():
        seen: set[str] = set()
        unique: list[dict] = []
        for record in records:
            key = _exact_key(record)
            if key in seen:
                stats.deduped_within_source += 1
                continue
            seen.add(key)
            unique.append(record)
        per_source_unique[source] = unique

    grouped: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for source, records in per_source_unique.items():
        for record in records:
            grouped[_identity_key(record)][source].append(record)

    merged: list[tuple[str, dict]] = []
    for _key, per_source in grouped.items():
        counts = Counter({source: len(rows) for source, rows in per_source.items()})
        best_source, best_count = counts.most_common(1)[0]
        total = sum(counts.values())
        stats.deduped_across_sources += total - best_count
        for record in per_source[best_source]:
            merged.append((best_source, record))
    return merged


# --- top level ---------------------------------------------------------------


def normalize(
    by_source: dict[str, list[dict]],
    market_index: dict[str, MarketClassification],
) -> tuple[list[Trade], NormalizeStats]:
    stats = NormalizeStats()
    stats.raw_records = sum(len(v) for v in by_source.values())

    trade_like: dict[str, list[dict]] = {}
    for source, records in by_source.items():
        kept = []
        for record in records:
            activity_type = record.get("type")
            if activity_type is not None and str(activity_type).upper() != TRADE_ACTIVITY_TYPE:
                stats.dropped_non_trade += 1
                continue
            kept.append(record)
        trade_like[source] = kept

    trades: list[Trade] = []
    for source, record in dedupe_records(trade_like, stats):
        condition_id = str(record.get("conditionId") or "")
        market = market_index.get(condition_id)
        if market is None:
            stats.dropped_no_market += 1
            continue
        if not market.in_scope:
            stats.dropped_out_of_scope += 1
            continue

        outcome = resolve_outcome(record, market)
        if outcome is None:
            stats.dropped_unresolved_outcome += 1
            continue

        ts = _int_ts(record.get("timestamp"))
        price = _num(record.get("price"))
        shares = _num(record.get("size"))
        side = str(record.get("side") or "").upper()
        if ts is None or price is None or shares is None or side not in ("BUY", "SELL"):
            stats.dropped_bad_numbers += 1
            continue

        usdc = _num(record.get("usdcSize"))
        dollars_derived = usdc is None
        dollars = usdc if usdc is not None else price * shares

        index_raw = record.get("outcomeIndex")
        outcome_index = None
        if isinstance(index_raw, (int, float)) and not isinstance(index_raw, bool):
            outcome_index = int(index_raw)
        elif isinstance(index_raw, str) and index_raw.strip().lstrip("-").isdigit():
            outcome_index = int(index_raw)

        start = market.window_start_ts
        end = market.window_end_ts
        trades.append(
            Trade(
                timestamp=ts,
                timestamp_iso=iso(ts),
                asset=market.asset or "",
                window_minutes=int(market.window_minutes or 0),
                condition_id=condition_id,
                market_id=market.market_id,
                market_slug=market.slug,
                question=market.question,
                event_slug=market.event_slug,
                event_title=market.event_title,
                outcome=outcome,
                outcome_index=outcome_index,
                side=side,
                price=price,
                shares=shares,
                dollars=dollars,
                dollars_derived=dollars_derived,
                token_id=str(record.get("asset") or record.get("tokenId") or ""),
                transaction_hash=str(record.get("transactionHash") or ""),
                window_start_ts=start,
                window_end_ts=end,
                seconds_from_window_start=(ts - start) if start is not None else None,
                seconds_to_expiry=(end - ts) if end is not None else None,
                window_source=market.window_source,
                window_conflict=market.window_conflict,
                bounds_reconstructed=market.bounds_reconstructed,
                source=source,
                feed_seq=_opt_int(record.get("feedSeq")),
                timestamp_uncertainty_s=_opt_int(record.get("tsUncertaintyS")),
                price_cents_displayed=_opt_int(record.get("priceCentsDisplayed")),
            )
        )
        if market.window_conflict:
            stats.window_conflicts += 1
        if market.bounds_reconstructed:
            stats.bounds_reconstructed += 1

    trades.sort(key=_ordering_key)
    stats.parsed = len(trades)
    return trades, stats


def extract_non_trade_activity(
    records: Iterable[dict], market_index: dict[str, MarketClassification]
) -> list[dict]:
    """SPLIT / MERGE / REDEEM / REWARD / CONVERSION rows on in-scope markets.

    A MERGE is how matched Up+Down shares are turned back into USDC, so these
    rows are context an inventory reading of the fills alone would miss.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for record in records:
        activity_type = str(record.get("type") or "").upper()
        if activity_type not in NON_TRADE_TYPES:
            continue
        # The backwards time cursor overlaps pages on purpose, so the same
        # merge/redeem can arrive twice.
        exact = _exact_key(record)
        if exact in seen:
            continue
        seen.add(exact)
        condition_id = str(record.get("conditionId") or "")
        market = market_index.get(condition_id)
        if market is None or not market.in_scope:
            continue
        ts = _int_ts(record.get("timestamp"))
        out.append(
            {
                "timestamp": ts,
                "timestamp_iso": iso(ts),
                "activity_type": activity_type,
                "asset": market.asset,
                "window_minutes": market.window_minutes,
                "condition_id": condition_id,
                "market_slug": market.slug,
                "event_slug": market.event_slug,
                "outcome": record.get("outcome"),
                "size": _num(record.get("size")),
                "usdc_size": _num(record.get("usdcSize")),
                "price": _num(record.get("price")),
                "transaction_hash": str(record.get("transactionHash") or ""),
            }
        )
    out.sort(key=lambda r: (r["timestamp"] or 0, r["condition_id"]))
    return out
