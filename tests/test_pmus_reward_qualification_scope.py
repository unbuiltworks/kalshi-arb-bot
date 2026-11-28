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

PER = {"programId": "p1", "pool": 10, "period": "live"}
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
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "account_day")
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
    """Checked in a fresh interpreter (no module reload in this process,
    which would replace state other tests hold)."""
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k != "PMUS_RN1_REWARD_QUAL_SCOPE"}
    out = subprocess.run(
        [sys.executable, "-c", "import src.pmus_sports_two_sided as M;"
         "print(M.RN1_REWARD_QUAL_SCOPE)"], env=env, capture_output=True,
        text=True, check=True)
    assert out.stdout.strip() == "market"


# ------------------------------------------------- program_period scope
def _pp(e, monkeypatch, pers):
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "program_period")
    monkeypatch.setattr(e, "rn1_reward", lambda slug: pers.get(slug))


def test_program_period_aggregates_only_the_same_pool(e, monkeypatch):
    live = {"programId": "tt_live_20261127", "period": "live"}
    other = {"programId": "tt_live_20261127", "period": "day_of"}
    _pp(e, monkeypatch, {A: live, B: other})
    _plan(e, B, 0.29)
    q = e.rn1_reward_qualifies(A, extra_usd_h=0.29)
    assert q["pool"] == "tt_live_20261127|live"
    assert q["rate_usd_per_hour"] == pytest.approx(0.29)   # B excluded
    assert not q["qualifies"]                              # 0.87 < 1
    _pp(e, monkeypatch, {A: live, B: dict(live)})
    q = e.rn1_reward_qualifies(A, extra_usd_h=0.29)
    assert q["rate_usd_per_hour"] == pytest.approx(0.58) and q["qualifies"]


def test_program_period_ignores_another_programmes_accrual(e, monkeypatch):
    live = {"programId": "tt_live_20261127", "period": "live"}
    _pp(e, monkeypatch, {A: live, B: live})
    e.b.m(B)["reward_theo_pool"] = {"tt_day_of_20261127|day_of": 50.0,
                                    "atp_live_20261127|live": 50.0}
    assert e.rn1_reward_pool_projection(A)["accrued_pool_usd"] == 0.0
    e.b.m(B)["reward_theo_pool"]["tt_live_20261127|live"] = 2.0
    assert e.rn1_reward_pool_projection(A)["accrued_pool_usd"] == \
        pytest.approx(0.8)


def test_program_period_no_paying_pool_qualifies_nothing(e, monkeypatch):
    _pp(e, monkeypatch, {A: None, B: None})
    q = e.rn1_reward_qualifies(A, extra_usd_h=100.0)
    assert q["why"] == "no_paying_pool" and not q["qualifies"]
    _pp(e, monkeypatch, {A: {"programId": "x"}, B: None})     # no period
    assert not e.rn1_reward_qualifies(A, extra_usd_h=100.0)["qualifies"]


def test_day_of_horizon_is_capped_at_the_event_start(e, monkeypatch):
    day_of = {"programId": "nba_day_of_20261127", "period": "day_of"}
    _pp(e, monkeypatch, {A: day_of, B: None})
    e.b.m(A)["game_start"] = time.time() + 1800
    q = e.rn1_reward_pool_projection(A, extra_usd_h=1.0)
    assert q["horizon_h"] == pytest.approx(0.5, abs=0.01)


def test_accrual_is_keyed_by_pool(e, monkeypatch):
    live = {"programId": "tt_live_20261127", "period": "live"}
    _pp(e, monkeypatch, {A: live, B: None})
    mk = e.b.m(A)
    mk["rn1_reward_est"] = {"t": time.time(), "est_usd_per_hour": 3.6}
    mk["reward_theo_t"] = time.time() - 100
    e._rn1_theo_accrue(A, mk, time.time())
    assert set(mk["reward_theo_pool"]) == {
        "tt_live_20261127|live", A + "|tt_live_20261127|live|"}
    assert mk["reward_theo_pool"]["tt_live_20261127|live"] == \
        pytest.approx(0.1, abs=0.002)


def test_pool_key():
    assert M.rn1_pool_key({"programId": "p", "period": "live"}) == "p|live"
    assert M.rn1_pool_key({"programId": "p"}) is None
    assert M.rn1_pool_key(None) is None


# ------------------------------------------------ contract_period scope
# The venue's payloads (data/pmus/incentives_2026-11-27/) show one
# programmeId|period spanning many contracts with the same rewardPool each:
# the pool is one contract's time period.
def _cp(e, monkeypatch, pers):
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "contract_period")
    monkeypatch.setattr(e, "rn1_reward", lambda slug: pers.get(slug))


LIVE = {"programId": "atp_wta_moneyline_live_20261018", "period": "live",
        "start": "2026-10-18T18:00:00Z"}


def test_contract_period_never_aggregates_another_match(e, monkeypatch):
    """Same programme and period on two matches: B's plan does not help A
    (program_period would have pooled them)."""
    _cp(e, monkeypatch, {A: dict(LIVE), B: dict(LIVE)})
    _plan(e, B, 0.29)
    q = e.rn1_reward_qualifies(A, extra_usd_h=0.29)
    assert q["pool"] == A + "|atp_wta_moneyline_live_20261018|live|" \
        "2026-10-18T18:00:00Z"
    assert q["rate_usd_per_hour"] == pytest.approx(0.29)
    assert q["markets"] == 0 and not q["qualifies"]        # 0.87 < 1
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "program_period")
    assert e.rn1_reward_qualifies(A, extra_usd_h=0.29)["qualifies"]


def test_contract_period_counts_only_own_accrual_in_the_period(
        e, monkeypatch):
    _cp(e, monkeypatch, {A: dict(LIVE), B: dict(LIVE)})
    own = A + "|atp_wta_moneyline_live_20261018|live|2026-10-18T18:00:00Z"
    e.b.m(B)["reward_theo_pool"] = {
        B + "|atp_wta_moneyline_live_20261018|live|2026-10-18T18:00:00Z":
        50.0, "atp_wta_moneyline_live_20261018|live": 50.0}
    e.b.m(A)["reward_theo_pool"] = {
        A + "|atp_wta_moneyline_day_of_20261018|day_of|"
        "2026-10-18T18:00:00Z": 50.0}                       # other period
    assert e.rn1_reward_pool_projection(A)["accrued_pool_usd"] == 0.0
    e.b.m(A)["reward_theo_pool"][own] = 2.0
    assert e.rn1_reward_pool_projection(A)["accrued_pool_usd"] == \
        pytest.approx(0.8)


def test_contract_period_own_rate_alone_can_qualify(e, monkeypatch):
    _cp(e, monkeypatch, {A: dict(LIVE), B: None})
    q = e.rn1_reward_qualifies(A, extra_usd_h=0.34)        # 1.02 >= 1
    assert q["qualifies"] and q["scope"] == "contract_period"
    assert not e.rn1_reward_qualifies(A, extra_usd_h=0.33)["qualifies"]


def test_contract_period_minimum_and_no_pool(e, monkeypatch):
    assert M.RN1_REWARD_MIN_DAY_USD == 1.0
    _cp(e, monkeypatch, {A: None, B: dict(LIVE)})
    q = e.rn1_reward_qualifies(A, extra_usd_h=100.0)
    assert q["why"] == "no_paying_pool" and not q["qualifies"]


def test_contract_pool_key():
    assert M.rn1_pool_key(LIVE, "s") == \
        "s|atp_wta_moneyline_live_20261018|live|2026-10-18T18:00:00Z"
    assert M.rn1_pool_key({"programId": "p"}, "s") is None


def test_contract_period_incentive_uses_it(e, monkeypatch):
    _cp(e, monkeypatch, {A: dict(LIVE), B: dict(LIVE)})
    monkeypatch.setattr(e, "_rn1_reward_estimate", lambda *a, **k: None)
    _plan(e, B, 5.0)                     # another match's large plan
    assert e.rn1_incentive(A)["why"] == "below_minimum_payout"


# --------------------------------------------- pool identity, no double count
def test_each_accrual_is_counted_once_per_scope(e, monkeypatch):
    """Both keys are stored per market, but each scope sums exactly one of
    them, and every market's accrual at most once."""
    live = {"programId": "atp_wta_moneyline_live_20261018", "period": "live",
            "start": "2026-10-18T18:00:00Z"}
    pk = "atp_wta_moneyline_live_20261018|live"
    for s_, amt in ((A, 1.0), (B, 2.0)):
        e.b.m(s_)["reward_theo_pool"] = {
            pk: amt, M.rn1_pool_key(live, s_): amt}
    _cp(e, monkeypatch, {A: dict(live), B: dict(live)})
    assert e.rn1_reward_pool_projection(A)["accrued_pool_usd"] == \
        pytest.approx(1.0 * 0.4)                   # A's own contract only
    assert e.rn1_reward_pool_projection(B)["accrued_pool_usd"] == \
        pytest.approx(2.0 * 0.4)
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "program_period")
    assert e.rn1_reward_pool_projection(A)["accrued_pool_usd"] == \
        pytest.approx(3.0 * 0.4)                   # A + B, each once


def test_contract_keys_never_collide_across_matches_or_periods():
    a = {"programId": "p", "period": "live", "start": "s"}
    keys = {M.rn1_pool_key(a, "m1"), M.rn1_pool_key(a, "m2"),
            M.rn1_pool_key(dict(a, period="day_of"), "m1"),
            M.rn1_pool_key(dict(a, start="s2"), "m1")}
    assert len(keys) == 4


def test_accrual_writes_each_key_once_per_pass(e, monkeypatch):
    live = {"programId": "tt", "period": "live", "start": "s"}
    _cp(e, monkeypatch, {A: dict(live), B: None})
    mk = e.b.m(A)
    mk["rn1_reward_est"] = {"t": time.time(), "est_usd_per_hour": 3.6}
    mk["reward_theo_t"] = time.time() - 100
    e._rn1_theo_accrue(A, mk, time.time())
    vals = list(mk["reward_theo_pool"].values())
    assert len(vals) == 2 and vals[0] == pytest.approx(vals[1])
    assert vals[0] == pytest.approx(0.1, abs=0.002)    # 3.6/h x 100 s, once
