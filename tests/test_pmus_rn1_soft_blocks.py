"""Market-level soft blocks vs per-side economics (Stage 1 accumulation
audit, 27 Nov): in 106 of 115 decisions with a completion pending, the
heavier side's extensions were removed by a market-level block.

* net_not_positive: with PMUS_RN1_MARGINAL_EV(=1, _NO_FAIR=1) the block is
  decided per bid -- a same-side extension that passes its own marginal EV
  rests while the market estimate is <= 0.
* max_markets: PMUS_RN1_EXTEND_THROUGH_SLOTS=1 (with
  PMUS_RN1_SLOTS_COUNT_INVENTORY=0 and marginal EV) lets a market that
  holds inventory keep extending its heavier side when every slot is held
  elsewhere; openings still wait for a slot.
* NET_HOLD flip churn: PMUS_RN1_NET_REPOST_MIN_S hysteresis.
* Slot counting (max_markets anomaly): what counts, from code.
Hard blocks still strip everything except completions.
"""
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_rewards as RW
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    SLUG, eng, resting)

OTHER = "aec-y-other"


@pytest.fixture
def sb(eng, monkeypatch):
    e, v = eng
    e.guard = None
    for name in ("RN1_TOUCH_EV", "RN1_REWARD_CALIBRATION", "RN1_MARGINAL_EV",
                 "RN1_MARGINAL_EV_NO_FAIR", "RN1_FIT_ALL_LEVELS",
                 "RN1_NET_RANK", "RN1_JOIN_TOUCH", "RN1_SIDE_INDEPENDENT",
                 "RN1_EXTEND_THROUGH_SLOTS"):
        monkeypatch.setattr(M, name, False)
    monkeypatch.setattr(M, "RN1_NET_REPOST_MIN_S", 0.0)
    monkeypatch.setattr(M, "RN1_NET_HOLD_S", 0.0)
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 1)
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setattr(M, "RN1_LIVE_QTY", D("2"))
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("3"))
    monkeypatch.setattr(R, "PER_MARKET_USD", D("8"))
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("6"))
    monkeypatch.setattr(R, "REFILL_COOLDOWN_S", 0.0)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", True)
    return e, v


def _economics_on(e, monkeypatch, reward=1.0):
    """Marginal EV with no fair value and a paying programme crediting
    `reward`/h to every bid (so the per-bid test decides)."""
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    monkeypatch.setattr(e, "intl_fair", lambda s: None)
    monkeypatch.setattr(e, "rn1_reward", lambda s: {
        "df": 0.3, "targetSize": 10, "pool_rate_usd_s": 0.01,
        "programId": "P", "maxSpread": None})
    monkeypatch.setattr(e, "rn1_incentive", lambda *a, **k: {
        "ticks": 0, "why": "qualifies"})
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda p: {"marketData": {"x": 1}}))
    monkeypatch.setattr(RW, "marginal_ladder_reward", lambda *a: {
        "incremental_reward_usd_per_hour": reward})


def _hold_yes(e, slug=SLUG, q="2", c="0.94"):
    mk = e.b.m(slug)
    mk["pos"]["YES"], mk["cost"]["YES"] = D(q), D(c)
    mk["orders"].setdefault("seed-" + slug, {
        "side": "YES", "px": str(D(c) / D(q)), "qty": q, "filled": q,
        "open": False, "stage": "rn1_open", "placed_at": time.time()})
    return mk


def _slot_holder_elsewhere(e, slug=OTHER):
    mk = e.b.m(slug)
    mk["orders"]["x1"] = {"side": "YES", "px": "0.40", "qty": "1",
                          "filled": "0", "open": True, "stage": "rn1_open",
                          "placed_at": time.time()}
    return mk


# ------------------------------------------------------- net_not_positive
def test_off_a_negative_market_estimate_strips_the_extension(sb,
                                                             monkeypatch):
    e, v = sb
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: False)
    _hold_yes(e)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    assert [r for r in resting(v) if r[0] == "NO"]        # completion only


def test_with_marginal_ev_an_extension_passing_its_own_ev_rests(
        sb, monkeypatch):
    e, v = sb
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: False)
    _economics_on(e, monkeypatch)
    _hold_yes(e)
    e.step_market(SLUG)
    assert ("YES", "0.47", "2") in resting(v)               # extension
    assert [r for r in resting(v) if r[0] == "NO"]          # completion


def test_with_marginal_ev_an_extension_failing_its_ev_does_not(
        sb, monkeypatch):
    e, v = sb
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: False)
    _economics_on(e, monkeypatch, reward=0.0)
    _hold_yes(e)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    assert [r for r in resting(v) if r[0] == "NO"]          # completion


# ---------------------------------------------------------- max_markets
@pytest.fixture
def slots(sb, monkeypatch):
    e, v = sb
    monkeypatch.setattr(M, "MAX_MARKETS", 1)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", False)
    _economics_on(e, monkeypatch)
    _slot_holder_elsewhere(e)
    return e, v


def test_off_full_slots_strip_a_held_markets_extension(slots):
    e, v = slots
    _hold_yes(e)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []
    assert [r for r in resting(v) if r[0] == "NO"]          # completion


def test_on_a_held_market_extends_its_heavier_side_through_full_slots(
        slots, monkeypatch):
    e, v = slots
    monkeypatch.setattr(M, "RN1_EXTEND_THROUGH_SLOTS", True)
    mk = _hold_yes(e)
    e.step_market(SLUG)
    assert ("YES", "0.47", "2") in resting(v)
    assert [r for r in resting(v) if r[0] == "NO"]          # completion
    stages = {o["stage"] for o in mk["orders"].values() if o.get("open")}
    assert stages == {"rn1_extend", "two_sided_complete"}
    # its extension is inventory management, not a slot
    assert not e._rn1_slot_holder(mk)
    assert e._rn1_slot_holder(e.b.m(OTHER))


def test_on_lighter_side_openings_behind_a_completion_still_wait(
        slots, monkeypatch):
    e, v = slots
    monkeypatch.setattr(M, "RN1_EXTEND_THROUGH_SLOTS", True)
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    mk = _hold_yes(e)
    e.step_market(SLUG)
    for o in mk["orders"].values():
        if o.get("open") and o["side"] == "NO":
            assert o["stage"] == "two_sided_complete"
    assert any(o.get("open") and o["side"] == "YES" and
               o["stage"] == "rn1_extend" for o in mk["orders"].values())


def test_on_a_flat_market_still_waits_for_a_slot(slots, monkeypatch):
    e, v = slots
    monkeypatch.setattr(M, "RN1_EXTEND_THROUGH_SLOTS", True)
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == []


def test_on_the_residual_cap_still_bounds_the_extension(slots, monkeypatch):
    e, v = slots
    monkeypatch.setattr(M, "RN1_EXTEND_THROUGH_SLOTS", True)
    _hold_yes(e, q="6", c="2.70")                 # one more clip > $3
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []


def test_on_a_hard_block_still_strips_the_extension(slots, monkeypatch):
    e, v = slots
    monkeypatch.setattr(M, "RN1_EXTEND_THROUGH_SLOTS", True)
    mk = _hold_yes(e)
    mk["vol_pause_until"] = time.time() + 60
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []


def test_the_flag_needs_slots_count_inventory_off(slots, monkeypatch):
    e, v = slots
    monkeypatch.setattr(M, "RN1_EXTEND_THROUGH_SLOTS", True)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", True)
    _hold_yes(e)
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "YES"] == []


# --------------------------------------------------- repost hysteresis
def test_hysteresis_waits_for_a_stable_positive_estimate(sb, monkeypatch):
    e, v = sb
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_NET_REPOST_MIN_S", 120.0)
    box = {"ok": False}
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: box["ok"])
    clock = {"t": 4_000_000.0}
    monkeypatch.setattr(M.time, "time", lambda: clock["t"])
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == []                                 # negative
    box["ok"] = True
    clock["t"] += 5
    e.step_market(SLUG)
    assert resting(v) == []                                 # waiting
    box["ok"] = False                                       # flips back
    clock["t"] += 5
    e.step_market(SLUG)
    box["ok"] = True
    clock["t"] += 100
    e.step_market(SLUG)
    assert resting(v) == []              # the wait restarted at the flip
    clock["t"] += 121
    e.step_market(SLUG)
    assert resting(v)                                       # stable: quote


def test_without_hysteresis_a_flip_reposts_at_once(sb, monkeypatch):
    e, v = sb
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    box = {"ok": False}
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: box["ok"])
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == []
    box["ok"] = True
    e.step_market(SLUG)
    assert resting(v)


def test_hysteresis_never_holds_a_completion(sb, monkeypatch):
    e, v = sb
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_NET_REPOST_MIN_S", 600.0)
    box = {"ok": False}
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, mk: box["ok"])
    _hold_yes(e)
    e.step_market(SLUG)
    box["ok"] = True
    e.step_market(SLUG)
    assert [r for r in resting(v) if r[0] == "NO"]          # completion
    assert [r for r in resting(v) if r[0] == "YES"] == []   # waiting


# ------------------------------------------------- slot counting (code)
def test_slot_counting_with_inventory_counted(sb, monkeypatch):
    """PMUS_RN1_SLOTS_COUNT_INVENTORY=1, MAX_MARKETS=2: a market may add
    only while fewer than 2 OTHER, not-done markets hold an unpaired leg
    or a resting opening/extension. A market marked done (released,
    abandoned, or closed because the venue no longer lists it) is NOT
    counted even if it still carries a leg -- the Stage 1 'passed while 4-5
    held legs' pattern is what this produces when legs sit in done
    markets. Its money still counts in every exposure limit."""
    e, v = sb
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    for i in range(3):
        _hold_yes(e, "aec-z-m%d" % i, q="1", c="0.5")
    me = e.b.m(SLUG)
    assert e.rn1_market_slots_full(SLUG, me) is True
    e.b.m("aec-z-m0")["done"] = True
    e.b.m("aec-z-m1")["done"] = True
    assert e.rn1_market_slots_full(SLUG, me) is False
    # the done markets' legs stay in the campaign exposure
    x = e.rn1_campaign_exposure()
    assert x["unmatched"] >= D("1.5")
    # the market being stepped is never counted against itself
    _hold_yes(e)
    assert e.rn1_market_slots_full(SLUG, me) is False      # only m2 counts
    e.b.m("aec-z-m0")["done"] = False
    assert e.rn1_market_slots_full(SLUG, me) is True       # m0 and m2
