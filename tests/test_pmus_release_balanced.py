"""PMUS_RN1_RELEASE_BALANCED: a completed pair (YES = NO > 0) wanting no bid
frees its quoting slot like a flat market; its inventory stays booked."""
import time
from decimal import Decimal as D

from src import pmus_sports_two_sided as M
from tests.test_pmus_enabled_mode_safety import en, quotes  # noqa: F401
from tests.test_pmus_sports_continuous_controller import SLUG, resting


def _paired(e):
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": "2", "NO": "2"}
    mk["cost"] = {"YES": "0.96", "NO": "0.98"}
    return mk


def _idle_no_want(e, feed):
    feed.projected_usd_h = 0.2            # reward under the minimum: no bid
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    mk["idle_since"] = time.time() - 200
    e.step_market(SLUG)
    return mk


def test_off_a_paired_market_keeps_its_slot(en, monkeypatch):
    e, v, feed = en
    monkeypatch.setattr(M, "RN1_RELEASE_BALANCED", False)
    _paired(e)
    mk = _idle_no_want(e, feed)
    assert not mk.get("done")


def test_on_a_paired_market_with_nothing_wanted_is_released(en, monkeypatch):
    e, v, feed = en
    monkeypatch.setattr(M, "RN1_RELEASE_BALANCED", True)
    _paired(e)
    mk = _idle_no_want(e, feed)
    assert mk.get("done") and mk.get("released_flat")
    assert mk["pos"] == {"YES": "2", "NO": "2"}          # inventory kept
    assert resting(v) == []


def test_on_an_unpaired_market_is_never_released(en, monkeypatch):
    e, v, feed = en
    monkeypatch.setattr(M, "RN1_RELEASE_BALANCED", True)
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": "2", "NO": "0"}
    mk["cost"] = {"YES": "0.96", "NO": "0"}
    mk = _idle_no_want(e, feed)
    assert not mk.get("done")


def test_released_pair_keeps_settlement_limits_and_is_never_reselected(
        en, monkeypatch):
    e, v, feed = en
    monkeypatch.setattr(M, "RN1_RELEASE_BALANCED", True)
    monkeypatch.setattr(M, "RN1_RESELECT", True)
    _paired(e)
    mk = _idle_no_want(e, feed)
    assert mk.get("done")
    # settlement lookup: a done market still holding contracts, unsettled
    assert mk.get("settle_px") is None and (D(mk["pos"]["YES"]) or
                                            D(mk["pos"]["NO"]))
    # it holds no unpaired cost and is never picked up again while it
    # holds contracts (so no new openings stack on it)
    assert e.b.total_unmatched() == 0
    mk["released_flat"] = 0                         # cooldown long past
    assert not e.reselectable(mk)
    # its inventory is still in the book every meter reads
    assert SLUG in (e.b.d.get("markets") or {})
    assert e.b.d["markets"][SLUG]["cost"] == {"YES": "0.96", "NO": "0.98"}
