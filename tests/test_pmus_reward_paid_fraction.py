"""PMUS_RN1_REWARD_MIN_RULE=paid_fraction and PMUS_RN1_FILL_RATE_PREGAME_MULT.

LIVE 27 Nov 21:56Z (9f22fd1c): every RN1_QUOTE credited reward 0
(`below_minimum_payout`, projected $0) because nothing in one market
projects $1 at a 2-contract clip, while our own reward rows show sub-$1
rows paid in 34 of 39 (competition, day) groups."""
import time

import pytest

from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

S = "aec-setkameua-plivol-kovnaz-2026-11-28"
PER = {"programId": "setka_day_of_20261120", "pool": 5, "period": "day_of"}
BY = {"PAID": {"n": 122, "usd": "24.09"}, "PENDING": {"n": 10, "usd": "0.86"},
      "SKIPPED": {"n": 65, "usd": "4.00"}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(M, "_PAID_FRACTION", {"v": None, "t": 0.0})
    monkeypatch.setattr(M, "RN1_PHASE", {})
    yield


@pytest.fixture
def e(tmp_path, monkeypatch):
    eng = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    eng.guard = None
    monkeypatch.setattr(eng, "active", lambda: [S])
    monkeypatch.setattr(eng, "rn1_reward", lambda slug: dict(PER))
    monkeypatch.setattr(M, "lock_window", lambda raw=None: None)
    eng.b.m(S)
    return eng


def test_paid_fraction_from_reward_check():
    assert M.rn1_set_paid_fraction(BY) == pytest.approx(24.09 / 28.09)
    assert M.rn1_paid_fraction() == pytest.approx(0.8576, abs=1e-4)


def test_paid_fraction_fails_closed():
    assert M.rn1_set_paid_fraction(None) is None
    assert M.rn1_paid_fraction() is None
    small = {"PAID": {"usd": "1"}, "SKIPPED": {"usd": "1"}}   # < $5 resolved
    assert M.rn1_set_paid_fraction(small) is None
    M.rn1_set_paid_fraction(BY, now=time.time() - M.RN1_PAID_FRACTION_MAX_AGE_S
                            - 1)
    assert M.rn1_paid_fraction() is None                       # stale
    assert M.rn1_set_paid_fraction({"PAID": {"usd": "x"}}) is None


@pytest.mark.parametrize("cal", [False, True])
def test_rule_off_is_unchanged(monkeypatch, cal):
    monkeypatch.setattr(M, "RN1_REWARD_CALIBRATION", cal)
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "projection")
    M.rn1_set_paid_fraction(BY)
    base = M.rn1_reward_mult(S, PER)
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "paid_fraction")
    assert M.rn1_reward_mult(S, PER) == pytest.approx(base * 24.09 / 28.09)
    M.rn1_set_paid_fraction(None)
    assert M.rn1_reward_mult(S, PER) == 0.0          # unknown: nothing


def test_default_rule_and_mult():
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k not in (
        "PMUS_RN1_REWARD_MIN_RULE", "PMUS_RN1_FILL_RATE_PREGAME_MULT")}
    out = subprocess.run([sys.executable, "-c",
                          "import src.pmus_sports_two_sided as M;"
                          "print(M.RN1_REWARD_MIN_RULE,"
                          "M.RN1_FILL_RATE_PREGAME_MULT)"],
                         env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["projection", "1.0"]


def test_incentive_qualifies_under_paid_fraction(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "paid_fraction")
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "market")
    assert e.rn1_incentive(S)["why"] == "paid_fraction_unavailable"
    M.rn1_set_paid_fraction(BY)
    inc = e.rn1_incentive(S)
    assert inc["why"] == "qualifies" and inc["ticks"] == \
        M.RN1_REWARD_CREDIT_TICKS
    assert inc["paid_fraction"] == pytest.approx(0.8576, abs=1e-4)
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "projection")
    assert e.rn1_incentive(S)["why"] == "below_minimum_payout"


def test_unified_qualification_under_paid_fraction(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "contract_period")
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "paid_fraction")
    assert not e.rn1_reward_qualifies(S, 0.01)["qualifies"]
    M.rn1_set_paid_fraction(BY)
    assert e.rn1_reward_qualifies(S, 0.01)["qualifies"]
    monkeypatch.setattr(e, "rn1_reward", lambda slug: None)
    assert not e.rn1_reward_qualifies(S, 0.01)["qualifies"]   # no pool
    monkeypatch.setattr(e, "rn1_reward", lambda slug: {"programId": "x"})
    assert not e.rn1_reward_qualifies(S, 0.01)["qualifies"]   # no period


def test_no_programme_still_credits_nothing(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "paid_fraction")
    M.rn1_set_paid_fraction(BY)
    monkeypatch.setattr(e, "rn1_reward", lambda slug: None)
    assert e.rn1_incentive(S)["ticks"] == 0


def test_pregame_fill_rate_multiplier(monkeypatch):
    monkeypatch.setattr(M, "RN1_FILL_RATE_PREGAME_MULT", 0.25)
    live = M.rn1_fill_param(M.RN1_FILL_RATE, 1, S)
    assert M.rn1_fill_param(M.RN1_FILL_RATE, 1, S) == live   # unknown
    M.RN1_PHASE[S] = "live"
    assert M.rn1_fill_param(M.RN1_FILL_RATE, 1, S) == live
    M.RN1_PHASE[S] = "pregame"
    assert M.rn1_fill_param(M.RN1_FILL_RATE, 1, S) == \
        pytest.approx(live * 0.25)
    loss = M.rn1_fill_param(M.RN1_FILL_LOSS, 1, S)
    monkeypatch.setattr(M, "RN1_FILL_RATE_PREGAME_MULT", 1.0)
    assert M.rn1_fill_param(M.RN1_FILL_LOSS, 1, S) == loss   # loss untouched
    assert M.rn1_fill_param(M.RN1_FILL_RATE, 1, S) == live


def test_event_live_records_phase(e, monkeypatch):
    monkeypatch.setattr(M, "slate", lambda: "both")
    e.b.m(S)["game_start"] = time.time() + 3600
    assert e.event_live(S) is False and M.RN1_PHASE[S] == "pregame"
    e.b.m(S)["game_start"] = time.time() - 60
    assert e.event_live(S) is True and M.RN1_PHASE[S] == "live"


# ------------------------------------------------ end to end (enabled mode)
from tests.test_pmus_enabled_mode_safety import en  # noqa: E402,F401
from tests.test_pmus_sports_continuous_controller import (  # noqa: E402
    SLUG, resting)


def test_e2e_below_projection_rests_only_under_paid_fraction(en,
                                                             monkeypatch):
    """The 21:56Z case: projection under $1, so the projection rule rests
    nothing; the paid-fraction rule credits the reward at the venue's paid
    share and the same reward-carried bids rest, every gate unchanged."""
    e, v, feed = en
    feed.projected_usd_h = 0.2
    e.step_market(SLUG)
    assert resting(v) == []
    monkeypatch.setattr(M, "RN1_REWARD_MIN_RULE", "paid_fraction")
    # the real incentive (the feed stubs the projection rule)
    monkeypatch.setattr(e, "rn1_incentive",
                        M.Engine.rn1_incentive.__get__(e))
    e.step_market(SLUG)
    assert resting(v) == []                  # paid fraction unknown: none
    M.rn1_set_paid_fraction(BY)
    e.step_market(SLUG)
    assert resting(v), "reward-carried bids must rest at the paid share"


def test_pregame_multiplier_per_competition(monkeypatch):
    monkeypatch.setattr(M, "RN1_FILL_RATE_PREGAME_MULT", 0.25)
    monkeypatch.setattr(M, "RN1_FILL_RATE_PREGAME_MULT_T",
                        {"": 0.25, "setkameua": 0.35, "atp": 0.1})
    t = "aec-atp-a-b-2026-11-28"
    for slug in (S, t, "aec-nba-x-y-2026-11-28"):
        M.RN1_PHASE[slug] = "pregame"
    live = {x: M.RN1_FILL_RATE[1].get(x.split("-")[1],
                                      M.RN1_FILL_RATE[1][""])
            for x in (S, t, "aec-nba-x-y-2026-11-28")}
    assert M.rn1_fill_param(M.RN1_FILL_RATE, 1, S) == \
        pytest.approx(live[S] * 0.35)
    assert M.rn1_fill_param(M.RN1_FILL_RATE, 1, t) == \
        pytest.approx(live[t] * 0.1)
    nba = "aec-nba-x-y-2026-11-28"
    assert M.rn1_fill_param(M.RN1_FILL_RATE, 1, nba) == \
        pytest.approx(live[nba] * 0.25)


def test_pregame_multiplier_env_parse():
    import os
    import subprocess
    import sys
    env = dict(os.environ, PMUS_RN1_FILL_RATE_PREGAME_MULT=
               "0.25,setkameua:0.35,atp:0.1,bad:x,utr:7")
    out = subprocess.run([sys.executable, "-c",
                          "import src.pmus_sports_two_sided as M;"
                          "print(sorted(M.RN1_FILL_RATE_PREGAME_MULT_T"
                          ".items()))"], env=env, capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == str(sorted({"": 0.25, "setkameua": 0.35,
                                             "atp": 0.1, "utr": 1.0}.items()))
