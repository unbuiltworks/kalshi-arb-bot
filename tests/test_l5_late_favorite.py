"""The late-window favourite maker candidate: default-off and fail-closed.

Every refusal here is a case where the permissive answer would have
risked real money on an unmeasured edge.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_late_favorite as LF

BOOK = {"bid": "0.97", "ask": "0.98", "bid_shares": "500", "ask_shares": "500"}
FLAT = {"UP": {"qty": "0", "cost": "0"}, "DOWN": {"qty": "0", "cost": "0"}}


def call(**kw):
    base = dict(p_up="0.995", p_age_s="0.2", t_in_window_s="270",
                window_s="300", book=BOOK, inventory=FLAT,
                resting=[],
                room_usd="5.00", loss_room_usd="5.00", clip_usd="1.00",
                fee_per_share="0.000", min_edge_per_share="0.005")
    base.update(kw)
    return LF.evaluate(**base)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")


# ------------------------------------------------------------ the switch

class TestItIsOffUntilSwitchedOn:

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv(LF.ENABLE_ENV, raising=False)
        assert LF.enabled() is False
        r = call()
        assert r["ok"] is False and r["reason"] == "LATE_FAVORITE_OFF"
        assert r["order"] is None

    @pytest.mark.parametrize("raw", ["", "no", "0", "true", "Y", "yes ", "ON"])
    def test_only_exactly_yes_enables_it(self, monkeypatch, raw):
        monkeypatch.setenv(LF.ENABLE_ENV, raw)
        if raw.strip().upper() == "YES":
            assert LF.enabled() is True
        else:
            assert LF.enabled() is False

    def test_yes_is_case_and_space_insensitive(self, monkeypatch):
        monkeypatch.setenv(LF.ENABLE_ENV, "  yes  ")
        assert LF.enabled() is True


# -------------------------------------------------------------- the maths

class TestTheEconomics:

    def test_break_even_is_the_price_plus_the_fee(self):
        assert LF.break_even_p(D("0.97"), D("0")) == D("0.97")
        assert LF.break_even_p(D("0.97"), D("0.002")) == D("0.972")

    def test_ev_is_zero_at_break_even(self):
        assert LF.ev_per_share(D("0.97"), D("0.97"), D("0")) == D("0")

    def test_one_loss_cancels_about_thirty_two_wins_at_97c(self):
        win = LF.ev_per_share(D("1"), D("0.97"), D("0"))
        loss = LF.ev_per_share(D("0"), D("0.97"), D("0"))
        assert win == D("0.03") and loss == D("-0.97")
        assert (abs(loss) / win).quantize(D("1")) == D("32")

    def test_a_fee_raises_the_bar_on_both_sides(self):
        """The fee is charged whether the share wins or loses."""
        assert LF.ev_per_share(D("1"), D("0.97"), D("0.01")) == D("0.02")
        assert LF.ev_per_share(D("0"), D("0.97"), D("0.01")) == D("-0.98")


# ------------------------------------------------------- maker-only rules

class TestMakerOnlyAndNoCrossing(object):

    def test_the_order_is_post_only(self, on):
        r = call()
        assert r["ok"] is True
        assert r["order"]["post_only"] is True
        assert r["order"]["intent"] == "BUY"

    def test_it_never_prices_at_or_above_the_ask(self, on):
        """A buy at or above the ask crosses. 0.97 and 0.98 are both
        refused when the ask is 0.97; only a strictly lower price rests."""
        r = call(book=dict(BOOK, ask="0.97"), prices=(D("0.97"), D("0.98")))
        assert r["ok"] is False and r["reason"] == "NO_LEVEL_QUALIFIED"
        assert {c["reject"] for c in r["considered"]} == {"WOULD_CROSS"}

    def test_the_cheapest_qualifying_level_wins(self, on):
        r = call(book=dict(BOOK, ask="0.99"),
                 prices=(D("0.98"), D("0.97")))
        assert r["ok"] is True and r["order"]["price"] == D("0.97")

    def test_a_down_favourite_prices_off_the_up_bid(self, on):
        """The DOWN ask is 1 - the UP bid, and its depth is the UP bid's."""
        r = call(p_up="0.004",
                 book={"bid": "0.01", "ask": "0.03",
                       "bid_shares": "400", "ask_shares": "7"})
        assert r["ok"] is True
        assert r["order"]["side"] == "DOWN"
        assert r["favourite_ask"] == "0.99"
        assert r["favourite_ask_shares"] == "400"


# -------------------------------------------------------- fail-closed gates

class TestItFailsClosed:

    @pytest.mark.parametrize("kw,reason", [
        ({"p_up": None}, "NO_PROBABILITY"),
        ({"p_up": "nan"}, "NO_PROBABILITY"),
        ({"p_up": "1.5"}, "NO_PROBABILITY"),
        ({"p_age_s": None}, "PROBABILITY_STALE"),
        ({"p_age_s": "9"}, "PROBABILITY_STALE"),
        ({"p_age_s": "-1"}, "PROBABILITY_STALE"),
        ({"window_s": "0"}, "WINDOW_UNREADABLE"),
        ({"t_in_window_s": None}, "WINDOW_UNREADABLE"),
        ({"t_in_window_s": "301"}, "WINDOW_OVER"),
        ({"t_in_window_s": "100"}, "TOO_EARLY"),
        ({"resting": None}, "RESTING_UNREADABLE"),
        ({"book": None}, "BOOK_UNUSABLE"),
        ({"book": {"bid": "0.9"}}, "NO_ASK_ON_FAVOURITE"),
        ({"book": {"ask": "0.98", "ask_shares": "0"}}, "NO_DEPTH"),
        ({"inventory": None}, "INVENTORY_UNREADABLE"),
        ({"fee_per_share": None}, "FEE_UNREADABLE"),
        ({"fee_per_share": "-0.01"}, "FEE_UNREADABLE"),
        ({"min_edge_per_share": None}, "NO_EDGE_MARGIN_SET"),
        ({"min_edge_per_share": "0"}, "NO_EDGE_MARGIN_SET"),
        ({"room_usd": None}, "BUDGET_UNREADABLE"),
        ({"loss_room_usd": None}, "BUDGET_UNREADABLE"),
        ({"clip_usd": None}, "BUDGET_UNREADABLE"),
    ])
    def test_a_missing_or_absurd_input_refuses(self, on, kw, reason):
        r = call(**kw)
        assert r["ok"] is False and r["order"] is None
        assert r["reason"] == reason

    def test_a_stale_probability_is_not_a_probability(self, on):
        """The feed polls about once a second, so two misses is stale."""
        assert call(p_age_s=str(LF.MAX_P_AGE_S))["ok"] is True
        assert call(p_age_s=str(LF.MAX_P_AGE_S + D("0.1")))["reason"] \
            == "PROBABILITY_STALE"

    def test_there_is_no_default_edge_margin(self, on):
        """An unmeasured edge gets no free margin."""
        assert call(min_edge_per_share="0")["reason"] == "NO_EDGE_MARGIN_SET"


# ------------------------------------------------------- the shared budget

class TestItSpendsOnlyWhatItIsGiven:

    def test_no_shared_room_means_no_order(self, on):
        assert call(room_usd="0")["reason"] == "NO_LEVEL_QUALIFIED"
        assert call(room_usd="0")["considered"][0]["reject"] == "NO_ROOM"

    def test_the_clip_bounds_the_order(self, on):
        r = call(clip_usd="0.97", room_usd="100", loss_room_usd="100")
        assert r["order"]["qty"] == D("1.00")

    def test_the_shared_room_bounds_the_order_below_the_clip(self, on):
        """Whatever the clip says, it may not exceed what the shared
        exposure budget still allows."""
        r = call(clip_usd="100", room_usd="1.94", loss_room_usd="100")
        assert D(r["cost_usd"]) <= D("1.94")
        assert r["order"]["qty"] == D("2.00")

    def test_the_daily_loss_budget_bounds_it_too(self, on):
        """The worst case is the WHOLE outlay: a losing favourite pays
        nothing, so the halt budget must hold the full cost."""
        r = call(clip_usd="100", room_usd="100", loss_room_usd="0.97")
        assert D(r["cost_usd"]) <= D("0.97")

    def test_depth_bounds_it(self, on):
        r = call(clip_usd="100", room_usd="100", loss_room_usd="100",
                 book=dict(BOOK, ask="0.99", ask_shares="3"))
        assert r["order"]["qty"] == D("3.00")

    def test_it_never_adds_to_a_favourite_it_already_holds(self, on):
        held = {"UP": {"qty": "2", "cost": "1.94"},
                "DOWN": {"qty": "0", "cost": "0"}}
        r = call(inventory=held)
        assert r["reason"] == "ALREADY_HOLD_FAVOURITE"

    def test_it_still_quotes_when_it_holds_only_the_OTHER_side(self, on):
        """Holding the underdog is the straddle's business, not a reason
        to refuse the favourite."""
        held = {"UP": {"qty": "0", "cost": "0"},
                "DOWN": {"qty": "2", "cost": "0.16"}}
        assert call(inventory=held)["ok"] is True


# ---------------------------------------------------------- the edge gate

class TestTheEdgeGate:

    def test_a_certain_favourite_at_97c_clears_a_half_cent_margin(self, on):
        r = call(p_up="0.9999", book=dict(BOOK, ask="0.99"))
        assert r["ok"] is True and D(r["ev_per_share"]) > D("0.005")

    def test_a_98_percent_favourite_does_not_clear_97c(self, on):
        """0.98 win on a 0.97 price is +1c gross; with a 1.5c margin
        required it is refused rather than traded thin."""
        r = call(p_up="0.98", book=dict(BOOK, ask="0.99"),
                 min_edge_per_share="0.015", prices=(D("0.97"),))
        assert r["ok"] is False
        assert r["considered"][0]["reject"] == "EDGE_BELOW_MARGIN"

    def test_the_break_even_is_reported_for_every_level(self, on):
        r = call(p_up="0.5001", book=dict(BOOK, ask="0.99"),
                 fee_per_share="0.002")
        assert r["ok"] is False
        assert all(D(c["break_even_p"]) == D(c["price"]) + D("0.002")
                   for c in r["considered"])


class TestItWillNotStackOnASideTheStraddleIsWorking:
    """The straddle rests a cheap rung on both sides. Buying the
    favourite at 0.97 while its 0.08 rung is still live risks both
    filling -- two shares of one side at wildly different prices, a
    directional position neither strategy chose."""

    def test_a_resting_order_on_the_favourite_refuses(self, on):
        r = call(resting=[{"side": "UP", "price": "0.08", "qty": "2",
                           "id": "b5m-" + "a" * 24}])
        assert r["ok"] is False and r["reason"] == "SIDE_ALREADY_WORKED"
        assert r["order"] is None
        assert [c["price"] for c in r["conflicts"]] == ["0.08"]

    def test_the_refusal_carries_the_order_it_would_have_placed(self, on):
        """The arbiter compares this candidate against the resting rung's
        continuation value. "Always refuse" is not a comparison."""
        r = call(resting=[{"side": "UP", "price": "0.08", "qty": "2",
                           "id": "b5m-" + "b" * 24}])
        assert r["blocked_order"]["side"] == "UP"
        assert r["blocked_order"]["post_only"] is True
        assert r["blocked_order"]["strategy"] == "LATE_FAVORITE"
        assert D(r["ev_usd"]) > D("0")

    def test_the_conflict_names_the_exact_order_to_cancel(self, on):
        """Exact-order cancellation needs the id, not a side."""
        coid = "b5m-" + "c" * 24
        r = call(resting=[{"side": "UP", "price": "0.08", "qty": "2",
                           "id": coid},
                          {"side": "DOWN", "price": "0.08", "qty": "2",
                           "id": "b5m-" + "d" * 24}])
        assert [c["id"] for c in r["conflicts"]] == [coid]

    def test_ev_usd_scales_with_the_clip(self, on):
        one = call(clip_usd="0.97", book=dict(BOOK, ask="0.99"))
        two = call(clip_usd="1.94", book=dict(BOOK, ask="0.99"))
        assert two["order"]["qty"] == one["order"]["qty"] * 2
        assert D(two["ev_usd"]) == D(one["ev_usd"]) * 2

    def test_a_resting_order_on_the_OTHER_side_does_not(self, on):
        """The underdog rung is the straddle's business and cannot
        combine with ours into a one-sided position."""
        r = call(resting=[{"side": "DOWN", "price": "0.08", "qty": "2"}])
        assert r["ok"] is True and r["order"]["side"] == "UP"

    def test_the_late_window_does_not_overlap_the_straddle_entry_cutoff(self):
        """Params.no_new_entry_after_s is 270 of 300. At the default 30s
        this strategy starts exactly when the straddle stops placing, so
        the two can never allocate from one budget in the same step."""
        from src import l5_btc5m_controller as C
        assert C.Params().window_s - C.Params().no_new_entry_after_s == 30.0
        assert LF.DEFAULT_WINDOW_S == D("30")

    def test_it_is_silent_before_that_cutoff(self, on):
        assert call(t_in_window_s="269")["reason"] == "TOO_EARLY"
        assert call(t_in_window_s="270")["ok"] is True
