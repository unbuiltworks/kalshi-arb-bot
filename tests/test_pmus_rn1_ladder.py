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


# ------------------------------------------ size down to fit (FIT_QTY)
def test_fit_qty_bids_the_largest_size_that_fits():
    """2 YES held at 0.45 ($0.90 unpaired), clip 4, $2 unpaired cap: the
    full clip would pass the cap; 2 contracts fit."""
    from tests.test_pmus_rn1_replica import qt
    args = dict(pos={"YES": "2", "NO": "0"}, cost={"YES": "0.90", "NO": "0"},
                qty="4", residual_cap_usd="2", per_market_usd="20")
    assert qt("0.45", "0.47", **args)["YES"] is None          # default
    q = qt("0.45", "0.47", fit_qty=True, **args)
    assert q["YES"]["qty"] == D("2") and q["YES"]["role"] == "extend"


def test_fit_qty_never_goes_below_the_venue_minimum():
    from tests.test_pmus_rn1_replica import qt
    q = qt("0.45", "0.47", pos={"YES": "2", "NO": "0"},
           cost={"YES": "0.90", "NO": "0"}, qty="4",
           residual_cap_usd="1.00", per_market_usd="20", fit_qty=True)
    assert q["YES"] is None and q["why_YES"] == "residual_cap"


def test_fit_qty_sizes_down_a_ladder_level(live, monkeypatch):
    """$3 unpaired: YES 0.47 x2 + 0.45 x2 = $1.84; a third level of 2 at
    0.43 ($0.86) passes $3 with the NO side's worst case counted, one fits
    only when sizing down is on."""
    e, v = live
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("2.40"))
    e.step_market(SLUG)
    off = sorted((px, q) for sd, px, q in resting(v) if sd == "YES")
    for o in v.orders.values():
        o["open"] = False
    e.b.m(SLUG)["orders"] = {}
    monkeypatch.setattr(R, "FIT_QTY", True)
    e.step_market(SLUG)
    on = sorted((px, q) for sd, px, q in resting(v) if sd == "YES")
    assert off == [("0.45", "2"), ("0.47", "2")]
    assert on == [("0.43", "1"), ("0.45", "2"), ("0.47", "2")]


# --------------------------------- pull only the threatened bid
def _react_one_tick_down(e, monkeypatch):
    calls = []
    monkeypatch.setattr(e, "step_market", lambda s: calls.append(s))
    prev = {"bid": D("0.48"), "ask": D("0.50")}
    new = {"bid": D("0.47"), "ask": D("0.49")}
    import time as _t
    e.react(SLUG, [(prev, new, _t.monotonic())])


def test_by_default_a_threatened_bid_pulls_every_bid(live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "depth_ticks", lambda t: 1)
    e.step_market(SLUG)
    assert len(resting(v)) == 6
    _react_one_tick_down(e, monkeypatch)
    assert resting(v) == []


def test_threatened_only_keeps_the_bids_the_move_did_not_reach(
        live, monkeypatch):
    """A one-tick fall puts YES 0.47 at the touch; YES 0.45/0.43 and all
    three NO bids are untouched and keep their place."""
    e, v = live
    monkeypatch.setattr(M, "depth_ticks", lambda t: 1)
    monkeypatch.setattr(M, "RN1_PULL_THREATENED_ONLY", True)
    e.step_market(SLUG)
    _react_one_tick_down(e, monkeypatch)
    assert resting(v) == [("NO", "0.45", "2"), ("NO", "0.47", "2"),
                          ("NO", "0.49", "2"), ("YES", "0.43", "2"),
                          ("YES", "0.45", "2")]


def test_a_jump_still_pulls_everything_with_threatened_only(
        live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "RN1_PULL_THREATENED_ONLY", True)
    e.step_market(SLUG)
    monkeypatch.setattr(e, "step_market", lambda s: None)
    import time as _t
    e.react(SLUG, [({"bid": D("0.48"), "ask": D("0.50")},
                    {"bid": D("0.44"), "ask": D("0.46")}, _t.monotonic())])
    assert resting(v) == []


# ------------------------------- reward measurement: the best level
def test_reward_estimate_counts_every_mature_visible_level(live, monkeypatch):
    """Full ladder (dev/chatgpt-rn1-ladder-score): every owned resting level
    older than the book lag is scored, each capped at what the public book
    shows at that price; a young order is left out."""
    import time as _t
    e, v = live
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    for o in mk["orders"].values():
        o["placed_at"] = _t.time() - 60
    seen = {}
    from src import pmus_sports_rewards as RW

    def est(book, per, yes_levels, no_levels, tick, own_yes, own_no):
        seen.update(yes=sorted(yes_levels), no=sorted(no_levels))
        return {"share": 0.1, "est_usd_per_hour": 0.1,
                "max_spread_ok": None}
    monkeypatch.setattr(RW, "ladder_quote_estimate", est)
    # public book: YES bids at our 0.47/0.45 (0.43 not shown), YES asks at
    # 0.51 (our NO 0.49, 2 contracts) and 0.53 (only 1 visible of our 2)
    book = {"marketData": {
        "bids": [{"px": {"value": "0.47"}, "qty": "5"},
                 {"px": {"value": "0.45"}, "qty": "2"}],
        "offers": [{"px": {"value": "0.51"}, "qty": "9"},
                   {"px": {"value": "0.53"}, "qty": "1"}]}}
    monkeypatch.setattr(e, "_reward_get", lambda: (lambda p: book))
    mk.pop("rn1_reward_est", None)
    e._rn1_reward_estimate(SLUG, mk, {"pool_rate_usd_s": 0.01, "df": 0.5,
                                      "targetSize": 10}, D("0.01"))
    assert seen["yes"] == [(0.45, 2.0), (0.47, 2.0)]
    assert seen["no"] == [(0.47, 1.0), (0.49, 2.0)]


# ------------------------------ loss stop counts resting bids
def _pilot_ok(e):
    import time as _t
    e._pilot = {"realized_net_usd": "0", "open_residual_worst_usd": "0",
                "unconfirmed": []}
    e._pilot_at = _t.monotonic()


def test_resting_ladder_cannot_carry_the_worst_case_past_the_loss_stop(
        live, monkeypatch):
    """LIVE 2026-11-25 18:40:57: two ladder levels filled together and the
    campaign worst case went past the $4 stop. With a $2.50 stop, the bids
    the bot rests are now limited so that ALL of them filling and losing
    stays inside it; without the rule every level rests."""
    e, v = live
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("2.50"))
    _pilot_ok(e)
    monkeypatch.setattr(M, "RN1_LOSS_STOP_COUNTS_RESTING", False)
    e.step_market(SLUG)
    assert len(resting(v)) == 6 and e.own_resting_worst() > D("2.50")
    for o in v.orders.values():
        o["open"] = False
    e.b.m(SLUG)["orders"] = {}
    monkeypatch.setattr(M, "RN1_LOSS_STOP_COUNTS_RESTING", True)
    e.step_market(SLUG)
    assert 0 < len(resting(v)) < 6
    assert e.own_resting_worst() < D("2.50")


def test_completions_are_never_refused_by_the_resting_loss_rule(
        live, monkeypatch):
    """2 YES held at 0.60 ($1.20 worst case) against a $1.50 stop: another
    2-contract YES bid would take the worst case to -$2.10 and is refused;
    the NO completion is not."""
    import time as _t
    e, v = live
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("1.50"))
    monkeypatch.setattr(M, "RN1_LOSS_STOP_COUNTS_RESTING", True)
    _pilot_ok(e)
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("1.20")
    mk["orders"]["old"] = {"side": "YES", "px": "0.60", "qty": "2",
                           "filled": "2", "open": False,
                           "stage": "rn1_open", "placed_at": _t.time()}
    why_open = e.final_gate(SLUG, "YES", D("0.45"), D("2"), "rn1_extend")
    why_done = e.final_gate(SLUG, "NO", D("0.35"), D("2"),
                            "two_sided_complete")
    assert why_open and why_open.startswith("campaign_loss_stop_with_resting")
    assert why_done is None


# ------------------------------------------- reward-qualified quoting
def test_join_on_a_one_tick_book_puts_one_side_at_the_best_bid(
        live, monkeypatch):
    e, v = live
    v.bid, v.ask = D("0.48"), D("0.49")
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", True)
    e.step_market(SLUG)
    off = [r for r in resting(v) if r[0] == "YES"]
    assert max(D(px) for _s, px, _q in off) == D("0.47")     # never joined
    for o in v.orders.values():
        o["open"] = False
    e.b.m(SLUG)["orders"] = {}
    monkeypatch.setattr(M, "RN1_JOIN_ONE_TICK", True)
    e.step_market(SLUG)
    yes = max(D(px) for s_, px, _q in resting(v) if s_ == "YES")
    no = max(D(px) for s_, px, _q in resting(v) if s_ == "NO")
    assert yes == D("0.48")                                    # best bid
    assert yes + no <= R.PAIR_CAP                              # pair limit


def test_a_joined_one_tick_bid_is_not_pulled_for_sitting_next_to_the_offer(
        live, monkeypatch):
    e, v = live
    v.bid, v.ask = D("0.48"), D("0.49")
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", True)
    monkeypatch.setattr(M, "RN1_JOIN_ONE_TICK", True)
    monkeypatch.setattr(M, "depth_ticks", lambda t: 1)
    e.step_market(SLUG)
    n0 = len(resting(v))
    monkeypatch.setattr(e, "step_market", lambda s: None)
    import time as _t
    e.react(SLUG, [({"bid": D("0.48"), "ask": D("0.50")},
                    {"bid": D("0.48"), "ask": D("0.49")}, _t.monotonic())])
    assert len(resting(v)) == n0


def test_ladder_levels_can_be_smaller_than_the_best_bid_clip(
        live, monkeypatch):
    e, v = live
    monkeypatch.setattr(M, "RN1_LADDER_QTY", D("1"))
    e.step_market(SLUG)
    yes = sorted((px, q) for s_, px, q in resting(v) if s_ == "YES")
    assert yes == [("0.43", "1"), ("0.45", "1"), ("0.47", "2")]


def test_a_market_that_stops_qualifying_is_released(live, monkeypatch):
    import time as _t
    e, v = live
    monkeypatch.setattr(M, "RN1_RELEASE_UNQUALIFIED_S", 30.0)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: False)
    e.step_market(SLUG)                         # starts the clock
    mk = e.b.m(SLUG)
    assert not mk.get("done")
    mk["unqualified_since"] = _t.time() - 31
    e.step_market(SLUG)                         # cancels its bids
    assert resting(v) == []
    e.step_market(SLUG)                         # then releases it
    assert mk.get("done") is True


def test_a_market_holding_inventory_is_never_released_for_rewards(
        live, monkeypatch):
    import time as _t
    e, v = live
    monkeypatch.setattr(M, "RN1_RELEASE_UNQUALIFIED_S", 30.0)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: False)
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.94")
    mk["unqualified_since"] = _t.time() - 300
    e.step_market(SLUG)
    assert not mk.get("done")
    assert any(s_ == "NO" for s_, _p, _q in resting(v))      # completion
