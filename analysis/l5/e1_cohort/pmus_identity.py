"""PMUS-NATIVE market identity: the venue's own name for a BTC15m window.

WHY THIS EXISTS. On 2026-10-07T20:39:36Z E1 sent
`btc-updown-15m-1791405000` to `api.polymarket.us` and the venue answered,
verbatim:

    HTTP 400 {"code":3,"message":"Market not found:
              btc-updown-15m-1791405000","details":[]}

That slug is `src.l5_market_binding.SERIES_SLUG`, resolved against
`gamma-api.polymarket.com`. Read that module's own header: it exists to
attribute L5's OBSERVED RESEARCH ACTIONS on Polymarket.com. It is a
research resolver for a different venue, and E1 was using it as its order
identity. E1 selected on Polymarket.com, streamed Polymarket.com's CLOB,
and ordered on Polymarket US.

PMUS names the same fifteen minutes `cpc-btc-updown-15m-YYYY-MM-DD-HHMMz`.
This is not inferred: `src/l5_pmus_transport.py` documents a side-by-side
probe on `cpc-btc-updown-15m-2026-09-24-0945z`, a window that TRADED
49,678 SHARES, and XR2W -- E1's own parent, the probe that actually
submitted -- is pinned to `cpc-btc-updown-15m-2026-10-06-2300z` in
`analysis/l5/live_exec5/fixture_a5.py`.

THERE IS NO TRANSLATION HERE, and there must never be. A Gamma slug and a
PMUS slug are two venues' own identifiers; mapping one to the other would
be a guess wearing the costume of a lookup. This module derives the PMUS
slug from the WINDOW CLOCK, which is the only thing the two venues
genuinely share, and then asks PMUS whether that market exists.

WHY `slug_for` IS COPIED HERE RATHER THAN IMPORTED. The canonical builder
lives in `src.l5_pmus_auth_book`, and importing that module drags
EIGHTEEN further first-party files into E1's image -- including
`src.pmus_q1_executor`, a second, independently-armed order-capable
executor with its own `place_limit_buy` and `run_campaign`. Putting a
second execution stack inside E1's container is a safety regression, so
the four-line formula is reproduced and
`test_e1_pmus_identity.py::test_slug_for_matches_the_canonical_builder`
asserts it agrees with the proven function across thousands of windows.
The proven function stays the source of truth; only the dependency is
avoided. `l5_pmus_transport` IS imported -- its closure adds four files
and no executor.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any, Dict, Mapping, Optional

from src import l5_pmus_transport as PT
from src import pmus_auth as A

#: the BTC15m grid, in seconds. The same 900 the rest of E1 uses.
WINDOW_S = 900

#: PMUS's own slug shape, reused from the proven transport rather than
#: re-expressed, so a drift in one is a failure in both.
BTC15M_SLUG_RE = PT.BTC15M_SLUG_RE

#: the venue hosts. GATEWAY serves the public metadata and books;
#: API is the account-scoped order host.
GATEWAY = A.GATEWAY
API = A.API

#: hosts that must never appear in this path. E1's identity defect was
#: exactly a Polymarket.com host answering for a Polymarket US order.
FORBIDDEN_HOSTS = ("gamma-api.polymarket.com",
                   "ws-subscriptions-clob.polymarket.com",
                   "clob.polymarket.com")


def window_start(now: Optional[float] = None) -> int:
    t = time.time() if now is None else now
    return int(t // WINDOW_S) * WINDOW_S


def slug_for(ws: int) -> str:
    """The PMUS slug for the window starting at `ws`.

    Byte-identical to `src.l5_pmus_auth_book.slug_for`, asserted by test.
    """
    d = dt.datetime.fromtimestamp(ws, dt.timezone.utc)
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        d.strftime("%Y-%m-%d"), d.hour, d.minute)


def slug_start(slug: str) -> Optional[int]:
    """Inverse of `slug_for`, or None. The inverse is what lets a decision
    prove the book it used belongs to the window it ordered."""
    try:
        d = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz")
    except (ValueError, TypeError):
        return None
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp())


def is_pmus_btc15m_slug(slug: Any) -> bool:
    return bool(slug) and bool(BTC15M_SLUG_RE.match(str(slug)))


def assert_pmus_slug(slug: Any) -> str:
    """The one gate every PMUS order identity passes through."""
    s = str(slug or "")
    if not is_pmus_btc15m_slug(s):
        raise ValueError(
            "not a PMUS BTC15m slug: %r. PMUS names these windows "
            "cpc-btc-updown-15m-YYYY-MM-DD-HHMMz; a Polymarket.com slug "
            "such as btc-updown-15m-<epoch> is a DIFFERENT VENUE'S "
            "identifier and the venue answers 'Market not found'." % (s,))
    return s


def read_only_transport() -> Any:
    """`pmus_auth.ReadOnlyTransport`: GET only, by construction.

    It cannot be talked into a mutation -- the refusal is in the transport,
    not in a caller's flag -- which is what makes it the right thing for
    an identity proof.
    """
    return A.ReadOnlyTransport()


def resolve(slug: str, http: Any = None) -> Dict[str, Any]:
    """PMUS's authoritative metadata row for this slug.

    DELEGATED to `l5_pmus_transport.resolve_market`, which already knows
    that `/v1/market/slug/{slug}` 404s for BTC15m while
    `/v1/events/slug/{event}` carries it, and which matches on the exact
    slug rather than trusting a query filter the gateway silently ignores.
    Raises LookupError when no host serves it, so identity fails closed.
    """
    return PT.resolve_market(http or read_only_transport(),
                             assert_pmus_slug(slug), base=GATEWAY)


def bbo(slug: str, http: Any = None) -> Dict[str, Any]:
    """PMUS's own top of book, FROM THE PUBLIC GATEWAY. Identity and
    cross-check only -- never the decision feed, never a timing
    authority, and NEVER a price.

    THE GATEWAY BOOK IS A ~30-SECOND CACHE. This is measured, not
    supposed: `pmus_ws_evidence_recorder` records that inferring trade
    direction against the gateway book held 46.2% forward and 35.3%
    inverted over 184 resolvable rows, with 18.5% of prints strictly
    inside the quoted spread where neither reading can apply -- its own
    conclusion being "that is not a close call between two hypotheses,
    it is the wrong book."

    So a difference between this and AuthBook is EXPECTED and says
    nothing about either parser. I compared the two on 2026-10-07 and
    reported a 12-tick "genuine disagreement"; it was a live
    authenticated feed against a stale public cache, and the verdict was
    withdrawn.
    """
    t = http or read_only_transport()
    return t.request("GET", "/v1/markets/%s/bbo" % assert_pmus_slug(slug),
                     None, GATEWAY) or {}


def book(slug: str, http: Any = None) -> Dict[str, Any]:
    """PMUS's own depth, FROM THE PUBLIC GATEWAY. Same ~30-second cache
    caveat as `bbo` above: identity and cross-check only, never a price
    and never a timing authority."""
    t = http or read_only_transport()
    return t.request("GET", "/v1/markets/%s/book" % assert_pmus_slug(slug),
                     None, GATEWAY) or {}


def outcome_sides(market: Mapping[str, Any]) -> Dict[str, Any]:
    """The venue's own outcome vocabulary for this market.

    PMUS identifies a side by its own outcome vocabulary, NOT by a CTF
    token id. The vocabulary is ASKED OF THE PROVEN SERIALIZER rather than
    restated here: `src.l5_order_body.outcome_side` is what has actually
    traded on this venue, and
    `test_no_file_in_this_package_contains_a_duplicate_serializer` forbids
    this package from naming the wire constants -- restating them is the
    first step of the duplicate-serializer bug that caused the Attempt-5
    HTTP 400 twice.
    """
    from src import l5_order_body as OB
    return {"up_is": OB.outcome_side("UP"),
            "down_is": OB.outcome_side("DOWN"),
            "venue_outcomes": market.get("outcomes"),
            "tick": market.get("orderPriceMinTickSize"),
            "min_qty": market.get("minimumTradeQty"),
            "slug": market.get("slug"),
            "status": market.get("status"),
            "active": market.get("active"),
            "closed": market.get("closed"),
            "IDENTITY_IS_SLUG_PLUS_OUTCOME_SIDE": True,
            "CTF_TOKEN_IDS_ARE_NOT_PMUS_IDENTITY": True}
