from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src import gemini_kalshi_xrp_maker_reprice_campaign as campaign


def test_round_trade_id_is_unique_and_bounded():
    a = campaign.round_trade_id("crypto-xrp-maker-demo", 1)
    b = campaign.round_trade_id("crypto-xrp-maker-demo", 2)
    assert a != b
    assert a.endswith("-r01")
    assert b.endswith("-r02")
    assert len(a) <= 80


def test_safe_to_continue_only_zero_fill_cancel_or_no_window():
    assert campaign.safe_to_continue({"status": "NO_FILLABLE_PARTIAL_SAFE_AT_BID_WINDOW", "realized": False})
    assert campaign.safe_to_continue({
        "status": "MAKER_CANCELED_ZERO_FILL",
        "cancel": {"ok": True},
        "filled_quantity": "0",
        "realized": False,
    })
    assert not campaign.safe_to_continue({
        "status": "MAKER_CANCELED_ZERO_FILL",
        "cancel": {"ok": False},
        "realized": False,
    })


def test_safe_to_continue_stops_on_fill_or_hard_halt():
    assert not campaign.safe_to_continue({"status": "BOTH_FILLED_RECONCILED_NOT_SETTLED", "filled_quantity": "1"})
    assert not campaign.safe_to_continue({"status": "FIRST_FILL_BELOW_3C_FLATTENED_HARD_HALT", "filled_quantity": "0.25"})
    assert not campaign.safe_to_continue({"status": "HARD_HALT_EXCEPTION"})


def test_select_viable_pair_skips_expiring_current_window():
    now = datetime(2026, 8, 24, 17, 14, 0, tzinfo=timezone.utc)
    expiring = SimpleNamespace(asset="XRP", window_end=now + timedelta(seconds=60))
    next_window = SimpleNamespace(asset="XRP", window_end=now + timedelta(minutes=16))
    other_asset = SimpleNamespace(asset="SOL", window_end=now + timedelta(minutes=15))
    chosen = campaign.select_viable_pair(
        [expiring, other_asset, next_window], now_ts=now.timestamp()
    )
    assert chosen is next_window
