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


# ================= THE GUARD THAT CLOSES IT: worst_pair_combination_refusal
#
# ADDITIVE. `pair_cost_refusal` above is unchanged and keeps its 0.96
# sequential-completion ceiling; the tests above still document its
# behaviour. This one asks the question that survives any fill order.

def test_the_guard_refuses_the_simultaneous_guaranteed_loss(ex):
    """THE DEFECT, CLOSED. The first leg stays unconstrained; the second is
    refused because a reachable pair would cost 1.84 against a $1.00
    redemption."""
    x, t, H, E = ex
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "UP"}, {"price": "0.94", "qty": "2.46"}) is None
    x.store.record_submit("c-up", {"market": SLUG, "side": "UP",
                                   "price": "0.94", "qty": "2.46"})
    bad = x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.90", "qty": "2.46"})
    assert bad is not None
    assert bad["why"] == "WORST_REACHABLE_PAIR_ABOVE_REDEMPTION"
    assert D(bad["worst_pair_cost"]) == D("1.84")
    assert D(bad["certain_loss_per_pair"]) == D("0.84")


def test_the_guard_uses_the_WORST_reachable_pair_not_the_average(ex):
    """THE CASE AVERAGING MISSES, and the reason the bound is a max.
    UP orders at 0.40 and 0.95 with a DOWN at 0.30 average to 0.975 and look
    fine. If only the 0.95 and the 0.30 fill, the pair costs 1.25.
    """
    x, t, H, E = ex
    x.store.record_submit("a", {"market": SLUG, "side": "UP",
                                "price": "0.40", "qty": "1"})
    x.store.record_submit("b", {"market": SLUG, "side": "UP",
                                "price": "0.95", "qty": "1"})
    bad = x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.30", "qty": "1"})
    assert bad is not None, "an averaged test would have admitted this"
    assert D(bad["dearest_up_own"]) == D("0.95")
    assert D(bad["worst_pair_cost"]) == D("1.25")


def test_the_guard_preserves_the_campaigns_profitable_pairs(ex):
    """0145z 0.9900, 0200z 0.9700, 0230z 0.9500 -- all admitted. A bound of
    0.96 here would have deleted two of the three."""
    x, t, H, E = ex
    for i, (up_px, dn_px) in enumerate((("0.47", "0.52"), ("0.51", "0.46"),
                                        ("0.58", "0.37"))):
        slug = "%s-%d" % (SLUG, i)
        x.store.record_submit("u%d" % i, {"market": slug, "side": "UP",
                                          "price": up_px, "qty": "1"})
        assert x.worst_pair_combination_refusal(
            slug, {"side": "DOWN"}, {"price": dn_px, "qty": "1"}) is None, (
            "pair %s must stay allowed" % (D(up_px) + D(dn_px)))


def test_the_guard_allows_a_pair_at_exactly_redemption(ex):
    """1.00 pays exactly what it costs: not a certain loss, so not refused
    here. Other gates govern whether it is worth doing."""
    x, t, H, E = ex
    x.store.record_submit("u", {"market": SLUG, "side": "UP",
                                "price": "0.58", "qty": "1"})
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.42", "qty": "1"}) is None


def test_the_guard_ignores_one_sided_inventory(ex):
    """No pair is reachable, so there is no pair cost to bound. Intentional
    imbalance is governed by the residual, exposure, cash and worst-case
    limits, not here."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("3"), D("0.30"), basis="OWN")
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "UP"}, {"price": "0.30", "qty": "3"}) is None
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "UP"}, {"price": "0.99", "qty": "1"}) is None


def test_confirmed_holdings_raise_the_dearest_price_too(ex):
    """A dear leg already BOUGHT is as reachable as a dear resting one."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2"), D("0.92"), basis="OWN")
    bad = x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.30", "qty": "2"})
    assert bad is not None
    assert D(bad["dearest_up_own"]) == D("0.92")


def test_a_resting_order_in_ANOTHER_market_is_irrelevant(ex):
    x, t, H, E = ex
    x.store.record_submit("other", {"market": "cpc-btc-updown-15m-OTHER",
                                    "side": "UP", "price": "0.99",
                                    "qty": "1"})
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.90", "qty": "1"}) is None


def test_the_guard_cannot_be_dodged_by_submitting_in_either_order(ex):
    """Whichever leg arrives second is the one refused, so sequencing buys
    nothing."""
    x, t, H, E = ex
    x.store.record_submit("d", {"market": SLUG, "side": "DOWN",
                                "price": "0.90", "qty": "1"})
    bad = x.worst_pair_combination_refusal(
        SLUG, {"side": "UP"}, {"price": "0.94", "qty": "1"})
    assert bad is not None
    assert D(bad["dearest_down_own"]) == D("0.90")


def test_the_guard_fails_closed_on_unreadable_inputs(ex):
    x, t, H, E = ex
    for dec, tr in (({"side": "SIDEWAYS"}, {"price": "0.5", "qty": "1"}),
                    ({"side": "UP"}, {"price": "0", "qty": "1"}),
                    ({"side": "UP"}, {"price": "0.5", "qty": "0"}),
                    ({"side": "UP"}, {"price": None, "qty": "1"})):
        assert x.worst_pair_combination_refusal(SLUG, dec, tr) is not None


def test_the_guard_fails_closed_when_live_orders_cannot_be_read(ex,
                                                               monkeypatch):
    x, t, H, E = ex

    def boom():
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(x.store, "live_orders", boom)
    bad = x.worst_pair_combination_refusal(
        SLUG, {"side": "UP"}, {"price": "0.40", "qty": "1"})
    assert bad is not None
    assert bad["why"].startswith("STATE_UNREADABLE")


def test_the_guard_is_additive_and_never_admits_what_the_ceiling_refused(ex):
    """The sequential case both gates see: the ceiling refuses it, and the
    guard must not be read as permission."""
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("2.46"), D("0.94"), basis="OWN")
    assert x.pair_cost_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.90", "qty": "2.46"}) is not None
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.90", "qty": "2.46"}) is not None


# ========== THE TWO CONVENTIONS, PINNED. Do not "simplify" to one of them.
#
# The guard uses the AVERAGE for confirmed holdings and the MAX for resting
# orders. That looks inconsistent and is not: unifying it in EITHER
# direction introduces a real defect, demonstrated by these two tests.

def test_held_inventory_uses_the_AVERAGE_because_units_are_fungible(ex):
    """Holding UP at 0.40 and 0.95 then buying DOWN 2 @ 0.30 realises cost
    1.95 against 2.00 of settlement -- genuinely +$0.05. We cannot choose
    which held unit to match, so a MAX-based test would refuse a profitable
    pair.
    """
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("1"), D("0.40"), basis="OWN")
    x.store.apply_fill(SLUG, "UP", D("1"), D("0.95"), basis="OWN")
    assert x.store.position(SLUG, "UP") == D("2")
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.30", "qty": "2"}) is None, (
        "the realised pair is 0.675 + 0.30 = 0.975 and PROFITABLE; a "
        "max-based check on held units would wrongly refuse it")


def test_resting_orders_use_the_MAX_because_a_subset_may_fill(ex):
    """Resting UP at 0.40 and 0.95 with a DOWN at 0.30: if only the 0.95
    and the DOWN fill, the pair costs 1.25 and loses $0.25. An
    average-based test would admit that reachable loss.
    """
    x, t, H, E = ex
    x.store.record_submit("r40", {"market": SLUG, "side": "UP",
                                  "price": "0.40", "qty": "1"})
    x.store.record_submit("r95", {"market": SLUG, "side": "UP",
                                  "price": "0.95", "qty": "1"})
    bad = x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.30", "qty": "1"})
    assert bad is not None, (
        "an average-based check would have admitted a reachable 1.25 pair")
    assert D(bad["dearest_up_own"]) == D("0.95")


def test_the_guard_rejects_nothing_the_live_campaign_actually_did(ex):
    """OBSERVATION FOR ITEM 8, pinned so it cannot be fitted to a result
    later. Every pair the 2026-11-02 campaign completed is below redemption,
    so the guard costs zero realised opportunity.
    """
    x, t, H, E = ex
    for i, (up_px, dn_px, pair) in enumerate((
            ("0.47", "0.52", "0.99"), ("0.51", "0.46", "0.97"),
            ("0.58", "0.37", "0.95"))):
        slug = "%s-camp%d" % (SLUG, i)
        assert D(up_px) + D(dn_px) == D(pair)
        x.store.apply_fill(slug, "UP", D("1"), D(up_px), basis="OWN")
        assert x.worst_pair_combination_refusal(
            slug, {"side": "DOWN"}, {"price": dn_px, "qty": "1"}) is None


def test_the_guard_is_own_price_based_and_that_is_maker_only_scoped(ex):
    """A SCOPE LIMIT RECORDED AS A TEST, not buried in a comment. The bound
    compares OWN prices, which equal fee-inclusive cost only while
    execution is maker-only -- the published schedule has no maker-fee term
    and the campaign booked actual_fees_usd 0 with taker_orders 0.

    A taker leg adds theta*p*(1-p) ~ $0.0174/contract, so a 0.99 pair with
    two taker legs truly costs ~1.0248: a certain loss this guard WOULD
    admit. That is not a defect today, and it becomes one the moment taker
    crossing is authorised. Whoever enables it must add the fee term here.
    """
    from src import l5_live_execution as EX
    assert EX.REDEMPTION_PAIR == D("1.00")
    x, t, H, E = ex
    x.store.apply_fill(SLUG, "UP", D("1"), D("0.47"), basis="OWN")
    # 0.99 is admitted on own prices, which is correct under maker-only
    assert x.worst_pair_combination_refusal(
        SLUG, {"side": "DOWN"}, {"price": "0.52", "qty": "1"}) is None
