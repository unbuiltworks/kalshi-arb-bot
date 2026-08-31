from decimal import Decimal as D
import time

import pytest

from src import kalshi_btc15m_policy_d_live_v1 as v1


def _print(i: int, side: str, qty: str = "1") -> v1.Print:
    return v1.Print(str(i), float(i), side, D("0.50"), D(qty))


def test_frozen_constants_are_exact_policy_d_values():
    assert v1.TAPE_WINDOW == 30
    assert v1.TAPE_THRESHOLD == D("0.10")
    assert v1.PRICE_FLOOR == D("0.30")
    assert v1.MIN_STANCE_SECONDS == 1.0
    assert v1.STOP_NEW_SECONDS == 90.0
    assert v1.QTY == D("1.00")
    assert v1.MAX_WINDOWS_DEFAULT == 4


def test_tape_requires_exactly_30_prints():
    tape = v1.Tape30()
    tape.ingest([_print(i, "yes") for i in range(29)])
    assert not tape.ready
    assert tape.contra_outcome() is None
    tape.ingest([_print(29, "yes")])
    assert tape.ready


def test_yes_accumulation_quotes_no_and_no_accumulation_quotes_yes():
    tape = v1.Tape30()
    tape.ingest([_print(i, "yes") for i in range(20)] + [_print(20+i, "no") for i in range(10)])
    assert tape.signed_yes_imbalance() == D("0.3333333333333333333333333333")
    assert tape.contra_outcome() == "NO"
    sig = v1.signal_from(tape, yes_bid=D("0.44"), no_bid=D("0.55"))
    assert sig is not None and sig.outcome == "NO" and sig.price == D("0.55")

    tape2 = v1.Tape30()
    tape2.ingest([_print(i, "no") for i in range(20)] + [_print(20+i, "yes") for i in range(10)])
    assert tape2.contra_outcome() == "YES"
    sig2 = v1.signal_from(tape2, yes_bid=D("0.43"), no_bid=D("0.56"))
    assert sig2 is not None and sig2.outcome == "YES" and sig2.price == D("0.43")


def test_exact_threshold_is_no_signal_and_price_floor_is_fail_closed():
    tape = v1.Tape30()
    # share-weighted +10% exactly: 16.5 YES / 13.5 NO.
    tape.ingest([_print(i, "yes", "1.1") for i in range(15)] + [_print(15+i, "no", "0.9") for i in range(15)])
    assert tape.signed_yes_imbalance() == D("0.10")
    assert tape.contra_outcome() is None

    tape2 = v1.Tape30()
    tape2.ingest([_print(i, "yes") for i in range(20)] + [_print(20+i, "no") for i in range(10)])
    assert v1.signal_from(tape2, yes_bid=D("0.70"), no_bid=D("0.29")) is None
    sig = v1.signal_from(tape2, yes_bid=D("0.70"), no_bid=D("0.30"))
    assert sig is not None and sig.price == D("0.30")


def test_best_bids_use_own_side_price_scale():
    yes, no = v1.best_bids({
        "orderbook_fp": {
            "yes_dollars": [["0.41", "100"], ["0.40", "20"]],
            "no_dollars": [["0.58", "50"], ["0.57", "20"]],
        }
    })
    assert yes == D("0.41")
    assert no == D("0.58")


def test_yes_payload_is_bid_and_no_payload_is_yes_book_ask_complement(monkeypatch):
    now = time.time()
    monkeypatch.setattr(v1, "_now", lambda: now)
    market = v1.Market("KXBTC15M-TEST", now - 10, now + 600)
    yes = v1.Signal("YES", D("0.41"), D("-0.20"), 30)
    no = v1.Signal("NO", D("0.58"), D("0.20"), 30)

    y = v1.order_payload(market=market, signal=yes, approval="approval-123", window_no=1)
    assert y["side"] == "bid"
    assert y["price"] == "0.4100"
    assert y["count"] == "1.00"
    assert y["post_only"] is True
    assert y["time_in_force"] == "good_till_canceled"
    assert y["expiration_time"] == int(now + 510)

    n = v1.order_payload(market=market, signal=no, approval="approval-123", window_no=2)
    assert n["side"] == "ask"
    assert n["price"] == "0.4200"  # buy NO 58c == ask YES at 42c
    assert n["count"] == "1.00"
    assert n["post_only"] is True


def test_payload_refuses_new_exposure_inside_90_seconds(monkeypatch):
    now = time.time()
    monkeypatch.setattr(v1, "_now", lambda: now)
    market = v1.Market("KXBTC15M-TEST", now - 10, now + 89)
    sig = v1.Signal("YES", D("0.40"), D("-0.20"), 30)
    with pytest.raises(RuntimeError):
        v1.order_payload(market=market, signal=sig, approval="approval-123", window_no=1)


def test_client_order_ids_are_unique_for_all_four_windows_and_compact():
    ids = [v1.client_order_id("a-very-long-live-approval-identifier", i) for i in range(1, 5)]
    assert len(ids) == len(set(ids)) == 4
    assert all(len(x) < 36 for x in ids)


def test_parse_print_uses_kalshi_fixed_point_fields():
    row = v1.parse_print({
        "trade_id": "abc",
        "created_time": "2026-08-31T02:00:00Z",
        "taker_side": "no",
        "yes_price_dollars": "0.37",
        "count_fp": "12.50",
    })
    assert row is not None
    assert row.taker_side == "no"
    assert row.yes_price == D("0.37")
    assert row.quantity == D("12.50")


def test_live_arm_requires_matching_one_use_approval(monkeypatch):
    monkeypatch.setenv(v1.LIVE_ENV, "YES")
    monkeypatch.setenv(v1.ARM_ID_ENV, "abc")
    monkeypatch.setenv(v1.ARM_ENV, "abc")
    assert v1.live_armed()
    monkeypatch.setenv(v1.ARM_ENV, "different")
    assert not v1.live_armed()


def test_max_windows_cannot_be_raised_above_four(monkeypatch):
    monkeypatch.setenv(v1.MAX_WINDOWS_ENV, "5")
    with pytest.raises(RuntimeError):
        v1.max_windows()
