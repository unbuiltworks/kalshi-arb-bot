"""ATTRIBUTION tests: each variant must differ ONLY by its declared mechanism.

Batch 1 cost us a whole experiment because three arms were secretly identical.
These tests exist so that cannot recur, and so no variant can smuggle in a size,
fee, settlement, fill-model, residual-cap or accounting change.
"""
from decimal import Decimal as D

import pytest

from src.l5_continuous import (V1, V2, V3, V4, V1ExtendOnly, V2NoBasisGate,
                               V3Continuous, V4Readjust, build)
from src.l5_two_sided import ARM_REPLICATION, TwoSided

LADDER = {"bids": {D("0.40"): D(50)}, "offers": {D("0.60"): D(50)}}


def feed(c, t=1000.0, bid="0.40", ask="0.60"):
    c.on_book({"bid": bid, "ask": ask, "bid_qty": "50", "ask_qty": "50",
               "status": "OK",
               "_ladder": {"bids": {D(bid): D(50)},
                           "offers": {D(ask): D(50)}}}, t)


def fill_resting(c, side, t):
    """Consume a resting leg the way the engine really does: a print at our
    price, after the queue ahead of us is gone. _book_fill is called BY on_trade
    only after leg.filled is advanced, which is what the replenish hook keys on.
    """
    leg = c.legs[side]
    leg.ahead = D(0)
    px = leg.px if side == "up" else (D(1) - leg.px)
    c.on_trade(px, leg.remaining(), t)
    return leg


def frozen(**kw):
    return TwoSided(arm=ARM_REPLICATION, clip=D(1), **kw)


# ---------------------------------------------- 5: no hidden differences

ARMS = [frozen, lambda: build(V1), lambda: build(V2), lambda: build(V3),
        lambda: build(V4)]


@pytest.mark.parametrize("mk", ARMS)
def test_no_variant_changes_size_cap_or_accounting(mk):
    c = mk()
    assert c.clip == D(1), "size must not differ between variants"
    assert c.residual_cap == D(2), "residual cap must not differ"
    assert c.pair_floor == D("0.01")
    assert c.cash_basis is False
    assert c.no_new_cycle_s == 120.0
    assert c.tighten_residual_s == 180.0


@pytest.mark.parametrize("mk", ARMS)
def test_every_variant_uses_the_frozen_fee_settlement_and_fill_code(mk):
    """Inherited, never overridden -- so economics cannot diverge silently."""
    c = mk()
    for meth in ("settle", "on_trade", "_bank_pairs", "matched", "basis",
                 "complement_cap", "_track_adverse", "flatten"):
        if not hasattr(TwoSided, meth):
            continue
        assert getattr(type(c), meth) is getattr(TwoSided, meth), \
            f"{type(c).__name__} overrides {meth}, which would change economics"


def test_the_frozen_benchmark_is_not_subclassed_into_the_control_path():
    """CONTROL/REPLICATION must remain the plain frozen class."""
    assert type(frozen()) is TwoSided


# ---------------------------------------------- 1: V1 vs REPLICATION

def test_v1_permits_a_heavy_side_acquisition_that_replication_forbids():
    rep, v1 = frozen(), build(V1)
    for c in (rep, v1):
        c.window_end = 10_000.0
        c.now = 1000.0
        c._book_fill("up", D(1), D("0.50"), maker=True, evidence="t")
    assert rep.deficient() == "down" and v1.deficient() == "down"
    assert rep.desired("up") is None, "frozen must block the heavy side"
    assert rep.blocked.get("heavy_side_extend_disabled", 0) >= 1
    feed(rep, t=1001.0, bid="0.40", ask="0.60")
    feed(v1, t=1001.0, bid="0.40", ask="0.60")
    assert v1.desired("up") is not None, "V1 must permit heavy-side acquisition"
    assert v1.blocked.get("heavy_side_extend_disabled", 0) == 0


def test_v1_still_honours_the_residual_cap():
    """The cap is NOT relaxed by V1 -- this is the predicted binding constraint."""
    v1 = build(V1)
    v1.window_end = 10_000.0
    v1.now = 1000.0
    v1._book_fill("up", D(2), D("0.50"), maker=True, evidence="t")
    feed(v1, t=1001.0, bid="0.40", ask="0.60")
    assert v1.desired("up") is None
    assert v1.blocked.get("extend_breaches_residual_cap", 0) >= 1


# ---------------------------------------------- 2: V2 vs V1

def test_v2_accepts_a_basis_worsening_extend_that_v1_rejects():
    """One shared state: own basis 0.40, touch 0.50 -> worsens the basis."""
    # the reactive take is disabled EQUALLY in both arms, otherwise the engine
    # completes the pair and the heavy-side state under test never exists.
    v1, v2 = (build(V1, allow_reactive_take=False),
              build(V2, allow_reactive_take=False))
    for c in (v1, v2):
        c.window_end = 10_000.0
        c.now = 1000.0
        c._book_fill("up", D(1), D("0.40"), maker=True, evidence="t")
        feed(c, t=1001.0, bid="0.50", ask="0.70")
    assert v1.desired("up") is None
    assert v1.blocked.get("extend_would_not_improve_basis", 0) >= 1
    assert v2.desired("up") == D("0.50")
    assert v2.blocked.get("extend_would_not_improve_basis", 0) == 0


def test_v2_matches_v1_when_the_extend_does_improve_basis():
    """V2 must differ ONLY on basis-worsening states."""
    v1, v2 = (build(V1, allow_reactive_take=False),
              build(V2, allow_reactive_take=False))
    for c in (v1, v2):
        c.window_end = 10_000.0
        c.now = 1000.0
        c._book_fill("up", D(1), D("0.50"), maker=True, evidence="t")
        feed(c, t=1001.0, bid="0.30", ask="0.70")
    assert v1.desired("up") == v2.desired("up") == D("0.30")


# ---------------------------------------------- 3: V3 keeps both processes

def test_v3_replenishes_the_filled_side_and_preserves_the_other_quote():
    v3 = build(V3)
    v3.window_end = 10_000.0
    feed(v3, t=1000.0)
    up_leg, dn_leg = v3.legs["up"], v3.legs["down"]
    assert up_leg is not None and dn_leg is not None
    # fully consume the UP leg the way the engine does
    fill_resting(v3, "up", 1001.0)
    assert v3.replenishes >= 1, "the filled side must be replenished"
    assert v3.legs["up"] is not None
    assert v3.legs["down"] is dn_leg, \
        "the untouched opposite quote must keep its identity AND queue position"


def test_v2_does_not_replenish_on_fill_but_v3_does():
    v2, v3 = build(V2), build(V3)
    for c in (v2, v3):
        c.window_end = 10_000.0
        feed(c, t=1000.0)
        fill_resting(c, "up", 1001.0)
    assert getattr(v2, "replenishes", 0) == 0
    assert v3.replenishes >= 1


def test_v3_replenishment_cannot_breach_a_gate():
    """Replenishment is not a licence to ignore the residual cap."""
    v3 = build(V3)
    v3.window_end = 10_000.0
    feed(v3, t=1000.0)
    # pre-load inventory so the residual cap already binds, then fill the leg
    v3._book_fill("up", D(3), D("0.50"), maker=True, evidence="t")
    fill_resting(v3, "up", 1001.0)
    assert v3.blocked.get("replenish_blocked_by_gate", 0) >= 1


# ---------------------------------------------- 4: V4 vs V3

def test_v4_rechooses_the_replenishment_price_where_v3_keeps_the_level():
    v3, v4 = build(V3), build(V4)
    for c in (v3, v4):
        c.window_end = 10_000.0
        feed(c, t=1000.0, bid="0.40", ask="0.60")
    filled_px = v3.legs["up"].px
    assert v4.legs["up"].px == filled_px
    # the book MOVES, then the resting leg fills at its old price
    for c in (v3, v4):
        c.on_book({"bid": "0.30", "ask": "0.60", "bid_qty": "50",
                   "ask_qty": "50", "status": "OK",
                   "_ladder": {"bids": {D("0.30"): D(50)},
                               "offers": {D("0.60"): D(50)}}}, 1001.0)
        fill_resting(c, "up", 1002.0)
    assert v3.legs["up"] is not None and v4.legs["up"] is not None
    assert v3.legs["up"].px == filled_px, "V3 replenishes at the OLD level"
    assert v4.legs["up"].px == D("0.30"), "V4 RE-CHOOSES from the current book"
    assert v3.legs["up"].px != v4.legs["up"].px


def test_neither_v3_nor_v4_cancels_a_valid_unfilled_quote_to_chase():
    """The HOLD_OPEN_EXTEND finding is preserved in both variants."""
    for name in (V3, V4):
        c = build(name)
        c.window_end = 10_000.0
        feed(c, t=1000.0, bid="0.40", ask="0.60")
        leg = c.legs["up"]
        before = c.cancels
        # a BETTER touch appears; neither variant may move the resting quote
        c.on_book({"bid": "0.45", "ask": "0.60", "bid_qty": "50",
                   "ask_qty": "50", "status": "OK",
                   "_ladder": {"bids": {D("0.45"): D(50)},
                               "offers": {D("0.60"): D(50)}}}, 1001.0)
        assert c.legs["up"] is leg, f"{name} chased a better touch"
        assert c.cancels == before


def test_v3_and_v4_differ_only_through_replenishment_price_selection():
    assert V4Readjust._replenish_price is not V3Continuous._replenish_price
    for meth in ("desired", "_book_fill", "report", "reconcile_quotes",
                 "_still_valid"):
        assert getattr(V4Readjust, meth) is getattr(V3Continuous, meth), \
            f"V4 also overrides {meth}, so the contrast is not isolated"


def test_the_subclass_chain_is_exactly_as_declared():
    assert V2NoBasisGate.__bases__ == (V1ExtendOnly,)
    assert V3Continuous.__bases__ == (V2NoBasisGate,)
    assert V4Readjust.__bases__ == (V3Continuous,)
    assert V1ExtendOnly.__bases__ == (TwoSided,)


def test_v4_is_behaviourally_equivalent_to_v2_and_that_is_REPORTED():
    """The frozen reconcile_quotes already replenishes at a re-chosen price, so
    "readjust" is not a new mechanism. V4 exists to make that explicit, and this
    test pins the equivalence so it can never be mistaken for a real contrast."""
    v2, v4 = build(V2), build(V4)
    for c in (v2, v4):
        c.window_end = 10_000.0
        feed(c, t=1000.0, bid="0.40", ask="0.60")
        c.on_book({"bid": "0.30", "ask": "0.60", "bid_qty": "50",
                   "ask_qty": "50", "status": "OK",
                   "_ladder": {"bids": {D("0.30"): D(50)},
                               "offers": {D("0.60"): D(50)}}}, 1001.0)
        fill_resting(c, "up", 1002.0)
    assert v2.legs["up"].px == v4.legs["up"].px, \
        "V4 must re-choose exactly as the frozen engine already does"


def test_v3_is_the_only_variant_that_persists_a_level():
    v2, v3, v4 = build(V2), build(V3), build(V4)
    for c in (v2, v3, v4):
        c.window_end = 10_000.0
        feed(c, t=1000.0, bid="0.40", ask="0.60")
        c.on_book({"bid": "0.30", "ask": "0.60", "bid_qty": "50",
                   "ask_qty": "50", "status": "OK",
                   "_ladder": {"bids": {D("0.30"): D(50)},
                               "offers": {D("0.60"): D(50)}}}, 1001.0)
        fill_resting(c, "up", 1002.0)
    assert v3.legs["up"].px == D("0.40"), "V3 persists the filled level"
    assert v2.legs["up"].px == D("0.30") and v4.legs["up"].px == D("0.30")


# ------------------------------------- residual-cap observer is decision-neutral

@pytest.mark.parametrize("name", [V1, V2, V3, V4])
def test_cap_observer_does_not_change_the_cap_or_any_decision(name):
    c = build(name)
    assert c.residual_cap == D(2), "the observer must not alter the cap"
    before = {k: v for k, v in c.__dict__.items()
              if k not in ("cap", "_cap_bound", "_cap_last_t")}
    c._cap_probe()
    after = {k: v for k, v in c.__dict__.items()
             if k not in ("cap", "_cap_bound", "_cap_last_t")}
    assert before == after


def test_cap_observer_records_a_bind_and_its_residual():
    c = build(V1)
    c.window_end = 10_000.0
    c.now = 1000.0
    c._book_fill("up", D(3), D("0.50"), maker=True, evidence="t")
    feed(c, t=1001.0, bid="0.40", ask="0.60")
    obs = c.report(winner="up")["residual_cap_observer"]
    assert obs["blocked_by_cap"] >= 1
    assert obs["blocked_up"] >= 1 and obs["blocked_down"] == 0
    assert obs["entries_into_cap"] >= 1
    assert obs["residual_at_first_bind"] is not None


def test_blocked_fraction_has_a_real_denominator():
    """The first version incremented attempts only in the blocked branch, so
    the fraction was vacuously 1.0. An attempt is every heavy-side opportunity."""
    c = build(V1)
    c.window_end = 10_000.0
    c.now = 1000.0
    # residual 1: a heavy side exists and the cap still PERMITS the extend
    c._book_fill("up", D(1), D("0.50"), maker=True, evidence="t")
    feed(c, t=1001.0, bid="0.40", ask="0.60")
    obs = c.report(winner="up")["residual_cap_observer"]
    assert obs["acquisitions_attempted"] > 0
    assert obs["blocked_by_cap"] == 0, "the cap permits an extend at residual 1"
    assert obs["blocked_fraction"] == 0.0


def test_v4_no_longer_double_counts_the_cap_diagnostic():
    """V4 used to call desired() inside _replenish_price AND again in
    reconcile_quotes, doubling a diagnostic counter and breaking a strict
    equivalence check for no behavioural reason."""
    v2, v4 = build(V2), build(V4)
    for c in (v2, v4):
        c.window_end = 10_000.0
        c.now = 1000.0
        c._book_fill("up", D(3), D("0.50"), maker=True, evidence="t")
        feed(c, t=1001.0, bid="0.40", ask="0.60")
        fill_resting(c, "down", 1002.0) if c.legs.get("down") else None
    assert (v4.blocked.get("extend_breaches_residual_cap", 0) ==
            v2.blocked.get("extend_breaches_residual_cap", 0))
