"""THE CAMPAIGN'S WORST CASE, MEASURED RATHER THAN ASSERTED.

A reservation fix existing does not prove a ceiling holds. This drives
the REAL controller adversarially -- every resting order on one side
fills at the moment that maximises unmatched exposure, repeatedly, for a
whole window, with the book oscillating so repricing keeps moving the
quote and concurrent same-side orders really do arise -- and measures
the PEAK worst-case settlement loss the controller ever permits.

WHY THE ADVERSARIAL FILL MODEL IS THE RIGHT ONE. The quantity at risk is
the cost of inventory that settles worthless, so the bound has to be
taken over the fill sequence that maximises unmatched cost. Filling one
side repeatedly and never the other is exactly that sequence.

WHAT IS AND IS NOT INCLUDED. Settlement is included by construction:
`worst_case_settlement_loss_usd` IS the cost of the unmatched leg, which
is what a worthless settlement costs. A cancel that loses to a fill is
included, because every resting order is assumed to fill. Fees
contribute ZERO: a Tier 1 maker pays no fee and receives no rebate --
confirmed on the venue's own fee page ("Maker Tier 1 receives no
rebate", no maker fee defined for standard trades) and observed as
$0.0000 on the two venue-confirmed fills in window 1500z. Taker is
disabled for this campaign, so no taker coefficient can apply.

WHAT THIS DOES NOT PROVE. Nothing here says the campaign makes money. It
bounds the LOSS. Whether the 1-2c gross pair margin survives adverse
selection is the open question the campaign exists to answer.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


#: the book oscillates, so inventory-aware repricing keeps moving the
#: quote and two same-side orders genuinely coexist -- the concurrency
#: that used to breach the ceiling by a clip.
BOOKS = [("0.30", "0.31"), ("0.29", "0.30"), ("0.31", "0.32"),
         ("0.28", "0.29"), ("0.32", "0.33"), ("0.27", "0.28"),
         ("0.33", "0.34"), ("0.26", "0.27")]


def params(clip, unmatched, side_cap):
    return C.Params(
        entry_mode=C.STRADDLE, straddle_price=D("0.08"), clip_usd=clip,
        hedge_enabled=False, reserve_mode="NONE",
        max_exposure_usd=D("8.00"),
        straddle_book_relative=True, straddle_fill_model_measured=True,
        straddle_pair_ceiling=D("0.99"), straddle_join_ticks=0,
        straddle_repeat_enabled=True,
        straddle_max_side_usd=side_cap,
        straddle_max_unmatched_usd=unmatched,
        straddle_completion_pair_cost=D("0"),      # maker-only campaign
        straddle_completion_reserve=True,
        inventory_ranking_enabled=True,
        #: synthetic rates, mechanism only -- the measured 0.08 rates
        #: refuse at these prices, which is why discovery needs separate
        #: authorization. See test_l5_book_relative_inventory.
        straddle_p_pair_natural=D("0.95"), straddle_p_complete=D("0"),
        straddle_p_lone_win=D("0.50"), straddle_min_ev_per_share=D("0"))


def run_window(p, heavy, steps=64):
    """One window, filling `heavy` adversarially. Returns peak worst-case
    settlement loss and the number of fills taken."""
    inv = {C.UP: [D("0"), D("0")], C.DOWN: [D("0"), D("0")]}
    rest, peak, oid, fills = [], D("0"), 0, 0
    for i in range(steps):
        b, a = BOOKS[i % len(BOOKS)]
        r = C.decide(book={"bid": b, "ask": a, "age_s": 0.1},
                     t_in_window_s=float(10 + i * 4),
                     inventory={s: {"qty": str(inv[s][0]),
                                    "cost": str(inv[s][1])}
                                for s in (C.UP, C.DOWN)},
                     resting=list(rest), params=p)
        gone = {o["id"] for o in r["orders"] if o.get("op") == "CANCEL"}
        rest = [o for o in rest if o["id"] not in gone]
        for o in r["orders"]:
            if o.get("op") != "PLACE":
                continue
            oid += 1
            rest.append({"id": "x%d" % oid, "side": o["side"],
                         "price": o["price"], "qty": o["qty"], "age_s": 1.0})
        keep = []
        for o in rest:
            if o["side"] == heavy:
                inv[heavy][0] += o["qty"]
                inv[heavy][1] += o["price"] * o["qty"]
                fills += 1
            else:
                keep.append(o)
        rest = keep
        exp = C.exposure({s: {"qty": str(inv[s][0]), "cost": str(inv[s][1])}
                          for s in (C.UP, C.DOWN)}, list(rest))
        peak = max(peak, D(str(exp["worst_case_settlement_loss_usd"])))
    return peak, fills


class TestTheCeilingHoldsUnderAdversarialFilling:

    @pytest.mark.parametrize("heavy", [C.UP, C.DOWN])
    def test_peak_never_exceeds_the_unmatched_ceiling(self, heavy):
        U = D("0.30")
        peak, fills = run_window(params(D("0.30"), U, D("0.60")), heavy)
        assert fills > 0, "the adversarial model never filled anything"
        assert peak <= U, "ceiling breached: %s > %s" % (peak, U)

    def test_the_fix_is_what_holds_it(self):
        #: THE REGRESSION GUARD. Before outstanding orders were counted,
        #: two concurrent same-side orders could each be admitted against
        #: an inventory excluding the other, so the reachable peak was the
        #: ceiling PLUS a clip. Dropping `rest` from the prospective
        #: calculation reproduces the old, looser bound -- this asserts
        #: the stricter one is genuinely stricter.
        inv = {C.UP: (D("0"), D("0")), C.DOWN: (D("0"), D("0"))}
        rested = [{"id": "o1", "side": C.UP, "price": D("0.30"),
                   "qty": D("1"), "age_s": 1.0}]
        loose = C._prospective_unmatched_usd(C.UP, D("0.30"), D("1"), inv)
        tight = C._prospective_unmatched_usd(C.UP, D("0.30"), D("1"), inv,
                                             rested)
        assert tight > loose and tight == D("0.60")

    def test_three_windows_stay_inside_the_dollar(self):
        #: the campaign bound. Worst case is taken over BOTH heavy sides
        #: and multiplied by the window count, because each window starts
        #: flat and can reach its own peak.
        U = D("0.30")
        worst = max(run_window(params(D("0.30"), U, D("0.60")), h)[0]
                    for h in (C.UP, C.DOWN))
        assert worst * 3 <= D("1.00"), worst * 3
        #: and it is not trivially small -- a bound that held because
        #: nothing traded would prove nothing
        assert worst >= D("0.25"), worst

    @pytest.mark.parametrize("clip,U,cap", [
        (D("0.30"), D("0.20"), D("0.60")),
        (D("0.20"), D("0.20"), D("0.40")),
        (D("0.15"), D("0.15"), D("0.30"))])
    def test_tighter_configurations_also_hold(self, clip, U, cap):
        worst = max(run_window(params(clip, U, cap), h)[0]
                    for h in (C.UP, C.DOWN))
        assert worst <= U and worst * 3 <= D("1.00")

    def test_fees_contribute_nothing_at_our_tier(self):
        #: maker-only campaign, Tier 1: no maker fee is defined and no
        #: rebate is paid, so the loss bound needs no fee term. Taker is
        #: disabled, so the 0.095 coefficient cannot apply.
        from src import pmus_fees as F
        theta, rebate = F.thetas()
        assert rebate == 0, "a rebate would be income, not a cost"
        assert theta == D("0.095"), theta
