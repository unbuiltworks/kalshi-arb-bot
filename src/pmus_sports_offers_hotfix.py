"""Read-only PMUS sports collector hotfix for the venue's sell-side schema.

Polymarket US /v1/markets/{slug}/book serves sell depth under `offers`, not
`asks`.  The base collector historically looked for `asks`, which made real
sell depth appear absent and forced ask_side_served_pct to zero.

This module changes ONLY the normalization hook used by the already-deployed
all-live-sports collector.  It adds no auth, no order path, no mutation, and
changes no readiness/economic threshold.  Missing/empty `offers` remains
explicitly missing; liquidity is never fabricated.
"""
from __future__ import annotations

from typing import Any, Mapping

from src import pmus_sports_collector as col


def bbo_book_row_offers(slug: str, meta: Mapping[str, Any],
                        bbo_md: Mapping[str, Any],
                        book_md: Mapping[str, Any]) -> dict:
    bids = book_md.get("bids") or []
    offers = book_md.get("offers") or []

    def val(key: str):
        value = bbo_md.get(key)
        return value.get("value") if isinstance(value, Mapping) else value

    return {
        "kind": "bbo_book",
        "t": col._utcnow().isoformat(),
        "slug": slug,
        "league": meta.get("league"),
        "why": meta.get("why"),
        "event_state": meta.get("event_state"),
        "bid": val("bestBid"),
        "bid_touch_qty": bids[0].get("qty") if bids else None,
        "ask": val("bestAsk"),
        "ask_touch_qty": offers[0].get("qty") if offers else None,
        "ask_book_missing": not offers,
        "last_trade_px": val("lastTradePx"),
        "shares_traded_cum": bbo_md.get("sharesTraded"),
        "open_interest": bbo_md.get("openInterest"),
        "market_state": bbo_md.get("state"),
        "source": col.SOURCE,
    }


def install() -> None:
    # One narrow runtime substitution. The live runner calls
    # col.bbo_book_row(...) for every observation, so all discovery,
    # persistence, rate limiting, status, and safety logic stays unchanged.
    col.bbo_book_row = bbo_book_row_offers


def main() -> int:
    install()
    from src import pmus_sports_live_collector as live
    return live.main()


if __name__ == "__main__":
    raise SystemExit(main())
