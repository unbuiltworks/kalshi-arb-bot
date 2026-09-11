from decimal import Decimal as D

from src.gemini_l5_queue_shadow import (
    fee_safe_price_arms, movement, populated_price_arms, queue_metrics,
    recent_flow, retain_book,
)


def book():
    return {
        "bids": [
            {"price": "0.49", "amount": "10"},
            {"price": "0.47", "amount": "20"},
            {"price": "0.46", "amount": "30"},
        ],
        "asks": [
            {"price": "0.51", "amount": "11"},
            {"price": "0.53", "amount": "21"},
            {"price": "0.54", "amount": "31"},
        ],
    }


def zero_fee(px, qty):
    return D("0")


def test_retain_book_keeps_depth_not_just_touch():
    out = retain_book(book(), max_levels=2)
    assert [x["price"] for x in out["bids"]] == ["0.49", "0.47"]
    assert [x["price"] for x in out["asks"]] == ["0.51", "0.53"]


def test_yes_queue_ahead_is_higher_populated_bids_only():
    q = queue_metrics(book(), True, D("0.47"))
    assert q.touch_price == D("0.49")
    assert q.touch_depth == D("10")
    assert q.queue_ahead_qty == D("10")
    assert q.size_at_price == D("20")
    assert q.populated_levels_better == 1


def test_no_queue_ahead_uses_lower_yes_asks():
    # NO 0.47 == YES ask 0.53. Better NO bid is NO 0.49 == YES ask 0.51.
    q = queue_metrics(book(), False, D("0.47"))
    assert q.touch_price == D("0.49")
    assert q.touch_depth == D("11")
    assert q.queue_ahead_qty == D("11")
    assert q.size_at_price == D("21")
    assert q.populated_levels_better == 1


def test_price_arms_walk_populated_levels_not_nominal_ticks_yes():
    arms = populated_price_arms(book(), True, D("0.01"), max_back=2)
    assert [(a["arm"], a["price"]) for a in arms] == [
        ("touch", "0.49"),
        ("1_populated_level_back", "0.47"),
        ("2_populated_level_back", "0.46"),
    ]
    assert arms[1]["queue"]["queue_ahead_qty"] == "10"


def test_price_arms_walk_populated_levels_not_nominal_ticks_no():
    arms = populated_price_arms(book(), False, D("0.01"), max_back=2)
    assert [(a["arm"], a["price"]) for a in arms] == [
        ("touch", "0.49"),
        ("1_populated_level_back", "0.47"),
        ("2_populated_level_back", "0.46"),
    ]
    assert arms[1]["queue"]["queue_ahead_qty"] == "11"


def test_fee_safe_arm_does_not_import_old_pair_target():
    arms = fee_safe_price_arms(book(), False, D("0.01"),
                              first_px=D("0.49"), maker_fee_fn=zero_fee,
                              min_edge_after_fees=D("0.01"), max_back=2)
    # touch pair is 0.98 -> 2c edge and is allowed; no shared 0.98 cap is used.
    assert arms[0]["pair_cost_after_known_fees"] == "0.98"
    assert arms[0]["edge_after_known_fees"] == "0.02"
    assert arms[0]["fee_safe"] is True


def test_recent_flow_counts_prints_and_two_sided_activity():
    prints = [
        {"timestampms": 100_000, "amount": "2", "type": "buy"},
        {"timestampms": 110_000, "amount": "3", "type": "sell"},
        {"timestampms": 1_000, "amount": "99", "type": "buy"},
    ]
    out = recent_flow(prints, now_ms=120_000, window_s=60)
    assert out["prints"] == 2
    assert out["quantity"] == "5"
    assert out["two_sided_print_flow"] is True


def test_movement_uses_recent_two_sided_mids():
    rows = [
        {"t": 50, "bid": "0.45", "ask": "0.47"},
        {"t": 90, "bid": "0.48", "ask": "0.50"},
        {"t": 100, "bid": "0.49", "ask": "0.51"},
    ]
    out = movement(rows, now_s=100, window_s=20)
    assert out["observations"] == 2
    assert out["mid_move"] == "0.01"
    assert out["mid_range"] == "0.01"
