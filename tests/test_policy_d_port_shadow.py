"""Frozen-D shadow evaluator: direction mapping, anti-lookahead, fill proxy."""

from __future__ import annotations

from src.policy_d_port.shadow_eval import (
    Tape, evaluate_market, maker_fee, _print_direction, _bbo_quotes,
)

T0 = 1_788_000_000.0


def kprint(t, taker_side, yes_price, count):
    return {"t": t, "venue": "kalshi", "kind": "print", "market": "M",
            "taker_side": taker_side, "yes_price": str(yes_price), "count": str(count)}


def kbbo(t, yes_bid, no_bid):
    return {"t": t, "venue": "kalshi", "kind": "bbo", "market": "M",
            "yes_bids": [[str(yes_bid), "100"]], "no_bids": [[str(no_bid), "100"]]}


def test_direction_mapping():
    assert _print_direction({"venue": "kalshi", "taker_side": "yes"}) == "Up"
    assert _print_direction({"venue": "kalshi", "taker_side": "no"}) == "Down"
    assert _print_direction({"venue": "gemini", "buyer_is_maker": False}) == "Up"
    assert _print_direction({"venue": "gemini", "buyer_is_maker": True}) == "Down"


def test_bbo_quotes_both_venues():
    q = _bbo_quotes({"venue": "kalshi", "yes_bids": [["0.51", "1"], ["0.52", "1"]],
                     "no_bids": [["0.46", "1"], ["0.47", "1"]]})
    assert q == {"Up": 0.52, "Down": 0.47}
    q = _bbo_quotes({"venue": "gemini", "bids": [["0.40", "5"]], "asks": [["0.55", "5"]]})
    assert q["Up"] == 0.40 and q["Down"] == 0.45


def test_contra_stance_fills_on_print_through():
    # Tape floods Up for 30+ prints -> Down side is light -> stance on Down.
    events = [kbbo(T0, 0.55, 0.40)]
    events += [kprint(T0 + i * 0.1, "yes", 0.55, 5) for i in range(31)]
    # After stance active >= 1s, a taker_side=yes print at no-price <= 0.40
    # (yes_price >= 0.60) hits our hypothetical Down bid at 0.40.
    events.append(kprint(T0 + 10.0, "yes", 0.62, 8))
    result = evaluate_market(events)
    assert len(result["fills"]) == 1
    fill = result["fills"][0]
    assert fill["side"] == "Down" and fill["price"] == 0.40 and fill["qty"] == 8


def test_no_fill_without_min_stance_age():
    events = [kbbo(T0, 0.55, 0.40)]
    events += [kprint(T0 + i * 0.01, "yes", 0.55, 5) for i in range(31)]
    events.append(kprint(T0 + 0.5, "yes", 0.62, 8))  # stance < 1s old
    assert evaluate_market(events)["fills"] == []


def test_price_floor_blocks_cheap_side():
    events = [kbbo(T0, 0.75, 0.20)]  # Down joinable only at 0.20 < floor
    events += [kprint(T0 + i * 0.1, "yes", 0.75, 5) for i in range(31)]
    events.append(kprint(T0 + 10.0, "yes", 0.85, 8))
    assert evaluate_market(events)["fills"] == []


def test_one_fill_per_quote_until_bbo_refresh():
    events = [kbbo(T0, 0.55, 0.40)]
    events += [kprint(T0 + i * 0.1, "yes", 0.55, 5) for i in range(31)]
    events.append(kprint(T0 + 10.0, "yes", 0.62, 8))
    events.append(kprint(T0 + 10.5, "yes", 0.62, 8))  # same resting order gone
    result = evaluate_market(events)
    assert len(result["fills"]) == 1
    events.append(kbbo(T0 + 11.0, 0.55, 0.40))        # re-quote
    events.append(kprint(T0 + 13.0, "yes", 0.62, 8))
    result = evaluate_market(events)
    assert len(result["fills"]) == 2


def test_tape_window_and_imbalance():
    tape = Tape()
    for _ in range(30):
        tape.add("Up", 1.0)
    assert tape.imbalance_toward("Up") == 1.0
    assert tape.imbalance_toward("Down") == -1.0
    for _ in range(30):
        tape.add("Down", 1.0)
    assert tape.imbalance_toward("Down") == 1.0  # window slid fully


def test_maker_fee_rounds_up():
    assert maker_fee(0.5, 10) == 0.05   # 0.04375 -> ceil cent
    assert maker_fee(0.4, 8) == 0.04    # 0.0336 -> 0.04
