from decimal import Decimal

from src import gemini_kalshi_xrp_first_fill_watch as watch
from src import gemini_kalshi_xrp_maker_triggered_canary as triggered

D = Decimal


def test_safe_zero_mutation_dominance_blocker_continues():
    stop, reason = watch.terminal_requires_stop({
        "status": "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND",
        "terminal_status": "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER",
        "terminal_result": {"order_entry": False, "realized": False},
    })
    assert not stop
    assert reason == "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER"


def test_same_blocker_stops_if_order_entry_happened():
    stop, _ = watch.terminal_requires_stop({
        "status": "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND",
        "terminal_status": "DOMINANCE_NOT_AUTHORIZED_FOR_GEMINI_MAKER",
        "terminal_result": {"order_entry": True, "realized": False},
    })
    assert stop


def test_fill_or_hard_halt_still_stops():
    stop, _ = watch.terminal_requires_stop({
        "status": "CAMPAIGN_STOPPED_ON_TERMINAL_ROUND",
        "terminal_status": "BOTH_FILLED_RECONCILED_NOT_SETTLED",
        "terminal_result": {"order_entry": True, "filled_quantity": "1"},
    })
    assert stop
    stop2, _ = watch.terminal_requires_stop({"status": "HARD_HALT_EXCEPTION"})
    assert stop2


def test_sticky_rest_preserves_expiry_cutoff():
    assert watch.STICKY_REST_SECONDS >= 60
    assert watch.STICKY_MIN_SECONDS_TO_EXPIRY == watch.STICKY_REST_SECONDS + watch.BASE_MIN_SECONDS_TO_EXPIRY
    assert watch.BASE_MIN_SECONDS_TO_EXPIRY == 90


def test_cushioned_quote_only_raises_admission_target_and_restores_hard_floor():
    seen = {}
    original = watch._ORIGINAL_FILLABLE_QUOTE

    def fake_quote(**kwargs):
        seen["target"] = triggered.TARGET
        return {"maker_quote": "0.50"}

    try:
        watch._ORIGINAL_FILLABLE_QUOTE = fake_quote
        before = triggered.TARGET
        result = watch.cushioned_fillable_quote(g=None, k=None, market={}, fee_meta=None)
        assert result == {"maker_quote": "0.50"}
        assert seen["target"] == before + watch.ENTRY_CUSHION
        assert triggered.TARGET == before
        assert before == D("0.03")
    finally:
        watch._ORIGINAL_FILLABLE_QUOTE = original
