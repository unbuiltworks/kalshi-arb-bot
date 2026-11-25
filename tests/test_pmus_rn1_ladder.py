"""Inventory at several price levels (PMUS_RN1_LADDER_LEVELS).

Owner 2026-11-25: "Build inventory at different price levels", "Maintain
multiple resting orders", "We want sustained inventory accumulation, not
isolated two-contract transactions followed by inactivity". Each side of a
market used to hold one resting bid; with the ladder on, deeper bids of the
same size rest behind every opening/extension bid, inside every cap.
"""
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    SLUG, eng, resting)


@pytest.fixture
def lad(eng, monkeypatch):
    e, v = eng
    e.guard = None                     # no international price in this test
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    e.b.m(SLUG)
    return e, v


def test_off_by_default_one_bid_per_side(eng):
    e, v = eng
    assert M.RN1_LADDER_LEVELS == 1
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


def test_ladder_rests_deeper_bids_on_both_sides(lad):
    e, v = lad
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.45", "4"), ("NO", "0.47", "4"),
                          ("NO", "0.49", "4"), ("YES", "0.43", "4"),
                          ("YES", "0.45", "4"), ("YES", "0.47", "4")]
    # a second pass changes nothing: no cancel, no re-place
    before = set(v.orders)
    e.step_market(SLUG)
    assert set(v.orders) == before


def test_the_unpaired_cap_bounds_the_ladder(lad, monkeypatch):
    """$4 unpaired a market: two 4-contract levels fit on each side
    (YES 1.88 + 1.80 = 3.68; NO 1.96 + 1.88 = 3.84); a third does not."""
    e, v = lad
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("4"))
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.47", "4"), ("NO", "0.49", "4"),
                          ("YES", "0.45", "4"), ("YES", "0.47", "4")]


def test_the_campaign_caps_bound_the_ladder(lad, monkeypatch):
    e, v = lad
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("2"))
    e.step_market(SLUG)
    # one bid a side fits $2 unpaired either way; no deeper level does
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


def test_a_price_move_keeps_bids_that_land_on_another_level(lad):
    """bid 0.48 -> 0.46: YES levels 0.47/0.45/0.43 become 0.45/0.43/0.41.
    The bids at 0.45 and 0.43 stay; only 0.47 is cancelled and 0.41 added.
    """
    e, v = lad
    e.step_market(SLUG)
    yes_before = {oid for oid, o in v.orders.items()
                  if o["open"] and o["side"] == "YES"}
    v.bid, v.ask = D("0.46"), D("0.48")
    e.step_market(SLUG)
    yes_now = {oid: str(o["px"]) for oid, o in v.orders.items()
               if o["open"] and o["side"] == "YES"}
    assert sorted(yes_now.values()) == ["0.41", "0.43", "0.45"]
    kept = yes_before & set(yes_now)
    assert sorted(yes_now[o] for o in kept) == ["0.43", "0.45"]


def test_both_sides_ladder_when_one_side_holds_inventory(lad):
    """4 YES held at 0.45: YES keeps its extension and two deeper levels;
    NO has its completing bid and, behind it, two cheaper opening bids --
    every NO price still pairs with the YES average inside the cap."""
    e, v = lad
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.80")   # 4 @ 0.45
    mk.setdefault("last_fill_t", {})["YES"] = time.time() - 3600
    e.step_market(SLUG)
    rs = resting(v)
    no = sorted(D(r[1]) for r in rs if r[0] == "NO")
    yes = [r for r in rs if r[0] == "YES"]
    assert len(no) == 3 and len(yes) == 3
    assert all(px + D("0.45") <= R.PAIR_CAP for px in no)
    stages = sorted(o_["stage"] for o_ in e.b.m(SLUG)["orders"].values()
                    if o_.get("open") and o_["side"] == "NO")
    assert stages == ["rn1_open", "rn1_open", "two_sided_complete"]


def test_every_ladder_order_passes_the_final_gate(lad, monkeypatch):
    """The final gate re-checks each order against everything resting: a
    per-market spend cap that the ladder planner did not see still holds."""
    e, v = lad
    refused = []
    real = e.final_gate

    def gate(*a, **k):
        why = real(*a, **k)
        if why:
            refused.append(why)
        return why
    monkeypatch.setattr(e, "final_gate", gate)
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 6)
    monkeypatch.setattr(R, "PER_MARKET_USD", D("8"))
    e.step_market(SLUG)
    total = sum(D(px) * D(q) for _s, px, q in resting(v))
    assert total <= D("8")


def test_in_play_only_also_holds_for_a_market_already_tracked(
        eng, monkeypatch):
    """LIVE 2026-11-25: slate switched to live while a pre-game market was
    already tracked; it kept opening. Now no opening or extension rests in
    a pre-game market under the live slate; a completion still does."""
    e, v = eng
    mk = e.b.m(SLUG)
    mk["game_start"] = time.time() + 3600            # not started
    monkeypatch.setattr(M, "SLATE", "live")
    e.step_market(SLUG)
    assert resting(v) == []
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.80")
    mk.setdefault("last_fill_t", {})["YES"] = time.time() - 3600
    e.step_market(SLUG)
    assert [r[0] for r in resting(v)] == ["NO"]       # the completion only
    monkeypatch.setattr(M, "SLATE", "both")           # before: it opened
    mk["pos"]["YES"], mk["cost"]["YES"] = D("0"), D("0")
    e.step_market(SLUG)
    assert sorted(r[0] for r in resting(v)) == ["NO", "YES"]


# ---------------------------------------------- accumulation through fills
def _fill(v, side, px):
    for oid, o in v.orders.items():
        if o["open"] and o["side"] == side and str(o["px"]) == px:
            o["filled"], o["open"] = o["qty"], False
            return oid
    raise AssertionError("no resting %s bid at %s" % (side, px))


@pytest.fixture
def live(eng, monkeypatch):
    """In-play market, two contracts a level, three levels two ticks apart,
    the production caps ($3 a market unpaired, $8 a market, $6 campaign)."""
    e, v = eng
    e.guard = None
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setattr(M, "RN1_LIVE_QTY", D("2"))
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("3"))
    monkeypatch.setattr(R, "PER_MARKET_USD", D("8"))
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("6"))
    monkeypatch.setattr(R, "REFILL_COOLDOWN_S", 10.0)
    e.b.m(SLUG)
    return e, v


def _cool_down(e):
    lf = e.b.m(SLUG).setdefault("last_fill_t", {})
    for s_ in list(lf):
        lf[s_] = float(lf[s_]) - 11


def test_live_market_rests_three_levels_on_each_side(live):
    e, v = live
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.45", "2"), ("NO", "0.47", "2"),
                          ("NO", "0.49", "2"), ("YES", "0.43", "2"),
                          ("YES", "0.45", "2"), ("YES", "0.47", "2")]


def test_a_fill_keeps_both_ladders_working(live):
    """YES 0.47 fills. The YES ladder's deeper bids stay through the
    cooldown; NO keeps a completing bid AND deeper opening bids."""
    e, v = live
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert e.b.m(SLUG)["pos"]["YES"] in ("2", D("2"))
    rs = resting(v)
    assert ("YES", "0.45", "2") in rs and ("YES", "0.43", "2") in rs
    no = sorted(px for sd, px, _q in rs if sd == "NO")
    assert len(no) >= 2 and no[-1] == "0.49"            # completion + deeper
    assert D(no[-1]) + D("0.47") <= R.PAIR_CAP


def test_same_side_inventory_builds_through_successive_fills(live):
    """The price falls through the YES ladder: three fills, 6 contracts,
    average cost falling, never above the $3 unpaired cap."""
    e, v = live
    e.step_market(SLUG)
    for px in ("0.47", "0.45", "0.43"):
        _fill(v, "YES", px)
        e.step_market(SLUG)
    mk = e.b.m(SLUG)
    assert D(str(mk["pos"]["YES"])) == D("6")
    assert D(str(mk["cost"]["YES"])) == D("2.70")        # avg 0.45
    assert D(str(mk["cost"]["YES"])) <= R.RESIDUAL_CAP_USD
    # nothing more on YES can rest: one more clip would pass $3 unpaired
    _cool_down(e)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []


def test_a_filled_level_is_replenished_after_the_cooldown(live):
    e, v = live
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert ("YES", "0.47", "2") not in resting(v)        # cooling down
    _cool_down(e)
    e.step_market(SLUG)
    yes = sorted(px for sd, px, _q in resting(v) if sd == "YES")
    assert yes[-1] == "0.47"                              # replenished
    assert "0.45" in yes                                  # deeper kept


def test_both_sides_accumulate_and_pairs_form_naturally(live):
    e, v = live
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    _fill(v, "NO", "0.49")                                # completes 2 pairs
    e.step_market(SLUG)
    _cool_down(e)
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    assert D(str(mk["pos"]["YES"])) == D(str(mk["pos"]["NO"])) == D("2")
    rs = resting(v)
    assert len([r for r in rs if r[0] == "YES"]) >= 2     # both sides open
    assert len([r for r in rs if r[0] == "NO"]) >= 2      # and laddered


def test_a_pause_still_pulls_the_whole_ladder(live):
    """Keeping bids through a cooldown never overrides a market block."""
    e, v = live
    e.step_market(SLUG)
    _fill(v, "YES", "0.47")
    e.b.m(SLUG)["vol_pause_until"] = time.time() + 60
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []


def test_the_caps_hold_through_any_sequence_of_fills(live):
    """Fill every YES bid the bot rests, pass after pass: filled unpaired
    cost never exceeds $3 in the market."""
    e, v = live
    for _ in range(6):
        e.step_market(SLUG)
        yes = [o for o in v.orders.values()
               if o["open"] and o["side"] == "YES"]
        for o in yes:
            o["filled"], o["open"] = o["qty"], False
        e.step_market(SLUG)
        _cool_down(e)
        mk = e.b.m(SLUG)
        assert R.unmatched_usd(mk["pos"], mk["cost"]) <= D("3")
