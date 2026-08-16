from src.kalshi_nested_live_candidate import first_probe_row_is_fillable_enough


def _row(bid="0.94", quote="0.95", improvement="0.01"):
    return {
        "current_best_bid": bid,
        "shadow_quote": quote,
        "quote_improvement": improvement,
    }


def test_accepts_one_cent_improvement_on_real_bid():
    assert first_probe_row_is_fillable_enough(_row())


def test_accepts_two_cent_cap():
    assert first_probe_row_is_fillable_enough(_row("0.93", "0.95", "0.02"))


def test_rejects_current_thirty_nine_cent_jump():
    assert not first_probe_row_is_fillable_enough(_row("0.56", "0.95", "0.39"))


def test_rejects_empty_bid_even_if_telemetry_says_small_improvement():
    assert not first_probe_row_is_fillable_enough(_row("0", "0.01", "0.01"))


def test_rejects_inconsistent_improvement_telemetry():
    assert not first_probe_row_is_fillable_enough(_row("0.94", "0.95", "0.02"))
