"""Feasible reward ladder (account_day): a bid refused on its own economics
never influences the reward estimate or inventory of a surviving bid.
Covers the standing-start fixed point (dev/chatgpt-feasible-reward-ladder,
3981feabf) and the same rule on the regular path."""
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

SLUG = "aec-setkamemd-copmih-dumvit-2026-11-27"
PER = {"programId": "p1"}


@pytest.fixture
def e(tmp_path, monkeypatch):
    eng = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    eng.guard = None
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "account_day")
    monkeypatch.setattr(eng, "rn1_reward", lambda slug: dict(PER))
    monkeypatch.setattr(eng, "_reward_get",
                        lambda: (lambda path: {"marketData": {}}))
    monkeypatch.setattr(eng, "_rn1_visible_own", lambda *a, **k: ([], []))
    monkeypatch.setattr(eng, "_rn1_mature_own", lambda *a, **k: [])
    eng.b.m(SLUG)
    return eng


def _cands():
    return [{"side": "YES", "px": D("0.47"), "qty": D(1), "role": "open",
             "level": 0},
            {"side": "YES", "px": D("0.45"), "qty": D(1), "role": "open",
             "level": 1},
            {"side": "NO", "px": D("0.50"), "qty": D(1), "role": "open",
             "level": 0}]


def _spy_judge(e, monkeypatch, refuse_px):
    calls = []

    def judge(slug, mk, cands, planned, *a):
        calls.append(([str(c["px"]) for c in cands],
                      [str(x["px"]) for x in planned]))
        return [{"ok": str(c["px"]) != refuse_px, "why": None if
                 str(c["px"]) != refuse_px else "marginal_ev_not_positive",
                 "ev_usd_per_hour": 0.1 if str(c["px"]) != refuse_px
                 else -0.07, "reward_usd_per_hour": 0.05, "basis": "no_fair",
                 "side": c["side"], "level": c.get("level", 0),
                 "reward_why": None} for c in cands]
    monkeypatch.setattr(e, "_rn1_judge", judge)
    return calls


def _run(e, cands):
    mk = e.b.m(SLUG)
    bbo = {"bid": D("0.48"), "ask": D("0.49")}
    return e.rn1_marginal_ev(SLUG, mk, bbo, D("0.01"), {}, cands,
                             list(cands))


def test_regular_path_survivors_are_judged_without_the_refused_bid(
        e, monkeypatch):
    e._rn1_inc = {SLUG: {"why": "qualifies", "projected_usd": 2.0}}
    calls = _spy_judge(e, monkeypatch, "0.45")
    out = _run(e, _cands())
    assert len(calls) == 2
    assert "0.45" in calls[0][0] and "0.45" in calls[0][1]
    # the final judgement of the survivors excludes the refused bid
    assert "0.45" not in calls[-1][0] and "0.45" not in calls[-1][1]
    oks = {str(c["px"]): r["ok"] for c, r in zip(_cands(), out)}
    assert oks == {"0.47": True, "0.45": False, "0.50": True}
    # the refused bid keeps its own figures (not a placeholder)
    assert out[1]["ev_usd_per_hour"] == -0.07 and out[1]["basis"] == \
        "no_fair"


def test_standing_start_survivors_are_judged_without_the_refused_bid(
        e, monkeypatch):
    e._rn1_inc = {SLUG: {"why": "below_minimum_payout",
                         "projected_usd": 0.0}}
    monkeypatch.setattr(e, "_rn1_boot_projection",
                        lambda *a, **k: 5.0)
    calls = _spy_judge(e, monkeypatch, "0.45")
    out = _run(e, _cands())
    assert "0.45" not in calls[-1][0] and "0.45" not in calls[-1][1]
    assert [r["ok"] for r in out] == [True, False, True]


def test_market_scope_is_a_single_judgement_of_everything(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_REWARD_QUAL_SCOPE", "market")
    e._rn1_inc = {SLUG: {"why": "qualifies", "projected_usd": 2.0}}
    calls = _spy_judge(e, monkeypatch, "0.45")
    _run(e, _cands())
    assert len(calls) == 1 and "0.45" in calls[0][1]


def test_rejected_result_has_every_key_the_caps_priority_reads():
    r = M.Engine._rn1_rejected(_cands()[1])
    for k in ("ok", "why", "ev_usd_per_hour", "reward_usd_per_hour",
              "reward_why", "side", "level"):
        assert k in r
    assert r["ok"] is False and r["reward_usd_per_hour"] == 0.0
