"""PMUS_RN1_COMPLETE_KEEP_TICKS: a resting completion bid is kept when the
new completion target is 0..k ticks above it (never dearer than the
target), instead of being cancelled and re-placed on every touch tick."""
from decimal import Decimal as D

from src import pmus_sports_two_sided as M
from tests.test_pmus_enabled_mode_safety import en  # noqa: F401
from tests.test_pmus_sports_continuous_controller import SLUG


def _completion_bids(v, e=None):
    e = e or _E[0]
    mk = e.b.m(SLUG)
    return {oid: (o["side"], D(str(o["px"])))
            for oid, o in mk["orders"].items()
            if o.get("open") and o.get("stage") == "two_sided_complete"}


_E = [None]


def _setup(e, v):
    _E[0] = e
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": "2", "NO": "0"}
    mk["cost"] = {"YES": "0.80", "NO": "0"}         # YES avg 0.40
    e.step_market(SLUG)
    return _completion_bids(v)


def _move_up_one_tick(v):
    v.bid, v.ask = D("0.48"), D("0.49")              # NO touch 0.50 -> 0.51


def _run(e, v):
    before = _completion_bids(v)
    _move_up_one_tick(v)
    e.step_market(SLUG)
    e.step_market(SLUG)
    return before, _completion_bids(v)


def test_off_a_one_tick_rise_replaces_the_completion(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_COMPLETE_KEEP_TICKS", 0)
    first = _setup(e, v)
    assert first, "a completion bid rests"
    before, after = _run(e, v)
    assert set(after) != set(before)                   # cancelled + re-placed


def test_on_a_one_tick_rise_keeps_the_resting_completion(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_COMPLETE_KEEP_TICKS", 1)
    first = _setup(e, v)
    assert first
    before, after = _run(e, v)
    assert set(after) == set(before)                   # same order, no churn
    assert all(px <= D("0.97") - D("0.40") for _s, px in after.values())


def test_on_a_lower_target_still_replaces(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_COMPLETE_KEEP_TICKS", 3)
    first = _setup(e, v)
    assert first
    v.bid, v.ask = D("0.48"), D("0.53")               # NO touch 0.47: lower
    e.step_market(SLUG)
    e.step_market(SLUG)
    after = _completion_bids(v)
    assert set(after) != set(first)
    assert all(px <= max(p for _s, p in first.values())
               for _s, px in after.values())
