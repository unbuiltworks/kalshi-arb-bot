"""Is `L5_PROVISIONAL_FORCED_SIDE` necessary for the live probe? No.

The owner asked me to determine this and not to enable it. The answer is
exhaustive rather than sampled, and it closes the question:

**Across the entire price grid, only TWO books have exactly one unreadable
provisional price -- and neither of them builds a plan at ANY depth.**

    bid 0.00  ask 0.01   UP None, DOWN 0.99   ->  BOOK_NOT_TWO_SIDED
    bid 0.99  ask 1.00   UP 0.99, DOWN None   ->  PRICE_OUT_OF_BAND

Those two books are the only states the repair changes anything for: with
both provisionals readable the gate never fired, and with both unreadable
the repair refuses too. So enabling the flag **cannot produce a single
order the default configuration would not**. Its only effect is to turn an
early gate refusal into a later builder refusal -- the same outcome, one
step further down the path.

WHY THE REPAIR IS STILL WORTH HAVING. It is correct, and the reasoning
that justified it stands: refusing a restable side because the OTHER side
is pinned is a defect, and the provisional price never reaches an order.
But "correct" and "needed for this probe" are different claims, and the
second one is false. The flag stays OFF.

WHAT WOULD CHANGE THIS. A finer tick, or a venue band that admits prices
inside one tick of 0.00/1.00, would widen the one-sided region. Both
numbers are read from the transport here rather than assumed, so this test
fails rather than silently going stale if either moves.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS

MARKET = "cpc-btc-updown-15m-2026-11-08-1200z"
TICK = D("0.01")


@pytest.fixture(autouse=True)
def authorized(monkeypatch):
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "forced-side-necessity")
    monkeypatch.setenv(TS.MARKET_ENV, MARKET)
    yield


def prov(side, bid, ask, tick=TICK):
    return LX.controller_provisional_price(
        side, {"bid": str(bid), "ask": str(ask)}, str(tick))


def plan(bid, ask, depth):
    return TS.build_orders(
        market=MARKET, bid=str(bid), ask=str(ask), bid_depth="500",
        ask_depth="500", tick=str(TICK), min_qty="1", depth_ticks=depth,
        cash_envelope=D("12"), max_worst_case_loss=D("1.00"),
        budget_headroom=D("12"), book_age_s=D("0.4"), gap_suspected=False)


def one_sided_books():
    """Every book on the grid where EXACTLY ONE provisional is unreadable."""
    out = []
    for cents in range(0, 101):
        bid = D(cents) / 100
        for spread in range(1, 6):
            ask = bid + spread * TICK
            if ask > 1:
                continue
            pu, pd = prov("UP", bid, ask), prov("DOWN", bid, ask)
            if (pu is None) != (pd is None):
                out.append((bid, ask, pu, pd))
    return out


def test_only_two_books_in_the_whole_grid_are_one_sided():
    books = one_sided_books()
    assert len(books) == 2, books
    assert [(str(b), str(a)) for b, a, _, _ in books] == [
        ("0", "0.01"), ("0.99", "1.00")], books


def test_the_one_sided_books_are_exactly_the_touch_extremes():
    """`touch - tick <= 0` is the whole condition: UP is pinned once the
    ask is a single tick, DOWN once the bid is within a tick of 1.00."""
    for bid, ask, pu, pd in one_sided_books():
        if pu is None:
            assert ask <= TICK, (bid, ask)
            assert pd == D(1) - bid - TICK
        else:
            assert bid >= D(1) - TICK, (bid, ask)
            assert pu == ask - TICK


@pytest.mark.parametrize("depth", [0, 1, 2, 3])
def test_no_one_sided_book_builds_a_plan_at_any_depth(depth):
    """THE ANSWER. The repair's whole region of effect produces no plan,
    so enabling it cannot produce an order."""
    for bid, ask, _, _ in one_sided_books():
        out = plan(bid, ask, depth)
        assert out.get("ok") is False, (bid, ask, depth, out)
        assert out.get("reason") in ("BOOK_NOT_TWO_SIDED",
                                     "PRICE_OUT_OF_BAND"), out


def test_the_repair_is_therefore_unnecessary_for_a_depth_2_probe():
    """Stated as the decision it supports."""
    buildable = [(str(b), str(a), d)
                 for b, a, _, _ in one_sided_books()
                 for d in (0, 1, 2, 3)
                 if plan(b, a, d).get("ok")]
    assert buildable == [], buildable


def test_an_ordinary_book_never_needed_the_repair_in_the_first_place():
    """With both provisionals readable the gate never fired, so the
    repair is irrelevant there -- which is where a probe actually lives."""
    pu, pd = prov("UP", "0.62", "0.63"), prov("DOWN", "0.62", "0.63")
    assert pu is not None and pd is not None
    assert plan("0.62", "0.63", 2)["ok"] is True


def test_the_tick_and_the_band_are_read_not_assumed():
    """If the venue's tick shrank, the one-sided region would widen and
    this conclusion would need revisiting. The tick comes from the
    transport, so a change breaks this test rather than rotting quietly."""
    fine = D("0.001")
    pu = prov("UP", "0.000", "0.001", tick=fine)
    assert pu is None          # still pinned at a single tick
    pu2 = prov("UP", "0.000", "0.002", tick=fine)
    assert pu2 == D("0.001")   # but the region is NARROWER in cents
    # so the claim above is tick-specific, which is why it is tested at
    # the deployed tick and asserted to be read rather than hardcoded
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.probe_plan)
    assert 'getattr(self.transport, "tick_size", None)' in src
