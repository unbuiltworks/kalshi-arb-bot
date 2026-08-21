"""Production combo supervisor v17: fair sports-family discovery.

v16 proved cross-venue prepricing, but Kalshi collection associated_events can be
ordered heavily toward one sport.  With the bounded 24-request event-market budget,
that could starve MLB games/props before candidate construction.  This wrapper
reorders associated events *inside each collection* before the existing generator
consumes them.  No market semantics, q=1 gates, rule certification, or order entry
are changed.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v16 as v16

base = v16.base
generator = v16.generator
logger = v16.logger

_ORIGINAL_OPEN_COLLECTIONS = generator._open_collections

_MLB_GAME_PREFIXES = ("KXMLBGAME-", "KXMLBSPREAD-", "KXMLBTOTAL-")
_MLB_PROP_PREFIXES = (
    "KXMLBHIT-", "KXMLBHR-", "KXMLBTB-", "KXMLBKS-",
    "KXMLBRBI-", "KXMLBHRR-",
)


def _family(ticker: str) -> str:
    t = str(ticker or "").upper()
    if t.startswith(_MLB_PROP_PREFIXES):
        return "MLB_PROP"
    if t.startswith(_MLB_GAME_PREFIXES):
        return "MLB_GAME"
    if t.startswith("KXNFL"):
        return "NFL"
    return "OTHER"


def _interleave(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in ("MLB_PROP", "MLB_GAME", "NFL", "OTHER")}
    for row in rows:
        buckets[_family(str(row.get("ticker") or ""))].append(row)
    out: list[dict[str, Any]] = []
    # Put MLB first because v16's existing production history was NFL-heavy;
    # round-robin thereafter so no family can monopolize the bounded fetch budget.
    order = ("MLB_PROP", "MLB_GAME", "NFL", "OTHER")
    while any(buckets[k] for k in order):
        for key in order:
            if buckets[key]:
                out.append(buckets[key].pop(0))
    return out


async def _family_fair_collections(client: Any) -> list[dict[str, Any]]:
    rows = await _ORIGINAL_OPEN_COLLECTIONS(client)
    family_counts = {"MLB_PROP": 0, "MLB_GAME": 0, "NFL": 0, "OTHER": 0}
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        associated = [dict(x) for x in (row.get("associated_events") or []) if isinstance(x, dict)]
        if associated:
            for event in associated:
                family_counts[_family(str(event.get("ticker") or ""))] += 1
            row["associated_events"] = _interleave(associated)
        else:
            tickers = [str(x) for x in (row.get("associated_event_tickers") or []) if x]
            for ticker in tickers:
                family_counts[_family(ticker)] += 1
            if tickers:
                boxed = [{"ticker": t, "is_yes_only": bool(row.get("is_all_yes", False))} for t in tickers]
                row["associated_events"] = _interleave(boxed)
        out.append(row)
    logger.warning("SPORTS_MVE_FAMILY_INTERLEAVE %s", json.dumps({
        "collection_count": len(out),
        "associated_event_family_counts": family_counts,
        "priority_order": ["MLB_PROP", "MLB_GAME", "NFL", "OTHER"],
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":")))
    return out


generator._open_collections = _family_fair_collections

run = v16.run
main = v16.main

if __name__ == "__main__":
    main()
