from src.polymarket_l5.disciplined_clone import ReplayState, accept_buy, settlement_pnl


def test_pair_ceiling_rejects_expensive_opposite_fill_and_leaves_residual():
    s = ReplayState()
    ok, _, _ = accept_buy(s, outcome="UP", price=0.70, shares=10, pair_ceiling=0.99, residual_cap=None)
    assert ok
    ok, reason, pair = accept_buy(s, outcome="DOWN", price=0.35, shares=10, pair_ceiling=0.99, residual_cap=None)
    assert not ok
    assert reason == "PAIR_CEILING"
    assert round(pair, 6) == 1.05
    assert s.up_qty == 10 and s.down_qty == 0
    assert settlement_pnl(s, "Up") == 3.0
    assert settlement_pnl(s, "Down") == -7.0


def test_pair_ceiling_accepts_profitable_pair():
    s = ReplayState()
    assert accept_buy(s, outcome="UP", price=0.60, shares=10, pair_ceiling=0.99, residual_cap=None)[0]
    ok, reason, pair = accept_buy(s, outcome="DOWN", price=0.35, shares=10, pair_ceiling=0.99, residual_cap=None)
    assert ok and reason == "ACCEPT"
    assert round(pair, 6) == 0.95
    assert round(settlement_pnl(s, "Up"), 6) == 0.5
    assert round(settlement_pnl(s, "Down"), 6) == 0.5


def test_residual_cap_only_blocks_new_dominant_inventory_after_both_sides_exist():
    s = ReplayState()
    assert accept_buy(s, outcome="UP", price=0.60, shares=10, pair_ceiling=None, residual_cap=0.20)[0]
    assert accept_buy(s, outcome="DOWN", price=0.35, shares=9, pair_ceiling=None, residual_cap=0.20)[0]
    ok, reason, _ = accept_buy(s, outcome="UP", price=0.65, shares=5, pair_ceiling=None, residual_cap=0.20)
    assert not ok
    assert reason == "RESIDUAL_CAP"
    assert s.up_qty == 10 and s.down_qty == 9


def test_residual_reducing_fill_is_allowed_even_when_current_ratio_is_high():
    s = ReplayState()
    assert accept_buy(s, outcome="UP", price=0.60, shares=10, pair_ceiling=None, residual_cap=0.20)[0]
    # No opposite side yet: opening inventory is permitted.
    ok, _, _ = accept_buy(s, outcome="DOWN", price=0.30, shares=2, pair_ceiling=None, residual_cap=0.20)
    assert ok
    assert s.residual_ratio == 0.8
    # Another DOWN buy reduces residual and must not be blocked by the cap.
    ok, reason, _ = accept_buy(s, outcome="DOWN", price=0.30, shares=6, pair_ceiling=None, residual_cap=0.20)
    assert ok and reason == "ACCEPT"
    assert round(s.residual_ratio, 6) == 0.2
