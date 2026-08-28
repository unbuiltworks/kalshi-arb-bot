"""Guarded read-only entrypoint for strict MLB prop snapshot v4.

This composes the existing v4 scanner with fail-closed Kalshi book integrity
checks before any <=$1/<= $5 paper economics are calculated.  It intentionally
contains no authentication or order-entry capability.
"""
from __future__ import annotations

import asyncio

from src import strict_mlb_prop_snapshot as base
from src.mlb_kalshi_book_integrity_guard import audit_kalshi_top_book_payload

_ORIGINAL_KALSHI_TOP_BOOK = base.kalshi_top_book


def _guarded_kalshi_top_book(payload):
    audit = audit_kalshi_top_book_payload(payload)
    if audit["verified"] is not True:
        return None
    return _ORIGINAL_KALSHI_TOP_BOOK(payload)


# v4 resolves ``base.kalshi_top_book`` at call time, so install the read-only
# fail-closed parser before importing the scanner entrypoint.
base.kalshi_top_book = _guarded_kalshi_top_book

from src import strict_mlb_prop_snapshot_v4 as v4  # noqa: E402


run_once = v4.run_once


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
