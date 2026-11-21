"""The minimum-size census — P3.

Polymarket declares orderMinSize 5. An L5 action below it is not one we place
worse; it is one we cannot place at all, so the fraction below the minimum
bounds every executable-economics result that follows.

Two ways to get the answer wrong, and both are pinned here: counting a large
order's several small FILLS as several small INTENTS (a constraint invented by
our own intent construction), and letting "only 16% of actions" pass for "only
16% of the money".
"""
from decimal import Decimal as D

import analysis.l5.min_size.study as M


def _f(qty, price="0.5", *, t=0.0, outcome="Up", w="w1", seq=1):
    return {"window_id": w, "market_slug": "m", "asset": "BTC",
            "duration_minutes": "15", "outcome": outcome, "side": "BUY",
            "price": str(price), "quantity": str(qty),
            "dollars": str(D(str(qty)) * D(str(price))),
            "timestamp": str(t), "trade_seq": str(seq),
            "seconds_to_settlement": "100"}


# ------------------------------------------------- a fill is not an order

def test_fills_of_one_order_are_grouped():
    """Three fills, one price, one second apart: one order of 6, not three
    sub-minimum ones."""
    od = M.orders([_f(2, t=0), _f(2, t=1), _f(2, t=2)])
    assert len(od) == 1 and od[0]["qty"] == D("6")
    assert od[0]["n_fills"] == 3


def test_a_gap_makes_them_separate_decisions():
    od = M.orders([_f(2, t=0), _f(2, t=100)])
    assert len(od) == 2


def test_a_different_price_is_not_merged():
    """Conservative on purpose: merging eagerly would understate the
    constraint, which is the direction that flatters us."""
    od = M.orders([_f(2, "0.50", t=0), _f(2, "0.51", t=1)])
    assert len(od) == 2


def test_a_different_outcome_is_not_merged():
    od = M.orders([_f(2, t=0), _f(2, t=1, outcome="Down")])
    assert len(od) == 2


def test_a_different_market_is_not_merged():
    od = M.orders([_f(2, t=0), _f(2, t=1, w="w2")])
    assert len(od) == 2


def test_only_buys_are_counted():
    """A SELL is an exit; the minimum-size question is about acquiring."""
    rows = [_f(2), dict(_f(2), side="SELL")]
    assert len(M.buys(rows)) == 1


# --------------------------------------- actions are not dollars

def test_the_action_count_and_the_money_are_reported_separately():
    """Many tiny actions can be a trivial share of the notional, and reading
    one for the other is the whole trap."""
    units = [{"qty": D("1"), "price": D("0.1"), "dollars": D("0.1")}] * 90
    units += [{"qty": D("100"), "price": D("0.5"), "dollars": D("50")}] * 10
    e = M.economics(units)
    assert e["actions_below_min"] == 90
    assert float(e["notional_frac_below_min"]) < 0.02


def test_the_distribution_counts_volume_below_the_minimum_too():
    d = M.distribution([D("1")] * 9 + [D("91")])
    assert d["below_min"] == 9
    assert d["below_min_frac"] == 0.9
    assert float(d["volume_frac_below_min"]) == 0.09


def test_the_minimum_is_the_venue_declared_one():
    assert M.PM_MIN_SIZE == D("5") and M.PM_TICK == D("0.01")


def test_an_empty_distribution_is_not_a_zero():
    assert M.distribution([]) == {"n": 0}


# ------------------------------------------- aggregation is an ADAPTATION

def test_aggregating_to_the_minimum_is_labelled_adapted_never_fidelity():
    a = M.aggregation([{"qty": D("2"), "price": D("0.5"), "dollars": D("1"),
                        "outcome": "Up", "window_id": "w", "t_first": 0.0},
                       {"qty": D("4"), "price": D("0.5"), "dollars": D("2"),
                        "outcome": "Up", "window_id": "w", "t_first": 1.0}])
    assert a["label"] == "L5_ADAPTED"
    assert "never fidelity" in a["labels"]
    assert a["reached"] == 1


def test_a_side_change_before_the_minimum_means_it_cannot_be_placed():
    """Waiting past a switch would leave us holding what L5 did not."""
    a = M.aggregation([{"qty": D("2"), "price": D("0.5"), "dollars": D("1"),
                        "outcome": "Up", "window_id": "w", "t_first": 0.0},
                       {"qty": D("9"), "price": D("0.5"), "dollars": D("4.5"),
                        "outcome": "Down", "window_id": "w", "t_first": 1.0}])
    assert a["reached"] == 0
    assert a["never_reached"] == 1 and a["side_changed"] == 1


def test_a_wait_longer_than_the_intent_lifetime_is_counted():
    a = M.aggregation([{"qty": D("2"), "price": D("0.5"), "dollars": D("1"),
                        "outcome": "Up", "window_id": "w", "t_first": 0.0},
                       {"qty": D("4"), "price": D("0.5"), "dollars": D("2"),
                        "outcome": "Up", "window_id": "w",
                        "t_first": M.INTENT_VALID_S + 1}])
    assert a["outlived_intent"] == 1


def test_the_concession_is_charged_on_the_shares_it_recovers():
    """Marginal on both sides. Crediting one quantity and debiting another
    would inflate the ratio in our favour -- and we then REJECT aggregation
    on that ratio, so the bias would run the wrong way."""
    a = M.aggregation([{"qty": D("2"), "price": D("0.50"), "dollars": D("1"),
                        "outcome": "Up", "window_id": "w", "t_first": 0.0},
                       {"qty": D("4"), "price": D("0.60"), "dollars": D("2.4"),
                        "outcome": "Up", "window_id": "w", "t_first": 1.0}])
    assert a["notional_recovered"] == "1.00"
    assert a["extra_cost_paid"] == "0.20"            # 0.10 x 2 shares
    assert a["extra_cost_if_charged_on_whole_order"] == "0.50"   # x 5
    assert a["cost_per_dollar_recovered"] == 0.2


def test_the_cost_is_expressed_against_l5s_own_measured_edge():
    """A cent sounds small until it is put beside 0.037% per dollar."""
    a = M.aggregation([{"qty": D("2"), "price": D("0.50"), "dollars": D("1"),
                        "outcome": "Up", "window_id": "w", "t_first": 0.0},
                       {"qty": D("4"), "price": D("0.60"), "dollars": D("2.4"),
                        "outcome": "Up", "window_id": "w", "t_first": 1.0}])
    assert a["l5_drag_per_dollar"] == 0.00037
    assert a["cost_vs_l5_edge_x"] > 100


def test_an_action_already_at_the_minimum_needs_no_aggregation():
    a = M.aggregation([{"qty": D("5"), "price": D("0.5"), "dollars": D("2.5"),
                        "outcome": "Up", "window_id": "w", "t_first": 0.0}])
    assert a["reached"] == 0 and a["never_reached"] == 0


def test_the_model_intents_hardcoded_size_is_named_in_the_report():
    """FIDELITY v0.2 sets incremental_qty=1 unconditionally, which is below
    the venue minimum whatever L5's distribution looks like."""
    import inspect
    src = inspect.getsource(M.report)
    assert "incremental_qty=1" in src and "unplaceable" in src


def test_the_report_says_none_of_this_is_a_fill_or_realized_pnl():
    import inspect
    src = inspect.getsource(M.report)
    assert "placeability census" in src


def test_the_model_intent_really_does_hardcode_one():
    """Pinned against the engine, not against the prose describing it."""
    import inspect
    from src.l5_shadow import service as S
    assert 'qty = D("1")' in inspect.getsource(S.model_intent)
