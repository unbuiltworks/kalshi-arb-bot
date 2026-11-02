"""Two simultaneous orders can create a guaranteed-loss pair, unrefused.

THE DEFECT, stated precisely. `pair_cost_refusal` is the one boundary every
order passes, and it is judged on VENUE-CONFIRMED FILLS:

    lc = self.store.leg_cost(market)       # confirmed fills only
    matched_after = min(after[UP], after[DOWN])
    if matched_after <= matched_before:
        return None                        # creates no new matched quantity

That is correct for SEQUENTIAL completion -- "a submission is an intention,
not a hedge" -- and it is exactly why SIMULTANEOUS quoting escapes it. With
both legs submitted while the book is flat, each submission sees
`matched_before == matched_after == 0`, so each is a legitimate FIRST LEG
and neither is ever the leg that "completes a pair". The ceiling never
engages, and if both fill the pair can cost anything at all.

THIS IS THE SHAPE OF A REAL INCIDENT. The module's own comment records
cpc-btc-updown-15m-2026-10-20-1215z: UP 2.46 at 0.9400 and DOWN 2.46 at
0.9000, a pair costing 1.84 that pays exactly 1.00 -- a CERTAIN loss of
-$2.0664 before fees. The ceiling was moved into `handle` to stop that. It
stops the sequential version. These tests show the simultaneous version is
still open.

THE LIVE CAMPAIGN USED THIS PATH. 2026-11-02 completed pairs at 0.9900 and
0.9700, both above the 0.96 ceiling, admitted because simultaneous
submission meant neither leg was ever the completing one. Those two were
PROFITABLE -- the point is that nothing in the path required them to be.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS

#: the market from the recorded incident
SLUG = "cpc-btc-updown-15m-2026-10-20-1215z"


@pytest.fixture()
def ex(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "SIMULTANEOUS-PAIR")
    monkeypatch.delenv(TS.EXPERIMENT_ENV, raising=False)
    monkeypatch.delenv(TS.MARKET_ENV, raising=False)
    t = H.FakeTransport(fill_all=True)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    x = E.FaithfulExecutor(H.store(tmp_path),
                           H.limits(max_live_orders=4), transport=t,
                           now_fn=H.NOW)
    return x, t, H, E


# ===================================================== THE DEFECT ITSELF

def test_the_ceiling_does_not_engage_while_both_legs_are_only_submitted(ex):
    """THE CORE OF IT. Neither submission is ever the completing leg."""
    x, t, H, E = ex
    # leg one: submitted, NOT filled. A first leg is unconstrained.
    first = x.pair_cost_refusal(SLUG, {"side": "UP"},
                                {"price": "0.94", "qty": "2.46"})
    assert first is None, "a genuine first leg must stay unconstrained"
    x.store.record_submit("c-up", {"market": SLUG, "side": "UP",
                                   "price": "0.94", "qty": "2.46"})

    # leg two, submitted while leg one is still only RESTING. `leg_cost`
    # holds no confirmed fill, so matched_after is 0 and the ceiling
    # returns None -- even though the pair this creates costs 1.84.
    second = x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                                 {"price": "0.90", "qty": "2.46"})
    assert second is None, (
        "THE DEFECT: the second simultaneous leg is not judged either, so "
        "nothing refuses a pair costing 1.84")


def test_the_same_pair_IS_refused_when_the_first_leg_actually_filled(ex):
    """The contrast that proves it is the SIMULTANEITY, not the prices.
    Identical economics, one confirmed fill, and the ceiling engages."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2.46"), D("0.94"), basis="OWN")
    bad = x.pair_cost_refusal(SLUG, {"side": "DOWN"},
                              {"price": "0.90", "qty": "2.46"})
    assert bad is not None, (
        "sequentially the ceiling works -- so the gap is simultaneity")


def test_the_guaranteed_loss_is_arithmetic_not_a_modelled_risk(ex):
    """If both fill: 2.46 matched at a pair cost of 1.84 pays exactly 1.00
    per pair, so the loss is certain before any fee."""
    pair_cost = D("0.94") + D("0.90")
    qty = D("2.46")
    assert pair_cost > D("1.00")
    certain_loss = qty * (pair_cost - D("1.00"))
    assert certain_loss == D("2.0664")


def test_a_pair_above_one_dollar_is_reachable_at_any_split(ex):
    """It is not a quirk of 0.94/0.90. Any own-price split summing above
    1.00 is admissible while both legs are merely submitted.
    """
    x, t, H, E = ex
    for up_px, dn_px in (("0.60", "0.60"), ("0.95", "0.20"),
                         ("0.50", "0.55")):
        assert x.pair_cost_refusal(
            SLUG, {"side": "UP"}, {"price": up_px, "qty": "1"}) is None
        x.store.record_submit("c-%s" % up_px,
                              {"market": SLUG, "side": "UP",
                               "price": up_px, "qty": "1"})
        assert x.pair_cost_refusal(
            SLUG, {"side": "DOWN"}, {"price": dn_px, "qty": "1"}) is None, (
            "pair %s + %s = %s admitted unrefused"
            % (up_px, dn_px, D(up_px) + D(dn_px)))


# ============================= WHAT MUST KEEP WORKING AFTER ANY FIX

def test_profitable_simultaneous_pairs_at_0_97_to_0_99_must_stay_allowed(ex):
    """The live campaign's real pairs. A fix that forces 0.96 onto
    simultaneous quoting would delete these, and they settled PROFITABLY:
    0145z at 0.9900 (+$0.030625) and 0200z at 0.9700 (+$0.183732).
    """
    x, t, H, E = ex
    for up_px, dn_px in (("0.47", "0.52"), ("0.51", "0.46"),
                         ("0.58", "0.37")):
        pair = D(up_px) + D(dn_px)
        assert pair < D("1.00"), "these are genuinely profitable pairs"
        assert x.pair_cost_refusal(
            SLUG, {"side": "UP"}, {"price": up_px, "qty": "1"}) is None
        assert x.pair_cost_refusal(
            SLUG, {"side": "DOWN"}, {"price": dn_px, "qty": "1"}) is None


def test_a_one_sided_order_must_not_be_refused_merely_for_being_one_sided(ex):
    """INTENTIONAL IMBALANCE IS WELCOME. Directional exposure is bounded by
    the residual, exposure, cash and worst-case limits -- not by this gate.
    """
    x, t, H, E = ex
    assert x.pair_cost_refusal(
        SLUG, {"side": "UP"}, {"price": "0.30", "qty": "3"}) is None
    x.store.apply_fill(SLUG, "UP", D("3"), D("0.30"), basis="OWN")
    # adding MORE of the same side creates no matched quantity, so this gate
    # has nothing to say about it. That is correct: other limits govern.
    assert x.pair_cost_refusal(
        SLUG, {"side": "UP"}, {"price": "0.30", "qty": "3"}) is None


def test_a_cheap_completing_leg_is_still_allowed_sequentially(ex):
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2"), D("0.47"), basis="OWN")
    assert x.pair_cost_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.45", "qty": "2"}) is None


def test_unreadable_state_still_refuses(ex):
    """A pair cost we cannot compute is not one we may assume is fine."""
    x, t, H, E = ex
    bad = x.pair_cost_refusal(SLUG, {"side": "SIDEWAYS"},
                              {"price": "0.5", "qty": "1"})
    assert bad is not None
