"""The straddle gate must charge the unmatched leg for adverse selection.

THE DEFECT, measured 2026-11-19 against the production controller.

`l5_btc5m_controller`'s own module header states the value of a passive
maker buy at x: `p_s - x - adverse_selection_per_share`, from a measured ~6
cents a share. Its EDGE_GATED path charges that (`ev_at`). The STRADDLE path
did not. On this venue the maker fee is 0 and the maker rebate is Tier 1,
which is zero, so with the adverse charge missing the entry EV reduced to

    p_pair_natural * (1 - pair_cost)

which is positive for ANY p_pair_natural above zero. On a 0.52/0.46
book-relative quote the controller submitted two legs at
p_pair_natural = 0.001 -- one tenth of one percent. The gate was arithmetic,
not a decision.

WHY IT BELONGS ON THE LONE BRANCH ONLY. A natural pair is locked once both
legs fill: it pays 1 - (both costs) whatever the index does, so nothing can
adversely select it. A completion is bought deliberately at a known pair
cost. The leg that ends up ALONE is the one we were filled on BECAUSE the
price moved against us, and that is the entirety of the owner's stated
failure mode: profitable completed pairs overwhelmed by losses on unmatched
inventory. Measured on 226 settled BTC5m windows of recorded quotes
(`analysis/l5/btc5m/unmatched_exit_value.py`), joining both touches:
pairs paid +0.0352 while lone legs lost -0.4370.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_controller as C          # noqa: E402
from src import l5_straddle_ev as SEV             # noqa: E402

ADV = D("0.06")


def ev(**kw):
    base = dict(price=D("0.08"), other_price=D("0.08"), pair_target=D("0.96"),
                p_pair_natural=D("0.30"), p_complete=D("0.10"),
                p_lone_win=D("0.0981"), fee_per_share=D("0"),
                adverse_per_share=ADV)
    base.update(kw)
    return SEV.entry_ev(**base)


# ------------------------------------------- it is required, not defaulted

def test_the_charge_is_required():
    """A missing adverse-selection input must refuse, exactly as a missing
    probability does. Defaulting it to zero is the assumption that being
    filled tells us nothing -- the assumption that emptied the gate."""
    with pytest.raises(TypeError):
        SEV.entry_ev(price=D("0.08"), other_price=D("0.08"),
                     pair_target=D("0.96"), p_pair_natural=D("0.30"),
                     p_complete=D("0.10"), p_lone_win=D("0.0981"),
                     fee_per_share=D("0"))


@pytest.mark.parametrize("bad", [None, "abc", "", D("-0.01"), "-1", float("nan")])
def test_an_unreadable_charge_refuses(bad):
    got = ev(adverse_per_share=bad)
    assert got["ok"] is False
    assert got["reason"] == "ADVERSE_UNREADABLE"
    assert got["ev_per_share"] is None
    assert SEV.admits(got)["ok"] is False


def test_zero_is_accepted_but_must_be_stated():
    """Zero is a legitimate claim about a venue -- it just has to be made
    explicitly rather than assumed."""
    assert ev(adverse_per_share=D("0"))["ok"] is True


# --------------------------------------------- it is charged, and correctly

def test_the_charge_lowers_the_value():
    assert (ev(adverse_per_share=D("0"))["ev_per_share"]
            > ev(adverse_per_share=ADV)["ev_per_share"])


def test_it_is_charged_exactly_once_per_lone_share():
    """p_lone * adverse, and nothing more: the arithmetic, pinned."""
    free = ev(adverse_per_share=D("0"))["ev_per_share"]
    charged = ev(adverse_per_share=ADV)["ev_per_share"]
    p_lone = D("1") - D("0.30") - D("0.10")
    assert free - charged == p_lone * ADV


def test_it_is_not_charged_on_a_locked_pair():
    """A pair that fills both legs pays 1 - both costs whatever happens, so
    at p_pair_natural = 1 the charge must make no difference at all."""
    kw = dict(p_pair_natural=D("1"), p_complete=D("0"))
    assert (ev(adverse_per_share=D("0"), **kw)["ev_per_share"]
            == ev(adverse_per_share=ADV, **kw)["ev_per_share"])


def test_it_is_not_charged_on_a_deliberate_completion():
    kw = dict(p_pair_natural=D("0"), p_complete=D("1"))
    assert (ev(adverse_per_share=D("0"), **kw)["ev_per_share"]
            == ev(adverse_per_share=ADV, **kw)["ev_per_share"])


def test_the_breakdown_reports_it():
    """It must be visible in the evidence, not folded into a total."""
    b = ev()["breakdown"]["lone_leg"]
    assert b["adverse_per_share"] == str(ADV)


# ------------------------------- FAIL-CLOSED ECONOMIC GATING, end to end

def params(**kw):
    base = dict(
        entry_mode=C.STRADDLE, straddle_repeat_enabled=True,
        straddle_max_side_usd=D("0.60"), straddle_max_unmatched_usd=D("0.60"),
        max_exposure_usd=D("5.00"), no_new_entry_after_s=270.0,
        clip_usd=D("0.55"), hedge_enabled=False, reserve_mode="NONE",
        straddle_price=D("0.08"),
        straddle_p_pair_natural=D("0.070"), straddle_p_complete=D("0.131"),
        straddle_p_lone_win=D("0.0981"),
        straddle_book_relative=True, straddle_join_ticks=0,
        straddle_pair_ceiling=D("0.99"),
        straddle_fill_model_measured=True)
    base.update(kw)
    return C.Params(**base)


FLAT = {C.UP: {"qty": "0", "cost": "0"}, C.DOWN: {"qty": "0", "cost": "0"}}


def legs(p, bid="0.52", ask="0.54", t=60.0):
    r = C.decide(book={"bid": bid, "ask": ask, "age_s": 0.1},
                 t_in_window_s=t, inventory=FLAT, resting=[], params=p)
    return [o for o in r["orders"] if o.get("reason") == "STRADDLE_LEG"], r


def test_an_epsilon_pair_rate_no_longer_buys_two_legs():
    """THE DEFECT, as one assertion. 0.001 used to submit."""
    got, _ = legs(params(straddle_p_pair_natural=D("0.001")))
    assert got == []


def test_the_production_rate_no_longer_buys_a_touch_quote():
    """0.070 was measured at 0.08 rungs and used to admit a 0.52 quote."""
    got, _ = legs(params(straddle_p_pair_natural=D("0.070")))
    assert got == []


def test_a_rate_above_the_break_even_still_submits():
    """NOT a disable switch. At a 2-tick spread the pair pays 0.02 and the
    charge is 0.06, so the break-even rate is 0.06/(0.02+0.06) = 0.75 --
    and above it the controller still quotes both sides."""
    got, _ = legs(params(straddle_p_pair_natural=D("0.80")))
    assert len(got) == 2
    assert {o["side"] for o in got} == {C.UP, C.DOWN}


@pytest.mark.parametrize("rate,admitted", [
    ("0.7400", False), ("0.7500", False), ("0.7600", True), ("0.9000", True)])
def test_the_break_even_sits_where_the_arithmetic_says(rate, admitted):
    got, _ = legs(params(straddle_p_pair_natural=D(rate)))
    assert bool(got) is admitted, rate


def test_a_wider_spread_lowers_the_bar():
    """The pair pays the spread, so a 5-tick spread needs only 0.5455."""
    wide = params(straddle_p_pair_natural=D("0.60"))
    assert legs(wide, bid="0.50", ask="0.55")[0] != []
    assert legs(wide, bid="0.52", ask="0.54")[0] == []


def test_the_refusal_names_the_entry_ev_gate():
    _, r = legs(params(straddle_p_pair_natural=D("0.070")))
    blob = str(r.get("why")) + str(r.get("evaluation"))
    assert "ENTRY_EV" in blob or "EV_NOT_POSITIVE" in blob


# ------------------- SIMULTANEOUS UP/DOWN, and the rest of the brief

def test_both_sides_are_quoted_in_one_decision_when_justified():
    got, _ = legs(params(straddle_p_pair_natural=D("0.90")))
    assert sorted(o["side"] for o in got) == [C.DOWN, C.UP]
    #: and at BOOK prices, not a fixed 0.08
    px = {o["side"]: D(str(o["price"])) for o in got}
    assert px[C.UP] == D("0.52"), "UP joins the book bid"
    assert px[C.DOWN] == D("0.46"), "DOWN's touch is one minus the offer"
    assert px[C.UP] + px[C.DOWN] <= D("0.99"), "pair under the ceiling"


def test_the_two_legs_are_equal_size_so_a_fill_matches_one_for_one():
    got, _ = legs(params(straddle_p_pair_natural=D("0.90")))
    qtys = {str(o["qty"]) for o in got}
    assert len(qtys) == 1, "unequal legs would manufacture residual on purpose"


def test_both_legs_are_post_only_makers():
    got, _ = legs(params(straddle_p_pair_natural=D("0.90")))
    assert all(o.get("post_only") for o in got)
    assert all(o.get("intent") == "BUY" for o in got)


def test_no_new_entry_after_the_cutoff_however_good_the_economics():
    got, _ = legs(params(straddle_p_pair_natural=D("0.99")), t=280.0)
    assert got == []


def test_the_unmatched_ceiling_still_binds_above_the_break_even():
    """Good economics must not be able to buy past the inventory bound."""
    p = params(straddle_p_pair_natural=D("0.90"),
               straddle_max_unmatched_usd=D("0.01"))
    assert legs(p)[0] == []


def test_the_heavy_side_cap_still_binds_above_the_break_even():
    p = params(straddle_p_pair_natural=D("0.90"),
               straddle_max_side_usd=D("0.01"))
    assert legs(p)[0] == []


def test_the_fill_model_gate_still_precedes_all_of_it():
    """Book-relative quoting stays refused while the fill model for these
    prices is unmeasured, whatever the rates say."""
    p = params(straddle_p_pair_natural=D("0.99"),
               straddle_fill_model_measured=False)
    got, r = legs(p)
    assert got == []
    assert "FILL_MODEL_UNMEASURED" in str(r.get("evaluation")) + str(r.get("why"))


def test_a_stale_book_cannot_be_quoted_against():
    p = params(straddle_p_pair_natural=D("0.90"))
    r = C.decide(book={"bid": "0.52", "ask": "0.54", "age_s": 99.0},
                 t_in_window_s=60.0, inventory=FLAT, resting=[], params=p)
    assert [o for o in r["orders"] if o.get("reason") == "STRADDLE_LEG"] == []


# ------------------------------- FEES AND REBATES, as the venue has them

def test_the_fee_is_charged_on_every_share_win_or_lose():
    assert (ev(fee_per_share=D("0"))["ev_per_share"]
            - ev(fee_per_share=D("0.01"))["ev_per_share"]) == D("0.01")


def test_no_rebate_is_credited_anywhere():
    """Tier 1 maker rebate is ZERO on this account and Tier 2 needs
    $10,000,000 of 30-day maker volume (`pmus_fees`). Crediting an
    uncredited rebate is the error that made an earlier model profitable."""
    got = ev()
    assert "rebates are excluded" in got["note"]
    from src import pmus_fees as F
    assert F.ACCOUNT_MAKER_TIER == 1
    assert F.maker_theta() == D("0")


def test_the_fixed_rung_path_is_charged_too_but_survives_it():
    """The charge is in the SHARED gate, so the 0.08 ladder carries it too --
    and it survives, which is worth stating precisely because I assumed the
    opposite first.

    A 0.08/0.08 natural pair pays 0.84 a share. Against that, a 6-cent
    adverse charge on the lone branch is small: at the production rates the
    value falls but stays positive. So this fix does NOT reject the fixed
    straddle, and nothing here should be read as though it did.

    What rejects the fixed straddle is the CONDITIONAL-BASIS guard: its
    rates must be conditional on a fill, and measured conditionally the 0.08
    straddle went 0 for 20 on pairs and 0 for 20 on reached legs that won
    (`test_l5_btc5m_conditional_probability_basis.py`). Two different
    defects, two different guards.
    """
    rung = dict(price=D("0.08"), other_price=D("0.08"),
                pair_target=D("0.96"), p_pair_natural=D("0.070"),
                p_complete=D("0.131"), p_lone_win=D("0.0981"),
                fee_per_share=D("0"))
    free = SEV.entry_ev(**rung, adverse_per_share=D("0"))["ev_per_share"]
    charged = SEV.entry_ev(**rung, adverse_per_share=ADV)["ev_per_share"]
    assert charged < free, "the charge applies to the fixed path as well"
    assert charged > 0, "but a 0.84 pair payoff absorbs a 6-cent charge"
    #: and the controller consequently still quotes the 0.08 ladder
    p = params(straddle_book_relative=False, straddle_price=D("0.08"),
               straddle_rungs=(), clip_usd=D("0.08"),
               straddle_max_side_usd=D("0.30"),
               straddle_max_unmatched_usd=D("0.30"))
    assert len(legs(p)[0]) == 2


def test_what_the_charge_rejects_is_the_thin_pair_not_the_cheap_one():
    """The discriminating quantity is what a PAIR PAYS. A cheap straddle
    pays 0.84 and absorbs the charge; a touch quote pays the spread -- 0.02
    at two ticks -- and cannot. That is the whole economic content of this
    fix, so it is pinned as arithmetic."""
    def at(px, opx, p_nat):
        return SEV.entry_ev(price=px, other_price=opx, pair_target=D("0.96"),
                            p_pair_natural=p_nat, p_complete=D("0"),
                            p_lone_win=px, fee_per_share=D("0"),
                            adverse_per_share=ADV)["ev_per_share"]
    #: lone_pay is pw*(1-px) - (1-pw)*px, which at pw == px is exactly 0,
    #: so the lone branch is worth exactly -ADV and the bar is explicit
    assert at(D("0.52"), D("0.46"), D("0.70")) < 0
    assert at(D("0.52"), D("0.46"), D("0.80")) > 0
    #: the same 70% rate on a pair that pays 0.84 is comfortably positive
    assert at(D("0.08"), D("0.08"), D("0.70")) > 0
