"""Venue-tagged incentive credits. VENUE IS MANDATORY.

WHY THIS EXISTS. On 2026-09-10 we nearly re-attributed a 25-row, $0.42
maker-rebate sample from Polymarket US to Gemini, because no row in
`venue_rebate_credits.json` carried a venue field and both venues credit
"maker rebate" against this account. The primary source settled it -- every
slug was a PMUS slug (aec-/asc-/tsc-) and the study's own reference tied the
credits to R3L/R4 PMUS rungs -- but the ambiguity should never have existed,
and a coefficient derived from a pooled set would have been wrong for BOTH
venues while looking perfectly reasonable.

So: a credit without a venue is not a credit, it is an unlabelled number.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Iterable, Mapping, Optional
import json
import os

POLYMARKET_US = "polymarket_us"
GEMINI = "gemini"
VENUES = (POLYMARKET_US, GEMINI)

# Maker FEE charged by each venue. Polymarket's is zero, which is why its
# gross and net per-contract numbers coincide; Gemini charges makers and
# rebates separately, so its gross is NOT its income.
MAKER_FEE_IS_ZERO = {POLYMARKET_US: True, GEMINI: False}


class MissingVenue(ValueError):
    """A credit row with no venue. Fail closed -- never default one."""


class UnknownVenue(ValueError):
    """A venue we have not defined economics for."""


class MixedVenues(ValueError):
    """Refused: a coefficient across venues describes neither of them."""


class SourceUnavailable(RuntimeError):
    """The venue's credit source is not reachable from here. Reported as a
    gap, never filled with an estimate or a published example table."""


def validate_row(row: Mapping) -> str:
    v = (row.get("venue") or "").strip()
    if not v:
        raise MissingVenue(
            f"credit row {row.get('id')!r} has no venue; "
            f"an unlabelled credit cannot calibrate any venue")
    if v not in VENUES:
        raise UnknownVenue(f"{v!r} not in {VENUES}")
    return v


def load_credits(path: str) -> list:
    with open(path) as fh:
        doc = json.load(fh)
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    for r in rows or []:
        validate_row(r)
    return list(rows or [])


def for_venue(rows: Iterable[Mapping], venue: str) -> list:
    if venue not in VENUES:
        raise UnknownVenue(venue)
    return [r for r in rows if validate_row(r) == venue]


def coefficient_inputs(rows: Iterable[Mapping]) -> dict:
    """Totals for k in `credit = k * qty * p * (1-p)`.

    REFUSES a mixed-venue set. Pooling PMUS and Gemini credits yields a k
    that describes neither -- and reads as a plausible number, which is what
    makes it dangerous."""
    rows = list(rows)
    venues = {validate_row(r) for r in rows}
    if len(venues) > 1:
        raise MixedVenues(
            f"refusing a coefficient across {sorted(venues)}: "
            f"a pooled k is not a property of any venue")
    qty = sum(D(str(r.get("qty") or 0)) for r in rows)
    credited = sum(-D(str(r.get("our_commission") or 0)) for r in rows
                   if r.get("credited_to_us"))
    return {"venue": venues.pop() if venues else None,
            "rows": len(rows), "contracts": str(qty),
            "credited_usd": str(credited),
            "gross_per_contract": str(credited / qty) if qty else None}


def ingest_gemini_admin_credits(source: Optional[str] = None) -> list:
    """Gemini 'Predictions maker rebate' AdminCredit transfers on GEMI-*
    instruments.

    Requires the real transfer records. Raises rather than returning an
    empty list, because "no Gemini credits" and "could not read Gemini
    credits" are different facts and only one of them is a measurement."""
    path = source or os.getenv("GEMINI_CREDITS_JSON", "")
    if not path or not os.path.exists(path):
        raise SourceUnavailable(
            "Gemini AdminCredit records not reachable: set GEMINI_CREDITS_JSON "
            "to an export of the 'Predictions maker rebate' transfers, or run "
            "where GEMINI_API_KEY/GEMINI_API_SECRET are present. Gemini's "
            "published example table is NOT a substitute -- it is their "
            "illustration, not our executions.")
    with open(path) as fh:
        raw = json.load(fh)
    out = []
    for r in (raw.get("transfers") if isinstance(raw, dict) else raw) or []:
        out.append({**r, "venue": GEMINI,
                    "raw_source_label": r.get("type") or r.get("description"),
                    "raw_source_id": r.get("eid") or r.get("id")})
    for r in out:
        validate_row(r)
    return out
