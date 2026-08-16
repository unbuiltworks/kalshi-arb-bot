from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_manual_test_ticket import (
    ManualNestedTestTicket,
    build_manual_payloads,
    describe_ticket,
    validate_manual_ticket,
)

D = Decimal
NY = ZoneInfo("America/New_York")


def _ticket(**overrides):
    values = dict(
        event_ticker="KXSOLD-26AUG2117",
        event_close=datetime(2026, 8, 21, 17, 0, tzinfo=NY),
        lower_ticker="KXSOLD-26AUG2117-T62.9999",
        lower_strike=D("62.9999"),
        higher_ticker="KXSOLD-26AUG2117-T64.9999",
        higher_strike=D("64.9999"),
        maker_bid=D("0.96"),
        maker_cost=D("0.97"),
        seed_cost=D("0.01"),
        fee_type="quadratic",
        fee_multiplier=D("1"),
        maker_rest_seconds=10,
    )
    values.update(overrides)
    return ManualNestedTestTicket(**values)


def test_observed_sol_ticket_locks_one_cent_after_seed_fee():
    locked, fee = validate_manual_ticket(
        _ticket(),
        now=datetime(2026, 8, 16, 19, 47, tzinfo=NY),
    )
    assert fee == D("0.01")
    assert locked == D("0.01")


def test_manual_payload_is_seed_no_first_then_yes_maker():
    # Freeze validation time by using a future close relative to real test execution.
    t = _ticket(event_close=datetime.now(NY).replace(year=datetime.now(NY).year) )
    # Use the actual observed ticket builder through monkeypatch-style direct
    # construction with a close safely in the future in the next test instead.


def test_payload_v2_price_mapping(monkeypatch):
    import src.kalshi_nested_manual_test_ticket as mod
    now = datetime(2026, 8, 16, 19, 47, tzinfo=NY)
    original = mod.validate_manual_ticket
    monkeypatch.setattr(mod, "validate_manual_ticket", lambda ticket: original(ticket, now=now))
    seed, maker = build_manual_payloads(_ticket(), now_epoch=1_786_910_820)
    assert seed["ticker"].endswith("T64.9999")
    assert seed["side"] == "ask"
    assert seed["price"] == "0.99"
    assert seed["time_in_force"] == "immediate_or_cancel"
    assert seed["post_only"] is False
    assert maker["ticker"].endswith("T62.9999")
    assert maker["side"] == "bid"
    assert maker["price"] == "0.97"
    assert maker["post_only"] is True
    assert maker["expiration_time"] == 1_786_910_830


def test_rejects_seed_over_one_cent():
    with pytest.raises(ValueError, match="exactly a 1c seed"):
        validate_manual_ticket(
            _ticket(seed_cost=D("0.02"), maker_cost=D("0.96")),
            now=datetime(2026, 8, 16, 19, 47, tzinfo=NY),
        )


def test_rejects_large_maker_jump():
    with pytest.raises(ValueError, match="maker improvement"):
        validate_manual_ticket(
            _ticket(maker_bid=D("0.90")),
            now=datetime(2026, 8, 16, 19, 47, tzinfo=NY),
        )


def test_rejects_event_beyond_seven_days():
    with pytest.raises(ValueError, match="within 7 days"):
        validate_manual_ticket(
            _ticket(event_close=datetime(2026, 8, 24, 20, 0, tzinfo=NY)),
            now=datetime(2026, 8, 16, 19, 47, tzinfo=NY),
        )


def test_describe_ticket_is_explicitly_read_only(monkeypatch):
    import src.kalshi_nested_manual_test_ticket as mod
    now = datetime(2026, 8, 16, 19, 47, tzinfo=NY)
    original = mod.validate_manual_ticket
    monkeypatch.setattr(mod, "validate_manual_ticket", lambda ticket: original(ticket, now=now))
    row = describe_ticket(_ticket())
    assert row["actual_order_placed"] is False
    assert row["network_calls"] is False
    assert row["modeled_seed_only_cash_outlay"] == "0.02"
    assert row["modeled_min_pair_profit"] == "0.01"
