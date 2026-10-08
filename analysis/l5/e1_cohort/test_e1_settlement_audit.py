"""THE SETTLEMENT SCANNER: filtered, resumable, 429-aware, fail-closed.

The audit walk hit HTTP 429 after twenty pages of a 1,100-row feed, so all
three settlement questions answered UNCONFIRMED -- correctly fail-closed,
and useless. A read-only probe then established, by behaviour rather than
by assumption, that exactly one filter is honoured:

    &types=ACTIVITY_TYPE_POSITION_RESOLUTION   HONOURED
    &type= / &activityType=                    IGNORED
    &marketSlug= / &market= / &slug=           IGNORED
    &startDate= / &start= / &from=             IGNORED
    &limit=100                                 REJECTED 400

`type` and `types` differ by one character and only the plural works, which
is why nothing here was taken on trust.

THE FROZEN MODULES ARE NOT TOUCHED. The trading lifecycle is frozen at
8b729a94 / c576f741 and `order_state.py` is on the decision path, so the
scanner lives in its own module and `settlement_for` is reused unchanged.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import settlement_audit as SA

SLUG = "cpc-btc-updown-15m-2026-10-08-1215z"
OTHER = "cpc-btc-updown-15m-2026-10-08-1230z"


class Rate429(Exception):
    def __init__(self):
        super().__init__("429")
        self.code = 429


def resolution(slug, netq, cost, cash, fees=None, outcome="Yes"):
    return {"type": SA.RESOLUTION_TYPE,
            "positionResolution": {
                "marketSlug": slug,
                "beforePosition": {
                    "netPositionDecimal": netq,
                    "cost": {"value": cost, "currency": "USD"},
                    "cashValue": {"value": cash, "currency": "USD"},
                    "avgPx": {"value": "0.4900", "currency": "USD"},
                    "fees": (None if fees is None
                             else {"value": fees, "currency": "USD"}),
                    "marketMetadata": {"outcome": outcome,
                                       "slug": slug}}}}


def trade_row():
    return {"type": "ACTIVITY_TYPE_TRADE", "trade": {"id": "T1"}}


class FakeFeed:
    """Pages of resolutions, with an optional 429 schedule."""

    def __init__(self, pages, rate_limit_on=(), leak_trade_on=()):
        self.pages = pages
        self.rate_limit_on = set(rate_limit_on)
        self.leak_trade_on = set(leak_trade_on)
        self.calls = []
        self._429s = 0

    def __call__(self, path):
        self.calls.append(path)
        assert path.startswith("/v1/portfolio/activities")
        assert SA.TYPES_PARAM in path, "the scanner must send the filter"
        assert "limit=50" in path, "100 is rejected 400 by this venue"
        idx = 0
        if "&cursor=" in path:
            idx = int(path.split("&cursor=")[1].split("&")[0])
        if idx in self.rate_limit_on:
            self.rate_limit_on.discard(idx)   # fails once, then succeeds
            self._429s += 1
            raise Rate429()
        rows = list(self.pages[idx])
        if idx in self.leak_trade_on:
            rows.append(trade_row())
        last = idx >= len(self.pages) - 1
        return {"activities": rows,
                "nextCursor": "" if last else str(idx + 1),
                "eof": last}


# ------------------------------------------------------------- the filter

def test_the_scanner_sends_only_the_filter_the_venue_honours():
    feed = FakeFeed([[resolution(SLUG, "2.6000", "1.2740", "2.6")]])
    out = SA.scan_resolutions(feed, sleep_fn=lambda s: None)
    assert out["complete"] is True
    assert out["filter"] == "&types=ACTIVITY_TYPE_POSITION_RESOLUTION"
    assert out["page_size"] == 50
    assert all(SA.TYPES_PARAM in c for c in feed.calls)
    # the singular `type` is the one that does NOT work, so it is not sent
    assert not any("&type=" in c for c in feed.calls)


def test_a_non_resolution_row_means_the_filter_stopped_being_honoured():
    """A server that quietly stops filtering must not be trusted."""
    feed = FakeFeed([[resolution(SLUG, "2.6000", "1.2740", "2.6")]],
                    leak_trade_on=[0])
    out = SA.scan_resolutions(feed, sleep_fn=lambda s: None)
    assert out["filter_honoured"] is False
    assert out["filter_violation_types"] == ["ACTIVITY_TYPE_TRADE"]


def test_the_filtered_scan_is_far_shorter_than_the_full_walk():
    """92 resolutions out of 1,100 rows: about two pages, not twenty-two."""
    pages = [[resolution(SLUG, "2.6000", "1.2740", "2.6")] * 50,
             [resolution(OTHER, "0.0000", "0", "0")] * 42]
    feed = FakeFeed(pages)
    out = SA.scan_resolutions(feed, sleep_fn=lambda s: None)
    assert out["complete"] is True
    assert out["pages_read"] == 2
    assert len(out["rows"]) == 92


# ------------------------------------------------------------------- 429

def test_a_429_is_backed_off_and_the_scan_still_completes():
    pages = [[resolution(SLUG, "2.6000", "1.2740", "2.6")],
             [resolution(OTHER, "0.0000", "0", "0")]]
    slept = []
    feed = FakeFeed(pages, rate_limit_on=[1])
    out = SA.scan_resolutions(feed, sleep_fn=slept.append)
    assert out["complete"] is True
    assert out["rate_limit_hits"] == 1
    assert any(s >= 2.0 for s in slept), slept
    assert len(out["rows"]) == 2


def test_a_persistent_429_reports_incomplete_and_never_absence():
    class Always429:
        def __call__(self, path):
            raise Rate429()
    out = SA.scan_resolutions(Always429(), sleep_fn=lambda s: None)
    assert out["complete"] is False
    assert out["ABSENCE_IS_NOT_EVIDENCE"] is True
    assert out["errors"] and out["errors"][0]["code"] == 429
    assert out["rate_limit_hits"] == SA.MAX_RETRIES_PER_PAGE


def test_the_page_budget_is_bounded():
    """An audit that retries forever is a new outage."""
    pages = [[resolution(SLUG, "1", "1", "1")] for _ in range(100)]
    feed = FakeFeed(pages)
    out = SA.scan_resolutions(feed, max_pages=3, sleep_fn=lambda s: None)
    assert out["complete"] is False
    assert out["hit_page_budget"] is True
    assert out["pages_read"] == 3
    assert out["ABSENCE_IS_NOT_EVIDENCE"] is True


# ------------------------------------------------------------- resumable

def test_a_killed_scan_resumes_instead_of_restarting(tmp_path):
    pages = [[resolution(SLUG, "2.6000", "1.2740", "2.6")],
             [resolution(OTHER, "0.0000", "0", "0")],
             [resolution(SLUG, "9.9999", "1", "1")]]
    sp = str(tmp_path / "scan.json")
    # first attempt dies on page 2 (a 429 it cannot ride out)
    class DiesOnPage2:
        def __init__(self):
            self.n = 0

        def __call__(self, path):
            idx = 0
            if "&cursor=" in path:
                idx = int(path.split("&cursor=")[1].split("&")[0])
            if idx == 1:
                raise Rate429()
            last = idx >= len(pages) - 1
            return {"activities": list(pages[idx]),
                    "nextCursor": "" if last else str(idx + 1),
                    "eof": last}
    first = SA.scan_resolutions(DiesOnPage2(), state_path=sp,
                                sleep_fn=lambda s: None)
    assert first["complete"] is False
    assert first["pages_read"] == 1
    assert os.path.exists(sp)

    # the second attempt picks up from the cursor rather than page one
    feed = FakeFeed(pages)
    second = SA.scan_resolutions(feed, state_path=sp,
                                 sleep_fn=lambda s: None)
    assert second["resumed_from_page"] == 1
    assert second["complete"] is True
    assert len(second["rows"]) == 3
    assert all("&cursor=" in c for c in feed.calls), feed.calls


def test_a_completed_scan_is_served_from_durable_progress(tmp_path):
    sp = str(tmp_path / "scan.json")
    feed = FakeFeed([[resolution(SLUG, "2.6000", "1.2740", "2.6")]])
    SA.scan_resolutions(feed, state_path=sp, sleep_fn=lambda s: None)
    n = len(feed.calls)

    class MustNotBeCalled:
        def __call__(self, path):
            raise AssertionError("a completed scan must not re-walk")
    again = SA.scan_resolutions(MustNotBeCalled(), state_path=sp,
                                sleep_fn=lambda s: None)
    assert again["complete"] is True
    assert again["served_entirely_from_durable_progress"] is True
    assert len(again["rows"]) == 1
    assert n == 1


def test_a_damaged_state_file_starts_over_rather_than_crashing(tmp_path):
    sp = str(tmp_path / "scan.json")
    with open(sp, "w", encoding="utf8") as fh:
        fh.write("{not json")
    feed = FakeFeed([[resolution(SLUG, "2.6000", "1.2740", "2.6")]])
    out = SA.scan_resolutions(feed, state_path=sp, sleep_fn=lambda s: None)
    assert out["complete"] is True and len(out["rows"]) == 1


# ----------------------------------------------------- the reconciliation

def test_it_attributes_the_real_1215z_row_and_nothing_else():
    """THE DEMONSTRATION, against the real numbers: 2.6000 net, cost
    1.2740, cash 2.6000, gross +1.3260 -- with another desk's 16.21-share
    resolution in the SAME market present and refused."""
    pages = [[
        resolution(SLUG, "16.2100", "12.8000", "16.21"),   # another desk
        resolution(SLUG, "2.6000", "1.2740", "2.6"),       # OURS
        resolution(OTHER, "0.0000", "0", "0"),
    ]]
    out = SA.reconcile(FakeFeed(pages), slug=SLUG, expected_qty="2.60",
                       sleep_fn=lambda s: None)
    assert out["label"] == "SETTLED_VENUE_CONFIRMED_EXACT_QTY_MATCH"
    a = out["attributed"]
    assert a["net_position_decimal"] == "2.6000"
    assert a["cost_usd"] == "1.2740"
    assert a["cash_value_usd"] == "2.6"
    assert a["gross_net_usd"] == "1.3260"
    assert a["exact_qty_match"] is True
    # the other desk's row was SEEN and NOT attributed
    assert any(r["net_position_decimal"] == "16.2100"
               for r in out["rows"])
    assert out["NEVER_APPORTIONED"] is True
    assert out["attribution_is_the_frozen_settlement_for"] is True


def test_an_incomplete_scan_cannot_claim_the_row_is_absent():
    class Always429:
        def __call__(self, path):
            raise Rate429()
    out = SA.reconcile(Always429(), slug=SLUG, expected_qty="2.60",
                       sleep_fn=lambda s: None)
    assert out["attributed"] is None
    assert out["label"] in ("UNCONFIRMED_ACTIVITIES_UNREADABLE",
                            "UNCONFIRMED_WALK_INCOMPLETE")
    assert out["absence_is_not_evidence"] is True


def test_the_frozen_modules_are_not_imported_for_writing():
    """This module may READ order_state and must not alter it."""
    import inspect
    src = inspect.getsource(SA)
    assert "OS.settlement_for" in src
    for forbidden in ("OS.MAX_ORDER_STATE_AGE_S =", "OS.TERMINAL_STATES =",
                      "OS.ACTIVITIES_PATH =", "setattr(OS"):
        assert forbidden not in src, forbidden
