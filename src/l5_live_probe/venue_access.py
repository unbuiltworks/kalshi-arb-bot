"""Does the market we propose to trade exist on the venue we can authenticate to?

THE HOLE THIS CLOSES. The BTC15m windows the shadow reads come from Polymarket
INTERNATIONAL -- gamma-api.polymarket.com / clob.polymarket.com. Our account and
credentials are Polymarket US -- gateway.polymarket.us / api.polymarket.us. The
shadow's own market rows are labelled `venue: "PMUS"` regardless, so a preflight
that validated the market from one source and the credentials from the other
reported ten of eleven checks PASS for a probe that cannot place a single order.
Market eligibility and credential presence are both true; they are simply true
about DIFFERENT VENUES, and nothing was comparing them.

MEASURED, NOT ASSUMED, AND RE-MEASURED. src/l5_venue_access_reality.py found
this on 2026-09-09 over 25,000 markets. Re-run 2026-09-16 over 30,000 open+active
PMUS markets in 60 pages: slugs containing 'updown' = 0, and crypto markets
ending within 24h = 0. All 54 PMUS BTC markets are long-dated price targets
(cpc-btc-150k-*, cpc-btc-hitprice-*) expiring 2027-01-14.

The enumeration is BOUNDED, so a zero here is "not found in N pages", never a
proof of absence from the venue -- the check reports the bound alongside the
count and degrades to UNDETERMINED if enumeration fails.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

LABEL = "VENUE_ACCESS/credentialed_venue"

PMUS_GATEWAY = "https://gateway.polymarket.us"
INTL_CLOB = "https://clob.polymarket.com"

# Which venue each credential set authenticates to.
CREDENTIALED_VENUE = "POLYMARKET_US"
VENUE_OF_SOURCE = {
    "gamma-api.polymarket.com": "POLYMARKET_INTL",
    "clob.polymarket.com": "POLYMARKET_INTL",
    "gateway.polymarket.us": "POLYMARKET_US",
    "api.polymarket.us": "POLYMARKET_US",
}

PAGE = 500
MAX_PAGES = 60


def _get(url: str, timeout: int = 35) -> Optional[Any]:
    try:
        r = subprocess.run(["curl", "-sS", "--max-time", str(timeout), url],
                           capture_output=True, text=True, timeout=timeout + 10)
        return json.loads(r.stdout)
    except Exception:                                         # noqa: BLE001
        return None


def venue_of(terms_source: Optional[str]) -> Optional[str]:
    """Which venue a market row actually came from, by its source URL -- not by
    the `venue` label it carries, which says PMUS for international rows."""
    if not terms_source:
        return None
    for host, venue in VENUE_OF_SOURCE.items():
        if host in terms_source:
            return venue
    return None


def enumerate_pmus(max_pages: int = MAX_PAGES) -> Dict[str, Any]:
    """Bounded, unauthenticated, GET-only enumeration of open+active PMUS."""
    rows: List[dict] = []
    pages = 0
    for p in range(max_pages):
        d = _get(f"{PMUS_GATEWAY}/v1/markets?active=true&closed=false"
                 f"&limit={PAGE}&offset={p * PAGE}")
        ms = d.get("markets") if isinstance(d, dict) else None
        if not isinstance(ms, list) or not ms:
            break
        rows.extend(ms)
        pages += 1
        if len(ms) < PAGE:
            break
    return {"markets": rows, "pages": pages, "bounded_at": max_pages,
            "exhausted": pages < max_pages}


def tradeable_on_credentialed_venue(slug_fragment: str,
                                    enumerated: Optional[Dict[str, Any]] = None
                                    ) -> Dict[str, Any]:
    """Is anything matching this fragment listed on the venue we can sign for?"""
    e = enumerated if enumerated is not None else enumerate_pmus()
    rows = e.get("markets") or []
    if not rows:
        return {"label": LABEL, "state": "UNDETERMINED",
                "why": "PMUS enumeration returned nothing; absence cannot be "
                       "distinguished from an unreachable gateway",
                "pages": e.get("pages", 0)}
    hits = [m for m in rows
            if slug_fragment.lower() in str(m.get("slug", "")).lower()]
    return {
        "label": LABEL,
        "state": "TRADEABLE" if hits else "NOT_ON_CREDENTIALED_VENUE",
        "credentialed_venue": CREDENTIALED_VENUE,
        "slug_fragment": slug_fragment,
        "matches": len(hits),
        "examples": [m.get("slug") for m in hits[:5]],
        "enumerated": len(rows), "pages": e.get("pages"),
        "exhausted": e.get("exhausted"),
        "why": ("a bounded enumeration: 0 matches over the pages fetched means "
                "NOT FOUND within that bound, which is why the bound is "
                "reported beside the count"),
    }


def consistency(market: Optional[Dict[str, Any]],
                access: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The check the preflight runs: the market's real venue must be the venue
    the credentials authenticate to, AND it must be listed there."""
    if not market:
        return {"ok": None, "why": "no market supplied"}
    src_venue = venue_of(market.get("terms_source"))
    if src_venue is None:
        return {"ok": None, "why": "cannot attribute the market to a venue "
                                   "from its source"}
    if src_venue != CREDENTIALED_VENUE:
        return {"ok": False,
                "why": (f"the market was read from {src_venue} but our "
                        f"credentials authenticate to {CREDENTIALED_VENUE}; "
                        f"these are different venues and the `venue` label on "
                        f"the row says PMUS for both"),
                "market_venue": src_venue,
                "credentialed_venue": CREDENTIALED_VENUE}
    if access and access.get("state") == "NOT_ON_CREDENTIALED_VENUE":
        return {"ok": False,
                "why": (f"no market matching "
                        f"{access.get('slug_fragment')!r} is listed on "
                        f"{CREDENTIALED_VENUE} "
                        f"({access.get('enumerated')} enumerated)"),
                "access": access}
    if access and access.get("state") == "UNDETERMINED":
        return {"ok": None, "why": access.get("why"), "access": access}
    return {"ok": True, "market_venue": src_venue, "access": access}
