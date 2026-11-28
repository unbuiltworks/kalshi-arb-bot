"""Duration-aware ranking (RN1_DURATION_RANK, default off): a market about
to end does not take a slot from one with a long remaining life."""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_duration_rank as DR
from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    eng)

NOW = 1_800_000_000.0


# ------------------------------------------------------------------ pure
def test_default_table_and_patterns():
    t = DR.parse_table("")
    assert DR.duration_s("setkameua", t) == 40 * 60
    assert DR.duration_s("setkamecz", t) == 40 * 60
    assert DR.duration_s("atp", t) == 120 * 60
    assert DR.duration_s("wta", t) == 120 * 60
    assert DR.duration_s("atpdb", t) == 80 * 60     # exact, not "atp"
    assert DR.duration_s("wtadb", t) == 80 * 60
    assert DR.duration_s("utr", t) == 75 * 60
    assert DR.duration_s("itfwo", t) == 110 * 60
    assert DR.duration_s("itfme", t) == 110 * 60
    assert DR.duration_s("nfl", t) is None
    assert DR.duration_s("", t) is None


def test_table_override_and_longest_pattern_wins():
    t = DR.parse_table("utr:60, itfwo:90 ,setka*:30,bad:x,neg:-1,:5")
    assert DR.duration_s("utr", t) == 3600
    assert DR.duration_s("itfwo", t) == 90 * 60     # exact beats itf*
    assert DR.duration_s("itfme", t) == 110 * 60
    assert DR.duration_s("setkameua", t) == 40 * 60  # setkame* > setka*
    assert DR.duration_s("setkaxx", t) == 30 * 60
    assert "bad" not in t and "neg" not in t


@pytest.mark.parametrize("gs, state, live, rem, why", [
    (None, None, False, None, "unknown_start"),
    (NOW + 600, None, False, 600 + 75 * 60, "pregame"),
    (NOW - 600, {"type": "tennis"}, True, 75 * 60 - 600, "live"),
    (NOW - 99999, {"type": "tennis"}, True, 0.0, "live"),
    (NOW - 600, None, False, None, "start_passed_not_live"),
    (NOW - 600, {"ended": True}, True, 0.0, "ended"),
])
def test_remaining_time(gs, state, live, rem, why):
    got = DR.remaining_s("aec-utr-a-b-2026-11-26", NOW, gs, state, live,
                         DR.parse_table(""))
    assert got == (rem, why)


def test_unknown_competition_is_unknown():
    assert DR.remaining_s("aec-nfl-a-b", NOW, NOW + 60, None, False,
                          DR.parse_table("")) == (None,
                                                  "unknown_competition")


def test_factor_and_exclusion():
    assert DR.factor(7200, 3600, 0.5) == 1.0
    assert DR.factor(1800, 3600, 0.5) == 0.5
    assert DR.factor(0, 3600, 0.5) == 0.0
    assert DR.factor(None, 3600, 0.4) == 0.4      # unknown: fixed, no boost
    assert DR.excluded(600, 900) is True
    assert DR.excluded(900, 900) is False
    assert DR.excluded(None, 900) is False        # never for unknown alone
    assert DR.excluded(10, 0) is False            # 0 = no exclusion


# ------------------------------------------------------------- engine
def test_off_by_default():
    assert M.RN1_DURATION_RANK is False
    assert M.RN1_MIN_REMAINING_S == 900


def _share(e, monkeypatch, nets):
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "_reward_share_score", lambda s, per: {
        "per_dollar_hour": nets[s] / 10, "net_usd_per_hour": nets[s]})
    monkeypatch.setattr(M, "RANK_SHARE_NEW_PER_ROUND", 10)
    e._share_cache = {}                       # price every call afresh
    scored = [(0, 0, s) for s in nets]
    rw = {s: {"pool_rate_usd_s": 0.01} for s in nets}
    return [r[2] for r in e.rank_by_reward_share(scored, rw)]


def _times(e, v, starts):
    """starts: slug -> minutes since start (negative = pregame); listed
    live once started."""
    now = time.time()
    v.states = {}
    for s, m in starts.items():
        if m is None:
            continue
        e._game_start[s] = now - m * 60
        if m >= 0:
            v.states[s] = {"type": "tennis", "period": "S1"}


NEAR = "aec-setkameua-a-b-2026-11-26"      # 40 min match
LONG = "aec-atp-c-d-2026-11-26"            # 120 min match


def test_off_the_ordering_is_unchanged_and_no_estimate_is_made(
        eng, monkeypatch):
    e, v = eng
    _times(e, v, {NEAR: 30, LONG: 0})
    monkeypatch.setattr(e, "duration_info", lambda *a, **k: (_ for _ in ())
                        .throw(AssertionError("called while off")))
    assert _share(e, monkeypatch, {NEAR: 0.9, LONG: 0.5}) == [NEAR, LONG]


def test_on_a_market_about_to_end_loses_to_a_longer_lived_one(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_DURATION_RANK", True)
    # NEAR: 10 of 40 min left -> factor 1/6: 0.9/6 = 0.15 < 0.5
    _times(e, v, {NEAR: 30, LONG: 0})
    assert _share(e, monkeypatch, {NEAR: 0.9, LONG: 0.5}) == [LONG, NEAR]
    # both with a full horizon ahead: the better estimate wins again
    _times(e, v, {NEAR: -60, LONG: -60})
    assert _share(e, monkeypatch, {NEAR: 0.9, LONG: 0.5}) == [NEAR, LONG]


def test_on_unknown_start_gets_the_fixed_factor_not_exclusion(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_DURATION_RANK", True)
    _times(e, v, {NEAR: None, LONG: 0})
    d = e.duration_info(NEAR)
    assert d["remaining_s"] is None and d["why"] == "unknown_start"
    assert d["factor"] == 0.5 and d["excluded"] is False
    # 0.9 x 0.5 = 0.45 < 0.5 x 1.0
    assert _share(e, monkeypatch, {NEAR: 0.9, LONG: 0.5}) == [LONG, NEAR]
    # still ranked above a full-life market with a much smaller estimate
    assert _share(e, monkeypatch, {NEAR: 0.9, LONG: 0.3}) == [NEAR, LONG]


def test_on_non_positive_estimates_stay_excluded(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_DURATION_RANK", True)
    _times(e, v, {NEAR: -60, LONG: 0})
    assert _share(e, monkeypatch, {NEAR: -0.2, LONG: 0.5}) == [LONG]


def test_per_contract_ranking_counts_reward_only_for_the_remaining_time(
        eng, monkeypatch):
    e, v = eng
    nets = {NEAR: (D("0.01"), D("0.06")), LONG: (D("0.01"), D("0.04"))}

    def opp(slug, _r):
        t, rw = nets[slug]
        return {"net": t + rw, "trade": t, "reward": rw, "why": None}
    monkeypatch.setattr(e, "_opportunity", opp)
    monkeypatch.setattr(M, "RANK_NEW_PER_ROUND", 10)
    _times(e, v, {NEAR: 30, LONG: 0})
    scored = [(D("0.02"), D("0.01"), s) for s in (NEAR, LONG)]
    assert [r[2] for r in e.rank_net(scored)] == [NEAR, LONG]       # off
    monkeypatch.setattr(M, "RN1_DURATION_RANK", True)
    # NEAR: 0.01 + 0.06/6 = 0.02 < LONG 0.01 + 0.04
    assert [r[2] for r in e.rank_net(scored)] == [LONG, NEAR]
    # the cached estimates are not modified
    assert e._rank_cache[NEAR][1]["net"] == D("0.07")


def _pin(e, v, monkeypatch, slug):
    e.guard = None
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setenv("PMUS_RN1_PIN", slug + ":YES")


def test_on_a_known_short_remaining_market_is_not_a_candidate(
        eng, monkeypatch, capsys):
    e, v = eng
    _pin(e, v, monkeypatch, "aec-owner")
    monkeypatch.setattr(M, "RN1_DURATION_RANK", True)
    monkeypatch.setattr(M, "RN1_DURATION_MIN", "owner:20")
    e._game_start["aec-owner"] = time.time() - 10 * 60     # 10 min left
    v.states = {"aec-owner": {"type": "tennis", "period": "S1"}}
    e.select(v.account())
    assert list(e.active()) == []
    out = capsys.readouterr().out
    rows = [json.loads(x[len("PMUS_TWO_SIDED "):]) for x in out.splitlines()
            if x.startswith("PMUS_TWO_SIDED ")]
    why = [r for r in rows if r["kind"] == "PIN_NOT_CANDIDATE"
           ][-1]["reasons"]["aec-owner"]
    assert why.startswith("remaining_below_min:")
    row = [r for r in rows if r["kind"] == "RN1_DURATION_RANK"][-1]
    assert row["excluded"] == 1 and row["first"][0]["slug"] == "aec-owner"


def test_on_an_unknown_competition_is_still_selected(eng, monkeypatch):
    e, v = eng
    _pin(e, v, monkeypatch, "aec-owner")
    monkeypatch.setattr(M, "RN1_DURATION_RANK", True)
    e.select(v.account())
    assert list(e.active()) == ["aec-owner"]


def test_off_the_same_market_is_selected(eng, monkeypatch):
    e, v = eng
    _pin(e, v, monkeypatch, "aec-owner")
    monkeypatch.setattr(M, "RN1_DURATION_MIN", "owner:20")
    e._game_start["aec-owner"] = time.time() - 10 * 60
    v.states = {"aec-owner": {"type": "tennis", "period": "S1"}}
    e.select(v.account())
    assert list(e.active()) == ["aec-owner"]


def _limits(env):
    import os
    import subprocess
    import sys
    code = ("from src import pmus_sports_two_sided as M, "
            "pmus_rn1_replica as R; print(repr((M.MAX_MARKETS, M.TOTAL_CAP,"
            " R.PER_MARKET_USD, R.TOTAL_SPEND_USD, R.RESIDUAL_CAP_USD, "
            "R.TOTAL_UNMATCHED_USD, M.RN1_DURATION_RANK)))")
    full = {k: v for k, v in os.environ.items()
            if not k.startswith("PMUS_RN1_DURATION")
            and k != "PMUS_RN1_MIN_REMAINING_S"}
    full.update(env)
    return subprocess.run([sys.executable, "-c", code], env=full,
                          capture_output=True, text=True, check=True,
                          cwd=os.path.dirname(os.path.dirname(__file__))
                          ).stdout.strip()


def test_caps_and_defaults_unchanged_by_the_flag():
    off, on = _limits({}), _limits({"PMUS_RN1_DURATION_RANK": "1"})
    assert off.endswith("False)") and on.endswith("True)")
    assert off[:-len("False)")] == on[:-len("True)")]
