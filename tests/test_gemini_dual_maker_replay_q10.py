from decimal import Decimal as D

from src.gemini_dual_maker_replay_q10 import Ledger, Market, Q


def test_flat_dual_quote_uses_both_best_maker_bids_when_trade_only_positive():
    ledger = Ledger("T")
    ledger.plan_flat(D("0.37"), D("0.45"))
    assert ledger.yes_quote == D("0.37")
    assert ledger.no_quote == D("0.55")


def test_one_sided_fill_freezes_same_side_and_preserves_complement():
    ledger = Ledger("T")
    ledger.plan_flat(D("0.37"), D("0.45"))
    assert ledger.fill("YES", evidence="x") is True
    assert ledger.residual_side == "YES"
    assert ledger.yes_quote is None
    assert ledger.no_quote == D("0.55")
    assert ledger.fill("YES", evidence="x") is False


def test_complement_completion_locks_trade_only_profit():
    ledger = Ledger("T")
    ledger.plan_flat(D("0.37"), D("0.45"))
    ledger.fill("YES", evidence="x")
    ledger.fill("NO", evidence="y")
    assert ledger.residual_side is None
    assert len(ledger.pairs) == 1
    pair = ledger.pairs[0]
    assert D(pair.locked_edge_total_after_fees) == D("0.70")
    assert D(pair.locked_edge_per_pair_after_fees) == D("0.07")


def test_tape_requires_single_public_print_at_least_q10():
    market = Market("T", "S", "BTC", "5m")
    market.on_book(D("0.37"), D("0.45"))
    market.on_trade(D("0.37"), D("9.9"), True)
    assert not market.tape.fills
    market.on_trade(D("0.37"), Q, True)
    assert len(market.tape.fills) == 1
    assert market.tape.residual_side == "YES"


def test_strict_trade_through_can_complete_precommitted_pair():
    market = Market("T", "S", "BTC", "5m")
    market.on_book(D("0.37"), D("0.45"))
    # ask below resting YES bid credits strict YES fill.
    market.on_book(D("0.30"), D("0.36"))
    assert market.strict.residual_side == "YES"
    # Original NO quote was 0.55 -> normalized YES threshold 0.45.
    # Bid moving above 0.45 trades through the preserved NO quote.
    market.on_book(D("0.46"), D("0.50"))
    assert market.strict.residual_side is None
    assert len(market.strict.pairs) == 1


def test_reward_eligible_book_update_is_measurement_only():
    market = Market("T", "S", "ETH", "15m")
    market.on_book(D("0.35"), D("0.41"))
    assert market.reward_eligible_book_updates == 1
    assert market.arb_eligible_book_updates == 1
