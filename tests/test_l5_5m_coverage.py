"""The recorder must capture the CONTINUE states and never invent a number."""
from src.l5_5m_coverage import recorder as R


def test_windows_are_enumerated_so_late_subscribe_cannot_recur():
    """The Gemini collector lost 82 s/window to discovery. 5m windows sit on
    fixed 300 s epochs, so there is nothing to discover and capture opens a
    full PRE_ROLL_S before the window, by construction."""
    s = R.window_start(1789420000)
    assert s % R.WINDOW_S == 0 and s >= 1789420000
    assert R.due_at(s) == s - R.PRE_ROLL_S
    assert R.window_start(s) == s                      # already on a boundary
    assert R.window_start(s + 1) == s + R.WINDOW_S     # strictly after


def test_a_window_where_l5_does_nothing_is_still_a_full_observation():
    rec = R.sample(asset="btc", start=1789420200, now=1789420100.0,
                   book={"up_bid": 0.48, "up_ask": 0.52},
                   inv={}, flow=None, history=[])
    assert rec["pre_roll"] is True and rec["l5_acted"] is False
    assert rec["two_sided"] is True and abs(rec["spread"] - 0.04) < 1e-9
    assert abs(rec["attainable_pair_cost"] - 0.96) < 1e-9
    assert rec["tte_s"] == 400.0


def test_a_one_sided_book_is_null_with_a_reason_never_a_zero_spread():
    rec = R.sample(asset="eth", start=1789420200, now=1789420300.0,
                   book={"up_bid": 0.48}, inv={"dn": 10, "cost_dn": 4.5},
                   flow=None, history=[])
    assert rec["spread"] is None and rec["spread_reason"] == R.R_NO_BOOK
    assert rec["attainable_pair_cost"] is None
    assert rec["mid"] is None
    # a zero spread would read as a locked market; a zero depth as an empty one
    assert rec["spread"] != 0 and rec["depth_bid"] is None


def test_returns_are_as_of_and_never_look_ahead():
    hist = [{"t": 1789420200.0 + i, "mid": 0.50 + 0.001 * i} for i in range(61)]
    rec = R.sample(asset="btc", start=1789420200, now=1789420260.0,
                   book={"up_bid": 0.55, "up_ask": 0.57}, inv={},
                   flow=None, history=hist)
    # mid now is 0.56; 60 s ago it was 0.50
    assert abs(rec["ret_60s"] - 0.06) < 1e-9
    assert abs(rec["ret_5s"] - 0.005) < 1e-9
    # a lag with no history yet is null WITH a reason, not 0
    rec2 = R.sample(asset="btc", start=1789420200, now=1789420201.0,
                    book={"up_bid": 0.55, "up_ask": 0.57}, inv={},
                    flow=None, history=hist[:1])
    assert rec2["ret_60s"] is None and rec2["ret_60s_reason"] == R.R_NO_HISTORY


def test_inventory_gives_matched_absolute_and_relative_residual():
    st = R.inventory_state(up=3.0, dn=10.0, cost_up=1.5, cost_dn=4.5,
                           complement_price=0.55)
    assert st["matched_qty"] == 3.0
    assert st["residual_abs"] == 7.0 and st["residual_signed"] == -7.0
    assert abs(st["residual_relative"] + 7 / 13) < 1e-9
    assert abs(st["held_avg_cost"] - 0.45) < 1e-9      # holds DOWN
    assert abs(st["held_pair_cost"] - 1.00) < 1e-9


def test_pair_undefined_before_a_first_leg_is_null_with_a_reason():
    st = R.inventory_state(0, 0, 0, 0, complement_price=0.55)
    assert st["held_avg_cost"] is None
    assert st["held_pair_cost"] is None
    assert st["held_pair_cost_reason"] == R.R_NO_HELD
    assert st["residual_relative"] is None


def test_digest_surfaces_a_collection_defect_rather_than_hiding_it():
    start = 1789420200
    samples = [R.sample(asset="btc", start=start, now=start - 120.0 + i,
                        book={"up_bid": 0.48, "up_ask": 0.52}, inv={},
                        flow=None, history=[]) for i in range(0, 420)]
    d = R.digest("btc", start, samples)
    assert d["first_sample_lead_s"] == 120.0          # early, as intended
    assert d["max_gap_s"] == 1.0
    assert d["pre_roll_samples"] == 120
    assert d["two_sided_frac"] == 1.0
    empty = R.digest("btc", start, [])
    assert empty["coverage"] is None and "never opened" in empty["coverage_reason"]
