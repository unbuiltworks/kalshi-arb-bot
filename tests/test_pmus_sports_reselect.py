"""A pause is not a reason to abandon a market; a released market can come
back.

LIVE 2026-11-24 01:20Z: all three in-play tennis markets were released
within eight minutes because price-move pauses counted as "nothing to
quote", and none was looked at again; the bot sat idle for the rest of an
authorised hour while one of them traded in band.

Owner: "A temporary pause should never permanently disqualify an otherwise
eligible market." "Make sure retaining paused markets doesn't prevent
better opportunities from being selected." "Expired, unauthorized and
settled markets cannot be reselected." "Existing inventory and exposure
accounting survive reselection."
"""
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

A = "aec-atp-a-b-2026-11-25"
B = "aec-atp-c-d-2026-11-25"
NBA = "aec-nba-e-f-2026-11-25"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("50"))
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    v.targets = [{"slug": s, "market_meta": {"bestBidQuote": "0.48",
                                             "bestAskQuote": "0.50"}}
                 for s in (A,)]
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e, v


def targets(v, *slugs):
    v.targets = [{"slug": s, "market_meta": {"bestBidQuote": "0.48",
                                             "bestAskQuote": "0.50"}}
                 for s in slugs]


def open_n(e, slug):
    return len([o for o in e.b.d["markets"][slug]["orders"].values()
                if o.get("open")])


def pause(e, slug, seconds):
    e.b.d["markets"][slug]["vol_pause_until"] = time.time() + seconds


def idle_for(e, slug, seconds):
    e.b.d["markets"][slug]["idle_since"] = time.time() - seconds


# ----------------------------------------------------------- pause holds
def test_baseline_a_two_minute_pause_releases_the_market(rig):
    e, v = rig
    assert M.RN1_PAUSED_RELEASE_S == 120 and M.RN1_RESELECT is False
    e.select(v.account())
    pause(e, A, 600)
    e.step_market(A)
    idle_for(e, A, 121)
    e.step_market(A)
    assert A not in e.active()                    # today's behaviour


def test_a_five_minute_pause_keeps_the_market_and_it_resumes(rig,
                                                             monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_PAUSED_RELEASE_S", 600.0)
    e.select(v.account())
    pause(e, A, 600)
    e.step_market(A)
    assert open_n(e, A) == 0
    idle_for(e, A, 300)                           # five minutes paused
    e.step_market(A)
    assert A in e.active()
    pause(e, A, -1)                               # conditions permit again
    e.step_market(A)
    assert open_n(e, A) == 2                      # quoting resumed
    assert "idle_since" not in e.b.d["markets"][A]


def test_a_market_that_never_calms_down_is_still_released(rig, monkeypatch):
    """Retaining a paused market must not hold a slot for ever."""
    e, v = rig
    monkeypatch.setattr(M, "RN1_PAUSED_RELEASE_S", 600.0)
    e.select(v.account())
    pause(e, A, 3600)
    e.step_market(A)
    idle_for(e, A, 601)
    e.step_market(A)
    assert A not in e.active()
    assert e.b.d["markets"][A].get("released_flat") is not None


def test_only_a_pause_gets_the_longer_hold(rig, monkeypatch):
    """A market with nothing to quote for a reason of its own (here: out
    of scope) is released after two minutes as before."""
    e, v = rig
    monkeypatch.setattr(M, "RN1_PAUSED_RELEASE_S", 600.0)
    e.select(v.account())
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    e.step_market(A)
    idle_for(e, A, 121)
    e.step_market(A)
    assert A not in e.active()


def test_a_paused_market_does_not_block_a_free_slot(rig, monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_PAUSED_RELEASE_S", 600.0)
    e.select(v.account())
    pause(e, A, 600)
    e.step_market(A)
    targets(v, A, B)
    e.select(v.account())
    assert set(e.active()) == {A, B}              # the second slot is used


# ------------------------------------------------------------ reselection
def _release(e, slug):
    pause(e, slug, 3600)
    e.step_market(slug)
    idle_for(e, slug, 100000)
    e.step_market(slug)
    assert slug not in e.active()
    pause(e, slug, -1)


def test_a_released_market_is_selected_again_when_eligible(rig, monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 60.0)
    e.select(v.account())
    _release(e, A)
    e.select(v.account())
    assert A not in e.active()                    # cooldown not over
    e.b.d["markets"][A]["released_flat"] = time.time() - 61
    e.select(v.account())
    assert A in e.active()
    e.step_market(A)
    assert open_n(e, A) == 2


def test_without_the_setting_a_released_market_stays_released(rig):
    e, v = rig
    e.select(v.account())
    _release(e, A)
    e.b.d["markets"][A]["released_flat"] = time.time() - 1000
    e.select(v.account())
    assert A not in e.active()


def test_everything_gone_then_the_controller_resumes_by_itself(rig,
                                                               monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    targets(v, A, B)
    e.select(v.account())
    assert set(e.active()) == {A, B}
    _release(e, A)
    _release(e, B)
    assert e.active() == []                       # the 24 Nov state
    targets(v)                                    # nothing listed
    e.select(v.account())
    assert e.active() == []
    targets(v, A, B)                              # opportunities return
    e.select(v.account())
    assert set(e.active()) == {A, B}
    e.step_market(A)
    assert open_n(e, A) == 2


@pytest.mark.parametrize("how", ["closed", "settled", "holding",
                                 "resting_order", "never_released"])
def test_closed_settled_or_holding_markets_are_never_reselected(
        rig, monkeypatch, how):
    e, v = rig
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    e.select(v.account())
    _release(e, A)
    mk = e.b.d["markets"][A]
    if how == "closed":
        mk["closed"] = True
    elif how == "settled":
        mk["settle_px"] = "1"
    elif how == "holding":
        mk["pos"]["YES"], mk["cost"]["YES"] = "1", "0.47"
    elif how == "resting_order":
        mk["orders"]["x"] = {"side": "YES", "px": "0.4", "qty": "1",
                             "filled": "0", "open": True,
                             "stage": "rn1_open"}
    else:
        mk.pop("released_flat")                   # done for another reason
    e.select(v.account())
    assert A not in e.active()
    assert M.Engine.reselectable(mk) is False


def test_an_unauthorized_market_is_not_reselected(rig, monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    e.select(v.account())
    _release(e, A)
    monkeypatch.setattr(M, "STRICT_SPORTS", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nba"}))
    e.select(v.account())
    assert A not in e.active()


def test_a_market_out_of_band_is_not_reselected(rig, monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    e.select(v.account())
    _release(e, A)
    v.targets = [{"slug": A, "market_meta": {"bestBidQuote": "0.90",
                                             "bestAskQuote": "0.92"}}]
    e.select(v.account())
    assert A not in e.active()


def test_a_market_gone_from_the_slate_is_not_reselected(rig, monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    e.select(v.account())
    _release(e, A)
    targets(v, B)
    e.select(v.account())
    assert A not in e.active() and B in e.active()


def test_the_record_and_its_accounting_survive_reselection(rig, monkeypatch):
    e, v = rig
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    monkeypatch.setattr(M, "RN1_RESELECT_COOLDOWN_S", 0.0)
    e.since = None
    e.select(v.account())
    e.step_market(A)
    mk = e.b.d["markets"][A]
    n_orders = len(mk["orders"])
    assert n_orders == 2
    for o in v.orders.values():                   # the bids are pulled
        o["open"] = False
    for o in mk["orders"].values():
        o["open"] = False
    mk["note"] = "kept"
    _release(e, A)
    e.select(v.account())
    assert A in e.active()
    mk2 = e.b.d["markets"][A]
    assert mk2 is mk and mk2["note"] == "kept"
    assert len(mk2["orders"]) == n_orders         # history kept
    assert mk2["reselected"] == 1
    from src import pmus_pilot_pnl as PP
    assert A in PP.pilot_slugs(e.b.d["markets"], e.since)   # same campaign
    e.step_market(A)
    assert len(mk2["orders"]) == n_orders + 2
    assert e.own_exposure_usd() == sum(
        D(str(o["px"])) * D(str(o["qty"])) for o in mk2["orders"].values()
        if o.get("open"))


def test_a_closed_market_is_marked_so(rig, monkeypatch):
    e, v = rig
    e.select(v.account())
    mk = e.b.d["markets"][A]
    mk["released_flat"] = time.time()
    mk["closed"] = True
    mk["done"] = True
    assert M.Engine.reselectable(mk) is False
