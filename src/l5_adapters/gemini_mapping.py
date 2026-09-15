"""Map a Polymarket L5 window onto its Gemini contract -- or fail honestly.

THE MAPPING IS ARITHMETIC, AND IT IS EXACT. Both venues key a crypto up/down
window on the same real-world instant:

    Polymarket   btc-updown-15m-1789470900     slug carries the window START
    expiry       1789470900 + 900 = 1789471800
    Gemini       GEMI-BTC15M2609151250-UP      ticker carries the window EXPIRY

So the two are the same market when, and only when, asset and expiry agree.
There is no fuzzy matching and no name similarity anywhere here: a mapping that
guesses is worse than one that declines, because a wrong mapping routes real
capital into the wrong contract.

VERIFIED AGAINST THE VENUE, NOT AGAINST AN INDEX. Measured 2026-09-15:
`/v1/prediction-markets/events` listed BTC05M but NOT BTC15M, while
`GEMI-BTC15M2609151245-UP` returned a live two-sided book (0.62 / 0.70). The
events index is therefore incomplete, and the only reliable existence test is
to construct the symbol and ask for its book. `resolve()` does exactly that,
and a venue that answers "no" produces a `no_market` result rather than a
guess.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

# btc-updown-15m-1789470900 / eth-updown-5m-...
WINDOW_RE = re.compile(r"^([a-z]+)-updown-(\d+)(m)-(\d+)$", re.I)
DURATION_S = {"5m": 300, "15m": 900}
# Gemini writes the duration into the ticker body, zero-padded to two digits.
_GEMINI_DUR = {300: "05M", 900: "15M"}
SUPPORTED_ASSETS = {"btc"}          # only BTC is verified; ETH is NOT assumed


def parse_window(market_key: str) -> Optional[Tuple[str, int, int]]:
    """(asset, duration_s, start_epoch) from a Polymarket window slug."""
    m = WINDOW_RE.match(str(market_key or ""))
    if not m:
        return None
    asset, n, unit, start = m.group(1).lower(), m.group(2), m.group(3), m.group(4)
    dur = DURATION_S.get(f"{n}{unit.lower()}")
    if dur is None:
        return None
    return asset, dur, int(start)


def gemini_symbol(market_key: str) -> Optional[dict]:
    """The candidate Gemini symbol for a Polymarket window, or None.

    None means "we cannot even name a candidate" -- an unsupported asset or an
    unparseable slug -- which is a different fact from "the venue does not list
    it", and the two are kept separate on purpose.
    """
    parsed = parse_window(market_key)
    if parsed is None:
        return None
    asset, dur, start = parsed
    if asset not in SUPPORTED_ASSETS:
        return None
    body = _GEMINI_DUR.get(dur)
    if body is None:
        return None
    expiry = start + dur
    dt = datetime.fromtimestamp(expiry, timezone.utc)
    ticker = f"{asset.upper()}{body}{dt:%y%m%d%H%M}"
    return {"symbol": f"GEMI-{ticker}-UP", "event_ticker": ticker,
            "expiry_epoch": expiry, "asset": asset, "duration_s": dur,
            "basis": "exact: same asset, same expiry instant"}


def resolve(market_key: str, fetch_book: Callable[[str], Any], *,
            want_side: Optional[str] = None
            ) -> Tuple[Optional[dict], str, Optional[str]]:
    """(mapping, confidence, reason). Asks the venue; never guesses.

    confidence is one of:
      exact    the constructed symbol returned a book we can price on
      none     no candidate, or the venue does not list / does not quote it

    An empty book is never a mapping success. A ONE-SIDED book depends on which
    side we need, and `want_side` says: buying `Up` needs asks, buying `Down`
    means selling Up and so needs bids. Measured 2026-09-15 13:04Z,
    GEMI-BTC05M2609151305-UP had FOUR bid levels and no asks at all -- Down was
    freely executable there while Up was not, and requiring two-sidedness
    reported that contract as absent.

    With `want_side` omitted the stricter two-sided test is kept, because a
    caller that has not said which side it needs must not be handed a book it
    may be unable to trade on.
    """
    cand = gemini_symbol(market_key)
    if cand is None:
        return None, "none", (
            f"no Gemini candidate for {market_key!r}: unsupported asset or "
            f"unrecognised window format")
    try:
        book = fetch_book(cand["symbol"])
    except Exception as exc:                                  # noqa: BLE001
        return None, "none", (
            f"{cand['symbol']}: venue could not be asked "
            f"({type(exc).__name__}: {exc}) -- unknown, NOT absent")
    if not isinstance(book, dict):
        return None, "none", f"{cand['symbol']}: unreadable book response"
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids and not asks:
        return None, "none", (
            f"{cand['symbol']}: exists but quotes nothing at all "
            f"(bids=0, asks=0); unpriceable is not mapped")
    need = {"Up": "asks", "Down": "bids"}.get(str(want_side or ""))
    if need is None:
        if not bids or not asks:
            return None, "none", (
                f"{cand['symbol']}: exists but is not two-sided "
                f"(bids={len(bids)}, asks={len(asks)}); unpriceable is not mapped")
    elif not (asks if need == "asks" else bids):
        return None, "none", (
            f"{cand['symbol']}: exists but quotes no {need}, so {want_side} "
            f"cannot be bought here (bids={len(bids)}, asks={len(asks)})")
    # a side we did not require may legitimately be empty, so neither top is
    # assumed to exist: an IndexError here would turn a priceable contract into
    # an unreachable one.
    return ({**cand,
             "top_bid": (bids[0].get("price") if bids else None),
             "top_ask": (asks[0].get("price") if asks else None),
             "n_bid_levels": len(bids), "n_ask_levels": len(asks)},
            "exact", None)
