import pytest
from src.pmus_crypto_period_identity import identify

@pytest.mark.parametrize("asset", ["eth", "sol", "xrp"])
def test_hourly(asset):
    assert identify(f"cpc{asset}-{asset}-updown-1h-2026-11-28-2200z")["duration_seconds"] == 3600

def test_daily():
    assert identify("cpceth-eth-updown-1d-2026-11-28-2200z")["duration_seconds"] == 86400

@pytest.mark.parametrize("slug", [
    "cpc-btc-updown-1h-2026-11-28-2200z",
    "cpceth-sol-updown-1h-2026-11-28-2200z",
    "cpceth-eth-updown-5m-2026-11-28-2200z",
    "cpceth-eth-updown-1h-2026-11-28-2230z",
    "cpceth-eth-updown-1d-2026-11-28-2100z",
    "cpceth-eth-updown-1h-2026-02-30-2200z",
    "cpceth-eth-updown-1h-2026-11-28-2200z-extra",
    None,
])
def test_refused(slug):
    assert identify(slug) is None
