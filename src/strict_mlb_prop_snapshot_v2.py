"""Read-only MLB prop snapshot using Polymarket's proven direct market inventory.

The league events endpoint does not currently include player props in its nested
markets even though the Polymarket US app exposes them.  The general /v1/markets
inventory does expose those markets.  Reuse the existing strict identity, schedule,
book, fee, and rule gates, changing discovery only.

No credentials or order-entry path are added here.
"""
from __future__ import annotations

from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src.sports_universe_inventory import fetch_poly_sports


def _is_mlb_direct_prop(row: dict[str, Any]) -> bool:
    family = str(row.get("sportsMarketType") or "")
    if family not in base.FAMILY_SERIES:
        return False

    slug = str(row.get("slug") or "").lower()
    if "-mlb-" in slug:
        return True

    league = str(
        row.get("league")
        or row.get("sportsLeague")
        or row.get("subcategory")
        or ""
    ).strip().lower()
    return league == "mlb"


async def _fetch_poly_mlb_direct(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    rows = await fetch_poly_sports(http)
    return [dict(row) for row in rows if _is_mlb_direct_prop(row)]


# base.run_once resolves this module global at runtime, so replacing discovery keeps
# every existing strict matcher/economics/rule gate intact.
base._fetch_poly_mlb = _fetch_poly_mlb_direct


if __name__ == "__main__":
    base.main()
