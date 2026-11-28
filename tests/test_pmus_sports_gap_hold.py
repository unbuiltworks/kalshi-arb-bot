"""Keep an unchanged resting bid through a short fair-value gap.

LIVE 2026-11-23: the same prices were cancelled and re-posted every 30-90 s
as the international fair value dropped out for a pass; six of eight
exposure-adding fills had rested under 15 s and 13% of pre-game resting
time counted for rewards. Owner: "Preserve unchanged resting orders ... We
should only reprice when fair value, inventory, risk, or reward economics
materially change", and earlier: "retaining economically safe quotes
through short data interruptions, but not blindly quoting without a valid
fair value."
"""
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

SLUG = "aec-x"


def band(mid="0.49", settled=True, calm=True):
    m = None if mid is None else D(mid)
    return {"mid": m, "lo": m, "hi": m, "settled": settled, "calm": calm,
            "range": D("0"), "span_s": 15.0}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(R, "EDGE_GATE", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    box = {"band": band()}
    monkeypatch.setattr(M.Engine, "intl_fair_band",
                        lambda self, slug: dict(box["band"]))
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.b.m(SLUG)
    return e, v, box


def ids(e):
    return sorted(oid for oid, o in e.b.d["markets"][SLUG]["orders"].items()
                  if o.get("open"))


def placed(v):
    return len(v.orders)


def test_baseline_a_missing_fair_value_pulls_the_bids(rig):
    e, v, box = rig
    assert M.RN1_GAP_HOLD_S == 0
    e.step_market(SLUG)
    assert len(ids(e)) == 2
    box["band"] = band(mid=None, settled=False)
    e.step_market(SLUG)
    assert ids(e) == []                       # today: cancelled


def test_a_short_gap_leaves_the_same_orders_resting(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    first = ids(e)
    assert len(first) == 2
    n = placed(v)
    box["band"] = band(mid=None, settled=False)
    for _ in range(3):
        e.step_market(SLUG)
    assert ids(e) == first                    # same order ids: not re-posted
    assert placed(v) == n                     # nothing new was sent
    box["band"] = band()
    e.step_market(SLUG)
    assert ids(e) == first and placed(v) == n  # and still the same after


def test_a_hold_ends_after_the_limit_and_never_renews_itself(rig,
                                                             monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    assert len(ids(e)) == 2
    box["band"] = band(mid=None, settled=False)
    e.step_market(SLUG)                       # held
    assert len(ids(e)) == 2
    for sd in ("YES", "NO"):                  # 21 s since the real pass
        e.b.d["markets"][SLUG]["last_pass"][sd]["t"] -= 21
    e.step_market(SLUG)
    assert ids(e) == []


def test_no_hold_when_the_international_price_is_moving(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    assert len(ids(e)) == 2
    box["band"] = band(settled=False, calm=False)
    e.step_market(SLUG)
    assert ids(e) == []


def test_no_hold_when_the_venues_own_price_has_moved(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    assert len(ids(e)) == 2
    box["band"] = band(mid=None, settled=False)
    v.bid, v.ask = D("0.47"), D("0.49")
    e.step_market(SLUG)
    assert ids(e) == []


def test_a_failed_price_test_is_never_held(rig, monkeypatch):
    """Only a MISSING fair value is held through. A fair value that says
    the bid is no longer worth its price pulls it."""
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    assert len(ids(e)) == 2
    box["band"] = band(mid="0.40")            # YES bid now far too dear
    e.step_market(SLUG)
    yes = [o for o in e.b.d["markets"][SLUG]["orders"].values()
           if o.get("open") and o["side"] == "YES"]
    assert yes == []
    # and a gap right after a failure does not bring it back
    box["band"] = band(mid=None, settled=False)
    n = placed(v)
    e.step_market(SLUG)
    assert placed(v) == n


def test_any_block_still_pulls_held_bids(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    assert len(ids(e)) == 2
    box["band"] = band(mid=None, settled=False)
    e.b.d["markets"][SLUG]["vol_pause_until"] = time.time() + 60
    e.step_market(SLUG)
    assert ids(e) == []


def test_the_lock_still_pulls_held_bids(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    box["band"] = band(mid=None, settled=False)
    monkeypatch.setenv(M.SUBMISSION_LOCK_ENV, "1")
    e.enforce_lock()
    e.step_market(SLUG)
    assert ids(e) == []


def test_thin_depth_still_pulls_a_held_bid(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    assert len(ids(e)) == 2
    box["band"] = band(mid=None, settled=False)
    monkeypatch.setattr(e, "depth_block", lambda slug, side: "thin_touch")
    e.step_market(SLUG)
    assert ids(e) == []


def test_a_gap_never_places_a_new_bid(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    box["band"] = band(mid=None, settled=False)
    for _ in range(3):
        e.step_market(SLUG)
    assert placed(v) == 0


def test_a_fill_during_a_hold_is_handled_as_usual(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    box["band"] = band(mid=None, settled=False)
    e.step_market(SLUG)
    for o in v.orders.values():
        if o["side"] == "YES" and o["open"]:
            o["filled"], o["open"] = o["qty"], False
    e.step_market(SLUG)
    mk = e.b.d["markets"][SLUG]
    assert D(mk["pos"]["YES"]) > 0
    e.step_market(SLUG)
    # the filled side gets no fresh bid on a gap; what still rests is the
    # other side, which now completes the pair
    assert [o for o in mk["orders"].values()
            if o.get("open") and o["side"] == "YES"] == []
    assert len([o for o in mk["orders"].values() if o.get("open")]) <= 1


# ----------------------------------------- do not chase the best price up
def test_baseline_a_one_tick_better_market_reposts_both_bids(rig):
    e, v, box = rig
    assert M.RN1_KEEP_DEEPER_TICKS == 0
    e.step_market(SLUG)
    first = ids(e)
    v.bid, v.ask = D("0.49"), D("0.51")      # YES target moves up a tick
    box["band"] = band(mid="0.50")
    e.step_market(SLUG)
    e.step_market(SLUG)
    assert set(ids(e)) != set(first)          # today: cancelled and re-sent


def _yes(e):
    return [(oid, o["px"]) for oid, o in
            e.b.d["markets"][SLUG]["orders"].items()
            if o.get("open") and o["side"] == "YES"]


def test_a_bid_left_one_tick_deeper_keeps_its_place(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_KEEP_DEEPER_TICKS", 1)
    e.step_market(SLUG)
    yes0 = _yes(e)
    v.bid, v.ask = D("0.49"), D("0.51")      # best bid improved by a tick
    box["band"] = band(mid="0.50")
    e.step_market(SLUG)
    e.step_market(SLUG)
    assert _yes(e) == yes0                    # same order, same price


def test_two_ticks_deeper_is_repriced(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_KEEP_DEEPER_TICKS", 1)
    e.step_market(SLUG)
    yes0 = _yes(e)
    v.bid, v.ask = D("0.50"), D("0.52")
    box["band"] = band(mid="0.51")
    e.step_market(SLUG)
    e.step_market(SLUG)
    now = _yes(e)
    assert now and now != yes0
    assert D(str(now[0][1])) > D(str(yes0[0][1]))


def test_a_bid_now_above_its_target_is_never_kept(rig, monkeypatch):
    """The market came toward us: our bid is closer than intended. That is
    re-priced exactly as today, whatever the setting."""
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_KEEP_DEEPER_TICKS", 3)
    e.step_market(SLUG)
    yes0 = _yes(e)
    v.bid, v.ask = D("0.47"), D("0.49")
    box["band"] = band(mid="0.48")
    e.step_market(SLUG)
    e.step_market(SLUG)
    now = _yes(e)
    assert now != yes0
    assert all(D(str(px)) < D(str(yes0[0][1])) for _oid, px in now)


def test_a_completion_is_always_priced_at_its_target(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_KEEP_DEEPER_TICKS", 2)
    mk = e.b.d["markets"][SLUG]
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.88")
    e.step_market(SLUG)
    c0 = [(oid, o["px"]) for oid, o in mk["orders"].items() if o.get("open")
          and o.get("stage") == "two_sided_complete"]
    assert c0
    v.bid, v.ask = D("0.47"), D("0.49")      # NO target moves up a tick
    box["band"] = band(mid="0.48")
    e.step_market(SLUG)
    e.step_market(SLUG)
    c1 = [(oid, o["px"]) for oid, o in mk["orders"].items() if o.get("open")
          and o.get("stage") == "two_sided_complete"]
    assert c1 and c1 != c0


def test_a_deeper_bid_can_still_be_held_through_a_gap(rig, monkeypatch):
    e, v, box = rig
    monkeypatch.setattr(M, "RN1_KEEP_DEEPER_TICKS", 1)
    monkeypatch.setattr(M, "RN1_GAP_HOLD_S", 20.0)
    e.step_market(SLUG)
    yes0 = _yes(e)
    v.bid, v.ask = D("0.49"), D("0.51")
    box["band"] = band(mid="0.50")
    e.step_market(SLUG)
    assert _yes(e) == yes0
    box["band"] = band(mid=None, settled=False)
    e.step_market(SLUG)
    assert _yes(e) == yes0
