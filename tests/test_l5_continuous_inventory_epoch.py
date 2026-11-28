"""The duplicate-suppression defect in the continuous-inventory key, closed.

THE DEFECT. `action_key` was (market, side, price) alone. That collapses two
intents that must not be the same:

  * a re-fire at an UNCHANGED book, which must be suppressed -- measured:
    four identical ticks produced sizes 3, 1, 2 and would have sent three
    orders had quantity been in the key; and
  * the first leg of a SECOND pair at the same price, after the first pair
    completed, which must be ALLOWED -- that is the whole point of
    continuous inventory.

Because the key could not tell them apart it refused the second, so
`L5_TS_CONTINUOUS_INVENTORY` regressed pair-after-pair and the owner
correctly withheld it from promotion.

THE FIX. The key may now carry an `epoch` from `inventory_epoch(up_q, dn_q)`:
a fingerprint of REALIZED inventory. It moves when, and only when, a fill
lands. Still content-addressed -- no clock, no counter, no round -- and
`epoch=None` is byte-for-byte the old key, so nothing that has not opted in
changes.

BOTH FLAGS REMAIN OFF. This closes a precondition for promotion; it does not
promote anything, and the residual-opening behaviour (flag B) stays bounded by
an ADD budget that defaults to zero.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_continuous_inventory as CI

MKT = "cpc-btc-updown-15m-2026-11-02-2215z"


# ------------------------------------------------- the old behaviour is kept

def test_without_an_epoch_the_key_is_unchanged():
    k = CI.action_key(MKT, "UP", D("0.47"))
    assert k == CI.action_key(MKT, "UP", D("0.47"), epoch=None)
    assert MKT in k and "UP" in k and "0.47" in k


def test_quantity_still_does_not_mint_an_identity():
    """The measured reason qty was excluded: the executor shrinks orders."""
    a = CI.action_key(MKT, "UP", D("0.47"), D(3))
    b = CI.action_key(MKT, "UP", D("0.47"), D(1))
    c = CI.action_key(MKT, "UP", D("0.47"), D(2))
    assert a == b == c


def test_a_price_move_still_mints_a_new_identity():
    assert CI.action_key(MKT, "UP", D("0.47")) != CI.action_key(
        MKT, "UP", D("0.46"))


def test_the_side_still_matters():
    assert CI.action_key(MKT, "UP", D("0.47")) != CI.action_key(
        MKT, "DOWN", D("0.47"))


# ------------------------------------------------- suppression still fires

def test_a_retick_against_unchanged_inventory_is_still_ONE_identity():
    """Six identical triggers, inventory unchanged, trimmed sizes: one key."""
    ep = CI.inventory_epoch(D(0), D(0))
    keys = {CI.action_key(MKT, "DOWN", D("0.39"), q, epoch=ep)
            for q in (D(3), D(1), D(2), D("1.92"), D(3), D(1))}
    assert len(keys) == 1


def test_an_unfilled_requote_at_the_same_price_is_still_suppressed():
    """Nothing filled, so the epoch has not moved: still one intent."""
    ep = CI.inventory_epoch(D("0"), D("0"))
    first = CI.action_key(MKT, "UP", D("0.47"), epoch=ep)
    again = CI.action_key(MKT, "UP", D("0.47"), epoch=ep)
    assert first == again


# ------------------------------------------------- the second pair is allowed

def test_a_second_pair_at_the_SAME_price_is_a_NEW_identity():
    """THE DEFECT, DIRECTLY. Pair one completes (2 up, 2 down matched), and
    the next first leg wants the same side at the same price. Under the old
    key that was DUPLICATE_SUPPRESSED; now it is a distinct intent."""
    before = CI.action_key(MKT, "UP", D("0.47"),
                           epoch=CI.inventory_epoch(D(0), D(0)))
    after = CI.action_key(MKT, "UP", D("0.47"),
                          epoch=CI.inventory_epoch(D(2), D(2)))
    assert before != after


def test_each_completed_pair_advances_the_identity_again():
    seen = set()
    for n in (0, 1, 2, 3, 4):
        seen.add(CI.action_key(MKT, "UP", D("0.47"),
                               epoch=CI.inventory_epoch(D(n), D(n))))
    assert len(seen) == 5


def test_a_partial_fill_also_advances_it():
    """A fill is a fill: 1.92 of an intended 2 still changes realized state."""
    a = CI.action_key(MKT, "DOWN", D("0.39"),
                      epoch=CI.inventory_epoch(D(0), D(0)))
    b = CI.action_key(MKT, "DOWN", D("0.39"),
                      epoch=CI.inventory_epoch(D(0), D("1.92")))
    assert a != b


def test_the_epoch_distinguishes_matched_from_imbalanced_at_equal_totals():
    """(2,2) and (3,1) both hold four contracts; they are different states."""
    assert CI.inventory_epoch(D(2), D(2)) != CI.inventory_epoch(D(3), D(1))


# ------------------------------------------------- the epoch's own properties

def test_the_epoch_is_a_function_of_inventory_only():
    """No clock, no counter: the same inventory always gives the same epoch."""
    assert CI.inventory_epoch(D(2), D(2)) == CI.inventory_epoch(D(2), D(2))
    assert CI.inventory_epoch(D("2.00"), D(2)) == CI.inventory_epoch(D(2), D(2))


def test_the_epoch_cannot_be_advanced_without_a_fill():
    """Called a hundred times on unchanged inventory, it never moves -- so a
    retry loop cannot mint identities by calling it."""
    ep = CI.inventory_epoch(D("1.92"), D(0))
    assert all(CI.inventory_epoch(D("1.92"), D(0)) == ep for _ in range(100))


def test_the_epoch_survives_a_restart_because_it_is_recomputed():
    """It is derived from the durable ledger's quantities, not remembered, so
    a restart that reads the same book reproduces the same key."""
    before = CI.action_key(MKT, "UP", D("0.55"),
                           epoch=CI.inventory_epoch(D("1.92"), D(0)))
    after_restart = CI.action_key(MKT, "UP", D("0.55"),
                                  epoch=CI.inventory_epoch(D("1.92"), D(0)))
    assert before == after_restart


# ------------------------------------------------- residual opening stays off

def test_the_ADD_budget_still_defaults_to_zero():
    """Flag B -- additional opening exposure while unmatched inventory
    remains -- is what replay showed INCREASES losses. `decide` takes the
    cap explicitly and defaults it to zero, so no ADD is permitted unless a
    caller states a bound it can know before the order."""
    import inspect
    sig = inspect.signature(CI.decide)
    assert sig.parameters["imbalance_cap"].default == D(0)


def test_both_flags_are_still_off_by_default(monkeypatch):
    from src import l5_live_execution as EX
    monkeypatch.delenv(EX.CONTINUOUS_INVENTORY_ENV, raising=False)
    monkeypatch.delenv(EX.ROUND_RETIRE_RESIDUAL_ENV, raising=False)
    assert EX.continuous_inventory_enabled() is False
    assert EX.round_retire_with_residual() is False
