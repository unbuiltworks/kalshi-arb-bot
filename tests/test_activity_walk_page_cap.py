"""A walk that stopped on the PAGE CAP has not proved a market unsettled.

LIVE 2026-09-12T00:39:48Z. The 429s were gone and the shared walk completed
cleanly -- `truncated_by_error` false, `errors` empty, zero AUDIT_UNAVAILABLE
-- and the preflight still blocked, with two wrong verdicts built on a feed
that had simply not been read far enough back:

  activity_walk: {"since": "2026-09-07T19:25:04Z", "pages": 25, "rows": 500,
                  "ended": "max_pages", "truncated_by_error": false}
  21 markets PENDING_SETTLEMENT (ledger 27.22, 42.56, 33.4, ...)
  ledger_vs_venue_qty_mismatch: skaser-abaspa venue 6.1600 vs ledger 16.20

ACTIVITY_MAX_PAGES = 25 capped the walk at 500 rows reaching back only to
2026-09-08T03:11Z. Every market filled before that had no trade row and no
POSITION_RESOLUTION row in the snapshot -- not because none exists, but
because the walk stopped. Same rule as the error case: "we could not finish
asking" is never "the answer was no".
"""
from decimal import Decimal as D

import pytest

from src import pmus_settlement_auditor as auditor


SINCE = "2026-09-06T00:00:00Z"


def _walk(**kw):
    base = {"rows": [], "pages": 25, "ended": "max_pages",
            "cursor_param": "cursor", "errors": [],
            "complete_to_since": False, "since": SINCE,
            "truncated_by_error": False, "truncated_by_page_cap": True,
            "oldest_time": "2026-09-08T03:11:02Z",
            "newest_time": "2026-09-11T16:16:28Z"}
    base.update(kw)
    return base


# ------------------------------------------------------------- walk_covers
def test_a_page_capped_walk_does_not_cover_an_older_market():
    assert auditor.walk_covers(_walk(), SINCE) is False


def test_a_page_capped_walk_does_cover_a_market_inside_its_reach():
    """The cap is not a blanket verdict: rows back to 09-08T03:11 really do
    settle a market whose first fill was 09-09."""
    assert auditor.walk_covers(_walk(), "2026-09-09T12:00:00Z") is True


def test_a_walk_that_reached_the_requested_window_covers_everything():
    assert auditor.walk_covers(
        _walk(ended="older_than_since", complete_to_since=True,
              truncated_by_page_cap=False), SINCE) is True


def test_a_walk_that_reached_the_end_of_the_feed_covers_everything():
    for ending in ("eof", "no_cursor", "empty_page"):
        assert auditor.walk_covers(
            _walk(ended=ending, complete_to_since=True,
                  truncated_by_page_cap=False, oldest_time=None), SINCE) is True


def test_an_error_truncated_walk_never_covers_anything():
    assert auditor.walk_covers(
        _walk(ended="eof", complete_to_since=True,
              truncated_by_error=True, errors=["HTTPError:429"]), SINCE) is False


def test_a_market_with_no_recorded_first_fill_needs_a_finished_feed():
    """Without a `since` there is no reach to compare against, so only a walk
    that ran the feed out can say 'there is nothing here'."""
    assert auditor.walk_covers(_walk(), None) is False
    assert auditor.walk_covers(
        _walk(ended="eof", complete_to_since=True,
              truncated_by_page_cap=False), None) is True


def test_a_walk_from_before_these_keys_existed_is_treated_as_complete():
    """Every existing caller already assumed this; do not break them."""
    assert auditor.walk_covers({"rows": [], "ended": "eof"}, SINCE) is True


# --------------------------------------------------------------- the verdict
@pytest.fixture
def _flatless(monkeypatch):
    """Public market resolved, but nothing in the walk and no position row --
    the exact shape of the 21 markets."""
    monkeypatch.setattr(auditor, "first_fill_time", lambda slug: SINCE)
    monkeypatch.setattr(auditor, "_position", lambda t, slug: None)
    monkeypatch.setattr(auditor, "_available_position", lambda t, slug: None)
    monkeypatch.setattr(auditor, "ledger_qty_total", lambda slug: "27.22")
    monkeypatch.setattr(auditor, "public_market_view", lambda slug, t: {
        "public_resolved": True, "settlement_px": "0.0000",
        "status": "MARKET_STATUS_RESOLVED", "state": "MARKET_STATE_EXPIRED"})


def test_a_short_walk_raises_instead_of_reporting_pending_settlement(_flatless):
    with pytest.raises(RuntimeError) as e:
        auditor.audit_market("aec-atp-artgea-botzan-2026-09-06",
                             t=object(), walk=_walk())
    assert "activity_walk_short_of_since" in str(e.value)
    assert "max_pages" in str(e.value)


def test_the_same_market_reports_pending_when_the_walk_did_reach_back(_flatless):
    """Coverage is not permission either: a complete walk that finds nothing
    still blocks, it just blocks on a fact."""
    got = auditor.audit_market(
        "aec-atp-artgea-botzan-2026-09-06", t=object(),
        walk=_walk(ended="older_than_since", complete_to_since=True,
                   truncated_by_page_cap=False))
    assert got["status"] == "PENDING_SETTLEMENT"
    assert got["settled"] is False
    assert got["activity_walk"]["covers_market"] is True


# --------------------------------------------- the false ledger divergence
def _resolved_walk(**kw):
    """A POSITION_RESOLUTION row IS present, so settlement is confirmed --
    but the walk is still short, so the TRADE rows behind venue_qty are not
    all there. That is skaser-abaspa: venue 6.1600 vs ledger 16.20."""
    rows = [{"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
             "positionResolution": {"marketSlug": "m", "tradeId": "t1",
                                    "updateTime": "2026-09-09T00:00:00Z",
                                    "afterPosition": {"realized": {"value": "-2.1224"}}}}]
    return _walk(rows=rows, **kw)


@pytest.fixture
def _resolved(monkeypatch):
    monkeypatch.setattr(auditor, "first_fill_time", lambda slug: SINCE)
    monkeypatch.setattr(auditor, "_position", lambda t, slug: None)
    monkeypatch.setattr(auditor, "_available_position", lambda t, slug: None)
    monkeypatch.setattr(auditor, "ledger_qty_total", lambda slug: "16.20")
    monkeypatch.setattr(auditor, "venue_qty_total", lambda info: "6.1600")
    monkeypatch.setattr(auditor, "_resolution_for", lambda rows, slug: {
        "tradeId": "t1", "updateTime": "2026-09-09T00:00:00Z",
        "afterPosition": {"realized": {"value": "-2.1224"}}})


def test_a_short_walk_reports_venue_quantity_as_unknown_not_as_a_divergence(
        _resolved):
    got = auditor.audit_market("m", t=object(), walk=_resolved_walk())
    assert got["settled"] is True, "the resolution row is real; settlement stands"
    assert got["venue_qty_total"] is None, "we did not read all its trades"
    assert "short of this market's first fill" in got["venue_qty_unknown_reason"]


def test_a_covering_walk_reports_the_real_quantity_and_the_real_divergence(
        _resolved):
    got = auditor.audit_market("m", t=object(), walk=_resolved_walk(
        ended="older_than_since", complete_to_since=True,
        truncated_by_page_cap=False))
    assert got["venue_qty_total"] == "6.1600"
    assert got["venue_qty_unknown_reason"] is None
    assert D(got["venue_qty_total"]) != D(got["ledger_qty_total"]), (
        "this one IS a real ledger/venue divergence and must still be seen")
