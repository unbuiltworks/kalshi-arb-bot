import pytest
from src.pmus_crypto_hourly_fair import hourly_fair


def fair(**overrides):
    args = dict(spot=100, strike=100, annual_vol=.8, now=1800,
                observed_at=1799, start=0, end=3600, reference_verified=True)
    args.update(overrides)
    return hourly_fair(**args)


def test_at_strike_and_monotonicity():
    assert fair() == .5
    assert fair(spot=99) < .5 < fair(spot=101)


def test_time_decay_away_from_strike():
    assert fair(spot=101, now=3590, observed_at=3589) > fair(spot=101)


@pytest.mark.parametrize("bad", [
    dict(reference_verified=False), dict(observed_at=1801),
    dict(observed_at=1790), dict(end=900), dict(now=3600),
    dict(annual_vol=0), dict(spot=float("nan")), dict(strike=-1),
    dict(annual_vol=float("inf")), dict(now=-1), dict(max_age=-1),
])
def test_invalid_inputs_fail_closed(bad):
    assert fair(**bad) is None
