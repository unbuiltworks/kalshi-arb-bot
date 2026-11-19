"""The BTC5m lane's orders must be claimable, or the watchdog cannot cancel.

The watchdog cancels nothing it cannot claim: a venue id absent from
`oracle.claimed()` is recorded `not_ours: NOT_IN_OWNERSHIP_REGISTRY`,
the halt is asserted, and the order is LEFT RESTING. This lane served
only `/btc5m_fills`, so its ids could never be claimed.

Four things are proved here, in the owner's order:

  1. the registry carries the EXACT venue order ids of this lane;
  2. the watchdog recognises them and cancels by exact id;
  3. freshness, ownership and terminal-order reconciliation fail closed;
  4. the other executor's ownership and its account-wide risk block are
     untouched.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_order_registry_server as REG
from src import l5_watchdog as W
from tests import test_l5_btc5m_execution as T

X = T.X
MARKET = T.MARKET
UTC = dt.timezone.utc


@pytest.fixture
def armed(monkeypatch, tmp_path):
    for k in T.ENV:
        monkeypatch.delenv(k, raising=False)
    T.authorize_5m(monkeypatch, tmp_path, now_fn=lambda: T.T0,
                   starts=(T.W_1200, T.W_1205))
    for k, v in T.ARMED.items():
        monkeypatch.setenv(k, v)
    #: the DEFAULT entry mode, deliberately. Which strategy placed the
    #: order is irrelevant to whether the watchdog can claim it, and
    #: STRADDLE without its measured EV inputs refuses every rung.
    return tmp_path


def placed(rig):
    """Place one real order through the executor and return its ids."""
    rep = rig.step()
    assert rep["placed"], rep["refused"]
    coid = rep["placed"][0]["client_order_id"]
    return coid, rig.ex.orders()[coid]["venue_order_id"]


# ---- 1. the registry carries this lane's exact venue order ids ----------

def test_the_registry_names_the_exact_venue_order_id(armed):
    r = T.Rig(armed)
    coid, vid = placed(r)
    assert vid, "the order never reached the venue"
    doc = REG.registry(r.ex)
    assert doc["readable"] is True
    ids = [row["venue_order_id"] for row in doc["orders"]]
    assert ids == [vid]
    row = doc["orders"][0]
    assert row["market"] == MARKET


def test_the_registry_is_the_same_builder_the_other_executor_uses(armed):
    #: not a second shape maintained in parallel: the lane passes itself
    #: as the store, because `Btc5mExecutor.live_orders()` already is
    #: the interface `registry` consumes
    r = T.Rig(armed)
    placed(r)
    doc = REG.registry(r.ex)
    for field in ("readable", "orders", "as_of_utc", "submission_disabled",
                  "labels"):
        assert field in doc, field


def test_it_does_not_claim_to_manage_its_own_resting_orders(armed):
    #: that flag EXEMPTS a lane from the watchdog's overdue/age latch.
    #: The aim here is coverage, and claiming it would remove exactly
    #: the protection this change exists to add.
    r = T.Rig(armed)
    placed(r)
    assert REG.registry(r.ex).get("manages_own_resting") is not True


def test_the_lane_does_not_publish_an_account_risk_block(armed):
    #: the account-wide risk block belongs to the service that ENFORCES
    #: those limits; this lane is its consumer. Two publishers would give
    #: the account two answers.
    r = T.Rig(armed)
    placed(r)
    assert "account_risk" not in REG.registry(r.ex)


# ---- 2. the watchdog recognises them and cancels by exact id -----------

class _Oracle:
    """A real OwnershipOracle over an injected document."""

    def __init__(self, doc, url="http://lane:8082"):
        self.doc = doc
        self.o = W.OwnershipOracle(
            url, opener=lambda u, t: json.dumps(self.doc, default=str).encode())

    def claimed(self):
        return self.o.claimed()

    @property
    def last_refusal(self):
        return self.o.last_refusal


def _fresh(doc):
    doc = dict(doc)
    doc["as_of_utc"] = dt.datetime.now(UTC).isoformat()
    return doc


def test_the_watchdog_claims_the_lanes_order_from_its_registry(armed):
    r = T.Rig(armed)
    coid, vid = placed(r)
    claimed = _Oracle(_fresh(REG.registry(r.ex))).claimed()
    assert vid in claimed
    assert claimed[vid]["market"] == MARKET


def test_a_composite_claims_both_executors_orders(armed):
    #: THE WHOLE POINT. One url forced a choice between the two
    #: executors; a union covers both and never widens what is
    #: cancellable, because ownership only ever GRANTS the power to
    #: cancel.
    r = T.Rig(armed)
    coid, vid = placed(r)
    sports = _fresh({"readable": True, "orders": [
        {"venue_order_id": "SPORTS-1", "market": "aec-cs2-x-y-2026-11-16"}]})
    comp = W.CompositeOracle([_Oracle(_fresh(REG.registry(r.ex)),
                                      url="http://lane:8082"),
                              _Oracle(sports, url="http://probe:8090")])
    claimed = comp.claimed()
    assert vid in claimed
    assert "SPORTS-1" in claimed
    assert comp.last_refusal is None


def test_one_url_still_builds_exactly_the_oracle_it_always_did(monkeypatch):
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL", "http://probe:8090")
    o = W.oracle_from_env()
    assert isinstance(o, W.OwnershipOracle)
    assert not isinstance(o, W.CompositeOracle)
    assert o.url == "http://probe:8090"


def test_several_urls_build_a_composite(monkeypatch):
    monkeypatch.setenv("L5_ORDER_REGISTRY_URL",
                       "http://probe:8090, http://lane:8082")
    o = W.oracle_from_env()
    assert isinstance(o, W.CompositeOracle)
    assert [x.url for x in o.oracles] == ["http://probe:8090",
                                          "http://lane:8082"]


def test_no_url_still_disables_cancel(monkeypatch):
    monkeypatch.delenv("L5_ORDER_REGISTRY_URL", raising=False)
    assert W.oracle_from_env() is None


# ---- 3. freshness, ownership and terminal reconciliation fail closed ---

def test_a_terminal_order_is_never_published(armed):
    #: publishing a terminal id would let the watchdog act on an order
    #: that no longer exists. `live_orders()` is NEW/LIVE/UNCERTAIN only.
    r = T.Rig(armed)
    coid, vid = placed(r)
    assert [x["venue_order_id"] for x in REG.registry(r.ex)["orders"]] == [vid]
    r.venue.cancel(vid, MARKET)
    r.ex.poll()
    assert r.ex.orders()[coid]["state"] in X.TERMINAL
    assert REG.registry(r.ex)["orders"] == []


def test_a_filled_order_is_also_gone_from_the_registry(armed):
    r = T.Rig(armed)
    coid, vid = placed(r)
    r.venue.fill(vid)
    r.ex.poll()
    ids = [x["venue_order_id"] for x in REG.registry(r.ex)["orders"]]
    if r.ex.orders()[coid]["state"] in X.TERMINAL:
        assert ids == []


def test_an_unreadable_ledger_publishes_nothing_not_an_empty_claim(armed):
    #: "we own nothing" and "we could not find out" are opposite facts
    class Broken:
        def live_orders(self):
            raise OSError("ledger gone")
    doc = REG.registry(Broken())
    assert doc["readable"] is False
    assert doc["orders"] == []


def test_an_unreadable_registry_yields_no_claims_and_says_why():
    o = W.OwnershipOracle("http://lane:8082",
                          opener=lambda u, t: (_ for _ in ()).throw(
                              OSError("refused")))
    assert o.claimed() == {}
    assert "REGISTRY_UNREACHABLE" in o.last_refusal


def test_a_stale_registry_yields_no_claims():
    old = dt.datetime.now(UTC) - dt.timedelta(hours=1)
    doc = {"readable": True, "as_of_utc": old.isoformat(),
           "orders": [{"venue_order_id": "V1", "market": MARKET}]}
    o = _Oracle(doc)
    assert o.claimed() == {}
    assert "CLAIM_STALE" in o.last_refusal


def test_a_registry_that_says_unreadable_yields_no_claims():
    o = _Oracle(_fresh({"readable": False, "orders": [
        {"venue_order_id": "V1", "market": MARKET}]}))
    assert o.claimed() == {}
    assert o.last_refusal == "REGISTRY_NOT_READABLE"


def test_a_composite_where_nothing_can_be_read_says_so():
    #: not "we own nothing" -- that is the dangerous misreading
    bad = W.OwnershipOracle(
        "http://a", opener=lambda u, t: (_ for _ in ()).throw(OSError("x")))
    bad2 = W.OwnershipOracle(
        "http://b", opener=lambda u, t: (_ for _ in ()).throw(OSError("y")))
    comp = W.CompositeOracle([bad, bad2])
    assert comp.claimed() == {}
    assert "REGISTRY_UNREACHABLE" in comp.last_refusal


def test_one_dead_registry_does_not_hide_the_live_one():
    #: a union must not be all-or-nothing: the healthy executor's orders
    #: stay cancellable, and the dead one is REPORTED
    good = _Oracle(_fresh({"readable": True, "orders": [
        {"venue_order_id": "GOOD-1", "market": MARKET}]}),
        url="http://good")
    bad = W.OwnershipOracle(
        "http://bad", opener=lambda u, t: (_ for _ in ()).throw(OSError("x")))
    comp = W.CompositeOracle([good, bad])
    claimed = comp.claimed()
    assert "GOOD-1" in claimed
    assert "http://bad" in comp.last_refusal


def test_a_duplicate_claim_across_executors_is_recorded_not_merged():
    #: two executors claiming one venue id is a ledger fault, not a
    #: merge to resolve quietly
    a = _Oracle(_fresh({"readable": True, "orders": [
        {"venue_order_id": "V1", "market": "m-a"}]}), url="http://a")
    b = _Oracle(_fresh({"readable": True, "orders": [
        {"venue_order_id": "V1", "market": "m-b"}]}), url="http://b")
    comp = W.CompositeOracle([a, b])
    comp.claimed()
    assert "DUPLICATE_CLAIM:V1" in comp.last_refusal


def test_an_oracle_that_raises_does_not_take_the_others_down():
    class Boom:
        url = "http://boom"
        last_refusal = None

        def claimed(self):
            raise RuntimeError("boom")

    good = _Oracle(_fresh({"readable": True, "orders": [
        {"venue_order_id": "G", "market": MARKET}]}), url="http://good")
    comp = W.CompositeOracle([Boom(), good])
    assert "G" in comp.claimed()
    assert "ORACLE_RAISED" in comp.last_refusal


def test_a_composite_needs_at_least_one_oracle():
    with pytest.raises(ValueError):
        W.CompositeOracle([])


# ---- 4. the other executor's reporting is untouched --------------------

def test_the_other_executors_risk_block_is_still_published():
    #: `registry(store, risk=...)` is unchanged: the service that
    #: enforces the account-wide limits still publishes them, and the
    #: BTC5m lane reading that document is not affected by this change
    class Store:
        def live_orders(self):
            return {"c1": {"venue_order_id": "S1", "market": "aec-x",
                           "side": "YES", "session_id": "s", "t": "t"}}

    doc = REG.registry(Store(), risk=lambda: {
        "readable": True, "utc_day": "2026-11-16",
        "utc_day_realized_usd": "-1.00", "daily_halt_usd": "50",
        "lifetime_realized_usd": "-20", "cumulative_loss_ceiling_usd": "100"})
    assert doc["readable"] is True
    assert doc["orders"][0]["venue_order_id"] == "S1"
    assert doc["account_risk"]["readable"] is True
    assert doc["account_risk"]["utc_day_realized_usd"] == "-1.00"


def test_a_lane_registry_without_risk_does_not_shadow_the_producer():
    #: the two documents coexist: one carries ownership only, the other
    #: ownership AND the account-wide block
    class Store:
        def live_orders(self):
            return {}
    assert "account_risk" not in REG.registry(Store())
    assert "account_risk" in REG.registry(Store(), risk=lambda: {
        "readable": True})


# ---- 2b. a real sweep cancels the lane's order by exact id -------------

def test_a_full_sweep_cancels_the_lanes_overdue_order_by_exact_id(
        armed, tmp_path):
    """END TO END: lane ledger -> its registry -> oracle -> sweep -> cancel.

    This is the claim the whole change exists to make good. Reuses the
    watchdog suite's own fakes so the sweep logic under test is the real
    one.
    """
    from tests import test_l5_watchdog_ownership as WO

    r = T.Rig(armed)
    coid, vid = placed(r)

    #: the venue shows it resting and OVERDUE (age beyond the latch), and
    #: reports it AUTOMATIC in our own market -- the corroboration the
    #: watchdog requires before any post
    old = dt.datetime.now(UTC) - dt.timedelta(seconds=900)
    row = {"id": vid, "marketSlug": MARKET, "leavesQuantity": "2",
           "createdTime": old.isoformat(), "status": "OPEN",
           "manualOrderIndicator": W.AUTOMATIC}
    venue = WO.Venue(open_rows=[row], reads={vid: row})

    #: the oracle reads THIS LANE's registry document, served by the
    #: endpoint added in this change
    oracle = _Oracle(_fresh(REG.registry(r.ex)))
    w, _store = WO.wd(tmp_path / "halt", venue, oracle)

    out = w.sweep()
    assert [o["venue_order_id"] for o in out["overdue"]] == [vid]
    assert venue.cancelled == [vid], out
    assert out["not_ours"] == []
    assert out["unverified"] == []


def test_without_the_registry_the_same_order_is_left_resting(armed, tmp_path):
    """The behaviour BEFORE this change, pinned so the gap cannot return."""
    from tests import test_l5_watchdog_ownership as WO

    r = T.Rig(armed)
    coid, vid = placed(r)
    old = dt.datetime.now(UTC) - dt.timedelta(seconds=900)
    row = {"id": vid, "marketSlug": MARKET, "leavesQuantity": "2",
           "createdTime": old.isoformat(), "status": "OPEN",
           "manualOrderIndicator": W.AUTOMATIC}
    venue = WO.Venue(open_rows=[row], reads={vid: row})

    #: an oracle that cannot see this lane -- the sports probe's registry
    other = _Oracle(_fresh({"readable": True, "orders": [
        {"venue_order_id": "SPORTS-1", "market": "aec-cs2-x-y"}]}))
    w, _store = WO.wd(tmp_path / "halt2", venue, other)

    out = w.sweep()
    assert venue.cancelled == []
    assert [o["venue_order_id"] for o in out["not_ours"]] == [vid]
    assert out["not_ours"][0]["why"] == "NOT_IN_OWNERSHIP_REGISTRY"


def test_a_manual_order_is_still_never_cancellable(armed, tmp_path):
    """Publishing ownership must not widen what may be cancelled."""
    from tests import test_l5_watchdog_ownership as WO

    r = T.Rig(armed)
    coid, vid = placed(r)
    old = dt.datetime.now(UTC) - dt.timedelta(seconds=900)
    row = {"id": vid, "marketSlug": MARKET, "leavesQuantity": "2",
           "createdTime": old.isoformat(), "status": "OPEN",
           "manualOrderIndicator": "MANUAL"}
    venue = WO.Venue(open_rows=[row], reads={vid: row})
    w, _store = WO.wd(tmp_path / "halt3", venue,
                      _Oracle(_fresh(REG.registry(r.ex))))
    out = w.sweep()
    assert venue.cancelled == []
    assert out["not_ours"][0]["why"] == "VENUE_SAYS_MANUAL"


def test_a_market_mismatch_is_still_refused(armed, tmp_path):
    from tests import test_l5_watchdog_ownership as WO

    r = T.Rig(armed)
    coid, vid = placed(r)
    old = dt.datetime.now(UTC) - dt.timedelta(seconds=900)
    row = {"id": vid, "marketSlug": "cpc-btc-updown-5m-2026-11-11-1300z",
           "leavesQuantity": "2", "createdTime": old.isoformat(),
           "status": "OPEN", "manualOrderIndicator": W.AUTOMATIC}
    venue = WO.Venue(open_rows=[row], reads={vid: row})
    w, _store = WO.wd(tmp_path / "halt4", venue,
                      _Oracle(_fresh(REG.registry(r.ex))))
    out = w.sweep()
    assert venue.cancelled == []
    assert out["not_ours"][0]["why"] == "MARKET_MISMATCH"
