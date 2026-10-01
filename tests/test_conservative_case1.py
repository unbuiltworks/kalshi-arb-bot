"""Case 1 is UNRESOLVED, so replay refuses the favourable fill.

The 2026-10-01 live calibration failed on its own instrument and demonstrated
nothing about what the venue does to an already-resting post-only maker that the
market later crosses. Until a qualifying observation exists, replay must not
grant that fill.
"""
from decimal import Decimal as D

from src import l5_two_sided as T
from src.l5_two_sided import ARM_REPLICATION, TwoSided


def book(c, bid, ask, t, bq="500", aq="500"):
    c.on_book({"bid": bid, "ask": ask, "bid_qty": bq, "ask_qty": aq,
               "_ladder": {"bids": {D(str(bid)): D(str(bq))},
                           "offers": {D(str(ask)): D(str(aq))}}}, t)


def test_the_flag_defaults_to_conservative():
    assert T.CONSERVATIVE_CASE1 is True


def test_a_print_that_crosses_our_resting_price_grants_no_fill():
    """The unobserved case: our resting leg sits at a price the live book has
    crossed. No fill, and the event is COUNTED rather than silently dropped."""
    c = TwoSided(arm=ARM_REPLICATION, clip=D(5))
    book(c, "0.40", "0.42", 0.0)
    c.reconcile_quotes()
    leg = c.legs.get("up")
    assert leg is not None, "no resting up leg to test"
    #: force the contemporaneous book to CROSS our resting price
    book(c, "0.20", leg.px, 1.0)
    assert c.is_marketable("up", leg.px), "fixture did not cross our price"
    before = len(c.fills)
    c.on_trade(leg.px, D(1000), 2.0, "SELL")
    assert len(c.fills) == before, "a fill was granted for the unobserved case"
    assert c.case1_unresolved_no_fill >= 1
    assert c.case1_unresolved_qty > 0


def test_an_ordinary_uncrossed_print_still_fills():
    """The conservative rule must not suppress the demonstrated case too."""
    c = TwoSided(arm=ARM_REPLICATION, clip=D(5))
    book(c, "0.40", "0.42", 0.0)
    c.reconcile_quotes()
    leg = c.legs.get("up")
    assert leg is not None
    assert not c.is_marketable("up", leg.px), "fixture is already crossed"
    before = len(c.fills)
    c.on_trade(leg.px, D(1000), 2.0, "SELL")
    assert len(c.fills) > before, "the ordinary resting fill was suppressed"
    assert c.case1_unresolved_no_fill == 0


def test_the_counters_reach_the_report():
    c = TwoSided(arm=ARM_REPLICATION, clip=D(1))
    r = c.report(winner="up")
    assert "case1_unresolved_no_fill" in r
    assert "case1_unresolved_qty" in r


def test_turning_it_off_restores_the_generous_behaviour(monkeypatch):
    """Proves the flag is load-bearing rather than decorative."""
    monkeypatch.setattr(T, "CONSERVATIVE_CASE1", False)
    c = TwoSided(arm=ARM_REPLICATION, clip=D(5))
    book(c, "0.40", "0.42", 0.0)
    c.reconcile_quotes()
    leg = c.legs.get("up")
    book(c, "0.20", leg.px, 1.0)
    before = len(c.fills)
    c.on_trade(leg.px, D(1000), 2.0, "SELL")
    assert len(c.fills) > before
    assert c.case1_unresolved_no_fill == 0
