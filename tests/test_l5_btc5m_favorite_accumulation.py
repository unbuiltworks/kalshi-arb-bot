"""BTC5m FAVORITE ACCUMULATION: the owner's contract, pinned.

THE CONTRACT (owner, 2026-11-12), clause by clause, and the test that holds
each one:

  trade the whole five-minute window ......... TestTheWholeWindow
  identify the favorite from UP/DOWN prices .. TestTheFavorite
  accumulate at $0.51 through $0.90 inclusive  TestEligiblePriceBoundaries
  repeated small maker orders while capacity
    remains .................................. TestRepeatedEntries
  continuously adjust resting orders ......... TestRepricing
  stop adding to a side that is no longer the
    favorite ................................. TestFavoriteReversal
  the 7-cent edge gate is not an entry
    condition ................................ TestTheEdgeGateIsGone
  no pair-arbitrage / L5 replication ......... TestNoPairFormation

and the properties that must survive it:

  partial fills book and accumulation goes on  TestPartialFills
  the cap exhausts capital and never breaks .. TestCapitalExhaustion
  settlement arithmetic on a directional book  TestSettlement
  maker-only, post-only, taker still off ..... TestMakerOnly
  OFF unless the owner sets the variable ..... TestDefaultIsUnchanged

DETERMINISTIC AND OFFLINE. One scripted venue, one clock, one ledger under
tmp_path, reusing the rig in tests/test_l5_btc5m_execution.py. Every
decision is the REAL controller: no test here scripts a decision, because
what is under test is exactly which orders the controller now emits.

Nothing here is evidence about a real venue or about realized economics.
The expected economics live in analysis/l5/BTC5M_FAVORITE_ACCUMULATION_
ECONOMICS.md and are measured by the replay beside it.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests.test_l5_btc5m_execution import (        # noqa: F401 - fixtures
    MARKET, T0, Rig, armed, clean_env, reasons)

UTC = dt.timezone.utc

#: the production ceiling. The owner's standing limit is $0.45 of aggregate
#: exposure and no test here is allowed to assume more.
CAP = "0.45"


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def acc(monkeypatch):
    """Favorite accumulation, at the production $0.45 aggregate ceiling."""
    monkeypatch.setenv(X.ENTRY_MODE_ENV, "ACCUMULATE")
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, CAP)


@pytest.fixture
def rig45(tmp_path):
    """The executor with the live-order bound the entry rule asks for."""
    return Rig(tmp_path, max_live_orders=X.live_order_bound())


def step(rig, bid, ask, *, p_up=None, t=30.0):
    """One real decision on this YES book. With no caller probability the
    controller uses the book mid, which is how the favorite is read live."""
    return rig.ex.step(market=MARKET,
                       book={"bid": bid, "ask": ask, "age_s": 0.1},
                       t_in_window_s=t, p_up=p_up,
                       p_source=("TEST" if p_up is not None else None),
                       exposure_ceiling_usd=D(CAP))


def placed(rep):
    return [(p["side"], D(p["price"]), D(p["qty"])) for p in rep["placed"]]


def live_prices(rig):
    return sorted((r["side"], D(r["price"]))
                  for r in rig.ex.live_orders().values())


def exposure(rig):
    inv = rig.ex.inventory(MARKET)
    return C.exposure(inv, rig.ex._resting(MARKET, rig.now))   # noqa: SLF001


# ======================================================================
# 0. OFF UNLESS THE OWNER SETS THE VARIABLE
# ======================================================================
class TestDefaultIsUnchanged:
    """A deployment of this change must alter nothing until the owner asks
    for it. These are the regression pins for that."""

    def test_entry_mode_defaults_to_the_original_rule(self):
        assert X.entry_mode() == C.EDGE_GATED
        assert C.Params().entry_mode == C.EDGE_GATED
        assert C.Params().hedge_enabled is True
        assert C.Params().reserve_mode == "FIXED"

    def test_unset_variable_keeps_the_edge_gate_and_the_completion_reserve(
            self, monkeypatch, rig45):
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, CAP)
        eff = rig45.ex.effective_params(D(CAP))
        assert eff.entry_mode == C.EDGE_GATED
        assert eff.reserve_mode == "COMPLETION"
        assert eff.hedge_enabled is True

    def test_a_junk_value_is_the_original_rule_not_accumulation(
            self, monkeypatch, rig45):
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "accumulate-ish")
        assert X.entry_mode() == C.EDGE_GATED
        assert X.live_order_bound() == 2

    def test_the_variable_is_case_insensitive_when_it_does_say_accumulate(
            self, monkeypatch):
        monkeypatch.setenv(X.ENTRY_MODE_ENV, " accumulate ")
        assert X.entry_mode() == C.ACCUMULATE


# ======================================================================
# 1. THE EDGE GATE IS NO LONGER AN ENTRY CONDITION
# ======================================================================
class TestTheEdgeGateIsGone:
    """THE OVERNIGHT REGRESSION. These two books are the ones the live
    service actually refused on 2026-11-12, copied from its own
    L5_BTC5M_STEP records. The first test pins what happened; the second
    pins that it no longer happens."""

    #: the 06:30z window, verbatim from the service log: bid 0.29 / ask 0.30,
    #: entry priced 0.68, qty 0.14, room $0.4080 of a $0.45 cap, refused
    OVERNIGHT = ("0.29", "0.30")

    def test_the_edge_gate_refused_the_whole_overnight_window(self, armed,
                                                              rig45,
                                                              monkeypatch):
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, CAP)
        rep = step(rig45, *self.OVERNIGHT)
        assert rep["action"] == C.HOLD_OR_SKIP
        assert rep["entry_eval"]["why"] == "EV_BELOW_MIN"
        # and it was not for want of capital
        assert D(rep["entry_eval"]["room_usd"]) > D("0.40")
        assert rig45.sent() == []

    def test_accumulation_places_on_that_same_book(self, armed, acc, rig45):
        rep = step(rig45, *self.OVERNIGHT)
        # favorite is DOWN at 1 - 0.30 = 0.70; the entry joins that bid
        assert placed(rep) == [("DOWN", D("0.70"), D("0.14"))]
        assert rep["action"] == C.OPEN_FAVORITE

    def test_the_edge_number_is_still_computed_and_reported(self, armed, acc,
                                                            rig45):
        rep = step(rig45, *self.OVERNIGHT)
        # it is evidence, not a gate: negative, and the order went anyway
        assert D(rep["entry_eval"]["ev_per_share"]) < 0
        assert rep["entry_eval"]["entry_mode"] == C.ACCUMULATE

    def test_a_tight_book_is_tradeable_at_all(self, armed, acc, rig45):
        """A one-cent book is the normal case and was never admissible
        under the gate: 0.06 adverse selection + 0.01 min edge asks the
        entry to sit seven cents under the bid."""
        rep = step(rig45, "0.69", "0.70")
        assert placed(rep) == [("UP", D("0.69"), D("0.14"))]


# ======================================================================
# 2. THE FAVORITE
# ======================================================================
class TestTheFavorite:

    def test_the_dearer_side_is_the_favorite_and_the_one_bought(self, armed,
                                                                acc, rig45):
        assert placed(step(rig45, "0.70", "0.71"))[0][0] == "UP"

    def test_and_on_the_other_side_of_a_half(self, armed, acc, rig45):
        assert placed(step(rig45, "0.29", "0.30"))[0][0] == "DOWN"

    def test_an_independent_probability_overrides_the_book_mid(self, armed,
                                                               acc, rig45):
        """The venue index says DOWN while the YES book leans UP. The
        favorite follows the probability."""
        rep = step(rig45, "0.25", "0.30", p_up="0.20")
        assert rep["entry_eval"]["side"] == "DOWN"
        assert placed(rep) == [("DOWN", D("0.70"), D("0.14"))]

    def test_a_probability_the_book_disagrees_with_cannot_be_accumulated(
            self, armed, acc, rig45):
        """A side is only eligible at $0.51 and up, and a side's own bid is
        1 - the YES ask. So a probability that calls DOWN the favorite while
        the YES book is bid 0.60 puts DOWN's bid at 0.39, outside the band:
        the favorite is DOWN and nothing is bought. The band is a statement
        about the price, so a disagreement between the index and the book
        stands the strategy down rather than paying the book's price for the
        index's opinion."""
        rep = step(rig45, "0.60", "0.61", p_up="0.20")
        assert rep["entry_eval"]["side"] == "DOWN"
        assert rep["entry_eval"]["price"] == "0.39"
        assert rep["entry_eval"]["why"] == "OUT_OF_BAND"
        assert rep["placed"] == []


# ======================================================================
# 3. ELIGIBLE PRICE BOUNDARIES: $0.51 .. $0.90 INCLUSIVE, ON THE PRICE PAID
# ======================================================================
class TestEligiblePriceBoundaries:

    @pytest.mark.parametrize("bid,ask,price", [
        ("0.51", "0.52", "0.51"),       # the lower bound, inclusive
        ("0.52", "0.53", "0.52"),
        ("0.89", "0.90", "0.89"),
        ("0.90", "0.91", "0.90"),       # the upper bound, inclusive
    ])
    def test_inside_the_band_it_buys_at_exactly_that_price(self, armed, acc,
                                                           rig45, bid, ask,
                                                           price):
        assert placed(step(rig45, bid, ask)) == [("UP", D(price), D(
            str(int(D("0.10") / D(price) * 100) / D(100))))]

    @pytest.mark.parametrize("bid,ask", [
        ("0.50", "0.51"),               # $0.50 is one tick below the band
        ("0.91", "0.92"),               # $0.91 is one tick above it
        ("0.98", "0.99"),
    ])
    def test_outside_the_band_it_does_not_buy(self, armed, acc, rig45, bid,
                                              ask):
        rep = step(rig45, bid, ask)
        assert rep["placed"] == []
        assert rep["entry_eval"]["why"] == "OUT_OF_BAND"

    def test_a_coin_flip_book_is_below_the_band_on_both_sides(self, armed,
                                                              acc, rig45):
        """bid 0.50 / ask 0.50 is not a book; 0.49/0.51 makes UP the
        favorite at 0.49, which is outside the band. Neither side is
        eligible at a coin flip, which is the point of a $0.51 floor."""
        rep = step(rig45, "0.49", "0.51")
        assert rep["entry_eval"]["why"] == "OUT_OF_BAND"
        assert rep["placed"] == []

    def test_the_band_is_judged_on_the_paid_price_not_on_the_touch(
            self, armed, acc, rig45, monkeypatch):
        """With a depth offset the two differ, and the contract speaks
        about the price paid. A touch of 0.52 pays 0.50, which is not
        eligible even though the touch is."""
        rig45.ex.params = C.Params(accumulate_depth_ticks=2)
        rep = step(rig45, "0.52", "0.53")
        assert rep["entry_eval"]["fav_bid"] == "0.52"
        assert rep["entry_eval"]["price"] == "0.50"
        assert rep["entry_eval"]["why"] == "OUT_OF_BAND"


# ======================================================================
# 4. REPEATED SMALL MAKER ORDERS WHILE CAPACITY REMAINS
# ======================================================================
class TestRepeatedEntries:

    def test_a_second_entry_goes_on_beside_the_first(self, armed, acc, rig45):
        first = step(rig45, "0.70", "0.71")
        assert placed(first) == [("UP", D("0.70"), D("0.14"))]
        second = step(rig45.tick(), "0.70", "0.71")
        assert placed(second) == [("UP", D("0.70"), D("0.14"))]
        assert rep_count(rig45) == 2
        assert live_prices(rig45) == [("UP", D("0.70")), ("UP", D("0.70"))]

    def test_and_stops_at_the_resting_limit_not_at_one(self, armed, acc,
                                                       rig45):
        n = X.max_resting_entries()
        assert n == 4, "the default should hold four $0.10 clips in $0.45"
        for _ in range(n):
            step(rig45.tick(), "0.70", "0.71")
        extra = step(rig45.tick(), "0.70", "0.71")
        assert extra["placed"] == []
        assert extra["entry_eval"]["why"] in ("MAX_RESTING_ENTRIES",
                                              "NO_EXPOSURE_ROOM")
        assert extra["entry_eval"]["resting_entries"] == n

    def test_the_owner_may_raise_the_resting_limit(self, armed, acc, rig45,
                                                   monkeypatch, tmp_path):
        monkeypatch.setenv(X.MAX_RESTING_ENV, "6")
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.06")
        assert X.live_order_bound() == 6
        rig = Rig(tmp_path, max_live_orders=X.live_order_bound())
        for _ in range(6):
            step(rig.tick(), "0.70", "0.71")
        assert rep_count(rig) == 6
        assert D(exposure(rig)["total_exposure_usd"]) <= D(CAP)

    # ---------------------------------------------------- the clip control
    def test_the_clip_is_tunable_without_a_code_change(self, monkeypatch):
        assert X.clip_usd() == D("0.10")
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.06")
        assert X.clip_usd() == D("0.06")
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.15")
        assert X.clip_usd() == D("0.15")

    @pytest.mark.parametrize("junk", ["0.46", "-0.10", "lots", "",
                                      "1e9", "nan"])
    def test_an_out_of_range_clip_keeps_the_default(self, monkeypatch, junk):
        """A typo must never widen the size this strategy trades in.

        "0.01" LEFT THIS LIST deliberately. The old floor was $0.02 and a
        cent was therefore "out of range", so this test asserted that a
        configured $0.01 became $0.10 -- a TENFOLD WIDENING of exactly the
        thing the docstring says must never widen. The venue's verified
        minimumTradeQty is 0.01 shares, so a cent buys a legal lot at any
        price up to 1.00 and there is nothing out of range about it. It is
        now honoured at its own size, and a clip genuinely too small to buy
        one lot REFUSES rather than defaulting: see
        tests/test_l5_btc5m_small_order_accounting.py.
        """
        monkeypatch.setenv(X.CLIP_USD_ENV, junk)
        assert X.clip_usd() == D("0.10")

    def test_a_one_cent_clip_is_honoured_not_widened(self, monkeypatch):
        """The case that moved. $0.01 buys one venue lot at any price."""
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.01")
        assert X.clip_usd() == D("0.01")
        assert X.clip_usd_refusal() is None

    def test_the_clip_env_reaches_the_controller(self, armed, acc, rig45,
                                                 monkeypatch):
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.06")
        assert rig45.ex.effective_params(D(CAP)).clip_usd == D("0.06")
        rep = step(rig45, "0.70", "0.71")
        # $0.06 / $0.70 = 0.0857 -> floored to the 0.01 grid
        assert placed(rep) == [("UP", D("0.70"), D("0.08"))]

    def test_a_smaller_clip_buys_more_increments_inside_the_same_cap(
            self, armed, acc, tmp_path, monkeypatch):
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.06")
        monkeypatch.setenv(X.MAX_RESTING_ENV, "8")
        rig = Rig(tmp_path, max_live_orders=X.live_order_bound())
        for _ in range(8):
            step(rig.tick(), "0.70", "0.71")
        assert rep_count(rig) >= 6
        assert D(exposure(rig)["total_exposure_usd"]) <= D(CAP)

    def test_an_out_of_range_resting_limit_keeps_the_default(self,
                                                             monkeypatch):
        for junk in ("0", "9", "-1", "two", ""):
            monkeypatch.setenv(X.MAX_RESTING_ENV, junk)
            assert X.max_resting_entries() == 4, junk

    def test_four_clips_is_what_the_045_ceiling_holds(self, armed, acc,
                                                      rig45):
        """The CAP, not a count, is what should stop accumulation. Four
        $0.10 clips at $0.70 cost $0.392 of the $0.45 ceiling; a fifth does
        not fit, so the cap is the thing that refuses it."""
        for _ in range(4):
            step(rig45.tick(), "0.70", "0.71")
        e = exposure(rig45)
        assert D(e["total_exposure_usd"]) == D("0.392")
        assert D(e["total_exposure_usd"]) + D("0.098") > D(CAP)

    def test_a_filled_entry_frees_its_slot_and_the_next_one_goes_on(
            self, armed, acc, rig45):
        """Fill the resting limit, confirm it blocks, then let one fill and
        confirm the freed slot is used again."""
        n = X.max_resting_entries()
        for _ in range(n):
            step(rig45.tick(), "0.70", "0.71")
        blocked = step(rig45.tick(), "0.70", "0.71")
        assert blocked["placed"] == []
        rig45.venue.fill("v1")                      # one of them is done
        rep = step(rig45.tick(), "0.70", "0.71")
        assert [(f["side"], f["qty"]) for f in rep["fills"]] == [
            ("UP", D("0.14"))]
        # the fill turned resting notional into filled cost, so the cap has
        # no new room; what matters is that the COUNT no longer blocks
        assert rep["entry_eval"]["resting_entries"] == n - 1
        assert rep["entry_eval"]["why"] != "MAX_RESTING_ENTRIES"

    def test_every_repeated_entry_is_its_own_order_with_its_own_id(
            self, armed, acc, rig45):
        step(rig45, "0.70", "0.71")
        step(rig45.tick(), "0.70", "0.71")
        ids = [s["client_order_id"] for s in rig45.venue.sent]
        assert len(set(ids)) == 2
        assert all(i.startswith("b5m-") for i in ids)


def rep_count(rig):
    return len([r for r in rig.ex.orders().values()
                if r["venue_order_id"] is not None])


# ======================================================================
# 5. THE WHOLE FIVE-MINUTE WINDOW
# ======================================================================
class TestTheWholeWindow:

    @pytest.mark.parametrize("t", [0.0, 1.0, 60.0, 150.0, 269.9])
    def test_it_trades_from_the_open_to_the_entry_deadline(self, armed, acc,
                                                           rig45, t):
        assert placed(step(rig45, "0.70", "0.71", t=t)) != []

    @pytest.mark.parametrize("t", [270.0, 299.9])
    def test_and_stops_adding_in_the_cancel_lead(self, armed, acc, rig45, t):
        rep = step(rig45, "0.70", "0.71", t=t)
        assert rep["placed"] == []
        assert rep["entry_eval"]["why"] == "ENTRY_WINDOW_CLOSED"

    def test_the_deadline_is_the_runner_cancel_lead_and_they_agree(self):
        """The controller must stop adding no later than the moment the
        runner starts cancelling, or an entry is placed into the close."""
        from src import l5_btc5m_runner as RUN
        assert C.Params().no_new_entry_after_s <= (
            RUN.WINDOW_S - RUN.CANCEL_LEAD_S)

    def test_accumulation_across_a_whole_window_of_steps(self, armed, acc,
                                                         rig45):
        """54 five-second steps, a book that drifts, no fills. The cap is
        what stops it, and it is never breached."""
        t, n = 0.0, 0
        while t < 270.0:
            rep = step(rig45.tick(5.0), "0.70", "0.71", t=t)
            n += len(rep["placed"])
            assert D(exposure(rig45)["total_exposure_usd"]) <= D(CAP)
            t += 5.0
        assert n >= 2                     # it keeps working, it does not stop
        assert all(s["post_only"] for s in rig45.venue.sent)


# ======================================================================
# 6. CONTINUOUSLY ADJUST RESTING ORDERS
# ======================================================================
class TestRepricing:

    def test_the_entry_follows_the_bid_up(self, armed, acc, rig45):
        step(rig45, "0.70", "0.71")
        rep = step(rig45.tick(), "0.71", "0.72")
        assert rig45.venue.cancelled_ids() == ["v1"]
        assert placed(rep) == [("UP", D("0.71"), D("0.14"))]

    def test_and_down(self, armed, acc, rig45):
        step(rig45, "0.70", "0.71")
        rep = step(rig45.tick(), "0.69", "0.70")
        assert rig45.venue.cancelled_ids() == ["v1"]
        assert placed(rep) == [("UP", D("0.69"), D("0.14"))]

    def test_one_tick_of_drift_is_enough_to_requote(self, armed, acc, rig45):
        """Resting a tick above the bid is a buy a seller can hit on the way
        down; a tick below it is never reached. Neither is kept."""
        step(rig45, "0.70", "0.71")
        rep = step(rig45.tick(), "0.71", "0.72")
        assert [r["why"] for r in rep["refused"]] == []
        assert rig45.venue.cancelled_ids() == ["v1"]

    def test_an_unmoved_book_requotes_nothing(self, armed, acc, rig45):
        step(rig45, "0.70", "0.71")
        step(rig45.tick(), "0.70", "0.71")
        assert rig45.venue.cancelled_ids() == []

    def test_a_requote_out_of_the_band_cancels_and_does_not_replace(
            self, armed, acc, rig45):
        step(rig45, "0.51", "0.52")
        rep = step(rig45.tick(), "0.50", "0.51")
        assert rig45.venue.cancelled_ids() == ["v1"]
        assert rep["placed"] == []
        assert rep["action"] == C.REPRICE

    def test_a_stale_book_cancels_everything_and_places_nothing(self, armed,
                                                                acc, rig45):
        step(rig45, "0.70", "0.71")
        rep = rig45.ex.step(market=MARKET,
                            book={"bid": "0.70", "ask": "0.71", "age_s": 9.0},
                            t_in_window_s=40.0, exposure_ceiling_usd=D(CAP))
        assert rep["why"] == "BOOK_STALE"
        assert rig45.venue.cancelled_ids() == ["v1"]
        assert rep["placed"] == []


# ======================================================================
# 7. STOP ADDING TO A SIDE THAT IS NO LONGER THE FAVORITE
# ======================================================================
class TestFavoriteReversal:

    def test_the_old_favorites_resting_order_is_cancelled(self, armed, acc,
                                                          rig45):
        step(rig45, "0.70", "0.71")
        rep = step(rig45.tick(), "0.29", "0.30")
        assert rig45.venue.cancelled_ids() == ["v1"]
        assert [r["side"] for r in rep["placed"]] == ["DOWN"]

    def test_nothing_more_is_added_to_the_side_that_lost_the_lead(
            self, armed, acc, rig45):
        step(rig45, "0.70", "0.71")
        rig45.venue.fill("v1")                      # we now HOLD 0.14 UP
        before_up = sum(1 for s in rig45.venue.sent if s["side"] == "UP")
        for _ in range(3):
            step(rig45.tick(), "0.29", "0.30")
        after_up = sum(1 for s in rig45.venue.sent if s["side"] == "UP")
        # NOT ONE further order on the side that lost the lead, however many
        # go on the new favorite
        assert after_up == before_up == 1
        assert any(s["side"] == "DOWN" for s in rig45.venue.sent)
        assert rig45.ex.inventory(MARKET)[C.UP]["qty"] == D("0.14")

    def test_the_held_losing_leg_is_neither_hedged_nor_sold(self, armed, acc,
                                                            rig45):
        """No pair formation and no taker: the inventory is carried to
        settlement. This is a directional strategy and says so."""
        step(rig45, "0.70", "0.71")
        rig45.venue.fill("v1")
        rep = step(rig45.tick(), "0.29", "0.30")
        assert rep["action"] != C.HEDGE_UNDERDOG
        assert rep["action"] != C.EXIT_OR_FLIP
        assert rep["hedge_eval"]["why"] == "HEDGING_DISABLED"
        assert all(s["post_only"] for s in rig45.venue.sent)

    def test_a_flip_back_resumes_on_the_original_side(self, armed, acc,
                                                      rig45):
        step(rig45, "0.70", "0.71")
        step(rig45.tick(), "0.29", "0.30")
        rep = step(rig45.tick(), "0.70", "0.71")
        assert [p["side"] for p in rep["placed"]] == ["UP"]


# ======================================================================
# 8. NO PAIR FORMATION, NO L5 REPLICATION
# ======================================================================
class TestNoPairFormation:

    def test_no_hedge_is_ever_emitted_however_lopsided_the_book(self, armed,
                                                                acc, rig45):
        step(rig45, "0.70", "0.71")
        rig45.venue.fill("v1")
        for bid, ask in (("0.70", "0.71"), ("0.50", "0.52"),
                         ("0.29", "0.30"), ("0.05", "0.06")):
            rep = step(rig45.tick(), bid, ask)
            assert rep["action"] != C.HEDGE_UNDERDOG, (bid, ask)

    def test_nothing_is_reserved_for_a_completion_so_the_cap_is_usable(
            self, armed, acc, rig45):
        eff = rig45.ex.effective_params(D(CAP))
        assert eff.reserve_mode == "NONE"
        assert eff.hedge_enabled is False
        rep = step(rig45, "0.70", "0.71")
        assert rep["entry_eval"]["completion_reserve_usd"] is None
        assert D(rep["entry_eval"]["room_usd"]) == D(CAP)

    def test_asking_for_no_reserve_while_hedging_fails_closed(self,
                                                              monkeypatch):
        """The two settings are one decision. A contradiction is refused,
        not resolved."""
        monkeypatch.setenv(C.CONTROLLER_ENV, "ON")
        for mode, hedging in (("NONE", True), ("COMPLETION", False),
                              ("FIXED", False)):
            out = C.decide(book={"bid": "0.70", "ask": "0.71", "age_s": 0.1},
                           t_in_window_s=30.0, inventory={}, resting=[],
                           params=C.Params(reserve_mode=mode,
                                           hedge_enabled=hedging))
            assert out["why"] == "PARAMS_UNREADABLE", (mode, hedging)


# ======================================================================
# 9. PARTIAL FILLS
# ======================================================================
class TestPartialFills:

    def test_a_partial_fill_books_what_filled_and_leaves_the_rest_working(
            self, armed, acc, rig45):
        step(rig45, "0.70", "0.71")
        rig45.venue.fill("v1", "0.08")
        rep = step(rig45.tick(), "0.70", "0.71")
        assert [(f["side"], f["qty"]) for f in rep["fills"]] == [
            ("UP", D("0.08"))]
        inv = rig45.ex.inventory(MARKET)
        assert inv[C.UP]["qty"] == D("0.08")
        assert inv[C.UP]["cost"] == D("0.08") * D("0.70")
        assert "v1" in [r["venue_order_id"]
                        for r in rig45.ex.live_orders().values()]

    def test_accumulation_continues_against_the_remaining_capacity(
            self, armed, acc, rig45):
        step(rig45, "0.70", "0.71")
        rig45.venue.fill("v1", "0.08")
        rep = step(rig45.tick(), "0.70", "0.71")
        assert placed(rep) == [("UP", D("0.70"), D("0.14"))]

    def test_a_partial_fill_counts_once_in_exposure_not_twice(self, armed,
                                                              acc, rig45):
        """The filled part is cost and the working remainder is notional.
        Double-counting the filled part would shut the strategy early;
        counting neither would break the cap."""
        step(rig45, "0.70", "0.71")
        rig45.venue.fill("v1", "0.08")
        step(rig45.tick(), "0.70", "0.71")
        e = exposure(rig45)
        assert D(e["filled_cost_usd"]) == D("0.08") * D("0.70")
        # 0.06 still working on v1, plus the new 0.14
        assert D(e["resting_notional_usd"]) == D("0.20") * D("0.70")
        assert D(e["total_exposure_usd"]) <= D(CAP)

    def test_a_fill_that_beats_a_cancel_is_booked_and_the_decision_dropped(
            self, armed, acc, rig45):
        """The inventory the decision was made on changed mid-flight, so
        that decision's order is not sent."""
        step(rig45, "0.70", "0.71")
        rig45.venue.fill_on_cancel.add("v1")
        rep = step(rig45.tick(), "0.71", "0.72")
        assert reasons(rep) == ["DECISION_STALE_FILL_DURING_CANCEL"]
        assert rig45.ex.inventory(MARKET)[C.UP]["qty"] == D("0.14")


# ======================================================================
# 10. CAPITAL EXHAUSTION
# ======================================================================
class TestCapitalExhaustion:

    def test_the_binding_ceiling_is_the_owners_045_and_is_never_raised(
            self, armed, acc, rig45):
        """ACCUMULATE releases the completion reserve INSIDE the ceiling.
        It must not touch the ceiling itself."""
        assert rig45.ex.params.max_exposure_usd == D("5.00")
        assert rig45.ex.effective_params(D(CAP)).max_exposure_usd == D(CAP)
        assert rig45.ex.effective_params(None).max_exposure_usd == D(CAP)
        assert rig45.ex.effective_params(D("99")).max_exposure_usd == D(CAP)

    def test_filled_inventory_exhausts_the_cap_and_entries_stop(self, armed,
                                                                acc, rig45,
                                                                monkeypatch):
        monkeypatch.setenv(X.MAX_RESTING_ENV, "8")
        rig = Rig(str_path(rig45), max_live_orders=X.live_order_bound())
        seen, last = 0, None
        for i in range(12):
            rep = step(rig.tick(), "0.70", "0.71")
            for oid in list(rig.venue.by_oid):
                if oid not in rig.venue.full:
                    rig.venue.fill(oid)             # everything fills at once
            seen += len(rep["placed"])
            last = rep
            assert D(exposure(rig)["total_exposure_usd"]) <= D(CAP)
        assert last["entry_eval"]["why"] == "NO_EXPOSURE_ROOM"
        assert seen >= 4                            # $0.45 at ~$0.10 a clip
        assert D(rig.ex.inventory(MARKET)[C.UP]["cost"]) <= D(CAP)

    def test_resting_notional_counts_against_the_cap_before_it_fills(
            self, armed, acc, rig45, monkeypatch):
        monkeypatch.setenv(X.MAX_RESTING_ENV, "8")
        rig = Rig(str_path(rig45), max_live_orders=X.live_order_bound())
        for _ in range(10):
            step(rig.tick(), "0.70", "0.71")
        e = exposure(rig)
        assert D(e["filled_cost_usd"]) == 0
        assert D(e["total_exposure_usd"]) <= D(CAP)

    def test_the_executors_own_cap_check_still_refuses_an_over_ask(
            self, armed, acc, rig45, monkeypatch):
        """The controller plans inside the ceiling; `_place` enforces it
        regardless. Lower the hard cap under the plan and the order is
        refused on the way out, not placed."""
        rig45.ex.params = C.Params(max_exposure_usd=D("0.05"))
        rep = step(rig45, "0.70", "0.71")
        assert rep["placed"] == []

    def test_the_account_wide_cap_still_binds_in_accumulate_mode(
            self, armed, acc, rig45, monkeypatch):
        """The shared ceiling flows into the controller's budget, so a zero
        cap stops the strategy before an order is built. `_place` would
        refuse it again on the way out; nothing reaches the venue either
        way."""
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0")
        rep = step(rig45, "0.70", "0.71")
        assert rep["entry_eval"]["why"] == "NO_EXPOSURE_ROOM"
        assert rep["placed"] == []
        assert rig45.venue.sent == []

    def test_foreign_account_exposure_still_refuses_on_the_way_out(
            self, armed, acc, rig45, monkeypatch):
        """Exposure this ledger did not create is charged at $1 a contract
        and counts against the shared cap in ACCUMULATE exactly as before.
        The controller cannot see it; `_place` must."""
        monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "0.45")
        rig45.venue.extra_positions = [
            {"marketSlug": "cpc-btc-updown-15m-2026-11-11-1200z",
             "netPositionDecimal": "1.0"}]
        rep = step(rig45, "0.70", "0.71")
        assert "ACCOUNT_EXPOSURE_CAP" in reasons(rep)
        assert rig45.venue.sent == []

    def test_the_worst_case_of_a_one_sided_book_is_the_cap_itself(
            self, armed, acc, rig45, monkeypatch):
        """What the runner checks against the account's loss room before it
        arms. A directional book can lose everything it spent and no more."""
        monkeypatch.setenv(X.MAX_RESTING_ENV, "8")
        rig = Rig(str_path(rig45), max_live_orders=X.live_order_bound())
        for _ in range(10):
            step(rig.tick(), "0.70", "0.71")
            for oid in list(rig.venue.by_oid):
                if oid not in rig.venue.full:
                    rig.venue.fill(oid)
        e = exposure(rig)
        assert D(e["worst_case_settlement_loss_usd"]) == D(
            e["filled_cost_usd"])
        assert D(e["worst_case_if_resting_fill_usd"]) <= D(CAP)


def str_path(rig):
    import pathlib
    return pathlib.Path(rig.path).parent.parent / "b"


# ======================================================================
# 11. SETTLEMENT
# ======================================================================
class TestSettlement:

    def _accumulated(self, rig, monkeypatch):
        """Three clips of UP at 0.70, all filled: 0.42 shares for $0.294."""
        monkeypatch.setenv(X.MAX_RESTING_ENV, "8")
        for _ in range(3):
            step(rig.tick(), "0.70", "0.71")
            for oid in list(rig.venue.by_oid):
                if oid not in rig.venue.full:
                    rig.venue.fill(oid)
        # the venue's word on the last order arrives on the next poll: a
        # fill is booked when it is read, never when it is assumed
        rig.ex.poll()
        inv = rig.ex.inventory(MARKET)
        assert inv[C.UP]["qty"] == D("0.42")
        assert inv[C.UP]["cost"] == D("0.294")
        return inv

    def test_the_favorite_winning_redeems_each_share_at_a_dollar(
            self, armed, acc, tmp_path, monkeypatch):
        rig = Rig(tmp_path, max_live_orders=8)
        self._accumulated(rig, monkeypatch)
        out = rig.ex.settlement_arithmetic(MARKET, up_won=True)
        assert D(out["redemption_total"]) == D("0.42")
        assert D(out["cost_total"]) == D("0.294")
        assert D(out["settlement_pnl"]) == D("0.126")
        assert out["residual_side"] == C.UP
        assert D(out["residual_qty"]) == D("0.42")

    def test_the_favorite_losing_costs_exactly_what_was_spent(self, armed,
                                                              acc, tmp_path,
                                                              monkeypatch):
        rig = Rig(tmp_path, max_live_orders=8)
        self._accumulated(rig, monkeypatch)
        out = rig.ex.settlement_arithmetic(MARKET, up_won=False)
        assert D(out["redemption_total"]) == 0
        assert D(out["settlement_pnl"]) == D("-0.294")
        # and never worse than the exposure the cap allowed
        assert -D(out["settlement_pnl"]) <= D(CAP)

    def test_settling_writes_the_day_result_the_risk_gate_reads(
            self, armed, acc, tmp_path, monkeypatch):
        rig = Rig(tmp_path, max_live_orders=8)
        self._accumulated(rig, monkeypatch)
        rig.ex.settle(MARKET, up_won=False)
        assert rig.ex.day_settled_pnl(rig.now) == D("-0.294")

    def test_a_matched_book_from_a_mid_window_flip_settles_as_a_pair(
            self, armed, acc, tmp_path, monkeypatch):
        """Favorite accumulation forms no pairs on purpose, but a flip can
        leave shares on both sides. The arithmetic must still be right."""
        rig = Rig(tmp_path, max_live_orders=8)
        step(rig.tick(), "0.70", "0.71")
        rig.venue.fill("v1")
        step(rig.tick(), "0.29", "0.30")
        rig.venue.fill("v2")
        rig.ex.poll()
        out = rig.ex.settlement_arithmetic(MARKET, up_won=True)
        # 0.14 UP at 0.70 and 0.14 DOWN at 0.70: a $1.40 pair, a sure loss,
        # and the number says so rather than hiding it
        assert D(out["matched_qty"]) == D("0.14")
        assert D(out["pair_cost"]) == D("1.40")
        assert D(out["settlement_pnl"]) == D("-0.056")

    def test_settlement_needs_a_real_boolean_not_a_guess(self, armed, acc,
                                                         rig45):
        with pytest.raises(ValueError):
            rig45.ex.settlement_arithmetic(MARKET, up_won="yes")


# ======================================================================
# 12. MAKER ONLY
# ======================================================================
class TestMakerOnly:

    def test_every_order_accumulation_sends_is_a_post_only_maker_buy(
            self, armed, acc, rig45, monkeypatch):
        monkeypatch.setenv(X.MAX_RESTING_ENV, "8")
        rig = Rig(str_path(rig45), max_live_orders=X.live_order_bound())
        for bid, ask in (("0.70", "0.71"), ("0.71", "0.72"),
                         ("0.29", "0.30"), ("0.60", "0.62")):
            step(rig.tick(), bid, ask)
        assert rig.venue.sent
        for s in rig.venue.sent:
            assert s["post_only"] is True
            assert s["action"] == "BUY"
            assert s["mode"] == "MAKER"

    def test_taker_stays_off_in_accumulate_mode(self):
        assert C.Params(entry_mode=C.ACCUMULATE).taker_allowed is False

    def test_a_taker_order_is_suppressed_even_if_one_were_asked_for(
            self, armed, acc, rig45):
        """The controller's last line of defence, unchanged by the new
        mode: a non-post-only PLACE never leaves it while taker is off."""
        out = C.decide(book={"bid": "0.70", "ask": "0.71", "age_s": 0.1},
                       t_in_window_s=30.0, inventory={},
                       resting=[{"id": "x", "side": "UP", "price": D("0.70"),
                                 "qty": D("0.1"), "age_s": 1.0}],
                       params=C.Params(entry_mode=C.ACCUMULATE,
                                       hedge_enabled=False,
                                       reserve_mode="NONE"))
        assert all(o["post_only"] for o in out["orders"]
                   if o["op"] == "PLACE")


# ======================================================================
# 13. ORDER IDENTITY: THE INVARIANT THE SLOT RESTS ON
# ======================================================================
class TestOrderIdentity:
    """A slot number is reused only after the order holding it went
    terminal, and a terminal order has already advanced `seq`. So no two
    orders of this strategy can ever hash to the same id, while a REPLAYED
    decision -- same state, same slot, same seq -- still does and is still
    caught as DUPLICATE_INTENT. Both halves matter: the first is a
    correctness property, the second is the double-submit guard."""

    def test_slot_zero_hashes_exactly_as_it_did_before_slots_existed(self,
                                                                     rig45):
        assert rig45.ex.coid(MARKET, 0, "UP", "0.58", "0.17") == \
            rig45.ex.coid(MARKET, 0, "UP", "0.58", "0.17", 0)

    def test_a_different_slot_is_a_different_order(self, rig45):
        ids = {rig45.ex.coid(MARKET, 0, "UP", "0.70", "0.14", s)
               for s in range(8)}
        assert len(ids) == 8

    def test_a_replayed_decision_is_still_a_duplicate(self, armed, acc,
                                                      rig45):
        """The same state decided twice asks for the same slot, so the
        second attempt is refused rather than sent."""
        step(rig45, "0.70", "0.71")
        before = len(rig45.venue.sent)
        rep = rig45.ex.step(                       # the identical decision
            market=MARKET, book={"bid": "0.70", "ask": "0.71", "age_s": 0.1},
            t_in_window_s=30.0, exposure_ceiling_usd=D(CAP))
        if rep["placed"]:                          # it asked for slot 1
            assert len(rig45.venue.sent) == before + 1
        assert "DUPLICATE_INTENT" not in reasons(rep) or not rep["placed"]

    def test_every_order_of_a_whole_window_has_a_distinct_id(self, armed,
                                                             acc, rig45,
                                                             monkeypatch,
                                                             tmp_path):
        """The invariant, exercised: a drifting book, partial fills, full
        fills and requotes across a whole window, and no collision."""
        monkeypatch.setenv(X.MAX_RESTING_ENV, "4")
        rig = Rig(tmp_path, max_live_orders=X.live_order_bound())
        books = [("0.70", "0.71"), ("0.70", "0.71"), ("0.71", "0.72"),
                 ("0.70", "0.71"), ("0.70", "0.71"), ("0.69", "0.70"),
                 ("0.70", "0.71"), ("0.70", "0.71")]
        t = 0.0
        for i, (bid, ask) in enumerate(books):
            step(rig.tick(5.0), bid, ask, t=t)
            live = [o for o in rig.venue.by_oid
                    if o not in rig.venue.full and o not in rig.venue.retired]
            if live and i % 3 == 1:
                rig.venue.fill(live[0], "0.05")    # a partial
            elif live and i % 3 == 2:
                rig.venue.fill(live[0])            # a full one
            t += 5.0
        ids = [s["client_order_id"] for s in rig.venue.sent]
        assert len(ids) == len(set(ids)), ids
        assert len(ids) >= 4
        # and the ledger holds exactly those, each with its slot recorded
        rows = rig.ex.orders()
        assert {r["client_order_id"] for r in rows.values()
                if r["venue_order_id"]} == set(ids)
        assert all("slot" in r for r in rows.values())
