"""Guards on the causal BRTI settlement-state arithmetic.

Synthetic candles only -- no tape, no outcomes. These exist so that the
publication discipline and the required-remaining-average algebra are checked
independently of any data run.
"""

from decimal import Decimal as D

import pytest

from src.pmus import brti_state as bs


T = 1_000_000  # a window end, in unix seconds


def candles(level="100000.00", lo=T - 240, hi=T + 5, ticks=3):
    return {s: {"close": D(level), "ticks": ticks} for s in range(lo, hi)}


# --- publication discipline -------------------------------------------------

def test_sixty_settlement_buckets_are_T_minus_60_to_T_minus_1():
    b = bs.settlement_buckets(T)
    assert (b.start, b.stop) == (T - 60, T)
    assert len(b) == 60


@pytest.mark.parametrize("t,expected", [
    (T - 61, 0), (T - 60, 0), (T - 59, 1), (T - 30, 30),
    (T - 2, 58), (T - 1, 59), (T, 60), (T + 5, 60),
])
def test_availability_count_lags_the_bucket_stamp_by_one(t, expected):
    """Bucket s is available at s+1, so at t = T-1 only 59 are published."""
    assert bs.n_published(T, t) == expected


def test_a_remaining_observation_always_exists_inside_the_final_minute():
    """No outcome can be arithmetically determined before T."""
    for t in range(T - 60, T):
        assert bs.STAT_N - bs.n_published(T, t) >= 1


def test_causal_level_never_reads_an_unpublished_bucket():
    c = {T - 3: {"close": D("1"), "ticks": 1},
         T - 2: {"close": D("2"), "ticks": 1}}
    # at t = T-2 only bucket T-3 has closed
    assert bs.causal_level(c, T - 2) == D("1")
    assert bs.causal_level(c, T - 1) == D("2")
    assert bs.causal_level({}, T) is None


# --- the settlement algebra -------------------------------------------------

def test_known_contribution_sums_only_available_buckets():
    c = candles()
    ks, n, zero = bs.known_contribution(c, T, T - 30)
    assert n == 30
    assert ks == D("100000.00") * 30
    assert zero == 0


def test_known_contribution_refuses_to_invent_a_missing_bucket():
    c = candles()
    del c[T - 45]
    ks, n, _ = bs.known_contribution(c, T, T - 30)
    assert ks is None and n == 30


def test_known_contribution_counts_zero_tick_buckets():
    c = candles(ticks=0)
    _, _, zero = bs.known_contribution(c, T, T - 30)
    assert zero == 30


def test_required_remaining_average_is_the_exact_flip_point():
    """Printing the required average lands settlement on the strike; a cent
    above settles UP and a cent below settles DOWN."""
    strike = D("100000.00")
    ks = D("99000.00") * 20          # 20 known observations well below strike
    req = bs.required_remaining_average(ks, 20, strike)
    assert req == (D(60) * strike - ks) / D(40)

    at = (ks + D(40) * req) / D(60)
    assert at == strike                                   # exactly on it
    above = (ks + D(40) * (req + D("0.01"))) / D(60)
    below = (ks + D(40) * (req - D("0.01"))) / D(60)
    assert above > strike and below < strike


def test_required_remaining_average_is_none_only_when_nothing_remains():
    assert bs.required_remaining_average(D(0), 60, D(1)) is None
    assert bs.required_remaining_average(D(0), 59, D(1)) is not None


def test_required_average_runs_away_as_observations_are_consumed():
    """With the index flat below the strike, the average the opposite outcome
    needs rises without bound as the remaining count shrinks."""
    strike, level = D("100000.00"), D("99900.00")
    reqs = []
    for t in range(T - 59, T):
        ks, n, _ = bs.known_contribution(candles(str(level)), T, t)
        reqs.append(bs.required_remaining_average(ks, n, strike))
    assert all(b > a for a, b in zip(reqs, reqs[1:]))


def test_projected_settlement_is_arithmetic_not_forecast():
    """If every remaining observation printed at the current level, settlement
    is exactly this -- and with a flat index it equals that level."""
    c = candles("100000.00")
    ks, n, _ = bs.known_contribution(c, T, T - 20)
    assert bs.projected_settlement(ks, n, D("100000.00")) == D("100000.00")


def test_projected_side_follows_the_tie_rule_ties_settle_up():
    strike = D("100000.00")
    st = bs.second_state("s", T, T - 30, candles("100000.00"), strike, [], [])
    assert st.projected_settlement == "100000.00"
    assert st.projected_side == "UP"              # close == open settles UP


# --- volatility and assembly ------------------------------------------------

def test_realized_vol_is_none_on_a_flat_or_empty_series():
    assert bs.realized_vol_1s({}, T) is None
    v = bs.realized_vol_1s(candles(), T)
    assert v is not None and v["sigma_1s"] == 0.0


def test_realized_vol_uses_only_published_returns():
    c = {s: {"close": D(100000 + (s % 7)), "ticks": 1}
         for s in range(T - 200, T + 50)}
    v = bs.realized_vol_1s(c, T - 30, seconds=60)
    assert v["n_returns"] <= 60 and v["sigma_1s"] > 0


def test_brti_change_over_prior_30_and_60_seconds():
    c = {s: {"close": D(s - (T - 300)), "ticks": 1} for s in range(T - 300, T)}
    assert bs.brti_change(c, T - 1, 30) == D(30)
    assert bs.brti_change(c, T - 1, 60) == D(60)


def test_executable_prices_come_from_one_snapshot_by_the_pair_identity():
    row = {"t": 1.0, "bid": D("0.60"), "ask": D("0.64"),
           "bid_qty": D(10), "ask_qty": D(7)}
    e = bs.executable_from_book(row)
    assert e["up_ask"] == D("0.64") and e["up_capacity"] == D(7)
    assert e["down_ask"] == D("0.40") and e["down_capacity"] == D(10)
    assert e["up_ask"] + e["down_ask"] == D(1) + e["spread"]


def test_one_sided_book_is_refused_not_guessed():
    assert bs.executable_from_book({"t": 1.0, "bid": None, "ask": D("0.6")}) is None


def test_book_at_is_strictly_causal():
    rows = [{"t": 10.0}, {"t": 20.0}, {"t": 30.0}]
    times = [r["t"] for r in rows]
    assert bs.book_at(times, rows, 25.0)["t"] == 20.0
    assert bs.book_at(times, rows, 30.0)["t"] == 30.0
    assert bs.book_at(times, rows, 9.9) is None


def test_second_state_carries_quote_age_and_labels_outcome_only():
    rows = [{"t": float(T - 45), "bid": D("0.30"), "ask": D("0.34"),
             "bid_qty": D(5), "ask_qty": D(9)}]
    st = bs.second_state("slug", T, T - 30, candles(), D("100000.00"),
                         [r["t"] for r in rows], rows, settled_outcome="DOWN")
    assert st.quote_age_s == 15.0
    assert st.n_known == 30 and st.n_remaining == 30
    assert st.settled_outcome == "DOWN"
    assert st.up_ask == "0.34" and st.down_ask == "0.70"


def test_no_locked_flag_is_ever_emitted():
    st = bs.second_state("s", T, T - 1, candles(), D("100000.00"), [], [])
    assert "lock" not in " ".join(st.as_dict().keys()).lower()
    assert st.n_remaining == 1


def test_sigma_normalisation_is_refused_on_a_degenerate_index():
    """A perfectly flat index has zero volatility, which would make any
    required move look like millions of sigmas. That is reported as unknown."""
    st = bs.second_state("s", T, T - 30, candles("100000.00"), D("100000.00"),
                         [], [])
    assert st.required_move_sigmas is None
    # the arithmetic itself is still reported
    assert st.required_remaining_avg is not None
    assert st.required_move_dollars is not None
