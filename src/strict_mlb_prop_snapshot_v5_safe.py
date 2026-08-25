"""Guarded read-only entrypoint for the participation-aware MLB prop v5 scanner.

Installs the fail-closed Kalshi top-book integrity audit before importing v5, so
all v5 <=$1/<= $5 paper economics inherit the same crossed/out-of-range/depth
protection as the guarded v4 path. No authentication or order-entry capability.
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


base.kalshi_top_book = _guarded_kalshi_top_book

from src import strict_mlb_prop_snapshot_v5 as v5  # noqa: E402

run_once = v5.run_once


def main() -> None:
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
