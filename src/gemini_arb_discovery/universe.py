"""Discovery of every active Gemini prediction market, from public data.

THE DISCOVERY SURFACE. `GET /v1/prediction-markets/events` is public,
unauthenticated, and paginated (`pagination: {limit, offset, total}`). Each event
carries its category, type, ticker, status and its full contract list; each
contract carries `instrumentSymbol`, `ticker`, `strike`, `expiryDate`, `source`,
`status`, venue order terms (`quantityMinimum`, `priceIncrement`, ...) and a
`prices` block.

THE `prices` BLOCK IS NOT DEPTH. It reports `bestBid`, `bestAsk` and a
`buy`/`sell` convenience view. It says nothing about size, so it can establish
that a candidate is QUOTED and never that it is executable. Depth comes from
`GET /v1/book/{instrumentSymbol}` in `books.py`, and no economic claim in this
package is computed from `prices` alone.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional

from src.gemini_arb_discovery.http import get_json

EVENTS_PATH = "/v1/prediction-markets/events"
PAGE = 50


def fetch_events_page(offset: int, limit: int = PAGE,
                      timeout: float = 60.0) -> Dict[str, Any]:
    return get_json("%s?limit=%d&offset=%d" % (EVENTS_PATH, limit, offset),
                    timeout=timeout)


def fetch_all_events(max_pages: int = 60, limit: int = PAGE,
                     sleep_s: float = 0.15) -> Dict[str, Any]:
    """Page the whole catalogue. Returns {events, pages, errors, total_claimed}.

    A page that fails to read is recorded as an error and does NOT silently
    shrink the universe: a scan over an unknown fraction of the platform cannot
    support a platform-wide negative claim, so the caller is told what it missed.
    """
    events: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, Any]] = []
    total_claimed: Optional[int] = None
    pages = 0
    offset = 0
    t0 = time.time()
    while pages < max_pages:
        r = fetch_events_page(offset, limit=limit)
        pages += 1
        if not r.get("readable"):
            errors.append({"offset": offset, "err": r.get("err"),
                           "status": r.get("status")})
            offset += limit
            if len(errors) >= 5:
                break
            continue
        payload = r["json"]
        rows = payload.get("data") or []
        pag = payload.get("pagination") or {}
        if total_claimed is None:
            total_claimed = pag.get("total")
        for e in rows:
            if e.get("id") is not None:
                events[str(e["id"])] = e
        if not rows:
            break
        offset += limit
        if total_claimed is not None and offset >= int(total_claimed):
            break
        time.sleep(sleep_s)
    return {"events": list(events.values()), "pages": pages, "errors": errors,
            "total_claimed": total_claimed, "elapsed_s": round(time.time() - t0, 2),
            "fetched_at": time.time()}


def active_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("status") == "active"]


def active_contracts(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in (event.get("contracts") or [])
            if c.get("status") == "active"]


# ------------------------------------------------------------- provenance

#: Fields kept in the committed evidence snapshot. Everything the analysis reads
#: is here; what is dropped is the `description` rich-text document, which is
#: ~95% of the payload by bytes and whose authoritative content is the PDF at
#: `termsAndConditionsUrl` -- that URL is kept, so the settlement rules behind
#: every claim remain reachable from the committed record.
CONTRACT_EVIDENCE_FIELDS = (
    "id", "label", "ticker", "instrumentSymbol", "status", "marketState",
    "prices", "strike", "source", "expiryDate", "effectiveDate", "isCombo",
    "quantityMinimum", "quantityIncrement", "priceMinimum", "priceIncrement",
    "quoteAssetPrecision", "termsAndConditionsUrl", "abbreviatedName",
)
EVENT_EVIDENCE_FIELDS = ("id", "title", "slug", "type", "category", "ticker",
                         "status", "createdAt")


def prune_for_evidence(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The committed snapshot: every field the analysis uses, no rich text."""
    out = []
    for e in events:
        row = {k: e.get(k) for k in EVENT_EVIDENCE_FIELDS}
        row["contracts"] = [{k: c.get(k) for k in CONTRACT_EVIDENCE_FIELDS}
                            for c in (e.get("contracts") or [])]
        out.append(row)
    return out


def write_evidence(out_dir: str, disc: Dict[str, Any]) -> Dict[str, Any]:
    """Write a gzipped pruned snapshot plus a digest of the FULL payload.

    The digest is over the untruncated discovery result, so the committed
    snapshot can be shown to derive from a specific complete read of the venue
    even though the rich text itself is not carried in the repository.
    """
    import gzip
    import hashlib
    os.makedirs(out_dir, exist_ok=True)
    full = json.dumps(disc, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(full).hexdigest()
    snap = {"fetched_at": disc.get("fetched_at"),
            "pages": disc.get("pages"), "errors": disc.get("errors"),
            "total_claimed": disc.get("total_claimed"),
            "full_payload_sha256": digest,
            "full_payload_bytes": len(full),
            "pruned_fields_note": (
                "contract `description` rich text dropped; the authoritative "
                "settlement text is the PDF at termsAndConditionsUrl, retained"),
            "events": prune_for_evidence(disc.get("events") or [])}
    path = os.path.join(out_dir, "universe_snapshot.json.gz")
    with gzip.open(path, "wt") as fh:
        json.dump(snap, fh, separators=(",", ":"))
    return {"path": path, "sha256": digest, "bytes": len(full)}
