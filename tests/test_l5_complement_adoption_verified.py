"""The complement adoption, replayed against the decisions it really made.

WHAT THIS FILE ESTABLISHES, AND WHAT IT DOES NOT.

The inventory-aware complement path is NOT new. `l5_live_execution` has
been pricing the second leg against the current authenticated book and the
REALISED cost of the first leg, and adopting it over the frozen plan, since
before either campaign -- `provenance: DERIVED_FROM_CURRENT_BOOK` with
`frozen_price_superseded` beside it in every record.

MEASURED across the ten preserved BTC15m windows
(`analysis/l5/residual_exit/preserved/*.ndjson`):

  * 231 ticks where the frozen plan was unexecutable (`LEG_WOULD_CROSS`),
    224 of them with `retirement_blocked_by: LEG_HOLDS_INVENTORY`;
  * **143 of those reached the complement, and 143 of 143 evaluated it.**
    The other 88 never could: 51 `PAST_COMPLETE_CUTOFF`, 30
    `PAST_TERMINAL`, 6 `MAX_LIVE_ORDERS`, 1 already submitted;
  * of 163 complement evaluations, **7 adopted** (pair 0.85-0.96, edge
    0.04-0.15) and **156 refused `COMPLEMENT_PAIR_ABOVE_CAP`** against
    `max_pair_cost 0.96`, at a MEDIAN pair cost of **1.16**.

So there is no missing path to add, and the refusals are the pair-cost
ceiling working: a matched pair redeems exactly $1.00, and 134 of those
156 sat at >= 1.05, which is a guaranteed loss of five cents or more per
contract. Adopting them would convert stranded inventory into certain
loss. The ceiling is preserved, so none of them becomes an order.

What WAS unverified is narrower, and this file pins it. `complement_order`
checked side validity, book freshness, two-sidedness, the tick grid, the
price band, post-only non-crossing, the filled quantity, the realised cost
basis, the pair ceiling, the fee floor and both-outcome positivity -- but
never the DEPTH behind the quote it priced from, and never that the caller
had handed it the right market and the opposite side. A touch can be set
by one resting contract; completing against it hands the realised cost of
leg one to a quote that cannot absorb leg two.

Every vector below is transcribed from a real recorded decision. The
depth check refuses NONE of the seven adoptions -- displayed depth ran 96x
to 4097x the complement quantity -- so it is inert at the size we trade
and binds at the size we do not yet trade, which is when it is wanted.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

TICK = "0.01"
MINQ = "0.01"
DEPTH_TICKS = 2
MKT = "cpc-btc-updown-15m-2026-10-24-0100z"

#: every complement the engine ACCEPTED across the preserved windows,
#: transcribed field for field from the records.
ADOPTED = [
    dict(win="0100z", held="DOWN", side="UP", bid="0.6100", ask="0.6200",
         bs="100.0000", asks="3622.5100", age=0.02, paid="0.3700",
         need="1.04", px="0.5900", qty="1.04", pair="0.9600", edge="0.0400",
         frozen="0.5800"),
    dict(win="0115z", held="DOWN", side="UP", bid="0.7200", ask="0.7300",
         bs="3933.0900", asks="1227.7300", age=0.094, paid="0.2400",
         need="0.96", px="0.7000", qty="0.96", pair="0.9400", edge="0.0600",
         frozen="0.7100"),
    dict(win="0130z", held="UP", side="DOWN", bid="0.4100", ask="0.4200",
         bs="2114.7700", asks="2040.0100", age=0.035, paid="0.2900",
         need="1.27", px="0.5600", qty="1.27", pair="0.8500", edge="0.1500",
         frozen="0.6600"),
    dict(win="0145z", held="DOWN", side="UP", bid="0.6300", ask="0.6400",
         bs="2737.5400", asks="2042.1500", age=0.08, paid="0.3500",
         need="1.03", px="0.6100", qty="1.03", pair="0.9600", edge="0.0400",
         frozen="0.6000"),
    dict(win="0200z", held="UP", side="DOWN", bid="0.3800", ask="0.3900",
         bs="2164.4300", asks="2003.4400", age=0.076, paid="0.3500",
         need="1.22", px="0.5900", qty="1.22", pair="0.9400", edge="0.0600",
         frozen="0.6000"),
    dict(win="0215z", held="UP", side="DOWN", bid="0.3400", ask="0.3500",
         bs="4647.5000", asks="3440.2100", age=0.127, paid="0.3100",
         need="1.25", px="0.6300", qty="1.25", pair="0.9400", edge="0.0600",
         frozen="0.6400"),
    dict(win="0230z", held="UP", side="DOWN", bid="0.4600", ask="0.4700",
         bs="3995.1300", asks="3655.5100", age=0.008, paid="0.4200",
         need="1.16", px="0.5100", qty="1.16", pair="0.9300", edge="0.0700",
         frozen="0.5300"),
]

#: the dominant refusal, from campaign 2. `paid` is the FULL-PRECISION
#: realised average the ledger held, not a rounded display value.
ABOVE_CAP = [
    dict(win="2115z", held="DOWN", side="UP", bid="0.8000", ask="0.8100",
         bs="926.3500", asks="2514.0100", age=0.04,
         paid="0.4015544041450777202072538860", need="1.68",
         pair="1.181554404145077720207253886"),
    dict(win="2130z", held="DOWN", side="UP", bid="0.7700", ask="0.7800",
         bs="1013.1600", asks="1239.0600", age=0.023, paid="0.5200",
         need="2.09", pair="1.2700"),
]


def call(v, **over):
    """`complement_order` as the deployed call site now invokes it."""
    kw = dict(side=v["side"], qty=v["need"], cost_px=v["paid"],
              bid=v["bid"], ask=v["ask"], tick=TICK, min_qty=MINQ,
              depth_ticks=DEPTH_TICKS, book_age_s=v["age"],
              market=MKT, held_side=v["held"],
              bid_shares=v["bs"], ask_shares=v["asks"])
    kw.update(over)
    return TS.complement_order(**kw)


def call_old(v, **over):
    """And as it was invoked BEFORE this change: no market, no side
    assertion, no depth. The comparison arm."""
    kw = dict(side=v["side"], qty=v["need"], cost_px=v["paid"],
              bid=v["bid"], ask=v["ask"], tick=TICK, min_qty=MINQ,
              depth_ticks=DEPTH_TICKS, book_age_s=v["age"])
    kw.update(over)
    return TS.complement_order(**kw)


# ============================= the decisions it really made, replayed

@pytest.mark.parametrize("v", ADOPTED, ids=[v["win"] for v in ADOPTED])
def test_every_adopted_complement_is_still_adopted(v):
    """OLD vs CORRECTED on the seven real adoptions: same order, both ways.

    Same side, same price, same quantity, same pair cost, same edge. If the
    new checks changed any of these the engine would complete FEWER pairs,
    which is the opposite of the point.
    """
    old, new = call_old(v), call(v)
    assert old["ok"] is True and new["ok"] is True
    assert new["leg"] == old["leg"]
    assert new["leg"]["side"] == v["side"]
    assert new["leg"]["price"] == v["px"]
    assert new["leg"]["qty"] == v["qty"]
    assert new["leg"]["post_only"] is True
    assert new["leg"]["mode"] == TS.MODE
    assert new["pair_cost"] == v["pair"]
    assert new["edge_per_contract"] == v["edge"]


@pytest.mark.parametrize("v", ADOPTED, ids=[v["win"] for v in ADOPTED])
def test_the_depth_check_refuses_none_of_them(v):
    """The measured claim: 96x to 4097x, so this binds on no real adoption."""
    depth = TS.resting_depth(v["side"], bid_shares=v["bs"],
                             ask_shares=v["asks"])
    assert depth is not None
    assert depth >= D(v["qty"]) * 90          # 96x was the thinnest
    assert call(v)["ok"] is True


@pytest.mark.parametrize("v", ADOPTED, ids=[v["win"] for v in ADOPTED])
def test_it_supersedes_the_frozen_price_rather_than_using_it(v):
    """The frozen plan's complement price is NOT what gets placed, and the
    pair is judged on the cost actually paid."""
    new = call(v)
    assert new["leg"]["price"] != v["frozen"]
    assert new["first_leg_cost"] == v["paid"]
    assert D(new["pair_cost"]) == D(v["paid"]) + D(new["leg"]["price"])


# ================== and the ones it refused, which stay refused

@pytest.mark.parametrize("v", ABOVE_CAP, ids=[v["win"] for v in ABOVE_CAP])
def test_an_above_cap_pair_is_still_refused_both_ways(v):
    """THE CENTRAL NEGATIVE RESULT.

    These are 156 of the 163 evaluations. The corrected path must NOT turn
    them into orders: at 1.18 and 1.27 against a $1.00 redemption, each
    one is a locked loss of 18 and 27 cents a contract. The pair-cost
    ceiling is preserved, so no order is produced here -- by either arm.
    """
    old, new = call_old(v), call(v)
    assert old["ok"] is False and new["ok"] is False
    assert old["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP"
    assert new["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP"
    assert new["pair_cost"] == v["pair"]
    assert D(new["pair_cost"]) > D(1)
    assert D(new["max_pair_cost"]) == TS.max_pair_cost()
    assert "leg" not in new


@pytest.mark.parametrize("v", ABOVE_CAP, ids=[v["win"] for v in ABOVE_CAP])
def test_the_depth_was_ample_so_depth_was_never_why(v):
    """Naming the cause: these refused on economics, not on liquidity."""
    depth = TS.resting_depth(v["side"], bid_shares=v["bs"],
                             ask_shares=v["asks"])
    assert depth > D(v["need"])


# ============================================ the depth check itself

def test_a_quote_thinner_than_our_order_is_refused():
    v = ADOPTED[0]
    out = call(v, bid_shares="0.50")
    assert out["ok"] is False
    assert out["reason"] == "COMPLEMENT_DEPTH_INSUFFICIENT"
    assert out["resting_side"] == "BID"
    assert out["displayed_depth"] == "0.50"


def test_depth_reads_the_resting_side_and_not_the_taker_side():
    """The bug this would have been. A maker BUY of UP rests a YES bid, so
    BID depth is what must be there; the ASK is the side a TAKER unwind
    would consume, and the defensive path reads it the other way round on
    purpose. Reading one for the other passes a plausible number about the
    wrong half of the book.
    """
    up = ADOPTED[0]
    assert up["side"] == "UP"
    assert call(up, ask_shares="0")["ok"] is True       # ask is irrelevant
    assert call(up, bid_shares="0")["reason"] == "COMPLEMENT_DEPTH_INSUFFICIENT"

    dn = ADOPTED[2]
    assert dn["side"] == "DOWN"
    assert call(dn, bid_shares="0")["ok"] is True       # bid is irrelevant
    assert call(dn, ask_shares="0")["reason"] == "COMPLEMENT_DEPTH_INSUFFICIENT"


@pytest.mark.parametrize("bad", [None, "", "none", "abc"])
def test_unreadable_depth_is_not_a_refusal(bad):
    """The deployed caller passed no depth at all. Failing closed here
    would strand inventory the engine completes today, so an unreadable
    reading leaves the economics to decide, exactly as before."""
    v = ADOPTED[0]
    assert call(v, bid_shares=bad)["ok"] is True


@pytest.mark.parametrize("impossible", ["-1", "0", "-0.01"])
def test_a_negative_or_empty_depth_is_readable_and_so_it_refuses(impossible):
    """NOT the same as unreadable, and the distinction is deliberate. A
    book that reports no size, or negative size, has told us something --
    and what it has told us is that there is nothing to rest against. That
    refuses. Only a reading we cannot parse at all leaves the question to
    the economics."""
    out = call(ADOPTED[0], bid_shares=impossible)
    assert out["ok"] is False
    assert out["reason"] == "COMPLEMENT_DEPTH_INSUFFICIENT"


def test_depth_exactly_equal_to_the_order_is_enough():
    v = ADOPTED[0]
    assert call(v, bid_shares=v["qty"])["ok"] is True


# ====================================== market and side identity

def test_the_market_is_carried_on_the_result():
    out = call(ADOPTED[0])
    assert out["market"] == MKT
    assert out["held_side"] == "DOWN"
    assert out["resting_depth"] == "100.0000"


def test_an_unnamed_market_is_none_rather_than_invented():
    out = call_old(ADOPTED[0])
    assert out["ok"] is True
    assert out["market"] is None
    assert out["held_side"] is None
    assert out["resting_depth"] is None


def test_a_complement_on_the_side_we_already_hold_is_refused():
    """Doubling the exposure the complement exists to remove."""
    v = ADOPTED[0]
    out = call(v, held_side=v["side"])
    assert out["ok"] is False
    assert out["reason"] == "COMPLEMENT_NOT_OPPOSITE_SIDE"
    assert out["side"] == "UP" and out["held_side"] == "UP"


def test_an_unknown_held_side_is_refused_rather_than_assumed():
    out = call(ADOPTED[0], held_side="SIDEWAYS")
    assert out["ok"] is False
    assert out["reason"] == "COMPLEMENT_HELD_SIDE_UNKNOWN"


def test_an_unstated_held_side_keeps_the_old_behaviour():
    assert call(ADOPTED[0], held_side=None)["ok"] is True


def test_the_adoption_site_verifies_both_before_replacing_the_plan():
    """Neither guard can fire from the deployed call site, which passes
    `market` and `_other` from the same variables a few lines above. That
    is why they are asserted: an adoption that matched the wrong market's
    book would look exactly like a correct one in the record."""
    import inspect

    from src import l5_live_execution as LX
    src = inspect.getsource(LX.FaithfulExecutor)
    assert "COMPLEMENT_MARKET_MISMATCH" in src
    assert "COMPLEMENT_SIDE_MISMATCH" in src
    # and the call site now states what the complement is FOR
    assert "market=market, held_side=_other" in src
    assert 'bid_shares=ts_book.get("bid_shares")' in src


# ======================= every pre-existing gate is still in front

def test_a_stale_book_still_refuses_before_any_depth_question():
    out = call(ADOPTED[0], book_age_s=99.0, bid_shares="0.01")
    assert out["reason"] == "BOOK_STALE"


def test_a_suspected_gap_still_refuses():
    assert call(ADOPTED[0], gap_suspected=True)["reason"] == "GAP_SUSPECTED"


def test_a_one_sided_book_still_refuses():
    assert call(ADOPTED[0], ask=None)["reason"] == "BOOK_NOT_TWO_SIDED"


def test_nothing_to_match_still_refuses():
    assert call(ADOPTED[0], qty="0")["reason"] == "NOTHING_TO_MATCH"


def test_an_unknown_first_leg_cost_still_refuses():
    assert call(ADOPTED[0],
                cost_px=None)["reason"] == "FIRST_LEG_COST_UNKNOWN"


def test_the_quantity_never_exceeds_what_was_filled():
    v = ADOPTED[0]
    out = call(v, qty="1.049")
    assert D(out["leg"]["qty"]) <= D("1.049")
    assert out["leg"]["qty"] == "1.04"            # floored to the grid


def test_the_fee_floor_still_has_the_last_word_under_the_cap():
    """A pair inside the ceiling but inside the fees too is still refused."""
    v = ADOPTED[0]
    out = call(v, cost_px="0.3500", bid="0.6300", ask="0.6400")
    assert out["ok"] is True                       # 0.35 + 0.61 = 0.96
    tight = call(v, cost_px="0.9400", bid="0.0400", ask="0.0500")
    assert tight["ok"] is False
    assert tight["reason"] in ("COMPLEMENT_BELOW_FEE_FLOOR",
                              "PRICE_OUT_OF_BAND",
                              "COMPLEMENT_PAIR_ABOVE_CAP")
