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
