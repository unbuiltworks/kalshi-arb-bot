"""Reset precondition honours the SAME managed-lane definition as the
watchdog's age exemption (owner 2026-11-14: sports two-sided + BTC5m)."""
import datetime as dt
import json

import pytest

from scripts import l5_halt_reset as R
from src import l5_watchdog as W
from tests.test_l5_halt_reset import FakeTransport

AUTO = {"manualOrderIndicator": W.AUTOMATIC, "state": "ORDER_STATE_OPEN",
        "leavesQuantity": "1"}


def row(oid):
    return dict(AUTO, id=oid, marketSlug="aec-x")


def lane_opener(ids, age_s=1.0):
    as_of = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_s)).isoformat()
    doc = {"readable": True, "manages_own_resting": True, "as_of_utc": as_of,
           "orders": [{"venue_order_id": i} for i in ids]}
    return lambda url, t: json.dumps(doc).encode()


def test_lane_attested_order_is_allowed_and_reported():
    ids = R.managed_lane_claims(["http://sports:8090"], opener=lane_opener(["S1"]))
    out = R.venue_evidence(FakeTransport([row("S1")]), claimed={}, lane_ids=ids)
    assert out["managed_lane_orders_allowed"] == [{"id": "S1", "market": "aec-x"}]


def test_unnamed_automated_order_still_refuses():
    ids = R.managed_lane_claims(["http://sports:8090"], opener=lane_opener(["S1"]))
    with pytest.raises(R.ResetRefused, match="UNATTRIBUTED_AUTOMATED_ORDERS"):
        R.venue_evidence(FakeTransport([row("S1"), row("X9")]), claimed={}, lane_ids=ids)


def test_stale_or_unreachable_lane_attests_nothing():
    assert R.managed_lane_claims(["http://s:1"], opener=lane_opener(["S1"], age_s=600)) == set()

    def boom(url, t):
        raise OSError("down")
    assert R.managed_lane_claims(["http://s:1"], opener=boom) == set()
    with pytest.raises(R.ResetRefused, match="UNATTRIBUTED"):
        R.venue_evidence(FakeTransport([row("S1")]), claimed={}, lane_ids=set())


def test_l5_owned_still_blocks_even_if_a_lane_names_it():
    with pytest.raises(R.ResetRefused, match="L5_OWNED_ORDERS_STILL_OPEN"):
        R.venue_evidence(FakeTransport([row("S1")]), claimed={"S1": {}}, lane_ids={"S1"})


def test_no_lanes_configured_is_unchanged(monkeypatch):
    monkeypatch.delenv("L5_WATCHDOG_MANAGED_LANE_URLS", raising=False)
    assert R.managed_lane_claims() == set()
