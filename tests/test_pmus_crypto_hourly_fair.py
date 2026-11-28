import math

import pytest
from src import pmus_crypto_hourly_fair as H
from src.pmus_crypto_hourly_fair import hourly_fair

T0 = 1_795_888_800          # 2026-11-28T18:00:00Z, top of an hour


def fair(**overrides):
    args = dict(spot=100, strike=100, annual_vol=.8, now=T0 + 1800,
                observed_at=T0 + 1799, start=T0, end=T0 + 3600,
                reference_verified=True, spot_index="BRTI",
                settlement_index="BRTI")
    args.update(overrides)
    return hourly_fair(**args)


def test_at_strike_and_monotonicity():
    assert fair() == .5
    assert fair(spot=99) < .5 < fair(spot=101)


def test_time_decay_away_from_strike():
    assert fair(spot=101, now=T0 + 3530, observed_at=T0 + 3529) > \
        fair(spot=101)


def test_variance_includes_the_60_print_settlement_average():
    # 1800 s of diffusion to the fixing minute + FIX_VAR_S for the average
    p = fair(spot=101)
    sig = .8 * math.sqrt((1800 - 60 + H.FIX_VAR_S) / H.YEAR_S)
    want = 0.5 * math.erfc(-math.log(1.01) / sig / math.sqrt(2))
    assert p == pytest.approx(want, rel=1e-12)
    assert 20.0 < H.FIX_VAR_S < 21.0      # ~L/3 of a 60 s window


def test_ties_are_up_after_rounding():
    # rounded mean >= strike settles UP, so the effective strike sits half a
    # rounding unit lower and spot == strike is (slightly) above one half
    assert fair(spot=100, price_decimals=2) > .5
    assert fair(spot=100, price_decimals=2) > fair(spot=100,
                                                   price_decimals=5) > .5


@pytest.mark.parametrize("bad", [
    dict(reference_verified=False), dict(reference_verified=1),
    dict(observed_at=T0 + 1801), dict(observed_at=T0 + 1790),
    dict(end=T0 + 900), dict(now=T0 + 3600),
    dict(annual_vol=0), dict(spot=float("nan")), dict(strike=-1),
    dict(annual_vol=float("inf")), dict(now=T0 - 1), dict(max_age=-1),
    # inside the fixing minute the collected prints are unknown
    dict(now=T0 + 3540, observed_at=T0 + 3540),
    dict(now=T0 + 3599, observed_at=T0 + 3599),
    # window not on the hour
    dict(start=T0 + 60, end=T0 + 3660),
    # settlement index identity
    dict(spot_index=None), dict(settlement_index=None),
    dict(spot_index="ETHUSD_RTI"), dict(spot_index="BTCUSD",
                                         settlement_index="BTCUSD"),
    # bools are not numbers
    dict(spot=True, strike=True), dict(price_decimals=True),
    dict(price_decimals=1.5), dict(price_decimals=-1),
])
def test_invalid_inputs_fail_closed(bad):
    assert fair(**bad) is None


def test_all_four_venue_indices_accepted():
    for ix in ("BRTI", "ETHUSD_RTI", "SOLUSD_RTI", "XRPUSD_RTI"):
        assert fair(spot_index=ix, settlement_index=ix) == .5


def test_module_is_pure():
    src = open(H.__file__).read()
    for word in ("import requests", "import httpx", "urllib", "socket",
                 "os.getenv", "time.time"):
        assert word not in src
