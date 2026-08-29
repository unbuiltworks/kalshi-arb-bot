"""Raw API payloads -> normalized trade records, with careful deduplication.

Deduplication rule
------------------
Overlapping offset pages return the *same* fill twice; a single transaction can
also legitimately contain two identical fills (same price, size, side, second).
Collapsing on a naive set would silently delete the second kind. So we count
occurrences of each fingerprint *within* each response batch and keep the
maximum count seen across batches -- a repeat page contributes nothing, while a
genuine duplicate inside one response survives.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .cache import RawCache
from .timeutil import iso_utc, to_epoch_s

TRADE_TYPES = {"TRADE", "BUY", "SELL", ""}


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def canonical_outcome(
    outcome: Any, outcome_index: Any, market_outcomes: Optional[Sequence[str]] = None
) -> Tuple[str, str]:
    """Map a raw outcome label to Up/Down. Returns ``(label, mapping_note)``."""
    text = str(outcome or "").strip()
    lowered = text.lower()
    if lowered in ("up", "yes-up"):
        return "Up", "direct"
    if lowered in ("down", "no-down"):
        return "Down", "direct"
    if lowered == "yes":
        return "Up", "yes_no_mapped"
    if lowered == "no":
        return "Down", "yes_no_mapped"
    if not text and market_outcomes and outcome_index is not None:
        try:
            resolved = str(market_outcomes[int(outcome_index)])
        except (ValueError, TypeError, IndexError):
            resolved = ""
        if resolved:
            label, _note = canonical_outcome(resolved, None, None)
            return label, "from_market_outcomes"
    if outcome_index in (0, "0", 0.0):
        return "Up", "index_fallback"
    if outcome_index in (1, "1", 1.0):
        return "Down", "index_fallback"
    return "", "unmapped"


def normalize_trade_item(item: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    """Normalize one activity/trades row. Returns None for non-trade activity."""
    activity_type = str(item.get("type", "") or "").upper()
    if activity_type and activity_type not in TRADE_TYPES:
        return None

    timestamp = to_epoch_s(item.get("timestamp"))
    price = _num(item.get("price"))
    shares = _num(item.get("size"))
    if shares is None:
        shares = _num(item.get("shares"))
    dollars = _num(item.get("usdcSize"))
    if dollars is None and price is not None and shares is not None:
        dollars = round(price * shares, 10)

    side = str(item.get("side", "") or "").upper()
    if side not in ("BUY", "SELL"):
        side = activity_type if activity_type in ("BUY", "SELL") else ""

    outcome, mapping_note = canonical_outcome(item.get("outcome"), item.get("outcomeIndex"))

    return {
        "source": source,
        "timestamp": timestamp,
        "datetime_utc": iso_utc(timestamp),
        "proxy_wallet": item.get("proxyWallet") or item.get("user") or "",
        "activity_type": activity_type or "TRADE",
        "condition_id": item.get("conditionId") or item.get("condition_id") or "",
        "token_id": str(item.get("asset") or item.get("assetId") or ""),
        "outcome_raw": item.get("outcome") or "",
        "outcome": outcome,
        "outcome_mapping": mapping_note,
        "outcome_index": item.get("outcomeIndex"),
        "side": side,
        "price": price,
        "shares": shares,
        "dollars": dollars,
        "transaction_hash": item.get("transactionHash") or item.get("transaction_hash") or "",
        "market_slug": item.get("slug") or "",
        "event_slug": item.get("eventSlug") or item.get("event_slug") or "",
        "title": item.get("title") or "",
    }


def fingerprint(record: Dict[str, Any]) -> Tuple:
    """Identity of a fill for dedup purposes."""
    return (
        record.get("transaction_hash", ""),
        record.get("timestamp"),
        record.get("condition_id", ""),
        record.get("token_id", ""),
        record.get("side", ""),
        _round(record.get("price")),
        _round(record.get("shares")),
    )


def _round(value: Optional[float], places: int = 8) -> Optional[float]:
    return None if value is None else round(float(value), places)


def dedupe_batches(batches: Iterable[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Multiset union of batches: max per-batch multiplicity wins (see module docstring)."""
    best_count: Counter = Counter()
    exemplar: Dict[Tuple, Dict[str, Any]] = {}
    order: List[Tuple] = []
    for batch in batches:
        counts = Counter()
        for record in batch:
            key = fingerprint(record)
            counts[key] += 1
            if key not in exemplar:
                exemplar[key] = record
                order.append(key)
        for key, count in counts.items():
            if count > best_count[key]:
                best_count[key] = count

    out: List[Dict[str, Any]] = []
    for key in order:
        out.extend(dict(exemplar[key]) for _ in range(best_count[key]))
    return out


def load_trade_records(cache: RawCache) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build the deduplicated fill list from the raw cache.

    ``/activity`` is authoritative. ``/trades`` is only used to surface fills the
    activity feed did not return; those are tagged so they can be audited.
    """
    report: Dict[str, Any] = {}

    activity_batches = []
    raw_activity = 0
    skipped_non_trade = Counter()
    for batch in cache.payload_batches("activity"):
        normalized = []
        for item in batch:
            raw_activity += 1
            record = normalize_trade_item(item, "activity")
            if record is None:
                skipped_non_trade[str(item.get("type", "")).upper()] += 1
                continue
            normalized.append(record)
        activity_batches.append(normalized)
    activity_records = dedupe_batches(activity_batches)

    trade_batches = []
    raw_trades = 0
    for batch in cache.payload_batches("trades"):
        normalized = []
        for item in batch:
            raw_trades += 1
            record = normalize_trade_item(item, "trades")
            if record is not None:
                normalized.append(record)
        trade_batches.append(normalized)
    trades_records = dedupe_batches(trade_batches)

    known = Counter(fingerprint(record) for record in activity_records)
    supplements: List[Dict[str, Any]] = []
    extra_counts = Counter(fingerprint(record) for record in trades_records)
    for key, count in extra_counts.items():
        missing = count - known.get(key, 0)
        if missing <= 0:
            continue
        exemplar = next(record for record in trades_records if fingerprint(record) == key)
        supplements.extend(dict(exemplar) for _ in range(missing))

    combined = activity_records + supplements
    combined.sort(key=lambda record: (record.get("timestamp") or 0, record.get("transaction_hash") or ""))

    report.update(
        {
            "raw_activity_rows": raw_activity,
            "raw_trades_rows": raw_trades,
            "activity_fills_after_dedup": len(activity_records),
            "trades_fills_after_dedup": len(trades_records),
            "supplemented_from_trades": len(supplements),
            "total_fills": len(combined),
            "non_trade_activity_skipped": dict(skipped_non_trade),
        }
    )
    return combined, report


def load_non_trade_activity(cache: RawCache) -> List[Dict[str, Any]]:
    """REDEEM / SPLIT / MERGE / REWARD rows -- kept for settlement bookkeeping."""
    batches: List[List[Dict[str, Any]]] = []
    for batch in cache.payload_batches("activity"):
        rows = []
        for item in batch:
            activity_type = str(item.get("type", "") or "").upper()
            if activity_type in TRADE_TYPES:
                continue
            rows.append(
                {
                    "activity_type": activity_type,
                    "timestamp": to_epoch_s(item.get("timestamp")),
                    "condition_id": item.get("conditionId") or "",
                    "token_id": str(item.get("asset") or ""),
                    "size": _num(item.get("size")),
                    "usdc_size": _num(item.get("usdcSize")),
                    "transaction_hash": item.get("transactionHash") or "",
                    "outcome": item.get("outcome") or "",
                    "title": item.get("title") or "",
                    "market_slug": item.get("slug") or "",
                }
            )
        batches.append(rows)
    seen = set()
    out = []
    for batch in batches:
        for row in batch:
            key = (
                row["activity_type"],
                row["timestamp"],
                row["condition_id"],
                row["token_id"],
                row["transaction_hash"],
                row["size"],
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    out.sort(key=lambda row: (row.get("timestamp") or 0))
    return out
