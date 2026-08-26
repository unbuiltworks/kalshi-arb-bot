from types import SimpleNamespace

from src.fee_calc import taker_fee_cents
from src.weather_shadow_daemon import _depth_repriced_q1_ev_cents


def _row(*, p_yes=0.196894, ticker="KXHIGHAUS-26AUG26-B105.5"):
    return SimpleNamespace(model_yes_probability=p_yes, market_ticker=ticker)


def _fill(*, cost=70.0, filled=1.0, fully=True):
    return SimpleNamespace(
        total_cost_cents=cost,
        filled_contracts=filled,
        fully_fillable=fully,
    )


def test_depth_repricing_uses_consumable_q1_cost_not_stale_displayed_ask():
    row = _row()
    ev, fee = _depth_repriced_q1_ev_cents(row, "no", _fill(cost=70.0))
    expected_fee = taker_fee_cents(1, 70, row.market_ticker)
    expected_ev = round((1.0 - row.model_yes_probability) * 100.0 - 70.0 - expected_fee, 2)
    assert fee == expected_fee
    assert ev == expected_ev
    # The live shadow previously showed a pre-depth value around 13.31c when the
    # displayed ask was 65c, even though q1 depth actually cost 70c. Repricing must
    # reduce that apparent edge by the 5c execution-price difference.
    assert ev < 13.31


def test_depth_repricing_handles_yes_side():
    row = _row(p_yes=0.634325, ticker="KXHIGHMIA-26AUG26-T87")
    ev, fee = _depth_repriced_q1_ev_cents(row, "yes", _fill(cost=1.0))
    expected_fee = taker_fee_cents(1, 1, row.market_ticker)
    assert fee == expected_fee
    assert ev == round(0.634325 * 100.0 - 1.0 - expected_fee, 2)


def test_depth_repricing_fails_closed_without_q1_fill_or_probability():
    assert _depth_repriced_q1_ev_cents(_row(), "no", _fill(fully=False)) == (None, None)
    assert _depth_repriced_q1_ev_cents(_row(), "no", _fill(filled=0.5)) == (None, None)
    assert _depth_repriced_q1_ev_cents(_row(p_yes=None), "no", _fill()) == (None, None)
    assert _depth_repriced_q1_ev_cents(_row(), "maybe", _fill()) == (None, None)
