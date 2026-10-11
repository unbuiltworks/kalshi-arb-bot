"""One comparable metric per venue. Pins which cells are measured, which are
empty, and that an unmeasured venue is never scored as a good one."""
from decimal import Decimal as D

from src import venue_expectancy_ranking as V


def test_only_two_rows_are_fully_measured():
    assert set(V.measured_rows()) == {"pmus_sports_ours",
                                      "l5_polymarket_crypto_not_us"}


def test_the_only_measured_venue_of_ours_is_negative():
    assert V.only_measured_own_venue_is_negative() is True


def test_kalshi_rows_are_unknown_not_optimistic():
    for k in ("kalshi_KXBTC15M_KXETH15M", "kalshi_KXCRYPTOLEAD15M",
              "kalshi_15m_commodities"):
        assert V.RANKING[k][0] == V.UNKNOWN
        assert V.RANKING[k][2] == V.UNKNOWN


def test_gemini_has_no_confirmed_economics_either():
    assert V.RANKING["gemini_btc_eth"][0] == V.UNKNOWN


# --- the Kalshi facts, verified live ---------------------------------------

def test_our_kalshi_series_pay_nothing_today():
    assert V.our_series_pay_nothing() is True


def test_the_subsidy_on_our_series_was_withdrawn_not_absent():
    assert V.OUR_SERIES_WITHDRAWN["KXBTC15M"] > 9000
    assert V.OUR_SERIES_WITHDRAWN["type"] == "volume"
    assert V.OUR_SERIES_WITHDRAWN["ended"] == "2026-05-12"


def test_the_paying_series_are_ones_we_have_no_tape_for():
    assert V.paying_series_have_no_evidence() is True
    assert V.PAYING_SERIES["KXCRYPTOLEAD15M"]["upcoming"] > 500


def test_reward_units_remain_unverified_with_the_ambiguity_sized():
    assert V.UNIT_STATUS.startswith("NOT VERIFIED")
    assert "NOT a verification" in V.UNIT_INFERENCE
    assert V.UNIT_AMBIGUITY_FACTOR == 100
    assert len(V.UNIT_RESOLUTION_PATHS) == 2


# --- the shadow is excluded, and why ---------------------------------------

def test_the_kalshi_shadow_has_never_traded():
    assert V.KALSHI_SHADOW["order_entry"] is False
    assert V.KALSHI_SHADOW["realized_pnl"] is None


def test_the_simulated_figure_is_excluded_from_the_ranking():
    assert "SIMULATED" in V.SHADOW_FIGURE_EXCLUDED
    assert V.RANKING["kalshi_KXBTC15M_KXETH15M"][0] == V.UNKNOWN


def test_current_shadow_pair_costs_are_above_par():
    assert V.shadow_pair_costs_are_above_par() is True


# --- the verdict ------------------------------------------------------------

def test_kalshi_is_unmeasured_rather_than_accepted_or_rejected():
    assert V.VERDICT == "KALSHI_IS_UNMEASURED_NOT_PROVEN"


def test_the_next_step_is_a_collector_not_an_order_path():
    assert "no order path" in V.CHEAP_NEXT_STEP
    assert V.AUTHORISES_ORDERS == ()
    assert V.AUTHORISES_RESTART == ()
