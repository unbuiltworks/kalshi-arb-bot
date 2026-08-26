"""Bidirectional lead/lag instrumentation for the Odds-API.io REST shadow.

Read-only wrapper around ``odds_api_io_rest_lead_lag_shadow``.  It pairs actual
price-signature changes on Kalshi/Polymarket with fresh sportsbook line changes
on the same canonical contract key.  Pairing is one-to-one per direct venue and
uses our receipt timestamps; Odds-API.io ``updatedAt`` remains explicitly
unverified provider metadata.

No order transport exists here.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import logging
from typing import Any

from src import odds_api_io_rest_lead_lag_shadow as rest
from src import odds_api_io_mlb_hot_shadow as base

logger = logging.getLogger("kalshi_arb.odds_api_io_rest_bidirectional_shadow")

_ORIGINAL_EVALUATE = base.evaluate_key
_ORIGINAL_HEARTBEAT = base.heartbeat
DIRECT_VENUES = {"kalshi", "polymarket"}
PAIR_HORIZON_MS = 30_000

# One pending unpaired movement per key/source is enough for first-mover counts.
_pending_retail: dict[tuple[str, str], dict[str, Any]] = {}
_pending_direct: dict[tuple[str, str], dict[str, Any]] = {}
_direct_signatures: dict[tuple[str, str], str] = {}
_counts: Counter[str] = Counter()
_leads_ms: dict[str, list[int]] = {
    "sportsbook_to_kalshi": [],
    "kalshi_to_sportsbook": [],
    "sportsbook_to_polymarket": [],
    "polymarket_to_sportsbook": [],
}


def _remember_lead(label: str, delta_ms: int) -> None:
    _counts[label] += 1
    values = _leads_ms[label]
    values.append(delta_ms)
    if len(values) > 5000:
        del values[: len(values) - 5000]


def _summary(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"n": 0, "median_ms": None, "p95_ms": None}
    rows = sorted(values)
    return {
        "n": len(rows),
        "median_ms": rows[len(rows) // 2],
        "p95_ms": rows[min(len(rows) - 1, int(len(rows) * 0.95))],
    }


async def _direct_signature(
    key: str,
    venue: str,
    universe: Any,
    poly: Any,
    kalshi: Any,
    gemini: Any,
    *,
    direct_ws_age_ms: int,
    gemini_age_ms: int,
) -> str | None:
    direct = await base.priced_quotes_for_key(
        key,
        universe,
        poly,
        kalshi,
        gemini,
        ws_book_max_age_ms=direct_ws_age_ms,
        gemini_max_age_ms=gemini_age_ms,
    )
    rows = []
    for priced in direct:
        q = priced.quote
        if str(q.venue) != venue:
            continue
        rows.append((
            str(q.market_id), str(q.yes_cost), str(q.no_cost),
            str(q.yes_depth), str(q.no_depth),
        ))
    if not rows:
        return None
    rows.sort()
    return json.dumps(rows, separators=(",", ":"))


def _pair_retail_move(key: str, receipt_ms: int, provider_meta: dict[str, Any]) -> None:
    for venue in sorted(DIRECT_VENUES):
        state_key = (key, venue)
        direct = _pending_direct.get(state_key)
        if direct is not None:
            delta = receipt_ms - int(direct["receipt_ms"])
            if 0 <= delta <= PAIR_HORIZON_MS:
                label = f"{venue}_to_sportsbook"
                _remember_lead(label, delta)
                logger.info("ODDS_API_IO_BIDIRECTIONAL_LEAD %s", json.dumps({
                    "settlement_key": key,
                    "leader": venue,
                    "follower": "sportsbook",
                    "observed_lead_ms": delta,
                    "direct_receipt_ms": direct["receipt_ms"],
                    "sportsbook_receipt_ms": receipt_ms,
                    "provider_updated_ms": provider_meta.get("provider_updated_ms"),
                    "provider_reported_age_ms": provider_meta.get("provider_reported_age_ms"),
                    "provider_timestamp_ground_truth": False,
                    "pairing_basis": "OUR_RECEIPT_TIMESTAMPS_ONE_TO_ONE",
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":"), default=str))
                _pending_direct.pop(state_key, None)
                continue
        _pending_retail[state_key] = {
            "receipt_ms": receipt_ms,
            "provider_updated_ms": provider_meta.get("provider_updated_ms"),
            "provider_reported_age_ms": provider_meta.get("provider_reported_age_ms"),
        }


def _pair_direct_move(key: str, venue: str, receipt_ms: int) -> None:
    state_key = (key, venue)
    retail = _pending_retail.get(state_key)
    if retail is not None:
        delta = receipt_ms - int(retail["receipt_ms"])
        if 0 <= delta <= PAIR_HORIZON_MS:
            label = f"sportsbook_to_{venue}"
            _remember_lead(label, delta)
            logger.info("ODDS_API_IO_BIDIRECTIONAL_LEAD %s", json.dumps({
                "settlement_key": key,
                "leader": "sportsbook",
                "follower": venue,
                "observed_lead_ms": delta,
                "sportsbook_receipt_ms": retail["receipt_ms"],
                "direct_receipt_ms": receipt_ms,
                "provider_updated_ms": retail.get("provider_updated_ms"),
                "provider_reported_age_ms": retail.get("provider_reported_age_ms"),
                "provider_timestamp_ground_truth": False,
                "pairing_basis": "OUR_RECEIPT_TIMESTAMPS_ONE_TO_ONE",
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
            _pending_retail.pop(state_key, None)
            return
    _pending_direct[state_key] = {"receipt_ms": receipt_ms}


async def evaluate_key(
    key: str,
    trigger: Any,
    universe: Any,
    poly: Any,
    kalshi: Any,
    gemini: Any,
    retail: Any,
    active: Any,
    stats: Any,
    *,
    direct_ws_age_ms: int,
    gemini_age_ms: int,
    retail_age_ms: int,
) -> None:
    venue = str(trigger.venue)
    if venue.startswith("sportsbook:"):
        book = base._retail_book_name(venue.split(":", 1)[1])
        meta = dict(rest._quote_meta.get((key, book)) or {})
        _pair_retail_move(key, int(trigger.received_ms), meta)
    elif venue in DIRECT_VENUES:
        signature = await _direct_signature(
            key,
            venue,
            universe,
            poly,
            kalshi,
            gemini,
            direct_ws_age_ms=direct_ws_age_ms,
            gemini_age_ms=gemini_age_ms,
        )
        sig_key = (key, venue)
        if signature is not None and signature != _direct_signatures.get(sig_key):
            prior = _direct_signatures.get(sig_key)
            _direct_signatures[sig_key] = signature
            # First snapshot only primes state; it is not a movement.
            if prior is not None:
                _pair_direct_move(key, venue, int(trigger.received_ms))

    await _ORIGINAL_EVALUATE(
        key,
        trigger,
        universe,
        poly,
        kalshi,
        gemini,
        retail,
        active,
        stats,
        direct_ws_age_ms=direct_ws_age_ms,
        gemini_age_ms=gemini_age_ms,
        retail_age_ms=retail_age_ms,
    )


async def heartbeat(*args: Any, **kwargs: Any) -> None:
    base_task = asyncio.create_task(_ORIGINAL_HEARTBEAT(*args, **kwargs))
    try:
        while True:
            await asyncio.sleep(15.0)
            logger.info("ODDS_API_IO_BIDIRECTIONAL_SUMMARY %s", json.dumps({
                "counts": dict(_counts),
                "sportsbook_to_kalshi": _summary(_leads_ms["sportsbook_to_kalshi"]),
                "kalshi_to_sportsbook": _summary(_leads_ms["kalshi_to_sportsbook"]),
                "sportsbook_to_polymarket": _summary(_leads_ms["sportsbook_to_polymarket"]),
                "polymarket_to_sportsbook": _summary(_leads_ms["polymarket_to_sportsbook"]),
                "pair_horizon_ms": PAIR_HORIZON_MS,
                "provider_timestamp_ground_truth": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
    finally:
        base_task.cancel()
        await asyncio.gather(base_task, return_exceptions=True)


base.evaluate_key = evaluate_key
base.heartbeat = heartbeat

# Deployment trigger after Railway watch-pattern update.
if __name__ == "__main__":
    base.main()
