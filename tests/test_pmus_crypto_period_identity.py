import pytest
from src.pmus_crypto_period_identity import (classify, et_date, identify,
                                             verify)


@pytest.mark.parametrize("asset", ["eth", "sol", "xrp"])
def test_hourly(asset):
    assert identify(f"cpc{asset}-{asset}-updown-1h-2026-11-28-2200z")["duration_seconds"] == 3600


def test_daily():
    assert identify("cpceth-eth-updown-1d-2026-11-28-2200z")["duration_seconds"] == 86400


@pytest.mark.parametrize("slug", [
    "cpceth-sol-updown-1h-2026-11-28-2200z",
    "cpceth-eth-updown-5m-2026-11-28-2200z",
    "cpceth-eth-updown-1h-2026-11-28-2230z",
    "cpceth-eth-updown-1d-2026-11-28-2100z",
    "cpceth-eth-updown-1h-2026-02-30-2200z",
    "cpceth-eth-updown-1h-2026-11-28-2200z-extra",
    "cpcbtc-btc-updown-1h-2026-11-28-2200z",      # BTC's prefix is cpc-btc
    "cpc-btc-above-hr-2026-11-28-2200z",
    "aec-nba-bos-nyk-2026-04-01",
    None,
])
def test_refused(slug):
    assert identify(slug) is None


# ---- corrections to the handoff version, against the real identifiers

def test_btc_is_a_real_identity_and_not_a_pilot_asset():
    """The handoff refused cpc-btc-...: the trading allowlist was embedded
    in the identity pattern. BTC is identified; it is just not a pilot."""
    for dur in ("1h", "1d"):
        hit = identify(f"cpc-btc-updown-{dur}-2026-11-28-2200z")
        assert hit["asset"] == "btc" and hit["index"] == "BRTI"
        assert hit["pilot_asset"] is False and hit["reward_window"] is True
    assert identify("cpceth-eth-updown-1h-2026-11-28-2000z")["pilot_asset"]


def test_the_slug_time_is_the_window_start():
    """Verified 28 Nov: ...-1h-2026-11-28-2000z runs 20:00Z -> 21:00Z and
    ...-1d-2026-11-27-2200z runs 22:00Z 27 Nov -> 22:00Z 28 Nov."""
    h = identify("cpc-btc-updown-1h-2026-11-28-2000z")
    assert h["window_start"] == "2026-11-28T20:00:00+00:00"
    assert h["window_end"] == "2026-11-28T21:00:00+00:00"
    assert h["slug_boundary"] == h["window_start"]
    d = identify("cpceth-eth-updown-1d-2026-11-27-2200z")
    assert d["window_start"] == "2026-11-27T22:00:00+00:00"
    assert d["window_end"] == "2026-11-28T22:00:00+00:00"


def test_daily_boundary_is_5pm_eastern_not_a_fixed_utc_hour():
    """22:00Z is 5 PM ET only in winter. A summer daily starts 21:00Z; the
    handoff's fixed hour 22 would have refused every one of them."""
    assert identify("cpceth-eth-updown-1d-2026-07-15-2100z") is not None
    assert identify("cpceth-eth-updown-1d-2026-07-15-2200z") is None
    assert identify("cpceth-eth-updown-1d-2026-11-28-2200z") is not None
    assert identify("cpceth-eth-updown-1d-2026-11-28-2100z") is None


@pytest.mark.parametrize("slug,dur", [
    ("cpcsol-sol-updown-15m-2026-11-28-2030z", "15m"),
    ("cpcxrp-xrp-updown-5m-2026-11-28-2035z", "5m"),
])
def test_short_windows_are_real_but_unsupported(slug, dur):
    c = classify(slug)
    assert c["supported"] is False and c["why"] == "unsupported_window:" + dur
    assert identify(slug) is None


def test_short_windows_must_sit_on_their_grid():
    assert classify("cpcsol-sol-updown-15m-2026-11-28-2040z") is None
    assert classify("cpcxrp-xrp-updown-5m-2026-11-28-2037z") is None


REAL_TERMS = {      # GET /v1/market/slug/cpc-btc-updown-1h-2026-11-28-2000z
    "marketType": "ASSET_PRICE_MARKET_TYPE_UP_DOWN",
    "asset": {"assetClass": "ASSET_CLASS_CRYPTO", "symbol": "btc"},
    "indexSymbol": "BRTI", "horizon": "1h",
    "windowStart": "2026-11-28T20:00:00Z",
    "windowEnd": "2026-11-28T21:00:00Z"}


def test_identity_is_confirmed_by_the_venue_terms():
    ident = identify("cpc-btc-updown-1h-2026-11-28-2000z")
    assert verify(ident, REAL_TERMS) is None


@pytest.mark.parametrize("change,why", [
    ({"windowEnd": "2026-11-28T21:30:00Z"}, "windowEnd"),
    ({"windowStart": "2026-11-28T19:00:00Z"}, "windowStart"),
    ({"horizon": "15m"}, "horizon"),
    ({"indexSymbol": "ETHUSD_RTI"}, "index"),
    ({"asset": {"symbol": "eth"}}, "asset"),
    ({"marketType": "ASSET_PRICE_MARKET_TYPE_ABOVE_BELOW"}, "market_type"),
    ({"windowEnd": None}, "windowEnd"),
])
def test_any_disagreement_with_the_venue_refuses(change, why):
    ident = identify("cpc-btc-updown-1h-2026-11-28-2000z")
    assert verify(ident, {**REAL_TERMS, **change}) == why


def test_missing_terms_or_identity_refuse():
    ident = identify("cpc-btc-updown-1h-2026-11-28-2000z")
    assert verify(ident, None) == "no_venue_terms"
    assert verify(None, REAL_TERMS) == "no_identity"


def test_a_daily_market_spans_two_eastern_dates():
    """Earnings rows and a daily_event pool are per Eastern date: a daily
    window (5 PM ET -> 5 PM ET) puts 7 hours on one date and 17 on the
    next."""
    d = identify("cpceth-eth-updown-1d-2026-11-27-2200z")
    assert et_date(d["window_start"]) == "2026-11-27"
    assert et_date(d["window_end"]) == "2026-11-28"
    assert et_date("2026-11-28T04:59:59Z") == "2026-11-27"
    assert et_date("2026-11-28T05:00:00Z") == "2026-11-28"
    assert et_date("not a time") is None
