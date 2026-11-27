"""RN1_REWARD_QUAL_SCOPE: one reward qualification (account/day) for the
selection check, RN1_TOUCH_EV, the regular marginal-test projection and the
standing-start projection. LIVE 2026-11-27 16:02Z: TOUCH_EV scored
copmih-dumvit +$0.166/h (calibrated reward $0.146/h a bid) while the
marginal test credited $0 because the standing start projected only that
market's planned bids (~$0.88 over 3 h) against the $1 minimum."""
import time
from datetime import datetime, timezone

import pytest

from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

PER = {"programId": "p1", "pool": 10}
A, B = "aec-setkamemd-copmih-dumvit-2026-11-27", \
    "aec-setkamemd-mocdav-ghifio-2026-11-27"


@pytest.fixture
def e(tmp_path, monkeypatch):
    eng = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    eng.guard = None
    monkeypatch.setattr(eng, "active", lambda: [A, B])
    monkeypatch.setattr(eng, "rn1_reward", lambda slug: dict(PER))
    monkeypatch.setattr(M, "RN1_REWARD_CALIBRATION", True)
    monkeypatch.setattr(M, "rn1_reward_cal", lambda slug, per=None: 0.4)
    monkeypatch.setattr(M, "lock_window", lambda raw=None: None)
    monkeypatch.setattr(M, "RN1_REWARD_PROJECT_H", 3.0)
    eng.b.m(A)
    eng.b.m(B)
    return eng


def _plan(e, slug, rate, age=0.0):
    e._net_choice = getattr(e, "_net_choice", None) or {}
    e._net_choice[slug] = {"t": time.time() - age,
                           "reward_usd_per_hour": rate}


def _rest(e, slug, raw_rate, age=0.0):
    e.b.m(slug)["rn1_reward_est"] = {"t": time.time() - age,
                                     "est_usd_per_hour": raw_rate}


def test_each_market_alone_is_short_but_the_account_qualifies(e,
                                                              monkeypatch):
    """The 16:02 case: each market's calibrated planned rate 0.29/h x 3 h =
    0.87 < $1 alone; together 1.74 >= $1."""
    _plan(e, B, 0.29)
    q = e.rn1_reward_qualifies(A, extra_usd_h=0.29)
    assert q["rate_usd_per_hour"] == pytest.approx(0.58)
    assert q["projected_usd"] == pytest.approx(1.74, abs=1e-3)
    assert q["qualifies"]
    monkeypatch.setattr(e, "active", lambda: [A])
    assert not e.rn1_reward_qualifies(A, extra_usd_h=0.29)["qualifies"]


def test_resting_estimate_is_calibrated_and_preferred_over_the_plan(e):
    _rest(e, B, 1.0)                      # raw 1.0/h -> 0.4/h calibrated
    _plan(e, B, 5.0)                      # ignored: B is resting
    q = e.rn1_reward_day_projection(A, extra_usd_h=0.0)
    assert q["rate_usd_per_hour"] == pytest.approx(0.4)


def test_stale_plan_and_stale_estimate_count_nothing(e):
    _plan(e, B, 0.29, age=M.RN1_REWARD_PLAN_TTL_S + 5)
    _rest(e, B, 1.0, age=4 * M.REWARD_WALK_CHECK_S + 5)
    assert e.rn1_reward_day_projection(A)["rate_usd_per_hour"] == 0.0


def test_reward_already_accrued_today_counts_calibrated(e):
    off = M.RN1_REWARD_CAL_DAY_OFFSET_H * 3600.0
    day = datetime.fromtimestamp(time.time() + off,
                                 timezone.utc).strftime("%Y-%m-%d")
    e.b.m(A)["reward_theo"] = {day: 2.0, "2000-01-01": 50.0}
    q = e.rn1_reward_day_projection(A)
    assert q["accrued_today_usd"] == pytest.approx(0.8)
    assert e.rn1_reward_qualifies(A, extra_usd_h=0.1)["projected_usd"] >= 0.8


def test_horizon_is_capped_by_the_window_end(e, monkeypatch):
    end = time.time() + 1800
    monkeypatch.setattr(M, "lock_window", lambda raw=None: (0.0, end))
    q = e.rn1_reward_day_projection(A, extra_usd_h=1.0)
    assert q["horizon_h"] == pytest.approx(0.5, abs=0.01)
    assert q["projected_usd"] == pytest.approx(0.5, abs=0.01)


def test_incentive_uses_the_account_scope_only_when_enabled(e,
                                                            monkeypatch):
    _rest(e, A, 0.9)
    _rest(e, B, 0.0)                    # B: nothing resting, no plan
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "account_day")
    inc = e.rn1_incentive(A)
    # 0.9 x 0.4 x 3 = 1.08 >= 1
    assert inc["why"] == "qualifies" and inc["scope"] == "account_day"
    _rest(e, A, 0.5)                    # 0.6 < 1
    assert e.rn1_incentive(A)["why"] == "below_minimum_payout"


def test_touch_ev_reward_counts_only_when_the_account_qualifies(
        e, monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "account_day")
    scored = {"reward_usd_per_hour": 0.29, "net_usd_per_hour": 0.166}
    bare = {"reward_usd_per_hour": 0.0, "net_usd_per_hour": -0.126}
    out = e._touch_ev_qualified(A, scored, lambda: dict(bare))
    assert out["net_usd_per_hour"] == -0.126          # alone: not paid
    assert out["unqualified_reward_usd_per_hour"] == 0.29
    _plan(e, B, 0.29)
    out = e._touch_ev_qualified(A, scored, lambda: dict(bare))
    assert out["net_usd_per_hour"] == 0.166 and out["qualification"][
        "qualifies"]


def test_market_scope_is_exactly_as_before(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "market")
    scored = {"reward_usd_per_hour": 0.29, "net_usd_per_hour": 0.166}
    assert e._touch_ev_qualified(A, scored, lambda: {}) is scored


def test_default_scope_is_market():
    import importlib
    import os
    if os.getenv("PMUS_RN1_REWARD_QUAL_SCOPE") is None:
        assert importlib.reload(M).RN1_REWARD_QUAL_SCOPE == "market"


def test_boot_projection_is_the_account_total_when_enabled(e, monkeypatch):
    from src import pmus_sports_rewards as RW
    monkeypatch.setattr(RW, "ladder_quote_estimate",
                        lambda *a, **k: {"est_usd_per_hour": 0.725})
    planned = [{"side": "YES", "px": "0.47", "qty": "1"},
               {"side": "NO", "px": "0.50", "qty": "1"}]
    alive = {id(x) for x in planned}
    legacy = e._rn1_boot_projection({}, PER, planned, planned, alive,
                                    0.01, [], [], A)
    assert legacy == pytest.approx(0.725 * 0.4 * 3)          # 0.87 < 1
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "account_day")
    _plan(e, B, 0.29)
    acct = e._rn1_boot_projection({}, PER, planned, planned, alive,
                                  0.01, [], [], A)
    assert acct == pytest.approx((0.29 + 0.29) * 3, abs=1e-3)  # 1.74


def test_the_minimum_itself_is_unchanged():
    assert M.RN1_REWARD_MIN_DAY_USD == 1.0
