from decimal import Decimal

from src import sports_kp_mve_exact_fee_probe as f

D = Decimal


def test_taker_fee_uses_centicent_precision():
    assert f.quadratic_fee(D("0.20"), D("1"), maker=False) == D("0.0112")
    assert f.quadratic_fee(D("0.819"), D("1"), maker=False) == D("0.0104")


def test_half_multiplier_mlb_fee():
    assert f.quadratic_fee(D("0.20"), D("0.5"), maker=False) == D("0.0056")


def test_maker_fee_formula():
    assert f.quadratic_fee(D("0.20"), D("1"), maker=True) == D("0.0028")


def test_zero_maker_multiplier_means_no_fee():
    assert f.quadratic_fee(D("0.20"), D("0"), maker=True) == D("0")


def test_mve_maker_target_preserves_three_cent_floor():
    target = f._mve_maker_target(D("0.20"), D("0"))
    assert target is not None
    total = D("0.20") + target + f.quadratic_fee(target, f.MVE_MAKER_MULTIPLIER, maker=True)
    assert D("1") - total >= D("0.03")
    next_px = target + D("0.001")
    next_total = D("0.20") + next_px + f.quadratic_fee(next_px, f.MVE_MAKER_MULTIPLIER, maker=True)
    assert D("1") - next_total < D("0.03")
