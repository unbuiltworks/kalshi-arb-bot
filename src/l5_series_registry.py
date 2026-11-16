"""THE registry of up/down market series. One table, one place to add one.

WHY THIS EXISTS. "cpc-btc-updown-5m-" was written literally in 34 files --
the campaign approval, the authorization claim, the origin registry, the
transport's preflight regex and the runtime config check among them. The
engine is required to stay market-agnostic (analysis/l5/CLAUDE.md: "Do not
assume L5 transfer is crypto-only"), and it was not: adding one asset meant
finding every literal and hoping none was missed.

WHAT IT CHANGES TODAY: nothing. The two rows below are exactly the two
series that already existed, with exactly their existing window lengths,
strategy ids and client-order-id prefixes. Every constant that used to be a
literal now derives from this table, so the behaviour is identical by
construction rather than by inspection.

HOW TO ADD AN ASSET. Add ONE row. It needs:

  prefix       the venue's series prefix, e.g. "cpc-eth-updown-5m-"
  asset        "eth"
  horizon_s    300.0
  strategy     a NEW strategy id, e.g. "ETH5M"
  coid_prefix  a NEW, UNIQUE client-order-id prefix, e.g. "e5m-"

Then give it caps in `l5_campaign_approval` (SERIES_MAX_TOTAL_EXPOSURE_USD,
SERIES_MAX_DAILY_LOSS_USD, SERIES_MAX_WINDOWS). That is the whole change.

WHY ADDING A ROW IS NOT THE SAME AS ARMING ONE. A row makes a series
RECOGNISABLE, never tradeable:

  * the strategy id must be in `l5_origin.ORIGINS` before anything it
    places can be attributed, and an unattributable order is a halt;
  * the coid prefix must be UNIQUE, or two strategies become
    indistinguishable in the ownership registry -- the exact confusion the
    watchdog latches on. `_check()` refuses a duplicate at import;
  * with no entry in the per-series cap tables, the campaign approval
    falls back to the GENERAL ceiling, which is tighter than any per-series
    grant, so a forgotten cap fails closed rather than open;
  * the owner still has to approve a campaign for it and arm the service.

So this module is a naming authority, not a permission.
"""
from __future__ import annotations

import re
from typing import Dict, NamedTuple, Optional, Tuple


class Series(NamedTuple):
    prefix: str
    asset: str
    horizon_s: float
    strategy: str
    coid_prefix: str
    #: THE VENUE'S OWN INDEX SYMBOL for this asset, as `GET
    #: /v1/market/slug/{slug}` reports it in
    #: `assetPriceTerms.indexSymbol`. It is NOT derivable from the asset:
    #: BTC is "BRTI" while every other asset is "<TICKER>USD_RTI", the
    #: same irregularity the slug prefixes have.
    #:
    #: VERIFIED AGAINST THE VENUE 2026-11-16, 8 consecutive 5-minute
    #: windows per asset, 32 of 32 exact: the 1-second bucket ending at
    #: `windowStart`, with `tickCount` 60, equals `priceToBeat` to the
    #: cent. So all four share one settlement convention -- a 60-second
    #: average of this index, sampled at the window boundaries -- and
    #: that was checked per asset rather than assumed from BTC.
    index_symbol: str

    @property
    def slug_re(self) -> "re.Pattern[str]":
        """`<prefix>YYYY-MM-DD-HHMMz`, anchored. Built from the prefix so a
        new row cannot disagree with its own pattern."""
        return re.compile("^" + re.escape(self.prefix)
                          + r"\d{4}-\d{2}-\d{2}-\d{4}z$")

    @property
    def venue_marker(self) -> str:
        """The series as it appears INSIDE a market slug: the prefix with
        its namespace and trailing dash removed.

        The transport's `market_series_is_*` gate asks whether this is a
        SUBSTRING of the market's own slug, so it must be the part the slug
        actually repeats:

            cpc-btc-updown-5m-      -> btc-updown-5m   in cpc-BTC-UPDOWN-5M-...
            cpceth-eth-updown-5m-   -> eth-updown-5m   in cpceth-ETH-UPDOWN-5M-...

        Stripping a literal "cpc-" was right only while BTC was the only
        asset; every other asset carries its ticker in the namespace too,
        so the rule is "up to and including the first hyphen".
        """
        return self.prefix.split("-", 1)[1].rstrip("-")


#: EVERY series this engine knows. Adding an asset is adding a row here.
#:
#: THE PREFIXES ARE IRREGULAR, and this is the whole reason the table
#: exists. BTC is "cpc-btc-updown-5m-"; every other asset embeds its own
#: ticker in the namespace too: "cpceth-eth-updown-5m-". Confirmed against
#: the venue 2026-11-15T22:06Z, all four MARKET_STATUS_OPEN on the same
#: window. No rule extrapolated from BTC produces the others -- 64 guessed
#: variants all 404'd -- so each one is written down from the venue's own
#: answer rather than derived.
REGISTRY: Tuple[Series, ...] = (
    Series("cpc-btc-updown-15m-", "btc", 900.0, "BTC15M", "l5f-", "BRTI"),
    Series("cpc-btc-updown-5m-", "btc", 300.0, "BTC5M", "b5m-", "BRTI"),
    Series("cpceth-eth-updown-5m-", "eth", 300.0, "ETH5M", "e5m-",
           "ETHUSD_RTI"),
    Series("cpcsol-sol-updown-5m-", "sol", 300.0, "SOL5M", "s5m-",
           "SOLUSD_RTI"),
    Series("cpcxrp-xrp-updown-5m-", "xrp", 300.0, "XRP5M", "x5m-",
           "XRPUSD_RTI"),
)

def _check(rows: Tuple[Series, ...]) -> None:
    """Refuse at import on the two confusions that would be dangerous."""
    pats = [r.prefix for r in rows]
    if len(set(pats)) != len(pats):
        raise ValueError("duplicate series prefix in REGISTRY")
    coids = [r.coid_prefix for r in rows]
    if len(set(coids)) != len(coids):
        raise ValueError(
            "duplicate client-order-id prefix in REGISTRY: two strategies "
            "would be indistinguishable in the ownership registry")
    strats = [r.strategy for r in rows]
    if len(set(strats)) != len(strats):
        raise ValueError("duplicate strategy id in REGISTRY")
    for r in rows:
        if not re.match(r"^cpc[a-z0-9]{0,6}-[a-z0-9]{2,10}-updown-\d{1,3}m-$",
                        r.prefix):
            raise ValueError("series prefix not in the venue's shape: %r"
                             % (r.prefix,))
        if r.horizon_s <= 0:
            raise ValueError("series horizon must be positive: %r" % (r,))
        #: AN INDEX SYMBOL IS NOT OPTIONAL. Without it the probability
        #: engine cannot tell whether the strike it fetched belongs to
        #: the series it asked about, which is the check that caught
        #: ETH/SOL/XRP as UNEXPECTED_INDEX rather than mispricing them.
        if not re.match(r"^[A-Z][A-Z0-9_]{2,19}$", r.index_symbol or ""):
            raise ValueError("series index symbol not in the venue's "
                             "shape: %r" % (r.index_symbol,))
    #: TWO ASSETS MAY SHARE AN INDEX (btc 5m and 15m both read BRTI), but
    #: one asset must not carry two, which would mean the table disagrees
    #: with itself about what the venue publishes.
    by_asset: Dict[str, str] = {}
    for r in rows:
        if by_asset.setdefault(r.asset, r.index_symbol) != r.index_symbol:
            raise ValueError("asset %r carries two index symbols: %r and %r"
                             % (r.asset, by_asset[r.asset], r.index_symbol))


_check(REGISTRY)

BY_PREFIX: Dict[str, Series] = {r.prefix: r for r in REGISTRY}
BY_STRATEGY: Dict[str, Series] = {r.strategy: r for r in REGISTRY}


def for_slug(slug: str) -> Optional[Series]:
    """The series a market slug belongs to, or None.

    The slug must match the series' full anchored pattern, not merely start
    with its prefix: "cpc-btc-updown-5m-garbage" belongs to nothing.
    """
    s = str(slug or "").strip()
    for r in REGISTRY:
        if r.slug_re.match(s):
            return r
    return None


def prefixes(horizon_s: Optional[float] = None) -> Tuple[str, ...]:
    """Every series prefix, or only those of one horizon."""
    return tuple(r.prefix for r in REGISTRY
                 if horizon_s is None or r.horizon_s == horizon_s)
