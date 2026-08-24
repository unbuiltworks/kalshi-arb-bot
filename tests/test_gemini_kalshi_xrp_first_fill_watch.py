from src import gemini_kalshi_xrp_first_fill_watch as watch


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
