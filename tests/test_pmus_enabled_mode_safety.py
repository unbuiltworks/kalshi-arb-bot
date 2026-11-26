"""ENABLED-MODE safety layer for the no-fair marginal-EV release candidate.

Every test here runs with the four settings the candidate is meant to run
with (set on the module constants, exactly as the env sets them at import):

    PMUS_RN1_MARGINAL_EV=1  PMUS_RN1_MARGINAL_EV_NO_FAIR=1
    PMUS_RN1_FIT_ALL_LEVELS=1  PMUS_RN1_EDGE_GATE=0

and a market with NO international fair value (a doubles match), so every
opening/extension bid and every deeper level rests only if its qualifying
incremental theoretical reward beats its expected fill cost. The market is
given a realistic paying liquidity programme and public book
(tests/pmus_enabled_mode.py, profile "doubles"), discovered and scored by
the engine's own code. Each protection is exercised AFTER real orders are
resting, so the assertion measures the protection, not the absence of a
reward. All reward numbers are THEORETICAL; none is P&L.
"""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_rewards as RW
from src import pmus_sports_two_sided as M
from tests.pmus_enabled_mode import (PROFILES, RewardFeed, enabled_flags_on,
                                     set_enabled_flags)
from tests.test_pmus_rn1_ladder import (_cool_down, _fill, _pilot_ok,
                                        _react_one_tick_down)
from tests.test_pmus_sports_continuous_controller import SLUG, resting
from tests.test_pmus_sports_final_gate import _attest
from tests.test_pmus_sports_fill_list import S1, f, rest  # noqa: F401
from tests.test_pmus_sports_safety_latch import row, snapshot
from tests.test_pmus_sports_two_sided import FakeVenue, _risk_doc
from tests.test_pmus_sports_window_lock import END, START, WIN, at, setlock

T = D("0.01")


# ------------------------------------------------------------ fixtures
def _engine(tmp_path, monkeypatch, path="s.json"):
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / path))
    e.guard = None                        # no international fair value
    e._sleep = lambda s: None
    return e, v


@pytest.fixture
def en(tmp_path, monkeypatch):
    """In-play doubles market, no fair value, enabled mode, the production
    caps ($3 unpaired a market, $8 a market, $6 campaign unpaired, $16
    campaign spend), two contracts a level, three levels a tick apart, and
    a paying programme. Book 0.48/0.50."""
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("50"))
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    for env in (M.SUBMISSION_LOCK_ENV, M.SAFETY_LATCH_ACK_ENV,
                M.SHARED_HALT_URL_ENV, M.STALL_DRILL_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setitem(M._LOOP, "beat", None)
    monkeypatch.setitem(M._LOOP, "thread", None)
    monkeypatch.setitem(M._LOOP, "max_gap", 0.0)
    for name, val in (("RESIDUAL_CAP_USD", "3"), ("PER_MARKET_USD", "8"),
                      ("TOTAL_UNMATCHED_USD", "6"),
                      ("TOTAL_SPEND_USD", "16")):
        monkeypatch.setattr(R, name, D(val))
    monkeypatch.setattr(R, "REFILL_COOLDOWN_S", 10.0)
    monkeypatch.setattr(M, "RN1_LIVE_QTY", D("2"))
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 1)
    set_enabled_flags(monkeypatch)
    e, v = _engine(tmp_path, monkeypatch)
    e.b.m(SLUG)
    feed = RewardFeed(e).install(monkeypatch)
    return e, v, feed


def quotes(capsys):
    pre = "PMUS_TWO_SIDED "
    rows = [json.loads(l[len(pre):])
            for l in capsys.readouterr().out.splitlines()
            if l.startswith(pre)]
    return [r for r in rows if r.get("kind") == "RN1_QUOTE"]


def kinds(capsys):
    pre = "PMUS_TWO_SIDED "
    return [json.loads(l[len(pre):]).get("kind")
            for l in capsys.readouterr().out.splitlines()
            if l.startswith(pre)]


def open_stages(e, slug=SLUG):
    return sorted((o["side"], o.get("stage"))
                  for o in e.b.m(slug)["orders"].values() if o.get("open"))


def openings(e):
    return [(s, o["side"], o["px"]) for s, mk in e.b.d["markets"].items()
            for o in mk["orders"].values()
            if o.get("open") and not M.is_completion_stage(o.get("stage"))]


def only_completion(e, slug=SLUG):
    """Every bid still resting in this market completes the held leg: on
    the lighter side only, no more than is unpaired, and pairing at or
    under the pair cap. (A resting opening bid on the lighter side becomes
    the completion once the other side fills; its stage label stays
    rn1_open.) True when at least one such bid rests."""
    mk = e.b.m(slug)
    y, n = D(str(mk["pos"]["YES"])), D(str(mk["pos"]["NO"]))
    if y == n:
        return False
    heavy, light = ("YES", "NO") if y > n else ("NO", "YES")
    avg = D(str(mk["cost"][heavy])) / D(str(mk["pos"][heavy]))
    rs = [o for o in mk["orders"].values() if o.get("open")]
    left = sum(D(str(o["qty"])) - D(str(o.get("filled") or "0"))
               for o in rs)
    return bool(rs) and all(o["side"] == light and
                            D(str(o["px"])) + avg <= R.PAIR_CAP
                            for o in rs) and left <= abs(y - n)


def worst(e, slug=SLUG):
    """This market's exact worst case over every way its bids can fill."""
    mk = e.b.m(slug)
    rs = [(o["side"], o["px"], D(str(o["qty"])) - D(str(o.get("filled")
                                                        or "0")),
           M.is_completion_stage(o.get("stage")))
          for o in mk["orders"].values() if o.get("open")]
    return R.committed(mk["pos"], mk["cost"], rs)


def full_ladder(e, v):
    """The reference plan: every level on both sides, each carried only by
    its own reward."""
    e.step_market(SLUG)
    rs = resting(v)
    assert rs == [("NO", "0.47", "2"), ("NO", "0.48", "2"),
                  ("NO", "0.49", "2"), ("YES", "0.45", "2"),
                  ("YES", "0.46", "2"), ("YES", "0.47", "2")]
    return rs


# ------------------------------------------------- the layer itself
def test_enabled_mode_flags_are_the_four_settings(en):
    assert enabled_flags_on()
    assert M.RN1_MARGINAL_EV and M.RN1_MARGINAL_EV_NO_FAIR
    assert M.RN1_FIT_ALL_LEVELS and not R.EDGE_GATE


def test_without_the_programme_nothing_rests(tmp_path, monkeypatch):
    """The reason this layer exists: enabled mode with no reward programme
    rests nothing, so a safety test with no programme proves nothing."""
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    set_enabled_flags(monkeypatch)
    e, v = _engine(tmp_path, monkeypatch)
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == []


def test_reward_carried_ladder_rests_and_every_bid_was_judged(en, capsys):
    e, v, feed = en
    full_ladder(e, v)
    q = quotes(capsys)[-1]
    mev = q["marginal_ev"]
    assert len(mev) == 6 and all(r["basis"] == "no_fair" for r in mev)
    assert all(r["ok"] and r["reward_usd_per_hour"] > 0 for r in mev)
    # realistic magnitudes (theoretical, after the haircut): cents an hour
    # for one 2-contract bid behind the touch
    assert all(0.005 < r["reward_usd_per_hour"] < 0.5 for r in mev)
    assert q["incentive"]["why"] == "qualifies"
    assert any(p.endswith("/book") for p in feed.reads)
    assert any(p.startswith("/v1/incentives?") for p in feed.reads)


@pytest.mark.parametrize("profile, lo, hi", [("doubles", 0.5, 9.0),
                                             ("thin", 0.005, 0.2)])
def test_reward_profiles_match_observed_live_magnitudes(en, profile, lo, hi):
    """Doubles: 10 contracts at each touch earn ~$1/h of a $6.25/h pool
    (live ATP doubles: up to ~$9.4/h for the market). Thin: 10 contracts a
    tick behind earn cents an hour. Venue rule, real scoring."""
    e, v, _ = en
    feed = RewardFeed(e, profile)
    per = feed.rn1_reward(SLUG)
    book = feed.book(SLUG)
    if profile == "doubles":
        est = RW.quote_estimate(book, per, 0.48, 0.50, 10.0, 0.01)
    else:
        est = RW.quote_estimate(book, per, 0.47, 0.49, 10.0, 0.01)
    assert lo <= est["est_usd_per_hour"] <= hi
    assert per["pool_rate_usd_s"] * 3600 <= 9.4


def test_a_thin_programme_below_the_minimum_payout_credits_nothing(
        en, monkeypatch, capsys):
    """Thin market: its projection over RN1_REWARD_PROJECT_H stays under
    the venue's $1 minimum payout, so no reward is credited and nothing
    rests on reward alone."""
    e, v, _ = en
    feed = RewardFeed(e, "thin").install(monkeypatch)
    assert feed.projected_usd_h * M.RN1_REWARD_PROJECT_H < \
        M.RN1_REWARD_MIN_DAY_USD
    e.step_market(SLUG)
    assert resting(v) == []
    q = quotes(capsys)[-1]
    assert q["incentive"]["why"] == "below_minimum_payout"
    assert all(r["reward_usd_per_hour"] == 0 for r in q["marginal_ev"])


# ------------------------------------------------- 1. loss stops
def test_campaign_loss_stop_refuses_openings_and_keeps_the_completion(
        en, monkeypatch, capsys):
    e, v, _ = en
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("4.00"))
    monkeypatch.setattr(M, "RN1_LOSS_STOP_COUNTS_RESTING", False)
    _pilot_ok(e)
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert any(s_ == "YES" for s_, _p, _q in resting(v))   # still adding
    e._pilot = {"realized_net_usd": "-4.10", "open_residual_worst_usd": "0",
                "unconfirmed": []}
    e._pilot_at = time.monotonic()
    _cool_down(e)
    e.step_market(SLUG)
    assert e.b.d["pilot_loss_stop"]["net"] == "-4.10"
    assert only_completion(e)                      # every opening pulled
    # the stop is latched in the book: a recovered meter does not lift it,
    # and pass after pass (cooldown over, reward still paying) no YES bid
    # is sent again
    _pilot_ok(e)
    sent_yes = len([o for o in v.orders.values() if o["side"] == "YES"])
    for _ in range(3):
        _cool_down(e)
        e.step_market(SLUG)
    assert only_completion(e) and e.pilot_block() == "pilot_loss_stop"
    assert len([o for o in v.orders.values() if o["side"] == "YES"]) == \
        sent_yes


def test_resting_ladder_worst_case_stays_inside_the_loss_stop(
        en, monkeypatch):
    """All resting bids filling and losing must stay inside the stop: the
    ladder is cut, and FIT_ALL_LEVELS cannot size around it."""
    e, v, _ = en
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("2.50"))
    monkeypatch.setattr(M, "RN1_LOSS_STOP_COUNTS_RESTING", True)
    _pilot_ok(e)
    e.step_market(SLUG)
    assert 0 < len(resting(v)) < 6
    assert e.own_resting_worst() < D("2.50")


def test_daily_loss_stop_refuses_openings_and_keeps_the_completion(
        en, monkeypatch, capsys):
    e, v, _ = en
    monkeypatch.setattr(M, "DAILY_LOSS_USD", D("0.10"))
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert not e.daily_stopped()
    v.bid, v.ask = D("0.20"), D("0.22")            # YES marked at 0.20
    e.step_market(SLUG)
    assert e.daily_stopped() and e.day_change() < D("-0.10")
    assert openings(e) == []
    assert [s for s, _st in open_stages(e)] == ["NO"]
    assert open_stages(e) == [("NO", "two_sided_complete")]
    # a fresh flat market with a paying programme opens nothing
    v.bid, v.ask = D("0.48"), D("0.50")
    e.b.m("aec-new")
    n = len(v.orders)
    e.step_market("aec-new")
    assert len(v.orders) == n


# ------------------------------------------- 2. simultaneous fills
def test_several_levels_fill_at_once_caps_hold(en):
    """Every YES level fills in one pass: 6 YES at 0.46 avg ($2.76 <= $3
    unpaired); no YES bid can rest (one more clip passes the cap); the NO
    side completes at or under the pair cap."""
    e, v, _ = en
    full_ladder(e, v)
    for px in ("0.47", "0.46", "0.45"):
        _fill(v, "YES", px)
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    assert D(str(mk["pos"]["YES"])) == D("6")
    assert D(str(mk["cost"]["YES"])) == D("2.76")
    assert R.unmatched_usd(mk["pos"], mk["cost"]) <= R.RESIDUAL_CAP_USD
    _cool_down(e)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    avg = D(str(mk["cost"]["YES"])) / D(str(mk["pos"]["YES"]))
    nos = [o for o in mk["orders"].values() if o.get("open")
           and o["side"] == "NO"]
    assert nos and all(D(str(o["px"])) + avg <= R.PAIR_CAP for o in nos)
    assert worst(e)["unmatched"] <= R.RESIDUAL_CAP_USD
    assert e.rn1_campaign_exposure()["unmatched"] <= R.TOTAL_UNMATCHED_USD


def test_both_ladders_fill_at_once_caps_hold_through_every_pass(en):
    e, v, _ = en
    full_ladder(e, v)
    for _ in range(4):
        for o in v.orders.values():
            if o["open"]:
                o["filled"], o["open"] = o["qty"], False
        e.step_market(SLUG)
        _cool_down(e)
        e.step_market(SLUG)
        mk = e.b.m(SLUG)
        assert R.unmatched_usd(mk["pos"], mk["cost"]) <= R.RESIDUAL_CAP_USD
        c = worst(e)
        assert c["unmatched"] <= R.RESIDUAL_CAP_USD
        assert c["spend"] <= R.TOTAL_SPEND_USD
        assert e.rn1_campaign_exposure()["unmatched"] <= \
            R.TOTAL_UNMATCHED_USD
        assert e.rn1_campaign_exposure()["spend"] <= R.TOTAL_SPEND_USD


def test_a_threatened_bid_pulls_every_bid(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "depth_ticks", lambda t: 1)
    full_ladder(e, v)
    _react_one_tick_down(e, monkeypatch)
    assert resting(v) == []


def test_threatened_only_keeps_bids_the_move_did_not_reach(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "depth_ticks", lambda t: 1)
    monkeypatch.setattr(M, "RN1_PULL_THREATENED_ONLY", True)
    full_ladder(e, v)
    _react_one_tick_down(e, monkeypatch)
    rs = resting(v)
    assert ("YES", "0.47", "2") not in rs          # now at the touch
    assert len(rs) == 5


# ------------------------------------------- 3. inventory and capital
@pytest.mark.parametrize("name, cap", [("RESIDUAL_CAP_USD", "2.35"),
                                       ("PER_MARKET_USD", "4.00"),
                                       ("TOTAL_UNMATCHED_USD", "2.35"),
                                       ("TOTAL_SPEND_USD", "4.00")])
def test_each_cap_bounds_the_reward_carried_ladder(en, monkeypatch,
                                                   name, cap):
    e, v, _ = en
    monkeypatch.setattr(R, name, D(cap))
    e.step_market(SLUG)
    rs = resting(v)
    assert 0 < len(rs) < 6
    c = worst(e)
    camp = e.rn1_campaign_exposure()
    assert c["unmatched"] <= R.RESIDUAL_CAP_USD
    assert camp["unmatched"] <= R.TOTAL_UNMATCHED_USD
    assert camp["spend"] <= R.TOTAL_SPEND_USD
    assert sum(D(px) * D(q) for _s, px, q in rs) <= R.PER_MARKET_USD


def test_fit_all_levels_sizes_a_level_down_inside_the_unpaired_cap(
        en, monkeypatch):
    """$2.35 unpaired: YES 0.94 + 0.92 leaves 0.49, so the third level is
    sized down to one contract (0.45) instead of dropped; NO 0.98 + 0.96
    leaves 0.41, under one contract at 0.47, so none. Never above the cap."""
    e, v, _ = en
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("2.35"))
    e.step_market(SLUG)
    rs = resting(v)
    assert ("YES", "0.45", "1") in rs
    for side in ("YES", "NO"):
        assert sum(D(px) * D(q) for s, px, q in rs if s == side) <= \
            D("2.35")
    assert all(q in ("1", "2") for _s, _p, q in rs)


def test_campaign_unmatched_cap_spans_markets_completions_still_go(
        en, monkeypatch):
    """$6 campaign unpaired, $5.40 held in two other markets: a flat market
    may rest only what fits in the $0.60 left (sized down); a holder still
    rests its completion."""
    e, v, _ = en
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("6"))
    for s in ("aec-a", "aec-b"):
        mk = e.b.m(s)
        mk["pos"]["YES"], mk["cost"]["YES"] = "6", "2.70"
        mk["orders"]["h-" + s] = {"side": "YES", "px": "0.45", "qty": "6",
                                  "filled": "6", "open": False,
                                  "stage": "rn1_open", "placed_at": 2e9}
    e.step_market(SLUG)
    assert e.rn1_campaign_exposure()["unmatched"] <= D("6")
    assert all(D(q) <= 1 for _s, _p, q in resting(v))
    e.step_market("aec-a")
    sides = {o["side"] for o in e.b.m("aec-a")["orders"].values()
             if o.get("open")}
    assert sides == {"NO"}
    assert e.rn1_campaign_exposure()["unmatched"] <= D("6")


def test_account_lane_cap_bounds_the_ladder(en, monkeypatch):
    """Shared account limits on, $1.80 account cap over two lanes = $0.90
    for this bot: its own exposure never passes the lane, whatever the
    reward."""
    from src import l5_account_risk as AR
    e, v, _ = en
    monkeypatch.setattr(M, "ACCOUNT_RISK_ON", True)
    monkeypatch.setattr(M, "ACCOUNT_RISK_TTL_S", 0)
    monkeypatch.setenv(AR.RISK_URL_ENV, "http://risk/l5/live_orders.json")
    monkeypatch.setenv(AR.ACCOUNT_CAP_ENV, "1.80")
    monkeypatch.setenv(AR.ACCOUNT_LANES_ENV, "2")
    monkeypatch.setattr(AR, "fetch_json",
                        lambda url, timeout=3.0: _risk_doc())
    e.step_market(SLUG)
    assert 0 < len(resting(v)) < 6
    assert e.own_exposure_usd() <= D("0.90")
    # the shared daily halt breached: every opening is refused
    monkeypatch.setattr(AR, "fetch_json",
                        lambda url, timeout=3.0: _risk_doc(day="-60"))
    e.step_market(SLUG)
    assert openings(e) == []


def test_at_risk_budget_bounds_the_ladder_at_placement(en, monkeypatch,
                                                      capsys):
    """Portfolio at-risk budget $3: unpaired filled cost plus every resting
    order plus the next one never passes it; the rest are refused in
    Engine.place, the one gate every order passes."""
    e, v, _ = en
    monkeypatch.setattr(M, "AT_RISK_BUDGET_USD", D("3.00"))
    e.step_market(SLUG)
    assert 0 < len(resting(v)) < 6
    assert e.own_exposure_usd() <= D("3.00")
    assert "place_refused_at_risk_budget" in kinds(capsys)
    _fill(v, "YES", "0.47")
    _cool_down(e)
    for _ in range(3):
        e.step_market(SLUG)
        assert e.own_exposure_usd() <= D("3.00")


# ------------------------------------------- 4. replenishment
def test_a_filled_level_waits_for_the_cooldown_then_is_replenished(
        en, capsys):
    e, v, _ = en
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert ("YES", "0.47", "2") not in resting(v)  # cooling down
    assert ("YES", "0.46", "2") in resting(v)      # deeper kept
    capsys.readouterr()
    _cool_down(e)
    e.step_market(SLUG)
    yes = sorted(px for sd, px, _q in resting(v) if sd == "YES")
    assert yes[-1] == "0.47"                       # replenished
    mev = quotes(capsys)[-1]["marginal_ev"]
    judged = [r for r in mev if r["side"] == "YES" and r["level"] == 0]
    assert judged and judged[0]["ok"] and judged[0]["basis"] == "no_fair"
    assert worst(e)["unmatched"] <= R.RESIDUAL_CAP_USD


def test_replenishment_is_re_priced_to_the_new_book(en):
    e, v, _ = en
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    v.bid, v.ask = D("0.46"), D("0.48")            # the market moved down
    _cool_down(e)
    e.step_market(SLUG)
    yes = sorted(px for sd, px, _q in resting(v) if sd == "YES")
    assert yes and yes[-1] == "0.45"               # one tick behind 0.46
    assert all(D(px) < D("0.46") for px in yes)


def test_replenishment_still_needs_its_reward(en, capsys):
    """After the cooldown the day's projection has fallen below the
    minimum payout: the filled level is not replenished and the other
    reward-carried bids are withdrawn; the completion stays."""
    e, v, feed = en
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    feed.projected_usd_h = 0.2                     # 0.6 over 3 h < $1
    _cool_down(e)
    e.step_market(SLUG)
    assert only_completion(e)
    assert ("YES", "0.47", "2") not in resting(v)


def test_replenishment_stops_at_the_unpaired_cap(en):
    """The best YES bid fills, pass after pass, each time replenished after
    the cooldown: held YES never passes $3, and once one more clip would,
    no YES bid is replenished at all."""
    e, v, _ = en
    full_ladder(e, v)
    mk = e.b.m(SLUG)
    for _ in range(6):
        yes = [o for o in v.orders.values() if o["open"]
               and o["side"] == "YES"]
        if not yes:
            break
        top = max(yes, key=lambda o: o["px"])
        top["filled"], top["open"] = top["qty"], False
        e.step_market(SLUG)
        _cool_down(e)
        e.step_market(SLUG)
        assert R.unmatched_usd(mk["pos"], mk["cost"]) <= R.RESIDUAL_CAP_USD
        assert worst(e)["unmatched"] <= R.RESIDUAL_CAP_USD
    held = D(str(mk["cost"]["YES"]))
    assert D(str(mk["pos"]["YES"])) >= 6 and held <= R.RESIDUAL_CAP_USD
    _cool_down(e)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []


# ------------------------------------------- 5. window and locks
def test_no_order_before_the_window_then_orders_inside_it(en, monkeypatch):
    e, v, _ = en
    setlock(monkeypatch, WIN)
    e.since = M.adopt_campaign_marker(e.b)
    at(monkeypatch, START - 5)
    e.step_market(SLUG)
    assert v.orders == {}
    at(monkeypatch, START + 5)
    e.step_market(SLUG)
    assert len(resting(v)) == 6


def test_at_the_deadline_resting_bids_are_cancelled_and_none_placed(
        en, monkeypatch):
    e, v, _ = en
    setlock(monkeypatch, WIN)
    e.since = M.adopt_campaign_marker(e.b)
    at(monkeypatch, START + 5)
    e.step_market(SLUG)
    assert len(resting(v)) == 6
    at(monkeypatch, END + 1)
    assert e.enforce_lock() == "window_ended"
    assert resting(v) == [] and open_stages(e) == []
    n = len(v.orders)
    e.step_market(SLUG)
    e.place(SLUG, "YES", D("0.40"), D("2"), "rn1_open")
    e.place(SLUG, "NO", D("0.40"), D("2"), "two_sided_complete")
    assert len(v.orders) == n


def test_completions_only_grace_keeps_only_the_maker_completion(
        en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "WINDOW_COMPLETE_GRACE_S", 1800.0)
    setlock(monkeypatch, WIN)
    e.since = M.adopt_campaign_marker(e.b)
    at(monkeypatch, START + 5)
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert any(s_ == "YES" for s_, _p, _q in resting(v))   # still adding
    at(monkeypatch, END + 60)
    assert e.enforce_lock() == "window_completions_only"
    e.step_market(SLUG)
    assert open_stages(e) == [("NO", "two_sided_complete")]
    at(monkeypatch, END + 1800)
    assert e.enforce_lock() == "window_ended"
    assert open_stages(e) == []


def test_lock_one_cancels_every_resting_bid(en, monkeypatch):
    e, v, _ = en
    full_ladder(e, v)
    setlock(monkeypatch, "1")
    assert e.enforce_lock() == "locked"
    assert resting(v) == [] and open_stages(e) == []
    n = len(v.orders)
    e.step_market(SLUG)
    assert len(v.orders) == n


# ------------------------------------------- 6. halts and watchdog
def test_safety_latch_cancels_everything_and_refuses_every_stage(en,
                                                                 capsys):
    e, v, _ = en
    full_ladder(e, v)
    e.latch("position_mismatch", slug=SLUG, book_net=0, venue_net=4)
    assert e.enforce_lock() == "safety_latch:position_mismatch"
    assert resting(v) == []
    n = len(v.orders)
    for stage in ("rn1_open", "rn1_extend", "two_sided_open",
                  "two_sided_complete", "two_sided_exit"):
        e.place(SLUG, "YES", D("0.40"), D("2"), stage)
    assert len(v.orders) == n


def test_run_loop_latches_on_a_mismatch_and_steps_nothing_more(
        en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "MISMATCH_GRACE_S", 0.0)
    monkeypatch.setattr(M, "LOOP_S", 0)
    monkeypatch.setattr(M, "REWARD_CHECK_S", 0)
    monkeypatch.setattr(M, "SHARP_MODE", "off")
    full_ladder(e, v)
    v.acct = {"positions": {SLUG: D("7")}, "open_orders": []}
    v.targets = []
    snapshot(v, [row(SLUG, 7, 0)])
    e.b.d["markets"][SLUG]["mismatch_since"] = M.time.time() - 100
    steps = []
    real = e.step_market
    monkeypatch.setattr(e, "step_market",
                        lambda s: (steps.append(s), real(s))[1])
    n = [0]

    def sleep(_):
        n[0] += 1
        if n[0] >= 4:
            e.stop = True
    monkeypatch.setattr(e, "_sleep", sleep)
    monkeypatch.setattr(e, "report", lambda: None)
    monkeypatch.setattr(e, "orphan_sweep", lambda *a, **k: None)
    e.run()
    assert e.b.d["safety_latch"]["why"] == "position_mismatch"
    assert resting(v) == []
    assert len(steps) <= 1


def _halt_store(tmp_path, monkeypatch, rec):
    import datetime as dt
    from src import l5_halt_gate as HG
    store = HG.FileStore(str(tmp_path / "halt"))
    store.put(HG.HALT_KEY, json.dumps(dict(
        rec, t=dt.datetime.now(dt.timezone.utc).isoformat(),
        who="watchdog")).encode())
    monkeypatch.setenv(M.SHARED_HALT_URL_ENV, "http://watchdog.test")
    monkeypatch.setattr(HG, "HttpHaltStore", lambda url, **k: store)
    monkeypatch.setattr(M, "SHARED_HALT_TTL_S", 0.0)
    return store


def test_operator_halt_through_the_watchdog_refuses_every_placement(
        en, tmp_path, monkeypatch):
    """The operator asserts the shared halt: openings come off on the next
    pass and nothing at all is placed, at any stage."""
    e, v, _ = en
    _halt_store(tmp_path, monkeypatch, {"halt": False})
    assert e.shared_halt() is None
    full_ladder(e, v)
    _halt_store(tmp_path, monkeypatch, {"halt": True})
    assert e.shared_halt() == "HALT"
    e.step_market(SLUG)
    assert openings(e) == []
    n = len(v.orders)
    for stage in ("rn1_open", "rn1_extend", "two_sided_complete"):
        e.place(SLUG, "NO", D("0.40"), D("2"), stage)
    assert len(v.orders) == n


def test_a_stale_watchdog_attestation_cancels_resting_openings(
        en, tmp_path, monkeypatch):
    """The watchdog stops attesting: resting openings are pulled, the held
    leg's completion stays, nothing is latched."""
    from src import l5_halt_gate as HG
    e, v, _ = en
    box = {"store": _attest(tmp_path / "a", 5)}
    monkeypatch.setenv(M.SHARED_HALT_URL_ENV, "http://watchdog.test")
    monkeypatch.setattr(HG, "HttpHaltStore", lambda url, **k: box["store"])
    monkeypatch.setattr(M, "SHARED_HALT_TTL_S", 0.0)
    monkeypatch.setattr(M, "SHARED_HALT_MAX_AGE_S", 120.0)
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    assert any(s_ == "YES" for s_, _p, _q in resting(v))   # still adding
    box["store"] = _attest(tmp_path / "b", 130)
    assert e.shared_halt() == "UNAVAILABLE"
    e.step_market(SLUG)
    assert only_completion(e)
    assert not e.b.d.get("safety_latch")


def test_the_final_gate_refuses_an_opening_past_the_unpaired_ceiling(en):
    """The last check before the venue, with the reward-carried ladder
    resting: one more opening that would pass the $3 unpaired ceiling is
    refused; a completion is not."""
    e, v, _ = en
    full_ladder(e, v)
    why = e.final_gate(SLUG, "YES", D("0.44"), D("2"), "rn1_extend")
    assert why
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.94")
    assert e.final_gate(SLUG, "NO", D("0.47"), D("2"),
                        "two_sided_complete") is None


# ------------------------------------------- 7. reward feed loss / expiry
def _withdrawn(e, v):
    e.step_market(SLUG)
    return resting(v) == [] and openings(e) == []


def test_programme_disappears_bids_priced_only_by_reward_are_withdrawn(en):
    e, v, feed = en
    full_ladder(e, v)
    feed.present = False
    feed.refresh()                                 # the engine re-reads it
    assert e.rn1_reward(SLUG) is None
    assert _withdrawn(e, v)


def test_programme_period_ends_bids_are_withdrawn(en):
    e, v, feed = en
    full_ladder(e, v)
    feed.status = "ended"
    feed.refresh()
    assert e.rn1_reward(SLUG) is None
    assert _withdrawn(e, v)


def test_projection_stops_qualifying_bids_are_withdrawn(en, capsys):
    e, v, feed = en
    full_ladder(e, v)
    feed.projected_usd_h = 0.3                     # $0.90 < $1 min payout
    assert _withdrawn(e, v)
    q = quotes(capsys)[-1]
    assert q["incentive"]["why"] == "below_minimum_payout"
    assert q["why"] == {"YES": "marginal_ev:marginal_ev_not_positive",
                        "NO": "marginal_ev:marginal_ev_not_positive"}


def test_reward_book_read_fails_bids_are_withdrawn(en, capsys):
    """The public book cannot be read once the cached copy is older than
    RN1_MARGINAL_BOOK_TTL_S: no reward is credited (book_error) and the
    reward-carried bids come off."""
    e, v, feed = en
    full_ladder(e, v)
    feed.read_fails = True
    t, book = e._mev_book[SLUG]
    e._mev_book[SLUG] = (t - M.RN1_MARGINAL_BOOK_TTL_S - 1, book)
    assert _withdrawn(e, v)
    mev = quotes(capsys)[-1]["marginal_ev"]
    assert mev and all(r["reward_usd_per_hour"] == 0 for r in mev)


def test_programme_read_fails_on_refresh_bids_are_withdrawn(en):
    """The incentives read fails at refresh: the engine keeps no programme
    (fails closed) and the bids come off."""
    e, v, feed = en
    full_ladder(e, v)
    feed.read_fails = True
    feed.refresh()
    assert e.rn1_reward(SLUG) is None
    assert _withdrawn(e, v)


def test_a_completion_survives_the_reward_feed_going_away(en):
    e, v, feed = en
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    feed.present = False
    feed.refresh()
    _cool_down(e)
    e.step_market(SLUG)
    assert only_completion(e)


def test_the_engines_own_projection_credits_nothing_from_a_standing_start(
        en, monkeypatch, capsys):
    """With the engine's REAL rn1_incentive (no projection supplied): the
    projection is the theoretical reward of what the bot already rests
    across its markets, which is nothing yet, so it is below the minimum
    payout and no reward is credited -- fail closed. (Consequence reported:
    a campaign whose only markets have no fair value never starts quoting
    in this mode unless another market's resting bids carry the
    projection.)"""
    e, v, _ = en
    monkeypatch.setattr(e, "rn1_incentive",
                        M.Engine.rn1_incentive.__get__(e))
    for _ in range(3):
        e.step_market(SLUG)
    assert resting(v) == []
    q = quotes(capsys)[-1]
    assert q["incentive"]["why"] == "below_minimum_payout"
    assert q["incentive"]["projected_usd"] == 0


@pytest.mark.xfail(strict=True, reason=(
    "SAFETY DEFECT (reported, not fixed): in RN1 mode the programme cache "
    "Engine._prog of a market already being traded is refreshed only by "
    "refresh_active_rewards (PMUS_TWO_SIDED_REWARDS on) -- select() skips "
    "tracked markets -- and Engine.rn1_reward never checks the cache's "
    "age. A programme withdrawn at the venue therefore keeps crediting "
    "reward, and keeps no-fair bids resting on it, indefinitely."))
def test_a_programme_withdrawn_at_the_venue_is_noticed_without_refresh(
        en, monkeypatch):
    e, v, feed = en
    full_ladder(e, v)
    feed.present = False                           # gone at the venue
    for s in list(e._prog):                        # ... long ago
        t, prog = e._prog[s]
        e._prog[s] = (t - 10 * M.REWARD_REFRESH_S, prog)
    e.select(v.account())                          # the loop's 20 s pass
    assert _withdrawn(e, v)


# ------------------------------------------- 8. restart and reconciliation
def test_fills_are_booked_once_across_a_restart(en, tmp_path, monkeypatch):
    e, v, _ = en
    full_ladder(e, v)
    _fill(v, "YES", "0.47")
    _fill(v, "YES", "0.46")
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    pos, cost = D(str(mk["pos"]["YES"])), D(str(mk["cost"]["YES"]))
    assert (pos, cost) == (D("4"), D("1.86"))
    # restart: a new process on the same book and the same venue
    e2 = M.Engine(v, M.Book(tmp_path / "s.json"))
    e2.guard = None
    e2._sleep = lambda s: None
    RewardFeed(e2).install(monkeypatch)
    for _ in range(3):
        e2.step_market(SLUG)
        _cool_down(e2)
    mk2 = e2.b.m(SLUG)
    assert (D(str(mk2["pos"]["YES"])), D(str(mk2["cost"]["YES"]))) == \
        (pos, cost)
    assert worst(e2)["unmatched"] <= R.RESIDUAL_CAP_USD


def test_a_safety_latch_survives_a_restart(en, tmp_path, monkeypatch):
    e, v, _ = en
    full_ladder(e, v)
    e.latch("position_mismatch", slug=SLUG)
    e.enforce_lock()
    e2 = M.Engine(v, M.Book(tmp_path / "s.json"))
    e2.guard = None
    RewardFeed(e2).install(monkeypatch)
    assert e2.enforce_lock() == "safety_latch:position_mismatch"
    n = len(v.orders)
    e2.place(SLUG, "YES", D("0.40"), D("2"), "rn1_open")
    e2.place(SLUG, "NO", D("0.40"), D("2"), "two_sided_complete")
    assert len(v.orders) == n


def test_a_fill_seen_late_latches_and_the_fill_is_still_booked(
        en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "FILL_LATE_LATCH_S", 30.0)
    full_ladder(e, v)
    base = v.status

    def status(oid):
        st = base(oid)
        st["t"] = time.time() - 89
        return st
    v.status = status
    _fill(v, "YES", "0.47")
    e.step_market(SLUG)
    lt = e.b.d["safety_latch"]
    assert lt["why"] == "fill_seen_late"
    assert D(str(e.b.m(SLUG)["pos"]["YES"])) == D("2")
    assert e.enforce_lock().startswith("safety_latch")
    assert resting(v) == []
    n = len(v.orders)
    e.step_market(SLUG)
    e.place(SLUG, "NO", D("0.40"), D("2"), "two_sided_complete")
    assert len(v.orders) == n


@pytest.fixture
def fl(f, monkeypatch):
    """tests/test_pmus_sports_fill_list.py's engine (open-order list on),
    in enabled mode with a paying programme. No fair value."""
    e, v = f
    set_enabled_flags(monkeypatch)
    e.guard = None
    e.b.m(S1)
    feed = RewardFeed(e).install(monkeypatch, [S1])
    return e, v, feed


def test_fill_list_vouches_for_reward_carried_bids_and_a_gone_one_is_read(
        fl):
    e, v, _ = fl
    oids = rest(e, v)
    assert len(oids) >= 2
    for _ in range(2):
        e._oo = None                               # a new pass, a new list
        e.step_market(S1)
    assert v.exact == [] and v.lists == 2          # the list vouched
    e._oo = None
    hit = oids[0]
    side = e.b.m(S1)["orders"][hit]["side"]
    v.orders[hit]["filled"], v.orders[hit]["open"] = D("1"), False
    e.step_market(S1)
    assert hit in v.exact                          # read exactly at once
    mk = e.b.m(S1)
    assert D(mk["pos"][side]) == D("1")
    for _ in range(3):                             # never booked again
        e._oo = None
        e.step_market(S1)
    assert D(e.b.m(S1)["pos"][side]) == D("1")


def test_a_failed_list_read_falls_back_to_exact_reads(fl):
    e, v, _ = fl
    oids = rest(e, v)
    v.list_rows = RuntimeError("list down")
    e.step_market(S1)
    assert sorted(set(v.exact)) == sorted(oids)


def test_a_fill_that_raced_the_cancel_is_booked_once(fl):
    """The reward goes away and the bids are pulled; one filled after the
    last list: its own status books it, once."""
    e, v, feed = fl
    oids = rest(e, v)
    e.step_market(S1)
    hit = oids[0]
    side = e.b.m(S1)["orders"][hit]["side"]
    v.orders[hit]["filled"] = D("1")
    feed.present = False
    feed.refresh([S1])
    e._oo = None
    e.step_market(S1)                              # withdraws the bids
    assert D(e.b.m(S1)["pos"][side]) == D("1")
    for _ in range(3):
        e._oo = None
        e.step_market(S1)
    assert D(e.b.m(S1)["pos"][side]) == D("1")
    assert only_completion(e, S1)                  # openings withdrawn


# ------------------------------------------- the profiles are documented
def test_profiles_are_theoretical_and_respect_the_minimum_payout():
    for name, p in PROFILES.items():
        proj = p["projected_usd_h"] * M.RN1_REWARD_PROJECT_H
        assert (proj >= M.RN1_REWARD_MIN_DAY_USD) == (name == "doubles")
