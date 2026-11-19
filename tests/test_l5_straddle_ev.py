"""What a cheap straddle rung is worth before it is placed.

The reflex this replaces opened a rung whenever the clock and the cap
allowed. Live that produced 15 consecutive lone legs, each settling
-0.16. These tests pin that a rung must now state a positive expected
value, and that an UNKNOWN value refuses exactly as a negative one does.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_straddle_ev as S

#: measured over 503 shadow windows
PN, PC, PW = D("0.070"), D("0.131"), D("0.0981")


def ev(**kw):
    base = dict(price=D("0.08"), other_price=D("0.08"),
                pair_target=D("0.83"), p_pair_natural=PN,
                p_complete=PC, p_lone_win=PW, fee_per_share=D("0"),
                #: REQUIRED since the lone leg is charged adverse selection.
                #: Zero here keeps every pre-existing assertion about the
                #: OTHER inputs exactly as it was; the charge itself has its
                #: own tests in test_l5_straddle_adverse_selection.py.
                adverse_per_share=D("0"))
    base.update(kw)
    return S.entry_ev(**base)


class TestTheMeasuredShape:

    def test_the_measured_rung_is_positive_and_admitted(self):
        r = ev()
        assert r["ok"] is True
        assert r["ev_per_share"] == D("0.095531900")
        assert S.admits(r)["ok"] is True

    def test_the_natural_pair_is_the_largest_contributor(self):
        b = ev()["breakdown"]
        assert D(b["natural_pair"]["contributes"]) > \
            D(b["completion"]["contributes"]) > \
            D(b["lone_leg"]["contributes"])

    def test_the_three_endings_sum_to_one(self):
        b = ev()["breakdown"]
        total = (D(b["natural_pair"]["p"]) + D(b["completion"]["p"])
                 + D(b["lone_leg"]["p"]))
        assert total == D("1")

    def test_a_pair_pays_one_minus_both_legs(self):
        b = ev(price=D("0.08"), other_price=D("0.08"))["breakdown"]
        assert D(b["natural_pair"]["pays"]) == D("0.84")
        b2 = ev(price=D("0.08"), other_price=D("0.20"))["breakdown"]
        assert D(b2["natural_pair"]["pays"]) == D("0.72")


class TestItRejectsWhatIsNotWorthBuying:

    def test_a_deep_rung_is_negative_and_refused(self):
        """Depth buys the losing side: at 0.20 the lone-leg term swamps
        the pair term. This is the same conclusion the ladder study
        reached independently."""
        r = ev(price=D("0.20"), other_price=D("0.20"))
        assert r["ev_per_share"] < 0
        assert S.admits(r)["reason"] == "EV_NOT_POSITIVE"

    def test_the_sign_flips_somewhere_in_between(self):
        last_ok = None
        for cents in range(5, 30):
            px = D(cents) / D("100")
            r = ev(price=px, other_price=px)
            if S.admits(r)["ok"]:
                last_ok = px
            elif last_ok is not None:
                assert px > last_ok
                break
        assert last_ok is not None and D("0.05") <= last_ok <= D("0.25")

    def test_exactly_zero_is_refused(self):
        """Nonpositive, not negative: a free coin flip is not a trade."""
        r = {"ok": True, "ev_per_share": D("0")}
        assert S.admits(r)["reason"] == "EV_NOT_POSITIVE"

    def test_a_floor_can_demand_more_than_positive(self):
        r = ev()
        assert S.admits(r, min_ev_per_share=D("0.05"))["ok"] is True
        got = S.admits(r, min_ev_per_share=D("0.50"))
        assert got["reason"] == "EV_BELOW_FLOOR"

    def test_a_fee_can_turn_a_rung_negative(self):
        assert ev(fee_per_share=D("0.09"))["ev_per_share"] > 0
        assert ev(fee_per_share=D("0.11"))["ev_per_share"] < 0


class TestUnknownIsRefusedLikeNegative:

    @pytest.mark.parametrize("kw,reason", [
        ({"p_pair_natural": None}, "P_PAIR_NATURAL_NOT_SET"),
        ({"p_complete": None}, "P_COMPLETE_NOT_SET"),
        ({"p_lone_win": None}, "P_LONE_WIN_NOT_SET"),
        ({"p_lone_win": "1.5"}, "P_LONE_WIN_NOT_SET"),
        ({"p_lone_win": "-0.1"}, "P_LONE_WIN_NOT_SET"),
        ({"price": None}, "PRICE_UNREADABLE"),
        ({"price": "1.2"}, "PRICE_UNREADABLE"),
        ({"other_price": None}, "OTHER_PRICE_UNREADABLE"),
        ({"fee_per_share": None}, "FEE_UNREADABLE"),
        ({"fee_per_share": "-0.01"}, "FEE_UNREADABLE"),
    ])
    def test_an_unset_rate_refuses_rather_than_being_guessed(self, kw, reason):
        """A guessed rate manufactures the edge the gate tests for."""
        r = ev(**kw)
        assert r["ok"] is False and r["reason"] == reason
        assert r["ev_per_share"] is None
        assert S.admits(r)["ok"] is False

    def test_probabilities_that_sum_past_one_are_refused(self):
        """The three endings are exclusive."""
        r = ev(p_pair_natural=D("0.6"), p_complete=D("0.6"))
        assert r["reason"] == "PROBABILITIES_INCONSISTENT"

    def test_a_completion_probability_needs_a_usable_target(self):
        for tgt in (None, D("0"), D("0.05"), D("1.5")):
            assert ev(pair_target=tgt)["reason"] == "PAIR_TARGET_UNREADABLE"

    def test_no_completion_probability_needs_no_target(self):
        """With completion off the rung is worth its natural pair plus
        its lone leg, and the target is irrelevant."""
        r = ev(p_complete=D("0"), pair_target=None)
        assert r["ok"] is True
        assert D(r["breakdown"]["completion"]["contributes"]) == D("0")

    def test_an_unavailable_ev_is_not_admitted(self):
        assert S.admits(None)["ok"] is False
        assert S.admits({})["ok"] is False
        assert S.admits({"ok": True, "ev_per_share": None})["ok"] is False


class TestRebatesAreNotIncome:

    def test_the_result_says_rebates_are_excluded(self):
        """An uncredited rebate is not income, so there is nowhere to put
        one: the fee is net or it is nothing."""
        assert "rebate" in ev()["note"]
