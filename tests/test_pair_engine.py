from src.gemini_maker.pair_engine import PairBook, PairInventory, decide


def _book(yb=0.47, ya=0.50, nb=0.50, na=0.53):
    return PairBook(yes_bid=yb, yes_ask=ya, no_bid=nb, no_ask=na)


def test_dead_no_pool_is_idle():
    d = decide(PairInventory(), _book(), 0.0, 200.0, in_reward_pool=False)
    assert d.state == "IDLE" and not d.quotes and d.cancel_all


def test_dead_reward_pool_quotes_q10_when_safe():
    d = decide(PairInventory(), _book(), 0.0, 200.0, in_reward_pool=True)
    assert d.state == "SUBSIDY"
    assert sorted(q.side for q in d.quotes) == ["no", "yes"]
    assert all(q.size == 10 for q in d.quotes)
    assert sum(q.price for q in d.quotes) <= 1.0


def test_subsidy_skips_if_q10_breaks_loss_cap():
    b = _book(yb=0.94, ya=0.95, nb=0.05, na=0.06)
    d = decide(PairInventory(), b, 0.0, 200.0, in_reward_pool=True, max_worst_loss=5.0)
    assert d.state == "IDLE" and not d.quotes
    assert d.reason == "SUBSIDY_UNSAFE_OR_UNQUALIFIED"


def test_hot_flat_joins_bids():
    d = decide(PairInventory(), _book(), 250.0, 200.0)
    assert d.state == "HOT"
    assert {q.side: q.price for q in d.quotes} == {"yes": 0.47, "no": 0.50}


def test_inventory_quotes_missing_side_only_and_completes_example():
    inv = PairInventory(); inv.apply_fill("yes", 5, 0.47)
    d = decide(inv, _book(yb=0.47, ya=0.50, nb=0.48, na=0.52), 250.0, 200.0)
    assert len(d.quotes) == 1
    q = d.quotes[0]
    assert q.side == "no" and q.reason == "LEAN_COMPLETE_PORTFOLIO"
    assert q.price == 0.51 and q.size == 5
    probe = inv.clone(); probe.apply_fill(q.side, q.size, q.price)
    assert abs(probe.residual) < 1e-9
    assert probe.avg_pair_cost() <= 1.0


def test_portfolio_ceiling_catches_old_pair_plus_expensive_new_fill():
    # Old pair costs 100c. Then 5 YES fill at 90c. Only 10c per NO remains
    # if the whole 10-pair portfolio is to finish <=100c average.
    inv = PairInventory()
    inv.apply_fill("yes", 5, 0.40); inv.apply_fill("no", 5, 0.60)
    inv.apply_fill("yes", 5, 0.90)
    b = _book(yb=0.90, ya=0.92, nb=0.30, na=0.32)
    d = decide(inv, b, 250.0, 200.0)
    assert len(d.quotes) == 1
    q = d.quotes[0]
    assert q.side == "no" and q.price == 0.10
    probe = inv.clone(); probe.apply_fill("no", 5, q.price)
    assert abs(probe.residual) < 1e-9
    assert abs(probe.avg_pair_cost() - 1.0) < 1e-9


def test_dead_with_inventory_manages_missing_side_not_subsidy():
    inv = PairInventory(); inv.apply_fill("yes", 3, 0.45)
    d = decide(inv, _book(nb=0.48, na=0.52), 0.0, 200.0, in_reward_pool=True)
    assert d.state == "INVENTORY_MANAGE"
    assert len(d.quotes) == 1 and d.quotes[0].side == "no"


def test_rescue_only_zone_and_bounded_premium():
    inv = PairInventory(); inv.apply_fill("yes", 5, 0.47)
    assert decide(inv, _book(), 0.0, 200.0).rescue is None
    assert decide(inv, _book(na=0.55), 0.0, 45.0).rescue is not None
    assert decide(inv, _book(na=0.60), 0.0, 45.0).rescue is None


def test_expiry_stop_allows_rescue_but_no_maker_quotes():
    inv = PairInventory(); inv.apply_fill("yes", 5, 0.47)
    d = decide(inv, _book(na=0.54), 500.0, 20.0)
    assert d.state == "EXPIRY_STOP" and not d.quotes and d.cancel_all
    assert d.rescue is not None


def test_flat_hot_quote_respects_one_sided_loss_cap():
    d = decide(PairInventory(), _book(), 250.0, 200.0, max_worst_loss=1.0)
    for q in d.quotes:
        p = PairInventory(); p.apply_fill(q.side, q.size, q.price)
        assert p.worst_settlement_loss() <= 1.0 + 1e-9
