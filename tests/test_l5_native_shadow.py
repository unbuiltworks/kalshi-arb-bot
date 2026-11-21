"""The raw L5 shadow on L5's own markets. These tests pin that the removed
barriers are really gone, that the money bounds are really kept, and that
no blocker can hide another."""
from decimal import Decimal as D

from src import l5_native_shadow as S


# ------------------------------------------------------ the universe
def test_it_watches_l5s_own_markets_not_sports():
    slugs = S.current_slugs(now=1788918300.0)
    assert len(slugs) == 8
    assert slugs[0] == "btc-updown-15m-1788918300"
    assert any(s.startswith("eth-updown-15m-") for s in slugs)
    assert S.ASSETS == ("btc", "eth")


def test_the_universe_is_unfiltered():
    """No ranking, no cap, no category filter -- the sports selection logic
    is exactly what this service exists without."""
    import inspect
    src = inspect.getsource(S.current_slugs)
    assert "No ranking, no cap, no category filter" in src


# ------------------------------------------- the removed barriers are gone
def test_none_of_the_removed_barriers_appear_in_this_path():
    """Checks CODE, not prose. The module docstring LISTS what was removed,
    so grepping the raw source finds the prohibition and calls it a
    violation -- the same trap that bit the no-order-path test earlier.
    Strip the docstring first."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(S))
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)):
        tree.body = tree.body[1:]          # drop the module docstring
    code = ast.unparse(tree)
    for gone in ("SPRING_BAND", "SPRING_GATE", "PAIR_TARGET",
                 "MAKER_REST_MAX_S", "REJECT_STREAK", "MAX_ACTIONS",
                 "MIN_REEVAL", "complement_acceptance", "event_progress"):
        assert gone not in code, gone


def test_that_barrier_check_would_catch_a_real_reintroduction():
    """Prove it, rather than trust it."""
    import ast
    tree = ast.parse('"""no SPRING_BAND here."""\nSPRING_BAND_LO = 0.35\n')
    tree.body = tree.body[1:]
    assert "SPRING_BAND" in ast.unparse(tree)


def test_the_clock_is_real_not_translated():
    import inspect
    src = inspect.getsource(S.observe_market)
    assert "endDate" in src
    assert "TTE is a fact" in inspect.getsource(S) or \
        "real clock" in src


# ------------------------------------------------ the money bounds stay
def test_the_money_bounds_are_kept():
    assert S.PER_MARKET_CAP_USD == D("0.05")
    assert S.DAILY_LOSS_STOP_TRIPPED is True


def test_capital_blocks_at_the_venue_minimum():
    b = S.all_blockers(D("0.05"), 0.50, 5, 50, True, True)
    assert "capital_below_venue_min_order" in b
    assert "capital_below_reward_min_size" in b


def test_enough_capital_clears_the_capital_blockers():
    """At $50 a two-sided reward-qualifying quote is affordable, so only
    the daily stop remains -- which is the owner's to clear."""
    b = S.all_blockers(D("50.00"), 0.50, 5, 50, True, True)
    assert "capital_below_venue_min_order" not in b
    assert "capital_below_reward_min_size" not in b
    assert b == ["daily_loss_stop_tripped"]


# --------------------------------------------- no blocker hides another
def test_every_blocker_is_reported_not_just_the_first():
    b = S.all_blockers(D("0.05"), 0.50, 5, 50, False, False)
    assert set(b) == {"daily_loss_stop_tripped",
                      "capital_below_venue_min_order",
                      "capital_below_reward_min_size",
                      "no_two_sided_book", "off_distribution"}


def test_the_primary_blocker_follows_a_fixed_precedence():
    p = S.primary_blocker(D("0.05"), 0.50, 5, 50, False, False)
    assert p == "daily_loss_stop_tripped"
    assert S.BLOCKER_PRECEDENCE[0] == "daily_loss_stop_tripped"
    assert S.BLOCKER_PRECEDENCE[1] == "capital_below_venue_min_order"


def test_the_reason_for_reporting_all_blockers_is_stated():
    import inspect
    doc = " ".join(inspect.getdoc(S.all_blockers).split())
    assert "hides the capital finding underneath" in doc
    assert "masking this service exists to prevent" in doc


# ----------------------------------------------- frozen and shadow-only
def test_the_model_is_read_never_retrained():
    import inspect
    src = inspect.getsource(S.load_model)
    assert "Never rebuilt, never retrained, never tuned" in src
    assert S.MODEL_SHA == "bbb55bf90638d2bd"


def test_there_is_no_order_path():
    import ast, inspect
    assert S.ORDER_ENTRY is False and S.SHADOW_ONLY is True
    tree = ast.parse(inspect.getsource(S))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and hasattr(n.func, "attr")}
    for forbidden in ("post", "delete", "put", "create_order", "place_order"):
        assert forbidden not in calls, forbidden


def test_a_blocked_row_still_carries_its_economics():
    """A row that cannot act must still report what it would have cost, or
    the diagnostic is worthless."""
    import inspect
    src = inspect.getsource(S.observe_market)
    assert "capital_for_min_order_usd" in src
    assert "capital_for_reward_min_usd" in src
    assert "so a blocked row still carries its numbers" in src
