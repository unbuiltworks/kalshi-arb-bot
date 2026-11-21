"""Executed volume from the tape, never inferred from displayed depth."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import window_volume as WV

EXP = 1789782000000          # 2026-09-19T01:40:00Z


def _t(ms_before, price, amount, typ="buy", tid=1):
    return {"tid": tid, "utc_ms": EXP - int(ms_before * 1000),
            "price": price, "amount": amount, "type": typ}


def _tape(prints, readable=True, why=None):
    return {"symbol": "S", "readable": readable, "why": why,
            "n_prints": len(prints), "prints": sorted(
                prints, key=lambda p: p["utc_ms"]), "truncated": False}


# ================================= depth and volume never mix

def test_displayed_depth_is_carried_apart_and_added_to_nothing():
    """C8B joined a 483-deep level that showed 0.01 the next cycle. Displayed
    size predicted nothing and must never become a volume figure."""
    v = WV.window_volume(_tape([_t(100, "0.50", "2")]), expiry_ms=EXP,
                         touch_depth="483.0")
    assert v["touch_depth_displayed"] == "483.0"
    assert v["contracts_traded"] == "2"
    assert v["notional_usd"] == "1.00"
    assert "not volume" in v["depth_note"]


def test_volume_is_measured_from_amount_not_inferred():
    v = WV.window_volume(_tape([_t(100, "0.50", "2"), _t(50, "0.25", "4")]),
                         expiry_ms=EXP)
    assert v["contracts_traded"] == "6"
    assert D(v["notional_usd"]) == D("1.00") + D("1.00")


# ================================= the life is measured, not assumed

def test_THE_CONTRACT_LIFE_IS_MEASURED_NOT_THE_NOMINAL_FIVE_MINUTES():
    """Every one of 19 captured BTC5m windows had its first print at TTE
    375.9-378.2 s -- before the nominal 300 s slot. Assuming a 300 s life
    would have hidden 48% of all prints behind an assumption."""
    v = WV.window_volume(_tape([_t(377, "0.50", "1"), _t(100, "0.50", "1")]),
                         expiry_ms=EXP)
    assert v["first_print_tte_s"] == 377.0
    assert v["observed_life_s"] == 377.0
    assert "not the contract's life" in v["life_why"]
    # a rate computed over 377 s, not 300
    assert v["prints_per_min"] == round(2 / (377.0 / 60.0), 3)


def test_prints_before_our_band_opens_are_counted_separately():
    v = WV.window_volume(_tape([_t(377, "0.50", "1"), _t(260, "0.50", "1"),
                                _t(100, "0.50", "1")]), expiry_ms=EXP)
    assert v["prints_before_our_band_opens_tte_over_280"] == 1
    assert v["prints_in_entry_region_240_280"] == 1
    assert v["prints_while_we_could_be_quoting_30_280"] == 2


def test_prints_after_the_withdraw_are_counted_separately():
    v = WV.window_volume(_tape([_t(20, "0.50", "9")]), expiry_ms=EXP)
    assert v["prints_after_our_withdraw_tte_30"] == 1
    assert v["contracts_after_our_withdraw"] == "9"
    assert v["prints_while_we_could_be_quoting_30_280"] == 0


# ================================= the side mapping is stated

def test_the_side_mapping_is_stated_and_no_NO_tape_is_invented():
    v = WV.window_volume(_tape([_t(100, "0.5", "1", "buy"),
                                _t(90, "0.5", "3", "sell")]), expiry_ms=EXP)
    assert v["yes_demand_prints"] == 1 and v["no_demand_prints"] == 1
    assert v["yes_demand_contracts"] == "1" and v["no_demand_contracts"] == "3"
    assert "no separate NO tape" in v["side_mapping"]


# ================================= sizes, gaps, refusals

def test_median_and_max_print_size_are_reported():
    v = WV.window_volume(_tape([_t(200, "0.5", "1"), _t(150, "0.5", "10"),
                                _t(100, "0.5", "714")]), expiry_ms=EXP)
    assert v["median_print_size"] == "10"
    assert v["max_print_size"] == "714"
    assert v["largest_print"]["amount"] == "714"


def test_the_longest_dead_gap_spans_the_measured_life():
    v = WV.window_volume(_tape([_t(300, "0.5", "1"), _t(40, "0.5", "1")]),
                         expiry_ms=EXP)
    assert v["longest_dead_gap_s"] == 260.0
    assert v["longest_dead_gap_position"] == "mid-window"


def test_an_unreadable_tape_is_refused_not_zeroed():
    v = WV.window_volume(_tape([], readable=False, why="timeout"),
                         expiry_ms=EXP, touch_depth="10")
    assert v["readable"] is False and v["why"] == "timeout"
    assert "contracts_traded" not in v
    assert v["touch_depth_displayed"] == "10"


def test_a_window_with_no_expiry_is_refused():
    v = WV.window_volume(_tape([_t(10, "0.5", "1")]), expiry_ms=None)
    assert v["readable"] is False


# ================================= our own fills

def test_our_fill_is_matched_by_tid_not_by_price():
    """Someone else trading at our price is not our fill."""
    v = WV.window_volume(_tape([_t(100, "0.31", "0.01", "sell", tid=99),
                                _t(90, "0.31", "5", "sell", tid=1)]),
                         expiry_ms=EXP, our_tids=["99"])
    assert v["our_fills_on_tape"] == 1
    assert v["our_fill_detail"][0]["amount"] == "0.01"


# ================================= the table

def test_the_table_ranks_by_executed_activity_not_by_depth():
    rows = [WV.window_volume(_tape([_t(100, "0.5", "1")]), expiry_ms=EXP,
                             touch_depth="750"),
            WV.window_volume(_tape([_t(100, "0.5", "500")]), expiry_ms=EXP,
                             touch_depth="10")]
    t = WV.table(rows)
    assert t[0]["NOTIONAL_USD"] == "250.0" and t[0]["TOUCH_DEPTH"] == "10"
    assert t[1]["TOUCH_DEPTH"] == "750"


def test_an_unreadable_window_still_appears_in_the_table():
    t = WV.table([WV.window_volume(_tape([], readable=False, why="404"),
                                   expiry_ms=EXP)])
    assert t[0]["PRINTS"] is None and "404" in t[0]["OUR_FILL"]


@pytest.mark.parametrize("bad", [None, "", 5])
def test_garbage_tape_in_is_refused(bad):
    assert WV.window_volume({"symbol": "S", "readable": False},
                            expiry_ms=bad)["readable"] is False
