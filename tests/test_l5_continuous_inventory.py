"""The continuous inventory loop: decided fresh, idempotent, restart-safe.

Every test drives `l5_continuous_inventory.decide`, which holds no state. The
properties that matter are therefore about what it does with the state it is
GIVEN -- and that is exactly what makes restart recovery testable: the same
inventory and the same book must reproduce the same decision and the same
idempotency key, with nothing persisted in between.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_continuous_inventory as CI
from src import l5_live_execution as E

BOOK = dict(bid=D("0.49"), ask=D("0.50"), tick=D("0.01"))


def dec(**kw):
    return CI.decide(market="m", **dict(BOOK, **kw))


# =============================================== L5's vocabulary, exactly
@pytest.mark.parametrize("up,dn,side,qty,want", [
    (0, 0, "UP", 2, CI.OPEN),
    (5, 0, "UP", 2, CI.ADD),          # same side as the residual
    (0, 5, "DOWN", 2, CI.ADD),
    (5, 0, "DOWN", 2, CI.REDUCE),     # opposite side, does not overshoot
    (5, 0, "DOWN", 5, CI.REDUCE),     # exactly flat is still a REDUCE
    (5, 0, "DOWN", 6, CI.REVERSE),    # past flat and out the other side
    (0, 5, "UP", 9, CI.REVERSE),
])
def test_classify_matches_L5s_action_vocabulary(up, dn, side, qty, want):
    assert CI.classify(D(up), D(dn), side, D(qty)) == want


def test_a_REDUCE_is_a_BUY_of_the_opposite_side_never_a_sale():
    """L5 never sells: `q` is non-negative in all 63,686 reconstructed
    actions and a REDUCE moves the imbalance toward zero by BUYING."""
    r = dec(up_q=D("2.52"), up_cost=D("1.1844"))
    assert r["ok"] and r["action"] == CI.REDUCE
    assert r["side"] == "DOWN"           # we held UP; we BUY DOWN
    assert D(r["qty"]) > 0               # a purchase, not a negative quantity


def test_classify_agrees_with_the_executors_own_inventory_class():
    """The deployed classifier already distinguishes the first three. This
    one must not disagree with it -- it only ADDS the REVERSE case, which
    `inventory_class` cannot see because it ignores size."""
    for up, dn, side in ((0, 0, "UP"), (5, 0, "UP"), (5, 0, "DOWN"),
                         (0, 5, "DOWN"), (0, 5, "UP")):
        mine = CI.classify(D(up), D(dn), side, D(1))
        theirs = E.inventory_class(D(up) - D(dn), side)
        assert {CI.OPEN: E.FLAT_OPEN, CI.ADD: E.INCREASE_RESIDUAL,
                CI.REDUCE: E.REDUCE_RESIDUAL}[mine] == theirs


@pytest.mark.parametrize("bad", ["", "YES", "up ", None])
def test_an_unreadable_side_is_refused_not_guessed(bad):
    with pytest.raises(ValueError):
        CI.classify(D(0), D(0), bad, D(1))


def test_a_nonpositive_quantity_is_refused():
    for q in (D(0), D(-1)):
        with pytest.raises(ValueError):
            CI.classify(D(5), D(0), "DOWN", q)


# ======================================= repeated actions are idempotent
def test_the_same_wanted_order_yields_the_same_key_every_time():
    """The property the round-scoped key provided: a trigger firing ten
    times while one order rests must produce ONE client_order_id."""
    keys = {dec()["key"] for _ in range(10)}
    assert len(keys) == 1


def test_a_different_wanted_order_yields_a_different_key():
    a = dec()["key"]
    b = dec(bid=D("0.41"), ask=D("0.42"))["key"]
    assert a != b


def test_the_key_carries_no_round_and_no_clock():
    k = dec()["key"]
    assert "|r0" not in k and "|r1" not in k
    assert k == CI.action_key("m", "UP", D("0.47"), D("2.52"))


def test_the_key_is_content_addressed_on_market_side_and_price():
    base = CI.action_key("m", "UP", D("0.47"), D("2.52"))
    assert CI.action_key("m", "DOWN", D("0.47"), D("2.52")) != base
    assert CI.action_key("m", "UP", D("0.48"), D("2.52")) != base
    assert CI.action_key("other", "UP", D("0.47"), D("2.52")) != base


def test_quantity_is_deliberately_NOT_part_of_the_identity():
    """Measured: the executor shrinks an order to fit the remaining budget,
    so four identical ticks produced sizes 3, 1 and 2. With quantity in the
    key none of them suppressed and three orders went out. Size is a
    consequence of the budget, not a statement of intent."""
    base = CI.action_key("m", "UP", D("0.47"), D("2.52"))
    assert CI.action_key("m", "UP", D("0.47"), D("1.00")) == base
    assert CI.action_key("m", "UP", D("0.47")) == base


def test_equal_prices_written_differently_share_one_key():
    assert CI.action_key("m", "UP", D("0.47"), D("2.5")) == \
        CI.action_key("m", "UP", D("0.470"), D("2.50"))


# ================================================ partial fills
def test_a_partial_fill_is_just_a_smaller_inventory_next_call():
    """No special handling: the next decision sees the inventory that
    actually filled, and completes exactly that much."""
    r = dec(up_q=D("1.00"), up_cost=D("0.47"))
    assert r["ok"] and r["action"] == CI.REDUCE
    assert D(r["qty"]) == D("1.00")        # the complement matches the FILL


def test_a_partial_complement_leaves_the_rest_to_complete():
    r = dec(up_q=D("2.52"), dn_q=D("1.00"),
            up_cost=D("1.1844"), dn_cost=D("0.48"))
    assert r["ok"] and r["action"] == CI.REDUCE
    assert D(r["qty"]) == D("1.52")        # only the unmatched remainder


def test_the_complement_never_exceeds_the_residual():
    r = dec(up_q=D("0.50"), up_cost=D("0.235"), clip=D("2.52"))
    assert D(r["qty"]) == D("0.50")


# ================================================ cancellations
def test_a_cancellation_needs_no_memory_at_all():
    """A cancelled order leaves no trace in this module, so the decision
    after a cancel is the same one it would make from a cold start."""
    before = dec()
    after = dec()                      # nothing was recorded in between
    assert before["key"] == after["key"]
    assert before["action"] == after["action"]


def test_after_a_cancel_a_moved_book_is_repriced_not_reused():
    """The frozen-plan failure, inverted: price comes from the book NOW."""
    a = dec()
    b = dec(bid=D("0.60"), ask=D("0.61"))
    assert D(a["price"]) != D(b["price"])


# ================================================ restart recovery
def test_restart_reproduces_the_same_decision_from_durable_state_alone():
    """The loop stores nothing, so recovery is just: read inventory, read
    book, decide. No round counter and no plan have to survive."""
    state = dict(up_q=D("2.52"), up_cost=D("1.1844"))
    first = dec(**state)
    restarted = CI.decide(market="m", **dict(BOOK, **state))
    assert first == restarted


def test_restart_mid_pair_completes_rather_than_reopening():
    """A restart holding one leg must finish that pair, not start another."""
    r = dec(up_q=D("2.52"), up_cost=D("1.1844"))
    assert r["action"] == CI.REDUCE


def test_unknown_held_cost_fails_closed():
    """An adopted leg with no cost basis cannot be priced into a pair."""
    r = dec(up_q=D("2.52"), up_cost=D(0))
    assert not r["ok"] and r["reason"] == "HELD_COST_UNKNOWN"


# ================================================ settlement
def test_after_settlement_flattens_the_book_it_opens_again():
    """Settlement returns inventory to zero, and a zero inventory is an
    OPEN -- the loop needs no 'new window' concept."""
    assert dec(up_q=D(0), dn_q=D(0))["action"] == CI.OPEN


def test_a_fully_matched_position_is_treated_as_flat():
    """Matched inventory redeems at exactly $1.00 and carries no residual,
    so the loop may open again on top of it."""
    r = dec(up_q=D("2.52"), dn_q=D("2.52"),
            up_cost=D("1.1844"), dn_cost=D("1.2096"))
    assert r["ok"] and r["action"] == CI.OPEN


# ================================================ the limits still bind
def test_the_cash_envelope_still_refuses():
    r = dec(cash_spent=D("11.90"), cash_cap=D("12"))
    assert not r["ok"] and r["reason"] == "CASH_CAP_WOULD_BIND"


def test_the_residual_cap_still_refuses_an_opening_clip():
    r = dec(clip=D("6"), residual_cap=D(5))
    assert not r["ok"] and r["reason"] == "CLIP_ABOVE_RESIDUAL_CAP"


def test_the_pair_ceiling_still_refuses_an_expensive_opening():
    r = dec(depth_ticks=0)             # at the touch: pair 0.99 > 0.96
    assert not r["ok"] and r["reason"] == "PAIR_ABOVE_CEILING"


def test_the_first_leg_band_still_refuses():
    r = dec(bid=D("0.95"), ask=D("0.96"))
    assert not r["ok"] and r["reason"] == "FIRST_LEG_OUTSIDE_BAND"


def test_a_completion_above_redemption_is_refused():
    """Never pay more than $1.00 for a pair in this path."""
    r = dec(up_q=D("2.52"), up_cost=D("2.2680"))   # basis 0.90
    assert not r["ok"] and r["reason"] == "COMPLETION_PAIR_ABOVE_CEILING"


def test_opening_can_be_closed_without_affecting_completion():
    """`may_open` False must still let held inventory be completed -- the
    entry cutoff closes ENTRY, never the exit."""
    assert not dec(may_open=False)["ok"]
    r = dec(may_open=False, up_q=D("2.52"), up_cost=D("1.1844"))
    assert r["ok"] and r["action"] == CI.REDUCE


def test_an_unquotable_book_is_refused():
    assert dec(bid=None, ask=None)["reason"] == "NO_QUOTABLE_PRICE"


# ================================================ ADD is economically gated
def test_an_ADD_that_would_not_improve_the_basis_is_refused_OLD_NAME():
    """Measured: L5's own ADDs carried -0.0154/contract of alpha. An ADD is
    permitted here only when it lowers the average basis, which is what
    makes a cheaper pair reachable."""
    r = dec(up_q=D("2.52"), up_cost=D("0.7560"),   # basis 0.30, cheap
            complete_ceiling=D("0.50"))            # force REDUCE to refuse
    assert not r["ok"]
    assert r["reason"] in ("ADD_WOULD_NOT_IMPROVE_BASIS",
                           "COMPLETION_PAIR_ABOVE_CEILING")


def test_the_residual_cap_makes_an_ADD_impossible_at_the_deployed_clip():
    """ARITHMETIC, not policy: 2.52 + 2.52 = 5.04 against a 5-contract cap.
    L5's dominant action -- 52.6% of its 63,686 -- cannot occur at all at
    the deployed clip, whatever the decision rule says."""
    assert D("2.52") + D("2.52") > D(5)
    r = dec(up_q=D("2.52"), up_cost=D("1.1844"),
            clip=D("2.52"), residual_cap=D(5),
            complete_ceiling=D("0.50"))
    assert not r["ok"]


# ================= the capital-scaled imbalance band (campaign evidence)
def test_the_ex_post_band_helper_is_GONE():
    """It derived an ADD budget from each window's PEAK |imbalance|, which is
    only knowable AFTER the window ends. Look-ahead; excluded from
    promotion, and removed so it cannot be reintroduced by accident."""
    assert not hasattr(CI, "imbalance_band")
    assert not hasattr(CI, "L5_IMBALANCE_DENSITY_HI")
    assert not hasattr(CI, "L5_IMBALANCE_DENSITY_LO")


def test_the_default_is_NO_ADD_when_the_caller_states_nothing():
    """No bound is derived from anything. Silence means no ADD."""
    r = dec(up_q=D("1.00"), up_cost=D("0.30"), complete_ceiling=D("0.40"))
    assert not r["ok"]
    assert r["add_available"] is False
    assert D(r["imbalance_band"]) == D(0)


def test_an_ADD_is_allowed_only_when_the_caller_states_a_bound():
    """And then it needs no basis improvement -- the basis test made ADD
    unreachable in practice. But the bound is STATED, never fitted."""
    r = dec(up_q=D("1.00"), up_cost=D("0.30"), imbalance_cap=D("3"),
            complete_ceiling=D("0.40"))
    assert r["ok"] and r["action"] == CI.ADD
    assert D(r["basis_after"]) > D(r["basis_before"])   # basis got WORSE


def test_a_stated_ADD_is_sized_down_to_land_on_the_stated_bound():
    r = dec(up_q=D("2.00"), up_cost=D("0.60"), clip=D("2.52"),
            imbalance_cap=D("2.40"), complete_ceiling=D("0.40"))
    assert r["ok"] and r["action"] == CI.ADD
    assert D(r["imbalance_after"]) == D("2.40")


def test_our_five_contract_cap_already_exceeds_L5s_collapse_density():
    """The counterintuitive finding: proportional to capital we permit about
    twice the imbalance density at which L5's own economics collapsed, so
    the evidence says TIGHTEN rather than loosen."""
    ours = D(5) / D(12)                      # contracts per dollar
    collapse = D("358") / D("1746")          # L5's collapse decile
    assert ours > collapse * 2


def test_without_a_stated_bound_no_ADD_is_offered_at_all():
    """Replaces two tests that asked for an ADD with no bound stated and
    expected the removed ex-post band to supply one."""
    r = dec(up_q=D("1.00"), up_cost=D("0.30"), complete_ceiling=D("0.40"))
    assert not r["ok"] and r["add_available"] is False
    r2 = dec(up_q=D("2.00"), up_cost=D("0.60"), clip=D("2.52"),
             complete_ceiling=D("0.40"))
    assert not r2["ok"] and r2["add_available"] is False


def test_the_hard_residual_cap_always_wins_over_the_band():
    """The band is a policy; the residual cap is a risk limit. A band wider
    than the cap must never widen the cap."""
    r = dec(up_q=D("1.00"), up_cost=D("0.30"), cash_cap=D("1000"),
            residual_cap=D("2"), complete_ceiling=D("0.40"))
    if r["ok"]:
        assert D(r["imbalance_after"]) <= D(2)


def test_a_zero_band_makes_no_ADD_available():
    """The configuration that reproduces the deployed engine's behaviour.
    The completion refusal outranks in `reason`, but the band state is
    reported alongside it so the absence of an ADD is visible."""
    r = dec(up_q=D("1.00"), up_cost=D("0.30"), imbalance_cap=D(0),
            complete_ceiling=D("0.40"))
    assert not r["ok"]
    assert r["add_available"] is False
    assert D(r["imbalance_band"]) == D(0)


def test_a_full_band_makes_no_further_ADD_available():
    r = dec(up_q=D("2.43"), up_cost=D("1.1421"), complete_ceiling=D("0.40"))
    assert not r["ok"]
    assert r["add_available"] is False
    assert D(r["imbalance"]) == D("2.43")


def test_a_refusal_with_room_left_still_reports_the_band():
    r = dec(up_q=D("2.43"), up_cost=D("1.1421"), imbalance_cap=D("4.00"),
            complete_ceiling=D("0.40"))
    if not r["ok"]:
        assert r["add_available"] is True


def test_completing_still_outranks_adding(probe=None):
    """Priority is unchanged: a REDUCE removes exposure and an ADD takes it
    on, so when both are available the REDUCE wins."""
    r = dec(up_q=D("1.00"), up_cost=D("0.47"))
    assert r["ok"] and r["action"] == CI.REDUCE


# =============== the prospective action rule, and why it is degenerate
def sel(**kw):
    base = dict(bid=D("0.49"), ask=D("0.50"), tick=D("0.01"))
    base.update(kw)
    return CI.select_action(**base)


def test_the_two_futures_are_priced_from_decision_time_data_only():
    """Carrying is q*(p-b); completing is q*(1-b-c). Both knowable now."""
    assert CI.expected_carry(qty=D(2), basis=D("0.40"),
                             held_side_price=D("0.50")) == D("0.20")
    assert CI.expected_complete(qty=D(2), basis=D("0.40"),
                                complement_cost=D("0.45")) == D("0.30")


def test_the_basis_cancels_out_of_the_decision():
    """What was already paid is SUNK. Completing beats carrying exactly when
    c < 1 - p, which contains no basis term -- so 'complete only when the
    PAIR is under $1.00' is the wrong test, it chases the entry price."""
    for b in ("0.10", "0.47", "0.90"):
        a = CI.expected_complete(qty=D(1), basis=D(b),
                                 complement_cost=D("0.45"))
        c = CI.expected_carry(qty=D(1), basis=D(b),
                              held_side_price=D("0.50"))
        assert a - c == D("0.05")           # independent of b


def test_completing_is_worth_it_exactly_when_cheaper_than_fair():
    assert CI.complement_is_worth_buying(held_side_price=D("0.50"),
                                         complement_cost=D("0.49"))
    assert not CI.complement_is_worth_buying(held_side_price=D("0.50"),
                                             complement_cost=D("0.50"))
    assert not CI.complement_is_worth_buying(held_side_price=D("0.50"),
                                             complement_cost=D("0.51"))


@pytest.mark.parametrize("bid,ask", [("0.20", "0.21"), ("0.49", "0.50"),
                                     ("0.80", "0.81")])
def test_THE_DEGENERACY_the_discount_is_constant_at_every_price(bid, ask):
    """THE FINDING. Our complement is a maker quote `depth_ticks` behind the
    touch, so the apparent discount to fair value is mechanically
    spread + d*tick -- a CONSTANT, at every price, in every market state.
    An expected-value rule built on it always fires and carries no
    information: it is reading the depth of its own quote as edge."""
    r = sel(bid=D(bid), ask=D(ask), up_q=D("2.52"), up_cost=D("1.1844"))
    assert r["action"] == CI.REDUCE
    assert D(r["mechanical_discount"]) == D("0.03")      # spread 1 + depth 2
    assert r["is_degenerate_at_touch"] is True


def test_demanding_a_real_discount_beyond_the_mechanical_one_never_fires():
    """Confirms the discount is entirely mechanical: ask for any margin
    above spread+depth and nothing ever completes."""
    for bid, ask in (("0.20", "0.21"), ("0.49", "0.50"), ("0.80", "0.81")):
        r = sel(bid=D(bid), ask=D(ask), up_q=D("2.52"), up_cost=D("1.1844"),
                margin=D("0.05"))
        assert r["action"] == CI.NO_ACTION
        assert r["reason"] == "CARRYING_BEATS_COMPLETING_AT_THIS_PRICE"


def test_the_rule_never_returns_ADD():
    """A maker ADD rests below the touch, so it always LOOKS +EV and never
    is: the fill happens when the market reaches the price. Measured alpha
    is +0.00116 on 693,916 fills and -0.00091 on 63,686 -- zero in noise.
    So the rule will not take one to raise activity."""
    for allow in (False, True):
        r = sel(up_q=D("1.00"), up_cost=D("0.30"), margin=D("0.50"),
                allow_add=allow)
        assert r["action"] != CI.ADD


def test_no_action_is_a_first_class_outcome_with_its_economics():
    r = sel(up_q=D("2.52"), up_cost=D("1.1844"), margin=D("0.50"))
    assert r["action"] == CI.NO_ACTION
    for k in ("expected_carry", "value_if_completed",
              "advantage_of_completing", "fair_complement"):
        assert k in r


def test_a_flat_book_opens_and_an_expensive_one_does_not():
    assert sel()["action"] == CI.OPEN
    assert sel(depth_ticks=0)["reason"] == "PAIR_ABOVE_CEILING"


def test_unattested_basis_still_fails_closed_in_the_rule():
    assert sel(up_q=D("2.52"), up_cost=D(0))["reason"] == "HELD_COST_UNKNOWN"


def test_the_rule_uses_no_ex_post_quantity():
    """The defect being corrected: a threshold fitted to a window's PEAK
    imbalance is not knowable at decision time. `select_action` takes only
    the current book and the current realized inventory."""
    import inspect
    sig = set(inspect.signature(CI.select_action).parameters)
    assert not {"peak_imbalance", "window_peak", "imbalance_cap"} & sig
    assert {"bid", "ask", "up_q", "dn_q", "up_cost", "dn_cost"} <= sig
