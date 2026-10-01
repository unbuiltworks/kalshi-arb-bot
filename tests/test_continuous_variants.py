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
    # only correctly directed flow can reach a resting queue: our "up" leg sits
    # on the YES bid (SELL reaches it), our "down" leg on the YES ask (BUY does)
    c.on_trade(px, leg.remaining(), t,
               "ORDER_SIDE_SELL" if side == "up" else "ORDER_SIDE_BUY")
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

def ladder(c, t, bids, ask="0.60"):
    """Feed an explicit multi-level ladder so represented depth is controllable."""
    c.on_book({"bid": max(bids), "ask": ask,
               "bid_qty": "50", "ask_qty": "50", "status": "OK",
               "_ladder": {"bids": {D(b): D(50) for b in bids},
                           "offers": {D(ask): D(50)}}}, t)


def test_v4_rechooses_the_replenishment_price_where_v3_keeps_the_level():
    """V3 may only persist a level that is a genuine HOLE inside represented
    depth. Here 0.40 sits between 0.45 and 0.35, so it is a hole and the
    persistence mechanism is exercised; V2/V4 re-choose the touch instead."""
    v3, v4 = build(V3), build(V4)
    for c in (v3, v4):
        c.window_end = 10_000.0
        ladder(c, 1000.0, ["0.40"])
    filled_px = v3.legs["up"].px
    assert filled_px == D("0.40")
    for c in (v3, v4):
        ladder(c, 1001.0, ["0.45", "0.35"])      # 0.40 becomes a hole in depth
        fill_resting(c, "up", 1002.0)
    assert v3.legs["up"] is not None and v4.legs["up"] is not None
    assert v3.legs["up"].px == filled_px, "V3 persists the filled level"
    assert v4.legs["up"].px == D("0.45"), "V4 RE-CHOOSES from the current book"
    assert v3.legs["up"].px != v4.legs["up"].px


def test_v3_cannot_persist_a_level_outside_represented_depth():
    """THE CASE-2 REPAIR. A stale price beyond the represented ladder is not a
    known-empty level, so no zero-ahead queue grant and no synthetic
    persistence -- the mechanism behind V3's +1.5281/window."""
    v3 = build(V3)
    v3.window_end = 10_000.0
    ladder(v3, 1000.0, ["0.40"])
    assert v3.legs["up"].px == D("0.40")
    ladder(v3, 1001.0, ["0.30", "0.20"])         # 0.40 is now ABOVE all depth
    fill_resting(v3, "up", 1002.0)
    assert v3.legs["up"] is None or v3.legs["up"].px != D("0.40"), \
        "V3 persisted a level whose queue position is unknowable"
    assert v3.blocked.get("replenish_queue_position_unknowable", 0) >= 1


def test_a_marketable_replenishment_is_refused_not_rested():
    """Post-only entry means the venue would refuse it, so replay must too."""
    v3 = build(V3)
    v3.window_end = 10_000.0
    ladder(v3, 1000.0, ["0.40"], ask="0.60")
    assert v3.legs["up"].px == D("0.40")
    # the ask collapses THROUGH our price: 0.40 is now marketable
    ladder(v3, 1001.0, ["0.38"], ask="0.39")
    fill_resting(v3, "up", 1002.0)
    assert not (v3.legs["up"] is not None
                and v3.legs["up"].px == D("0.40")), \
        "a marketable price was rested as maker inventory"


def test_an_absent_level_does_not_imply_zero_queue_ahead():
    c = frozen()
    c.window_end = 10_000.0
    ladder(c, 1000.0, ["0.45", "0.35"])
    assert c.displayed_at("up", D("0.40")) == D(0), "a hole inside depth is 0"
    assert c.displayed_at("up", D("0.90")) is None, "beyond depth is UNKNOWABLE"
    assert c.displayed_at("up", D("0.05")) is None


def test_no_synthetic_fill_from_a_vanished_persisted_price():
    """A stale price disappearing from the ladder must not create a fill."""
    v3 = build(V3)
    v3.window_end = 10_000.0
    ladder(v3, 1000.0, ["0.40"])
    before = v3.qty["up"]
    ladder(v3, 1001.0, ["0.30", "0.20"])
    assert v3.qty["up"] == before, "inventory moved without a print"


def test_an_old_resting_quote_crossed_later_is_not_reclassified():
    """CASE 1 is UNRESOLVED and must stay untouched: an already-resting leg that
    the market later crosses keeps its identity and maker flag. The repair
    governs NEW and REPLENISHED quotes, not this."""
    c = frozen()
    c.window_end = 10_000.0
    ladder(c, 1000.0, ["0.40"], ask="0.60")
    leg = c.legs["up"]
    assert leg is not None and leg.maker is True
    ladder(c, 1001.0, ["0.38"], ask="0.39")      # the market crosses our 0.40
    assert c.legs["up"] is leg, "the old resting leg was replaced"
    assert c.legs["up"].maker is True, "it was silently reclassified"


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
    """Same hole-in-depth construction as the V3/V4 contrast above, so the
    persistence mechanism is exercised rather than refused."""
    v2, v3, v4 = build(V2), build(V3), build(V4)
    for c in (v2, v3, v4):
        c.window_end = 10_000.0
        ladder(c, 1000.0, ["0.40"])
        ladder(c, 1001.0, ["0.45", "0.35"])      # 0.40 becomes a hole
        fill_resting(c, "up", 1002.0)
    assert v3.legs["up"].px == D("0.40"), "V3 persists the filled level"
    assert v2.legs["up"].px == D("0.45") and v4.legs["up"].px == D("0.45")
