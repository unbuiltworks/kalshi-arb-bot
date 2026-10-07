"""Select and PIN exactly one PMUS BTC15m window. PMUS identity only.

The old selector resolved `btc-updown-15m-<epoch>` against
`gamma-api.polymarket.com` and pinned two CTF token ids. E1 then ordered
that slug on `api.polymarket.us`, which answered "Market not found". This
one asks PMUS.

THE PIN CARRIES NO TOKEN IDS, because PMUS identity is not a token. A PMUS
order names a `marketSlug` and an `outcomeSide`; the 77-digit CTF ids
belong to Polymarket.com's conditional-token framework and mean nothing
here. Removing them from the pin is the point, not an omission.

Identity is proven by the venue, not assumed from the clock: the slug is
built from the window grid, and then `resolve_market` must return a
metadata row whose OWN slug equals the one we asked for. A window PMUS
will not confirm is a window E1 does not trade.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from analysis.l5.e1_cohort import pmus_identity as PI

#: PMUS's own series name for these windows.
SERIES = "cpc-btc-updown-15m"
WINDOW_S = PI.WINDOW_S


class WindowRefused(Exception):
    """This window is not tradeable and no order may be built from it."""


def window_start_for(ts: float) -> int:
    return PI.window_start(ts)


def next_window_starts(after: float, count: int) -> List[int]:
    ws = window_start_for(after)
    return [ws + k * WINDOW_S for k in range(1, int(count) + 1)]


def select_window(window_start: int, *, http: Any = None,
                  resolver: Any = None) -> Dict[str, Any]:
    """Resolve and PIN one PMUS window, or refuse with a reason."""
    ws = int(window_start)
    if ws % WINDOW_S != 0:
        raise WindowRefused("window start %d is off the %ds venue grid"
                            % (ws, WINDOW_S))
    slug = PI.slug_for(ws)
    try:
        PI.assert_pmus_slug(slug)
    except ValueError as exc:
        raise WindowRefused(str(exc)) from None

    resolve = resolver or PI.resolve
    try:
        market = resolve(slug, http) or {}
    except Exception as exc:                                   # noqa: BLE001
        raise WindowRefused(
            "PMUS would not confirm the identity of %s: %s: %s. A window "
            "the venue will not identify is a window we do not trade."
            % (slug, type(exc).__name__, exc)) from None

    got = str(market.get("slug") or "")
    if got != slug:
        raise WindowRefused(
            "PMUS returned metadata for %r when asked about %r; the "
            "resolver and the grid disagree, which is an identity fault"
            % (got, slug))
    if market.get("closed") is True:
        raise WindowRefused("%s is closed" % slug)
    if market.get("active") is False:
        raise WindowRefused("%s is not active" % slug)

    if PI.slug_start(slug) != ws:
        raise WindowRefused(
            "%s does not round-trip to window %d" % (slug, ws))

    outcomes = PI.outcome_sides(market)
    return {
        "series": SERIES,
        "venue": "polymarket_us",
        "slug": slug,
        "market_id": market.get("id") or market.get("marketId") or slug,
        "window_start": ws,
        "window_end": ws + WINDOW_S,
        "outcome_up": outcomes["up_is"],
        "outcome_down": outcomes["down_is"],
        "tick": market.get("orderPriceMinTickSize"),
        "min_qty": market.get("minimumTradeQty"),
        "status": market.get("status"),
        "question": market.get("question") or market.get("title"),
        "identity": "slug_plus_outcome_side",
        "token_ids_in_pin": False,
        "ctf_token_ids_are_not_pmus_identity": True,
        "selection_provenance": {
            "selector": "analysis.l5.e1_cohort.pmus_selector:select_window",
            "resolver": "src.l5_pmus_transport:resolve_market",
            "slug_builder": "analysis.l5.e1_cohort.pmus_identity:slug_for",
            "series_pinned": SERIES,
            "window_grid_s": WINDOW_S,
            "gamma_used": False,
            "clob_ws_used": False,
            "venue_confirmed_the_slug": True,
            "selected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime()),
            "selected_unix": time.time(),
        },
        "LABEL": "SELECTED AND PINNED -- selection is not authorization",
    }


def is_selected_slug(slug: str, pin: Optional[Dict[str, Any]]) -> bool:
    """Exact identity against a PIN. No prefix match, no normalisation."""
    return bool(pin) and str(slug) == str((pin or {}).get("slug"))
