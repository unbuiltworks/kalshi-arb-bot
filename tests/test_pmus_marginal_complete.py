"""Marginal per-level completion: take only what the book pays for."""
from decimal import Decimal as D

from src.pmus_completion_shadow import marginal_complete, THETA_TAKER


def unit_fee(p):
    return THETA_TAKER * D(p) * (D(1) - D(p))


def test_takes_only_the_levels_that_clear_theta():
    """basis 0.60; theta 1.00. 0.38 clears (0.60+0.38+fee<1.00); 0.44 does not."""
    lv = [["0.38", "10"], ["0.44", "100"]]
    q, c, f, short = marginal_complete(lv, D(50), D("0.60"), D("1.00"))
    assert q == D(10), "took beyond the affordable level"
    assert c == D(10) * D("0.38")


def test_a_blended_average_would_have_passed_and_must_not():
    """The whole reason marginal beats average: 1@0.30 + 99@0.60 averages
    0.597, which clears theta, while 99 of those contracts do not."""
    lv = [["0.30", "1"], ["0.60", "99"]]
    q, c, f, short = marginal_complete(lv, D(100), D("0.35"), D("0.70"))
    assert q == D(1), "a blended average smuggled in the expensive level"
    assert c == D("0.30")


def test_stops_at_the_first_failing_level_even_if_a_later_one_were_cheaper():
    """Ladders ascend in price; a cheaper later level cannot exist, and the
    walk must not go looking for one."""
    lv = [["0.40", "5"], ["0.50", "5"], ["0.41", "500"]]
    q, _, _, _ = marginal_complete(lv, D(100), D("0.55"), D("1.00"))
    assert q == D(5)


def test_completes_the_whole_residual_when_every_level_is_cheap():
    lv = [["0.10", "1000"]]
    q, c, f, short = marginal_complete(lv, D(150), D("0.20"), D("1.00"))
    assert q == D(150) and short is False
    assert c == D(150) * D("0.10")


def test_depth_limited_is_flagged_when_economics_said_yes():
    """Economics fine, book too thin: a MISSED QUALIFYING STATE."""
    lv = [["0.10", "7"]]
    q, _, _, short = marginal_complete(lv, D(150), D("0.20"), D("1.00"))
    assert q == D(7)
    assert short is True, "depth shortfall not distinguished from a price reject"


def test_price_rejection_is_not_reported_as_depth_limited():
    lv = [["0.90", "1000"]]
    q, _, _, short = marginal_complete(lv, D(10), D("0.50"), D("1.00"))
    assert q == 0
    assert short is False, "a price rejection was mislabelled as depth"


def test_fee_is_charged_per_level_at_that_levels_price():
    lv = [["0.20", "10"], ["0.30", "10"]]
    q, c, f, _ = marginal_complete(lv, D(20), D("0.10"), D("1.00"))
    assert q == D(20)
    assert f == D(10) * unit_fee("0.20") + D(10) * unit_fee("0.30")


def test_fee_can_be_what_rejects_a_level():
    """basis+px alone clears 1.00 exactly; the fee is what pushes it over."""
    lv = [["0.50", "10"]]
    q, _, _, _ = marginal_complete(lv, D(10), D("0.50"), D("1.00"))
    assert q == 0, "fee was not included in the marginal test"


def test_an_empty_ladder_completes_nothing():
    q, c, f, short = marginal_complete([], D(10), D("0.2"), D("1.0"))
    assert q == 0 and c == 0 and f == 0
