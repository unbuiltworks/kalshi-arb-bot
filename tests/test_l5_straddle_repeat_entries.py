"""BOUNDED REPEATED ENTRIES, replacing the one-fill-per-side lockout.

WHAT THE LOCKOUT WAS. `if inv[side][0] > 0: continue` -- "this side is
done". It capped the mode at two fills a window against the 106-207 per
window measured on the behaviour being replicated, and it was ALSO the
de-facto unmatched-inventory bound: one clip a side means imbalance can
never exceed one clip. Removing it alone would have left a directional
bet wearing a straddle's clothes, so these tests pin BOTH replacement
bounds, not just the repetition.

THESE TESTS PIN MECHANISM, NOT EDGE. Nothing here says repeated entry is
profitable. The entry-EV gate still decides that, and the probabilities
it needs are measured at 0.08 rungs and are NOT measured at mid price --
see `test_the_ev_gate_still_refuses_an_unmeasured_rung`.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C


@pytest.fixture(autouse=True)
def _controller_on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


BOOK = {"bid": "0.60", "ask": "0.61", "age_s": 0.1}
FLAT = {C.UP: {"qty": "0", "cost": "0"}, C.DOWN: {"qty": "0", "cost": "0"}}


def inv(up_qty="0", up_cost="0", dn_qty="0", dn_cost="0"):
    return {C.UP: {"qty": up_qty, "cost": up_cost},
            C.DOWN: {"qty": dn_qty, "cost": dn_cost}}


def params(**kw):
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.40"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("3.00"),
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"))
    base.update(kw)
    return C.Params(**base)


def decide(inventory=None, resting=(), t=10.0, **kw):
    return C.decide(book=BOOK, t_in_window_s=t,
                    inventory=inventory or FLAT, resting=list(resting),
                    params=params(**kw))


def legs(res, side=None):
    #: `.get` because the order list also carries CANCELs, which have no
    #: `reason` -- and a cancel beside a fresh leg is exactly what the
    #: replenishment and cap tests produce.
    return [o for o in res["orders"]
            if o.get("reason") == "STRADDLE_LEG" and (side is None
                                                      or o["side"] == side)]


def resting_order(side, price, qty, oid="o1"):
    return {"id": oid, "side": side, "price": D(price), "qty": D(qty)}


# ---------------------------------------------------------------- default

class TestTheDefaultIsUnchanged:
    """Every configuration that exists today must behave exactly as it did.
    Repetition is a new mode and has to be asked for."""

    def test_a_side_holding_inventory_is_still_finished_by_default(self):
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"))
        assert legs(res, C.UP) == []
        assert len(legs(res, C.DOWN)) == 1

    def test_repeat_defaults_off(self):
        assert C.Params().straddle_repeat_enabled is False
        assert C.Params().straddle_max_side_usd == D("0")
        assert C.Params().straddle_max_unmatched_usd == D("0")
        assert C.Params().straddle_reprice_ticks_per_clip == 0


class TestRepetitionWithoutBoundsIsRefused:
    """Repeated entry with no unmatched ceiling is the one shape this
    strategy must never hold, so the MODE refuses rather than defaulting
    a bound into existence."""

    @pytest.mark.parametrize("side_usd,unm_usd", [
        ("0", "0"), ("2.00", "0"), ("0", "2.00")])
    def test_the_mode_refuses(self, side_usd, unm_usd):
        res = decide(straddle_repeat_enabled=True,
                     straddle_max_side_usd=D(side_usd),
                     straddle_max_unmatched_usd=D(unm_usd))
        assert res["why"] == "REPEAT_WITHOUT_BOUNDS"
        assert legs(res) == []

    def test_with_both_bounds_it_proceeds(self):
        res = decide(straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert res["why"] == "STRADDLE_RESTING"
        assert len(legs(res)) == 2


# ------------------------------------------------------- repeated fills

class TestRepeatedFills:
    def test_a_filled_side_is_re_entered_when_repetition_is_on(self):
        """THE POINT OF THE CHANGE. The side that filled gets another clip
        instead of being retired for the window."""
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert len(legs(res, C.UP)) == 1
        assert len(legs(res, C.DOWN)) == 1

    def test_replenishment_only_happens_once_the_rung_is_gone(self):
        """A rung still resting is not re-placed: replenishment replaces a
        CONSUMED order, it does not stack duplicates at one price."""
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     resting=[resting_order(C.UP, "0.08", "5")],
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert legs(res, C.UP) == []

    def test_repetition_still_quotes_BOTH_sides(self):
        """Simultaneous UP/DOWN quoting is preserved: repetition must not
        turn the ladder one-sided."""
        res = decide(straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert {o["side"] for o in legs(res)} == {C.UP, C.DOWN}


class TestPartialFills:
    def test_a_partial_fill_does_not_block_the_rest_of_the_side(self):
        """Half a clip filled is still room on that side."""
        res = decide(inventory=inv(up_qty="2.5", up_cost="0.20"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert len(legs(res, C.UP)) == 1

    def test_a_partial_fill_does_not_nudge_the_quote(self):
        """Repricing moves on WHOLE clips of imbalance. A partial fill
        that has not completed a clip must leave the price alone, or the
        quote would chase every fragment."""
        res = decide(inventory=inv(up_qty="2.5", up_cost="0.20"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"),
                     straddle_reprice_ticks_per_clip=1)
        assert [str(o["price"]) for o in legs(res, C.UP)] == ["0.08"]


# ------------------------------------------------------ inventory limits

class TestTheSideCap:
    def test_a_side_at_its_cap_takes_no_more(self):
        res = decide(inventory=inv(up_qty="25", up_cost="2.00"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("99"))
        assert legs(res, C.UP) == []
        assert res["why"] in ("SIDE_CAP_REACHED", "STRADDLE_RESTING")

    def test_resting_notional_counts_against_the_side_cap(self):
        """An order we have already placed is money already committed; a
        cap that ignored it could be breached by fills we asked for."""
        res = decide(resting=[resting_order(C.UP, "0.07", "25")],
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("99"))
        assert legs(res, C.UP) == []


class TestTheUnmatchedCeiling:
    def test_it_refuses_the_clip_that_would_breach_it(self):
        """UP already holds an unmatched clip; a second one would double
        the directional exposure past the ceiling."""
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("99"),
                     straddle_max_unmatched_usd=D("0.50"))
        assert legs(res, C.UP) == []

    def test_the_LIGHT_side_is_still_allowed_because_it_reduces_imbalance(self):
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("99"),
                     straddle_max_unmatched_usd=D("0.50"))
        assert len(legs(res, C.DOWN)) == 1

    def test_balanced_inventory_has_no_unmatched_exposure(self):
        """A matched pair is not directional, so the ceiling does not bind
        on it however large it is."""
        res = decide(inventory=inv(up_qty="50", up_cost="4.00",
                                   dn_qty="50", dn_cost="4.00"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("99"),
                     straddle_max_unmatched_usd=D("0.50"),
                     max_exposure_usd=D("99"))
        assert len(legs(res)) == 2

    def test_unmatched_is_valued_at_what_was_paid_not_at_a_dollar(self):
        """What an unmatched contract loses is its cost. Valuing it at
        $1.00 would refuse clips the ceiling permits."""
        assert C._prospective_unmatched_usd(
            C.UP, D("0.08"), D("5"),
            {C.UP: (D("0"), D("0")), C.DOWN: (D("0"), D("0"))}) == D("0.40")


# ------------------------------------------------------------ repricing

class TestInventoryAwareRepricing:
    def test_disabled_by_default_the_price_never_moves(self):
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert [str(o["price"]) for o in legs(res, C.UP)] == ["0.08"]

    def test_the_heavy_side_bids_lower(self):
        """One clip of imbalance on UP shades UP down a tick: we bid less
        aggressively for more of what we already hold."""
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"),
                     straddle_reprice_ticks_per_clip=1)
        assert [str(o["price"]) for o in legs(res, C.UP)] == ["0.07"]

    def test_the_light_side_bids_HIGHER_to_rebalance(self):
        """The other half of repricing: the side we are short of is bid
        more aggressively, so the imbalance closes from both ends. This
        RAISES the pair cost, which is why the EV gate is given both
        actual prices rather than assuming symmetry."""
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"),
                     straddle_reprice_ticks_per_clip=1)
        assert [str(o["price"]) for o in legs(res, C.DOWN)] == ["0.09"]

    def test_repricing_keeps_the_pair_cost_bounded_by_the_band(self):
        """Both shades together: heavy 0.07 + light 0.09 = 0.16, the same
        pair cost as 0.08 + 0.08. Repricing rebalances WITHOUT making the
        pair more expensive."""
        res = decide(inventory=inv(up_qty="5", up_cost="0.40"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"),
                     straddle_reprice_ticks_per_clip=1)
        prices = sorted(o["price"] for o in legs(res))
        assert sum(prices) == D("0.16")

    def test_a_shift_out_of_the_band_is_refused_not_clamped(self):
        """A rung clamped to the edge of (0, 0.50) would quietly be a
        different trade from the configured one."""
        res = decide(inventory=inv(up_qty="25", up_cost="2.00"),
                     straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("99"),
                     straddle_max_unmatched_usd=D("99"),
                     straddle_reprice_ticks_per_clip=3,
                     max_exposure_usd=D("99"))
        assert legs(res, C.UP) == []

    def test_repricing_stays_on_the_tick_grid(self):
        for o in legs(decide(inventory=inv(up_qty="10", up_cost="0.80"),
                             straddle_repeat_enabled=True,
                             straddle_max_side_usd=D("9"),
                             straddle_max_unmatched_usd=D("9"),
                             straddle_reprice_ticks_per_clip=1,
                             max_exposure_usd=D("9"))):
            assert o["price"] == o["price"].quantize(D("0.01"))
            assert D("0") < o["price"] < D("0.50")


# --------------------------------------------- cancellation / rollover

class TestCancellationAndRollover:
    def test_entry_closes_before_the_cancel_sweep(self):
        """`no_new_entry_after_s` is 270 of 300, leaving the sweep 30s to
        cancel by exact id. Repetition must not place past it."""
        res = decide(t=271.0, straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert res["why"] == "ENTRY_WINDOW_CLOSED"
        assert legs(res) == []

    def test_past_the_window_nothing_is_placed(self):
        res = decide(t=301.0, straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"))
        assert legs(res) == []

    def test_repetition_never_exceeds_the_exposure_cap(self):
        """The cap is the outer bound and repetition is inside it, not
        beside it."""
        res = decide(straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("99"),
                     straddle_max_unmatched_usd=D("99"),
                     max_exposure_usd=D("0.40"))
        placed = sum(o["price"] * o["qty"] for o in legs(res))
        assert placed <= D("0.40")


# ------------------------------------------------------------ EV gate

class TestTheEVGateIsNotBypassed:
    def test_the_ev_gate_still_refuses_an_unmeasured_rung(self):
        """The gate's probabilities have NO defaults by design. Repetition
        does not supply them, so a rung whose rates are unset still
        refuses -- repeated entry cannot manufacture the edge."""
        res = decide(straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("2.00"),
                     straddle_max_unmatched_usd=D("2.00"),
                     straddle_p_pair_natural=None)
        assert legs(res) == []
        assert "P_PAIR_NATURAL_NOT_SET" in str(res["why"])

    def test_an_asymmetric_pair_is_priced_as_asymmetric(self):
        """With repricing on, the two rungs differ, so the gate must see
        the opposite side's ACTUAL price rather than assume symmetry."""
        ev = C.SEV.entry_ev(price=D("0.07"), other_price=D("0.08"),
                            pair_target=D("0.90"),
                            p_pair_natural=D("0.070"),
                            p_complete=D("0.131"),
                            p_lone_win=D("0.0981"),
                            fee_per_share=D("0"))
        sym = C.SEV.entry_ev(price=D("0.07"), other_price=D("0.07"),
                             pair_target=D("0.90"),
                             p_pair_natural=D("0.070"),
                             p_complete=D("0.131"),
                             p_lone_win=D("0.0981"),
                             fee_per_share=D("0"))
        assert ev["ev_per_share"] < sym["ev_per_share"]


# ------------------------------------------------- reachability from env

class TestTheEnvReadersMakeTheModeReachable:
    """A parameter with no environment reader sits at its dataclass default
    forever, and "refused every time" reads identically to "no
    opportunity" in a log. That defect disabled the late-favorite three
    ways over; these pin that it cannot happen to repetition."""

    def test_every_new_param_has_a_reader_wired_into_the_straddle_branch(self):
        from src import l5_btc5m_execution as E
        import inspect
        src = inspect.getsource(E)
        for name in ("straddle_repeat_enabled", "straddle_max_side_usd",
                     "straddle_max_unmatched_usd",
                     "straddle_reprice_ticks_per_clip"):
            assert "%s=" % name in src, name

    def test_defaults_are_off(self, monkeypatch):
        from src import l5_btc5m_execution as E
        for v in ("L5_BTC5M_STRADDLE_REPEAT", "L5_BTC5M_STRADDLE_MAX_SIDE_USD",
                  "L5_BTC5M_STRADDLE_MAX_UNMATCHED_USD",
                  "L5_BTC5M_STRADDLE_REPRICE_TICKS"):
            monkeypatch.delenv(v, raising=False)
        assert E.straddle_repeat_enabled() is False
        assert E.straddle_max_side_usd() == D("0")
        assert E.straddle_max_unmatched_usd() == D("0")
        assert E.straddle_reprice_ticks() == 0

    def test_only_an_explicit_YES_enables_repetition(self, monkeypatch):
        from src import l5_btc5m_execution as E
        for raw, want in (("YES", True), ("yes", True), ("Y", False),
                          ("1", False), ("TRUE", False), ("", False)):
            monkeypatch.setenv("L5_BTC5M_STRADDLE_REPEAT", raw)
            assert E.straddle_repeat_enabled() is want, raw

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-1"])
    def test_an_unusable_bound_refuses_rather_than_guessing(
            self, monkeypatch, bad):
        from src import l5_btc5m_execution as E
        monkeypatch.setenv("L5_BTC5M_STRADDLE_MAX_SIDE_USD", bad)
        monkeypatch.setenv("L5_BTC5M_STRADDLE_MAX_UNMATCHED_USD", bad)
        assert E.straddle_max_side_usd() == D("0")
        assert E.straddle_max_unmatched_usd() == D("0")

    def test_over_asking_is_clamped_DOWN_never_honoured(self, monkeypatch):
        """A typo must not widen risk. The unmatched ceiling IS the
        window's worst-case loss, so it is held to one clip's worth."""
        from src import l5_btc5m_execution as E
        monkeypatch.setenv("L5_BTC5M_STRADDLE_MAX_SIDE_USD", "999")
        monkeypatch.setenv("L5_BTC5M_STRADDLE_MAX_UNMATCHED_USD", "999")
        monkeypatch.setenv("L5_BTC5M_STRADDLE_REPRICE_TICKS", "999")
        assert E.straddle_max_side_usd() == E.STRADDLE_MAX_SIDE_USD_CEILING
        assert (E.straddle_max_unmatched_usd()
                == E.STRADDLE_MAX_UNMATCHED_USD_CEILING)
        assert E.straddle_reprice_ticks() == E.STRADDLE_REPRICE_TICKS_CEILING
        #: and the unmatched ceiling is no larger than one clip
        assert (E.STRADDLE_MAX_UNMATCHED_USD_CEILING
                <= E.STRADDLE_CLIP_MAX_USD)


class TestSettlementAccountingIsUnchanged:
    def test_matched_inventory_cannot_lose_at_these_rungs(self):
        """A pair bought at 2x0.08 pays $1.00, so the only way a window
        loses money is UNMATCHED inventory -- which is why the ceiling is
        denominated in dollars of unmatched cost."""
        pair_cost = D("0.08") * 2
        assert pair_cost < D("1.00")

    def test_the_unmatched_ceiling_bounds_the_windows_worst_case(self):
        """Worst case is the cost of inventory that settles worthless, and
        matched inventory never does."""
        inv = {C.UP: (D("10"), D("0.80")), C.DOWN: (D("5"), D("0.40"))}
        unmatched = C._prospective_unmatched_usd(C.UP, D("0.08"), D("0"), inv)
        assert unmatched == D("0.40")        # 5 unmatched at 0.08 avg
