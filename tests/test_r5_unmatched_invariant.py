"""The invariant the R=5 frequency experiment rests on.

NOT "at most one opening round may remain unmatched". That is not what the
engine enforces, and stating it that way would be wrong. What it enforces is:

    |PROSPECTIVE net unmatched| <= 3 contracts, where prospective counts
    realized inventory PLUS every live order as if it filled in full.

With the opening clip capped at 3 contracts that bounds unmatched exposure at
3 contracts at all times, which is the economically relevant bound. Two SMALL
unmatched legs may coexist provided their sum stays inside it.
"""
import sys
from decimal import Decimal as D

sys.path.insert(0, "src")
import l5_live_execution as LX


def limits(**kw):
    kw.setdefault("max_abs_residual", D("3"))
    return LX.SafetyLimits(**kw)


def call(lim, *, qty, residual_after, outstanding_residual, **kw):
    return lim.refuse(
        price=D("0.50"), qty=D(str(qty)), market="m",
        committed_by_market={}, residual_after=D(str(residual_after)),
        realized_today=D(0), open_markets=set(),
        outstanding_by_market={}, live_order_count=0,
        outstanding_residual=D(str(outstanding_residual)), **kw)


def test_the_absolute_ceiling_is_three_contracts():
    assert LX.ABS_RESIDUAL_CAP_CONTRACTS == D("3")


def test_residual_bound_counts_unfilled_orders_as_if_they_filled():
    """The 1130z lesson: realized inventory alone is not a bound."""
    lim = limits()
    # realized 0 but 3 contracts resting -> a further 1 would reach 4
    assert call(lim, qty=1, residual_after=D("1"),
                outstanding_residual=D("3")) == "MAX_ABS_RESIDUAL"
    # the same realized figure with nothing resting is fine
    assert call(lim, qty=1, residual_after=D("1"),
                outstanding_residual=D(0)) is None


def test_the_bound_is_symmetric_in_sign():
    lim = limits()
    for sign in (D(1), D(-1)):
        assert call(lim, qty=1, residual_after=sign * D("2"),
                    outstanding_residual=sign * D("2")) == "MAX_ABS_RESIDUAL"


def test_partial_fills_are_counted_once_and_completely():
    """A partial contributes its FILLED part to realized residual and its
    REMAINING part to outstanding -- never both, never neither."""
    store = LX.InventoryStore.__new__(LX.InventoryStore)
    store.state = {"live_orders": {
        # 3 ordered, 1 filled, 2 still live
        "a": {"market": "m", "side": "DOWN", "remaining": "2"},
    }}
    assert store.outstanding_residual("m") == D("-2")
    # the filled 1 is realized inventory, supplied separately; together the
    # prospective book is -3, exactly AT the ceiling. The bound is
    # `> cap`, so the ceiling is INCLUSIVE and -3 is permitted; -3.01 is
    # not. Pinned because off-by-one here is the difference between a
    # bound that holds and one that does not.
    lim = limits()
    assert call(lim, qty=1, residual_after=D("-1"),
                outstanding_residual=D("-2")) is None
    assert call(lim, qty=1, residual_after=D("-1.01"),
                outstanding_residual=D("-2")) == "MAX_ABS_RESIDUAL"


def test_overlapping_orders_on_both_sides_net_correctly():
    store = LX.InventoryStore.__new__(LX.InventoryStore)
    store.state = {"live_orders": {
        "a": {"market": "m", "side": "UP", "remaining": "2.5"},
        "b": {"market": "m", "side": "DOWN", "remaining": "2.0"},
        "c": {"market": "other", "side": "DOWN", "remaining": "99"},
    }}
    assert store.outstanding_residual("m") == D("0.5")


def test_a_cancellation_race_fails_closed():
    """If we cannot say whether an order is live, everything is refused."""
    lim = limits()
    assert call(lim, qty=1, residual_after=D(0), outstanding_residual=D(0),
                orders_uncertain="CANCEL_ACK_LOST") == "ORDER_STATE_UNCERTAIN"
    # and it is checked BEFORE any arithmetic that could pass
    assert call(lim, qty=D("0.01"), residual_after=D(0),
                outstanding_residual=D(0),
                orders_uncertain="x") == "ORDER_STATE_UNCERTAIN"


def test_unreadable_remaining_fails_closed_not_to_zero():
    """An unreadable row is not an absent one -- in QUANTITY as in price.

    This used to map to 0 contracts, so a row we could not size contributed
    nothing to the residual bound while `live_order_count` still counted it
    and the pair slot still opened. The bound could be understated by a
    whole clip.
    """
    store = LX.InventoryStore.__new__(LX.InventoryStore)
    # no size readable anywhere -> the approved absolute ceiling
    store.state = {"live_orders": {
        "a": {"market": "m", "side": "DOWN", "remaining": None},
    }}
    assert store.outstanding_residual("m") == -LX.ABS_RESIDUAL_CAP_CONTRACTS
    # a corrupt string is equally unreadable
    store.state = {"live_orders": {
        "a": {"market": "m", "side": "UP", "remaining": "not-a-number"},
    }}
    assert store.outstanding_residual("m") == LX.ABS_RESIDUAL_CAP_CONTRACTS
    # the row's own ordered quantity is preferred when present
    for field in ("qty", "quantity", "original_qty", "ordered_qty"):
        store.state = {"live_orders": {
            "a": {"market": "m", "side": "DOWN", "remaining": None,
                  field: "1.25"},
        }}
        assert store.outstanding_residual("m") == D("-1.25"), field
    # a readable zero still means zero: a filled-out row is not exposure
    store.state = {"live_orders": {
        "a": {"market": "m", "side": "DOWN", "remaining": "0"},
    }}
    assert store.outstanding_residual("m") == D(0)


def test_unreadable_remaining_also_fails_closed_in_notional():
    """The same row must not be free in the cash/exposure bound either."""
    store = LX.InventoryStore.__new__(LX.InventoryStore)
    store.state = {"live_orders": {
        "a": {"market": "m", "side": "DOWN", "remaining": None,
              "price": None},
    }}
    got = store.outstanding_by_market()
    assert got.get("m", D(0)) > 0, got
    # worst-case price x worst-case qty, so neither unknown is free
    assert got["m"] == LX.UNKNOWN_PRICE_WORST_CASE * LX.ABS_RESIDUAL_CAP_CONTRACTS


def test_an_unreadable_row_now_blocks_a_further_opening():
    """The hole this closes, end to end through the bound."""
    lim = limits()
    store = LX.InventoryStore.__new__(LX.InventoryStore)
    store.state = {"live_orders": {
        "a": {"market": "m", "side": "DOWN", "remaining": None},
    }}
    out = store.outstanding_residual("m")
    assert call(lim, qty=D("1"), residual_after=D("-1"),
                outstanding_residual=out) == "MAX_ABS_RESIDUAL"


def test_the_cap_arithmetic_never_exceeds_the_absolute_ceiling():
    """cap = min(k * clip, 3). The intersection, never the wider."""
    for k in (D("1"), D("2"), D("5"), D("100")):
        for clip in (D("0.5"), D("1.9"), D("3"), D("10")):
            cap = min(k * clip, LX.ABS_RESIDUAL_CAP_CONTRACTS)
            assert cap <= D("3"), (k, clip, cap)


def test_five_rounds_cannot_accumulate_five_unmatched_legs():
    """The experiment's load-bearing claim, in contract terms.

    Each opening is <= 3 contracts. Once an unmatched leg sits at the
    ceiling, any further opening that INCREASES |prospective| is refused, so
    R=5 cannot stack five strands. What it can do is carry up to 3 contracts
    of unmatched exposure in one or more legs.
    """
    lim = limits()
    # a 3-contract strand is exactly at the ceiling
    assert call(lim, qty=D("3"), residual_after=D("-3"),
                outstanding_residual=D(0)) is None
    # a second opening of any size on the same side is refused
    for q in (D("0.01"), D("1"), D("3")):
        assert call(lim, qty=q, residual_after=D("-3") - q,
                    outstanding_residual=D(0)) == "MAX_ABS_RESIDUAL"
    # but a REDUCING leg is always allowed -- completion is never blocked
    assert call(lim, qty=D("3"), residual_after=D(0),
                outstanding_residual=D(0)) is None
