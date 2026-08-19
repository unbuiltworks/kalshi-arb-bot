from src import gemini_kalshi_target_freshness_guard as guard


def test_boundary_floor_is_15m_aligned():
    # 19:15:02 UTC-equivalent epoch arithmetic: floor to 19:15:00.
    now_ms = 1787166902000
    assert guard.current_boundary_ms(now_ms) == 1787166900000


def test_preboundary_target_is_rejected_after_rollover():
    row = ("68163.46", "TEST", 1787166899500)
    assert not guard.row_is_current_window(row, now_ms=1787166900100)


def test_postboundary_target_is_accepted():
    row = ("68163.46", "TEST", 1787166900619)
    assert guard.row_is_current_window(row, now_ms=1787166901000)


def test_malformed_row_fails_closed():
    assert not guard.row_is_current_window(None, now_ms=1787166901000)
    assert not guard.row_is_current_window(("x",), now_ms=1787166901000)
    assert not guard.row_is_current_window(("x", "TEST", "bad"), now_ms=1787166901000)
