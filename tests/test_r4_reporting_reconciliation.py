"""The R4 shape: the native lane moved money and the report said zero.

2026-09-29, window `cpc-btc-updown-15m-2026-09-29-0045z`. The probe pinned
metadata, passed preflight, armed, submitted a 10-contract UP maker order
through the native lane and it filled. The venue then reported
`venue_net {"...0045z": "10.0000"}` and a drift of `ours 0 / venue 10.0000`,
and the printed report said:

    "submitted": 0, "filled": 0, "committed_usd": "0"

None of those were false about the L5-ACTION lane, which did nothing. All of
them were useless, because `report()` counted only `self.records`, which only
that lane writes. The native lane's orders lived in `mp.submitted` and were
never reported; `_post` journals nothing about a submission either, so the
durable evidence was only the mandate ledger's commit/link rows.

These tests reproduce that exact shape: a native q10 order and fill, venue
inventory 10 UP, the legacy action lane empty. The report must surface the
native order, its quantity, its fill, the cash committed and the
reconciliation state -- and must NOT be able to print a coherent zero.

Nothing here touches policy: theta, variant B, hold, residual cap, LOCK5,
floor guard, complement bias, taker completion and the q10 clip are
untouched.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as ONE
from src import l5_live_execution as EX
from src import l5_session_journal as JN

MARKET = "cpc-btc-updown-15m-2026-09-29-0045z"


class FakeRegistry:
    def as_dict(self):
        return {"filled_commitment": "4.00", "open_reserved": "0",
                "cumulative_submitted": "4.00", "exposure": "4.00"}


class FakeEngine:
    locked_at = None


class FakeMoneyPath:
    """Stands in for NativeMoneyPath after one filled q10 REST order."""

    def __init__(self):
        self.eng = FakeEngine()
        self.lock_transactions = []
        self.remainders = []
        self.registry = FakeRegistry()
        self.submitted = [{
            "kind": "REST", "side": "up", "oid": "v-r4-1",
            "ack": {"id": "v-r4-1", "filled_qty": "10.0000"},
            "body": {"marketSlug": MARKET, "outcomeSide": "OUTCOME_SIDE_YES",
                     "action": "ORDER_ACTION_BUY", "type": "ORDER_TYPE_LIMIT",
                     "price": {"value": "0.40", "currency": "USD"},
                     "quantity": "10.0000", "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
                     "participateDontInitiate": True}}]


#: the close-out block exactly as R4 printed it, trimmed to what report() reads
R4_CLOSE = {
    "reconciliation": {
        "reconciled": True,
        "drift": {"%s|UP" % MARKET: {"ours": "0", "venue": "10.0000"}},
        "venue_net": {MARKET: "10.0000"},
        "open_orders_at_venue": 0,
        "uncertainty_cleared": False,
    },
    "orders_uncertain": ("gross/net divergence ['%s']; incoherent book "
                         "['%s']" % (MARKET, MARKET)),
}

CLEAN_CLOSE = {
    "reconciliation": {"reconciled": True, "drift": {}, "venue_net": {},
                       "open_orders_at_venue": 0, "uncertainty_cleared": True},
    "orders_uncertain": None,
}


class NullFeed:
    """The report reads feed health; this window's feed is not under test."""

    def health(self):
        return {"market": MARKET, "connected": True, "frames": 8594,
                "fresh": False, "source": "AUTHENTICATED_WS"}

    def books(self):
        return []


def probe(tmp_path, *, native=None):
    p = ONE.Probe(
        market=MARKET,
        store=EX.InventoryStore(str(tmp_path / "inv.json")),
        journal=JN.Journal(str(tmp_path / "j.jsonl")),
        auth=ONE.AUTH.Authorization(str(tmp_path / "auth.jsonl")),
        transport=object(), book_feed=NullFeed())
    p.native = native
    return p


# --------------------------------------------- the native lane is surfaced

def test_the_report_surfaces_the_native_order_quantity_and_fill(tmp_path):
    rep = probe(tmp_path, native=FakeMoneyPath()).report(R4_CLOSE)
    lane = rep["native_lane"]
    assert lane["present"] is True
    assert lane["submitted"] == 1
    o = lane["orders"][0]
    assert o["order_id"] == "v-r4-1"
    assert o["side"] == "up"
    assert o["qty"] == "10.0000"
    assert o["price_wire"] == "0.40"
    assert o["ack"]["filled_qty"] == "10.0000"
    assert o["post_only"] is True


def test_the_cash_committed_by_the_native_lane_is_reported(tmp_path):
    rep = probe(tmp_path, native=FakeMoneyPath()).report(R4_CLOSE)
    exp = rep["native_lane"]["exposure"]
    assert exp["filled_commitment"] == "4.00"
    assert exp["open_reserved"] == "0"


def test_submitted_total_counts_both_lanes(tmp_path):
    """`submitted` keeps its meaning so existing evidence still reads; the
    total is the number that must never be zero while the venue holds
    inventory this session acquired."""
    rep = probe(tmp_path, native=FakeMoneyPath()).report(R4_CLOSE)
    assert rep["l5_actions"] == 0
    assert rep["submitted"] == 0              # the legacy lane really did none
    assert rep["submitted_total"] == 1


# ------------------------------------- and a zero report cannot look coherent

def test_the_exact_r4_shape_is_reported_incoherent(tmp_path):
    """Venue inventory with zero submissions across BOTH lanes. This is the
    report R4 actually printed, and it must now condemn itself."""
    rep = probe(tmp_path, native=None).report(R4_CLOSE)
    assert rep["submitted_total"] == 0
    assert rep["accounting_coherent"] is False
    why = rep["accounting_incoherence"]
    assert why["reason"] == "VENUE_POSITION_WITH_ZERO_SUBMISSIONS"
    assert why["venue_net"] == "10.0000"
    assert why["market"] == MARKET
    assert why["drift_keys"] == ["%s|UP" % MARKET]
    assert "cost basis" in why["note"]


def test_an_accounted_native_fill_is_coherent_even_with_drift(tmp_path):
    """With the native lane reported, drift is a booking problem to reconcile,
    not an unexplained position -- but uncertainty still refuses to clear."""
    rep = probe(tmp_path, native=FakeMoneyPath()).report(R4_CLOSE)
    assert rep["submitted_total"] == 1
    # uncertainty_cleared is False, so it is still not coherent -- for the
    # RIGHT reason now.
    assert rep["accounting_coherent"] is False
    assert rep["accounting_incoherence"]["reason"] == \
        "RECONCILIATION_UNCERTAIN"


def test_a_genuinely_empty_window_is_coherent(tmp_path):
    rep = probe(tmp_path, native=None).report(CLEAN_CLOSE)
    assert rep["submitted_total"] == 0
    assert rep["accounting_coherent"] is True
    assert rep["accounting_incoherence"] is None
    assert rep["native_lane"]["present"] is False


def test_nothing_is_called_realized(tmp_path):
    rep = probe(tmp_path, native=FakeMoneyPath()).report(R4_CLOSE)
    assert rep["realized"] == "NOT_REALIZED"
    assert rep["settlement"] == "NOT_OBSERVED_IN_THIS_PROCESS"


# ------------------------------- window eligibility is not a boot-time guess

def windows(*specs):
    return [{"start": s, "slug": g, "event_slug": g.replace("cpc-", ""),
             "event_closed": True, "event_active": True} for s, g in specs]


START = dt.datetime(2026, 9, 29, 0, 45, tzinfo=dt.timezone.utc)
NEXT = dt.datetime(2026, 9, 29, 1, 0, tzinfo=dt.timezone.utc)


def test_eligibility_is_decided_from_the_venue_window_state_not_boot_time():
    """R4 was steered by timing a redeploy; the redeploy then took five
    minutes instead of thirty seconds and the probe entered a window already
    94 seconds old. With the full-window rule the outcome does not depend on
    that at all."""
    rows = windows((START, "cpc-a"), (NEXT, "cpc-b"))
    at = (START + dt.timedelta(seconds=94)).timestamp()
    loose = ONE.select_venue_window(now=at, windows=rows,
                                   require_not_started=False)
    assert loose["slug"] == "cpc-a" and loose["in_progress"] is True
    strict = ONE.select_venue_window(now=at, windows=rows,
                                     require_not_started=True)
    assert strict["slug"] == "cpc-b" and strict["in_progress"] is False
    assert strict["lead_s"] == pytest.approx(806.0, abs=1)


def test_the_selection_reports_what_it_decided_and_when():
    rows = windows((START, "cpc-a"))
    at = (START - dt.timedelta(seconds=200)).timestamp()
    sel = ONE.select_venue_window(now=at, windows=rows)
    assert sel["remaining_s"] == pytest.approx(1100.0, abs=1)
    assert sel["lead_s"] == pytest.approx(200.0, abs=1)
    assert sel["decided_at_utc"].startswith("2026-09-29T00:41:40")
    assert sel["source"] == "VENUE_SERIES_LISTING"
    assert sel["require_not_started"] is False


def test_the_full_window_rule_is_read_from_the_environment(monkeypatch):
    rows = windows((START, "cpc-a"), (NEXT, "cpc-b"))
    at = (START + dt.timedelta(seconds=94)).timestamp()
    monkeypatch.setenv(ONE.FULL_WINDOW_ENV, "1")
    assert ONE.require_full_window() is True
    assert ONE.select_venue_window(now=at, windows=rows)["slug"] == "cpc-b"
    monkeypatch.setenv(ONE.FULL_WINDOW_ENV, "")
    assert ONE.require_full_window() is False
    assert ONE.select_venue_window(now=at, windows=rows)["slug"] == "cpc-a"


def test_no_eligible_window_still_refuses_rather_than_computing_one():
    rows = windows((START, "cpc-a"))
    at = (START + dt.timedelta(seconds=94)).timestamp()
    assert ONE.select_venue_window(now=at, windows=rows,
                                   require_not_started=True) == {}
