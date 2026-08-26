"""Receipt-fresh wrapper for the read-only Odds-API.io bidirectional shadow.

Odds-API.io ``updatedAt`` describes the bookmaker line's last reported change;
it is not the age of the REST response we just received.  The base OddsIoStore
was designed for websocket messages and therefore used market ``updatedAt`` as a
freshness gate.  That incorrectly rejected unchanged-but-current REST quotes.

For REST-created/updated messages only, this wrapper substitutes local receipt
time into the copy passed to OddsIoStore's freshness machinery.  The original
provider timestamp remains untouched in ``odds_api_io_rest_lead_lag_shadow``'s
provider metadata and is still logged as unverified line-change metadata.

Read-only.  No order transport is added.
"""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Mapping

from src import odds_api_io_rest_bidirectional_shadow as bidirectional  # noqa: F401
from src import odds_api_io_rest_lead_lag_shadow as rest
from src import odds_api_io_mlb_hot_shadow as base

_ORIGINAL_INGEST = base.OddsIoStore.ingest


def _receipt_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_fresh_ingest(self: base.OddsIoStore, message: Mapping[str, Any], *, freshness_ms: int) -> int:
    kind = str(message.get("type") or "")
    # The REST adapter emits only created/updated snapshots through this path.
    # Preserve all original provider timestamp metadata in rest._quote_meta; only
    # the store-facing copy uses receipt time for freshness validation.
    if kind in {"created", "updated"} and isinstance(message.get("markets"), list):
        receipt_s = time.time()
        receipt_iso = datetime.fromtimestamp(receipt_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        copied = dict(message)
        copied["timestamp"] = receipt_s
        copied_markets: list[Any] = []
        for market in message.get("markets") or []:
            if isinstance(market, Mapping):
                row = dict(market)
                row["updatedAt"] = receipt_iso
                copied_markets.append(row)
            else:
                copied_markets.append(market)
        copied["markets"] = copied_markets
        return _ORIGINAL_INGEST(self, copied, freshness_ms=freshness_ms)
    return _ORIGINAL_INGEST(self, message, freshness_ms=freshness_ms)


base.OddsIoStore.ingest = _receipt_fresh_ingest

# Railway deployment trigger after the service watch pattern was updated.
if __name__ == "__main__":
    base.main()
