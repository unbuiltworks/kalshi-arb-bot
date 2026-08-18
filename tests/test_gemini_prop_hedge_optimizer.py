from decimal import Decimal as D

from src.gemini_prop_hedge_optimizer import (
    best_guaranteed_buy_no_hedge,
    expected_buy_no_sweep,
    guaranteed_pair_pnl,
)


def test_tucker_depth_sizes_down_one_sportsbook_cent() -> None:
    # Live depth observed 2026-08-17: 36.95 NO available at 0.77, then 100 at 0.94.
    yes_bids = [(D("0.23"), D("36.95")), (D("0.06"), D("100"))]
    plan = best_guaranteed_buy_no_hedge(
        yes_bids=yes_bids,
        sportsbook_decimal_odds=D("8.0"),  # FanDuel +700
    )
    assert plan is not None
    assert plan.sportsbook_stake == D("4.61")
    assert plan.sportsbook_total_return == D("36.880")
    assert plan.gemini_quantity == D("36.88")
    assert plan.gemini_no_limit_price == D("0.77")
    assert plan.available_depth_through_limit == D("36.95")
    assert plan.gemini_guaranteed_max_raw_debit == D("28.3976")
    assert plan.gemini_guaranteed_max_fee == D("0.46")
    assert plan.worst_case_locked_profit == D("3.4124")
    assert plan.pnl_if_prop_hits_guaranteed == D("3.4124")
    assert plan.pnl_if_prop_misses_guaranteed == D("3.4124")


def test_one_hundredth_over_top_depth_reaches_94c_level() -> None:
    yes_bids = [(D("0.23"), D("36.95")), (D("0.06"), D("100"))]
    sweep = expected_buy_no_sweep(yes_bids, D("36.96"))
    assert sweep is not None
    raw, fees, fills = sweep
    assert raw == D("28.4609")
    assert fees == D("0.47")
    assert [(f.no_price, f.quantity) for f in fills] == [
        (D("0.77"), D("36.95")),
        (D("0.94"), D("0.01")),
    ]


def test_94c_fok_limit_is_not_guaranteed_arb_at_plus_700() -> None:
    q = D("36.96")
    sportsbook_stake = D("4.62")
    # Worst-case FOK debit if all contracts could execute at the 0.94 limit.
    gemini_worst_debit = q * D("0.94") + D("0.15")
    hit, miss, worst = guaranteed_pair_pnl(
        sportsbook_stake=sportsbook_stake,
        sportsbook_decimal_odds=D("8.0"),
        gemini_quantity=q,
        gemini_max_total_debit=gemini_worst_debit,
    )
    assert hit < 0
    assert miss < 0
    assert worst < 0


def test_manual_ticket_from_screenshot_is_rejected() -> None:
    # Screenshot pair: FD $5.28 @ +700 (total return $42.24) and Gemini
    # $35.59 max debit for $36.96 payout. It cannot be a two-outcome lock.
    hit, miss, worst = guaranteed_pair_pnl(
        sportsbook_stake=D("5.28"),
        sportsbook_decimal_odds=D("8.0"),
        gemini_quantity=D("36.96"),
        gemini_max_total_debit=D("35.59"),
    )
    assert hit == D("1.37")
    assert miss == D("-3.91")
    assert worst == D("-3.91")
