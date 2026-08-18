"""Read-only wrapper that probes Odds-API.io MLB player-prop WebSocket schemas.

The existing game-line hot matcher remains unchanged. This wrapper logs only the
first few fresh non-main-market samples by market name so we can build an exact,
non-fuzzy prop normalizer against prediction-market contracts. No order transport.
"""
from __future__ import annotations

from collections import Counter
import json
import logging
import time
from typing import Any, Mapping

from src import odds_api_io_mlb_hot_shadow as base

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_hot_prop_probe")
_seen: Counter[str] = Counter()
_orig_ingest = base.OddsIoStore.ingest


def _probe_ingest(self: base.OddsIoStore, message: Mapping[str, Any], *, freshness_ms: int) -> int:
    if str(message.get("type") or "") in {"created", "updated"}:
        received_ms = int(time.time() * 1000)
        markets = message.get("markets")
        if isinstance(markets, list):
            for market in markets:
                if not isinstance(market, Mapping):
                    continue
                name = str(market.get("name") or "").strip()
                if not name or name.lower() in {"ml", "spread", "totals"}:
                    continue
                updated_ms = base._market_updated_ms(market.get("updatedAt"))
                if updated_ms is None or abs(received_ms - updated_ms) > freshness_ms:
                    continue
                if _seen[name] >= 3:
                    continue
                _seen[name] += 1
                odds = market.get("odds")
                sample = odds[:2] if isinstance(odds, list) else odds
                logger.info(
                    "ODDS_API_IO_PROP_SCHEMA %s",
                    json.dumps({
                        "event_id": str(message.get("id") or ""),
                        "book": str(message.get("bookie") or message.get("bookmaker") or ""),
                        "market_name": name,
                        "updatedAt": market.get("updatedAt"),
                        "odds_sample": sample,
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":"), default=str),
                )
    return _orig_ingest(self, message, freshness_ms=freshness_ms)


base.OddsIoStore.ingest = _probe_ingest

if __name__ == "__main__":
    base.main()
