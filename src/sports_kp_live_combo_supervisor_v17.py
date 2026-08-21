"""Production combo supervisor v17: fair sports-family discovery.

v16 proved cross-venue prepricing, but Kalshi collection associated_events can be
ordered heavily toward one sport.  With the bounded 24-request event-market budget,
that could starve MLB games/props before candidate construction.  This wrapper
reorders associated events *inside each collection* before the existing generator
consumes them.  Within MLB, upcoming events are preferred over recently-started
ones and old events so finalized earlier games cannot consume the bounded budget.
No market semantics, q=1 gates, rule certification, or order entry are changed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from src import sports_kp_live_combo_supervisor_v16 as v16

base = v16.base
generator = v16.generator
logger = v16.logger

_ORIGINAL_OPEN_COLLECTIONS = generator._open_collections
_ET = ZoneInfo("America/New_York")
_MLB_TIME_RE = re.compile(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})(?P<mm>\d{2})[A-Z]+$", re.I)

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


def _mlb_start(ticker: str) -> datetime | None:
    match = _MLB_TIME_RE.search(str(ticker or "").upper())
    if match is None:
        return None
    try:
        return datetime.strptime(
            match.group("yy") + match.group("mon") + match.group("dd") + match.group("hh") + match.group("mm"),
            "%y%b%d%H%M",
        ).replace(tzinfo=_ET)
    except ValueError:
        return None


def _mlb_time_key(row: dict[str, Any]) -> tuple[int, float, str]:
    ticker = str(row.get("ticker") or "")
    start = _mlb_start(ticker)
    if start is None:
        return (3, float("inf"), ticker)
    now = datetime.now(_ET)
    if start >= now:
        # Upcoming first, nearest start first.
        return (0, (start - now).total_seconds(), ticker)
    if start >= now - timedelta(hours=3):
        # Then recently-started/live events, newest start first.
        return (1, (now - start).total_seconds(), ticker)
    # Old events last; closest old event before older finalized inventory.
    return (2, (now - start).total_seconds(), ticker)


def _interleave(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in ("MLB_PROP", "MLB_GAME", "NFL", "OTHER")}
    for row in rows:
        buckets[_family(str(row.get("ticker") or ""))].append(row)
    for key in ("MLB_PROP", "MLB_GAME"):
        buckets[key].sort(key=_mlb_time_key)
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
    leading_mlb: list[str] = []
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        associated = [dict(x) for x in (row.get("associated_events") or []) if isinstance(x, dict)]
        if associated:
            for event in associated:
                family_counts[_family(str(event.get("ticker") or ""))] += 1
            ordered = _interleave(associated)
            if len(leading_mlb) < 12:
                leading_mlb.extend(str(x.get("ticker") or "") for x in ordered if _family(str(x.get("ticker") or "")) in {"MLB_PROP", "MLB_GAME"})
                leading_mlb = leading_mlb[:12]
            row["associated_events"] = ordered
        else:
            tickers = [str(x) for x in (row.get("associated_event_tickers") or []) if x]
            for ticker in tickers:
                family_counts[_family(ticker)] += 1
            if tickers:
                boxed = [{"ticker": t, "is_yes_only": bool(row.get("is_all_yes", False))} for t in tickers]
                ordered = _interleave(boxed)
                if len(leading_mlb) < 12:
                    leading_mlb.extend(str(x.get("ticker") or "") for x in ordered if _family(str(x.get("ticker") or "")) in {"MLB_PROP", "MLB_GAME"})
                    leading_mlb = leading_mlb[:12]
                row["associated_events"] = ordered
        out.append(row)
    logger.warning("SPORTS_MVE_FAMILY_INTERLEAVE %s", json.dumps({
        "collection_count": len(out),
        "associated_event_family_counts": family_counts,
        "priority_order": ["MLB_PROP", "MLB_GAME", "NFL", "OTHER"],
        "mlb_ordering": "UPCOMING_THEN_RECENT_LIVE_THEN_OLD",
        "leading_mlb_events": leading_mlb,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":")))
    return out


generator._open_collections = _family_fair_collections

run = v16.run
main = v16.main

if __name__ == "__main__":
    main()
