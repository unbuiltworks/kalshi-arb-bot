"""Two executors on one account, both their orders cancellable.

The oracle read exactly one url, and the watchdog cancels nothing it
cannot claim: an id absent from `claimed()` is recorded `not_ours:
NOT_IN_OWNERSHIP_REGISTRY`, the halt asserts, and the order is LEFT
RESTING. With a second executor on the account that single url forced a
choice -- whichever registry it named, the other executor's orders
became uncancellable. Repointing it is a removal of protection from one
lane, not a configuration decision.

A union is the safe direction: ownership only ever GRANTS the power to
cancel, so a claim that fails to arrive costs a cancellation we would
have made and can never cause one we should not.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_watchdog as W
from tests import test_l5_watchdog_ownership as WO

UTC = dt.timezone.utc
MARKET = "cpc-btc-updown-5m-2026-11-16-1815z"
SPORTS = "aec-cs2-one-two-2026-11-16"


def _doc(rows, age_s=1.0):
    t = dt.datetime.now(UTC) - dt.timedelta(seconds=age_s)
    return {"readable": True, "as_of_utc": t.isoformat(), "orders": rows}


def _oracle(doc, url):
    return W.OwnershipOracle(
        url, opener=lambda u, t: json.dumps(doc, default=str).encode())


# ---- 1. existing single-url behaviour is preserved exactly -------------

def test_one_url_builds_the_same_oracle_it_always_did(monkeypatch):
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://probe:8090")
    o = W.oracle_from_env()
    assert type(o) is W.OwnershipOracle
    assert o.url == "http://probe:8090"


def test_no_url_still_disables_cancel(monkeypatch):
    monkeypatch.delenv("L5_ORDER_REGISTRY_URL", raising=False)
    assert W.oracle_from_env() is None


def test_a_trailing_comma_does_not_invent_a_source(monkeypatch):
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://probe:8090,")
    o = W.oracle_from_env()
    assert type(o) is W.OwnershipOracle


def test_whitespace_around_urls_is_tolerated(monkeypatch):
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL",
                       "  http://probe:8090 , http://lane:8082  ")
    o = W.oracle_from_env()
    assert isinstance(o, W.CompositeOracle)
    assert [x.url for x in o.oracles] == ["http://probe:8090",
                                          "http://lane:8082"]


# ---- 2. the BTC5m registry as a second source --------------------------

def test_both_executors_orders_are_claimed():
    comp = W.CompositeOracle([
        _oracle(_doc([{"venue_order_id": "SPORTS-1", "market": SPORTS}]),
                "http://probe:8090"),
        _oracle(_doc([{"venue_order_id": "BTC5M-1", "market": MARKET}]),
                "http://lane:8082")])
    claimed = comp.claimed()
    assert set(claimed) == {"SPORTS-1", "BTC5M-1"}
    assert claimed["SPORTS-1"]["market"] == SPORTS
    assert claimed["BTC5M-1"]["market"] == MARKET
    assert comp.last_refusal is None


def test_the_sports_probe_keeps_working_when_the_lane_is_down():
    #: POINT 1 OF THE OWNER'S LIST. Adding a source must not make the
    #: existing one conditional on it.
    dead = W.OwnershipOracle(
        "http://lane:8082",
        opener=lambda u, t: (_ for _ in ()).throw(OSError("refused")))
    comp = W.CompositeOracle([
        _oracle(_doc([{"venue_order_id": "SPORTS-1", "market": SPORTS}]),
                "http://probe:8090"), dead])
    claimed = comp.claimed()
    assert "SPORTS-1" in claimed
    assert "http://lane:8082" in comp.last_refusal


# ---- 3/4. exact-id cancellation, freshness, fail-closed ----------------

def _resting(vid, market, age_s=900):
    old = dt.datetime.now(UTC) - dt.timedelta(seconds=age_s)
    return {"id": vid, "marketSlug": market, "leavesQuantity": "2",
            "createdTime": old.isoformat(), "status": "OPEN",
            "manualOrderIndicator": W.AUTOMATIC}


def test_a_sweep_cancels_each_executors_order_by_exact_id(tmp_path):
    rows = [_resting("SPORTS-1", SPORTS), _resting("BTC5M-1", MARKET)]
    venue = WO.Venue(open_rows=rows, reads={r["id"]: r for r in rows})
    comp = W.CompositeOracle([
        _oracle(_doc([{"venue_order_id": "SPORTS-1", "market": SPORTS}]),
                "http://probe:8090"),
        _oracle(_doc([{"venue_order_id": "BTC5M-1", "market": MARKET}]),
                "http://lane:8082")])
    w, _store = WO.wd(tmp_path, venue, comp)
    out = w.sweep()
    assert sorted(venue.cancelled) == ["BTC5M-1", "SPORTS-1"], out
    assert out["not_ours"] == []


def test_an_unclaimed_order_is_still_left_resting(tmp_path):
    #: a union must not widen what is cancellable
    rows = [_resting("STRANGER", SPORTS)]
    venue = WO.Venue(open_rows=rows, reads={r["id"]: r for r in rows})
    comp = W.CompositeOracle([
        _oracle(_doc([{"venue_order_id": "SPORTS-1", "market": SPORTS}]),
                "http://probe:8090"),
        _oracle(_doc([{"venue_order_id": "BTC5M-1", "market": MARKET}]),
                "http://lane:8082")])
    w, _store = WO.wd(tmp_path, venue, comp)
    out = w.sweep()
    assert venue.cancelled == []
    assert out["not_ours"][0]["why"] == "NOT_IN_OWNERSHIP_REGISTRY"


def test_a_manual_order_is_never_cancellable_however_claimed(tmp_path):
    row = _resting("BTC5M-1", MARKET)
    row["manualOrderIndicator"] = "MANUAL"
    venue = WO.Venue(open_rows=[row], reads={"BTC5M-1": row})
    comp = W.CompositeOracle([
        _oracle(_doc([{"venue_order_id": "BTC5M-1", "market": MARKET}]),
                "http://lane:8082")])
    w, _store = WO.wd(tmp_path, venue, comp)
    out = w.sweep()
    assert venue.cancelled == []
    assert out["not_ours"][0]["why"] == "VENUE_SAYS_MANUAL"


def test_a_stale_source_contributes_nothing():
    comp = W.CompositeOracle([
        _oracle(_doc([{"venue_order_id": "OLD-1", "market": MARKET}],
                     age_s=3600), "http://lane:8082")])
    assert comp.claimed() == {}
    assert "CLAIM_STALE" in comp.last_refusal


def test_a_source_that_declares_itself_unreadable_contributes_nothing():
    comp = W.CompositeOracle([
        _oracle({"readable": False, "orders": [
            {"venue_order_id": "X", "market": MARKET}],
            "as_of_utc": dt.datetime.now(UTC).isoformat()},
            "http://lane:8082")])
    assert comp.claimed() == {}
    assert "REGISTRY_NOT_READABLE" in comp.last_refusal


def test_every_source_dead_is_not_read_as_owning_nothing():
    #: the dangerous misreading: {} because nothing could be read must be
    #: distinguishable from {} because we genuinely own nothing
    dead = [W.OwnershipOracle(
        u, opener=lambda a, b: (_ for _ in ()).throw(OSError("x")))
        for u in ("http://a", "http://b")]
    comp = W.CompositeOracle(dead)
    assert comp.claimed() == {}
    assert comp.last_refusal
    assert "REGISTRY_UNREACHABLE" in comp.last_refusal


def test_a_duplicate_claim_is_recorded_not_merged_silently():
    comp = W.CompositeOracle([
        _oracle(_doc([{"venue_order_id": "V1", "market": "m-a"}]),
                "http://a"),
        _oracle(_doc([{"venue_order_id": "V1", "market": "m-b"}]),
                "http://b")])
    comp.claimed()
    assert "DUPLICATE_CLAIM:V1" in comp.last_refusal


def test_an_oracle_that_raises_does_not_take_the_others_down():
    class Boom:
        url = "http://boom"
        last_refusal = None

        def claimed(self):
            raise RuntimeError("boom")

    comp = W.CompositeOracle([Boom(), _oracle(
        _doc([{"venue_order_id": "G", "market": MARKET}]), "http://good")])
    assert "G" in comp.claimed()
    assert "ORACLE_RAISED" in comp.last_refusal


def test_it_needs_at_least_one_source():
    with pytest.raises(ValueError):
        W.CompositeOracle([])


def test_cancel_stays_unavailable_without_any_oracle(tmp_path):
    #: no oracle, no cancel -- and not as a default a flag can override
    venue = WO.Venue(open_rows=[], reads={})
    w, _store = WO.wd(tmp_path, venue, None)
    assert w.cancel_enabled is False
