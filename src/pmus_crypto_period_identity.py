"""Research-only market identity validation. Does not authorize orders.

Identity of a crypto up/down market, kept apart from every trading
allowlist: `identify` says what a slug IS, never whether it may be traded
(`pilot_asset` / `reward_window` are facts about the identity; the lane's
own table in src/pmus_crypto_rewards.py decides what is quoted).

Identifiers verified against the venue on 2026-11-28 (public
/v1/market/slug/<slug>, assetPriceTerms):

  cpc-btc-updown-1h-2026-11-28-2000z     BRTI        20:00Z -> 21:00Z
  cpceth-eth-updown-1h-...               ETHUSD_RTI
  cpcsol-sol-updown-1h-...               SOLUSD_RTI
  cpcxrp-xrp-updown-1h-...               XRPUSD_RTI
  cpceth-eth-updown-1d-2026-11-27-2200z  22:00Z 27 Nov -> 22:00Z 28 Nov
  ...-updown-15m-<date>-<HHMM>z, ...-updown-5m-<date>-<HHMM>z exist too.

The date/time token is the window START (not its end, and not the market's
`endDate`, which is 30 minutes after the window end). The venue says slugs
are not a public contract ("never parse the symbol"), so a slug-derived
identity is only a CLAIM: `verify` checks it against the venue's own
assetPriceTerms and anything that disagrees is refused.

Corrections to the first version of this module (handoff 76c5ae83f):
  * BTC was refused although it is a real identifier with a live reward
    programme: the pilot allowlist (eth|sol|xrp) was embedded in the
    identity pattern. BTC is now identified with pilot_asset False.
  * the daily boundary was hard-coded to 22:00Z, which is 5 PM Eastern only
    in winter; it is now 17:00 America/New_York (21:00Z in summer).
  * the boundary was unlabelled; it is the window START. window_end added.
  * 5m / 15m are real families: `classify` names them as unsupported
    instead of treating them as malformed; `identify` still returns None.
"""
import re
from datetime import datetime, timedelta, timezone

try:                                    # stdlib; absent tz data fails closed
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                        # noqa: BLE001
    _ET = None

#: venue slug prefix -> (asset symbol, settlement index)
FAMILIES = {"cpc-btc": ("btc", "BRTI"),
            "cpceth-eth": ("eth", "ETHUSD_RTI"),
            "cpcsol-sol": ("sol", "SOLUSD_RTI"),
            "cpcxrp-xrp": ("xrp", "XRPUSD_RTI")}
DURATIONS = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
#: windows that carry a liquidity programme row (crypto_*_updown_1h/1d_*)
REWARD_WINDOWS = ("1h", "1d")
#: assets the reward lane may be configured to quote (never BTC)
PILOT_ASSETS = ("eth", "sol", "xrp")

_PATTERN = re.compile(
    r"(cpc-btc|cpceth-eth|cpcsol-sol|cpcxrp-xrp)-updown-(5m|15m|1h|1d)-"
    r"(\d{4}-\d{2}-\d{2})-(\d{4})z", re.ASCII)


def _daily_start_ok(start):
    """A daily window starts at the exchange's trade-day roll, 5 PM ET."""
    if _ET is None:
        return start.hour == 22 and start.minute == 0
    local = start.astimezone(_ET)
    return local.hour == 17 and local.minute == 0


def classify(slug):
    """Every real up/down family, supported or not:
    {"asset", "duration", ..., "supported": bool, "why": reason or None},
    or None for anything that is not a well-formed up/down slug."""
    if not isinstance(slug, str):
        return None
    m = _PATTERN.fullmatch(slug)
    if m is None:
        return None
    prefix, duration, date, clock = m.groups()
    try:
        start = datetime.strptime(date + clock, "%Y-%m-%d%H%M").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    secs = DURATIONS[duration]
    if duration == "1d":
        if not _daily_start_ok(start):
            return None
    elif (start.hour * 3600 + start.minute * 60) % secs != 0:
        return None
    asset, index = FAMILIES[prefix]
    supported = duration in REWARD_WINDOWS
    return {"asset": asset, "duration": duration, "index": index,
            "prefix": prefix,
            "window_start": start.isoformat(),
            "window_end": (start + timedelta(seconds=secs)).isoformat(),
            "slug_boundary": start.isoformat(),      # = window_start
            "duration_seconds": secs,
            "reward_window": supported,
            "pilot_asset": asset in PILOT_ASSETS,
            "supported": supported,
            "why": None if supported else "unsupported_window:" + duration}


def identify(slug):
    """Identity of an hourly or daily up/down market of ANY listed asset
    (BTC included), or None. Says nothing about whether it may be traded."""
    c = classify(slug)
    return c if c and c["supported"] else None


def _ts(v):
    try:
        t = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def verify(identity, terms):
    """Why the venue's own assetPriceTerms do NOT confirm a slug-derived
    identity, or None when every field agrees. Missing terms refuse."""
    if not isinstance(identity, dict):
        return "no_identity"
    if not isinstance(terms, dict):
        return "no_venue_terms"
    if terms.get("marketType") != "ASSET_PRICE_MARKET_TYPE_UP_DOWN":
        return "market_type"
    asset = terms.get("asset")
    sym = asset.get("symbol") if isinstance(asset, dict) else None
    if str(sym or "").lower() != identity.get("asset"):
        return "asset"
    if terms.get("indexSymbol") != identity.get("index"):
        return "index"
    if terms.get("horizon") != identity.get("duration"):
        return "horizon"
    for key, field in (("window_start", "windowStart"),
                       ("window_end", "windowEnd")):
        a, b = _ts(identity.get(key)), _ts(terms.get(field))
        if a is None or b is None or a != b:
            return field
    return None


def et_date(when):
    """The Eastern-Time calendar date (YYYY-MM-DD) an instant falls on:
    the unit of the venue's earnings rows and of a `daily_event` pool
    (midnight to midnight ET). None when it cannot be computed."""
    t = when if isinstance(when, datetime) else _ts(when)
    if t is None or _ET is None:
        return None
    return t.astimezone(_ET).date().isoformat()
