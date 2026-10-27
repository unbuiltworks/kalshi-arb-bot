"""Confirm a window by asking for it, not by scanning the series.

MEASURED on 2026-10-27 against the live public gateway: the paginated
scan took **34 GETs, 7.83s and 20.7 MB** to confirm one fifteen-minute
window, because it reads `/v1/events?series_id=375` from offset 0 in
pages of 100 and the window being traded is always at the far end.
`/v1/events/slug/{event}` answered the same question, from the same
venue, with the same `startTime`, in **1 GET, 0.45s and 18.8 KB**.

It is also the more precise read: the route MATCHES the slug, where the
scan hunts for it and gives up at offset 4000 with "not listed".

The scan stays as the fallback, so a venue that stops serving the slug
route degrades to today's behaviour rather than to an unconfirmed
window. Both paths end in the same place -- no usable startTime leaves
`aligned` falsy, `venue_window_bounds` returns None, and the arming
boundary refuses WINDOW_BOUNDS_UNCONFIRMED by name.

These tests count GETs. None of them touches the network.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_pmus_realtime_observer as OBS

WS = 1793474100          # 2026-10-31T19:15:00Z, a clean 900s grid point
WANT = dt.datetime.fromtimestamp(WS, dt.timezone.utc)
EVENT = "btc-updown-15m-%s-%02d%02dz" % (
    WANT.strftime("%Y-%m-%d"), WANT.hour, WANT.minute)
SLUG = "cpc-" + EVENT


def fake_gateway(monkeypatch, *, slug_route=None, pages=None):
    """Count GETs and serve the two routes independently."""
    calls = []

    def _get(url, timeout=20.0):
        calls.append(url)
        if "/v1/events/slug/" in url:
            if slug_route is None:
                raise RuntimeError("HTTP Error 404: Not Found")
            return slug_route
        if "/v1/events?" in url:
            off = int(url.split("offset=")[1])
            return {"events": (pages or {}).get(off, [])}
        raise AssertionError("unexpected route " + url)

    monkeypatch.setattr(OBS, "_get", _get)
    return calls


def event_doc(start="2026-10-31T19:15:00Z", slug=EVENT):
    return {"event": {"slug": slug, "startTime": start,
                      "markets": [{"slug": SLUG}]}}


# ------------------------------------------------------------- the fast path

def test_one_request_confirms_the_window(monkeypatch):
    calls = fake_gateway(monkeypatch, slug_route=event_doc())
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is True
    assert rep["source"] == "events_slug"
    assert rep["pmus_start_utc"] == "2026-10-31T19:15:00+00:00"
    assert len(calls) == 1, calls
    assert "/v1/events/slug/%s" % EVENT in calls[0]


def test_a_disagreeing_start_is_a_disagreement_not_a_fallback(monkeypatch):
    """The scan must NOT be consulted to overturn a venue answer we got."""
    calls = fake_gateway(monkeypatch,
                         slug_route=event_doc(start="2026-10-31T19:30:00Z"))
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is False
    assert rep["source"] == "events_slug"
    assert len(calls) == 1, calls
    assert OBS.venue_window_bounds(WS, 900, verify=lambda _w: rep) is None


def test_a_mismatched_slug_in_the_body_falls_through_to_the_scan(monkeypatch):
    """A 200 about some OTHER event is not an answer about this one."""
    calls = fake_gateway(
        monkeypatch, slug_route=event_doc(slug="btc-updown-15m-elsewhere"),
        pages={0: [{"slug": EVENT, "startTime": "2026-10-31T19:15:00Z"}]})
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is True
    assert rep["source"] == "series_scan"
    assert len(calls) == 2, calls


# -------------------------------------------------------------- the fallback

def test_the_scan_still_answers_when_the_slug_route_fails(monkeypatch):
    calls = fake_gateway(
        monkeypatch, slug_route=None,
        pages={0: [{"slug": "btc-updown-15m-other", "startTime": "x"}] * 100,
               100: [{"slug": EVENT, "startTime": "2026-10-31T19:15:00Z"}]})
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is True
    assert rep["source"] == "series_scan"
    assert rep["pmus_start_utc"] == "2026-10-31T19:15:00+00:00"
    assert len(calls) == 3, calls      # 1 slug attempt + 2 pages


def test_both_paths_failing_leaves_the_window_unconfirmed(monkeypatch):
    fake_gateway(monkeypatch, slug_route=None, pages={0: []})
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is None
    assert OBS.venue_window_bounds(WS, 900, verify=lambda _w: rep) is None


def test_an_unparseable_start_falls_through_rather_than_raising(monkeypatch):
    calls = fake_gateway(
        monkeypatch, slug_route=event_doc(start="not-a-timestamp"),
        pages={0: [{"slug": EVENT, "startTime": "2026-10-31T19:15:00Z"}]})
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is True
    assert rep["source"] == "series_scan"
    assert len(calls) == 2, calls


def test_a_missing_start_falls_through_rather_than_claiming_alignment(
        monkeypatch):
    calls = fake_gateway(
        monkeypatch, slug_route={"event": {"slug": EVENT}},
        pages={0: []})
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is None
    assert len(calls) == 2, calls


# --------------------------------------------------- the cost that motivated it

def test_the_fast_path_is_one_request_where_the_scan_is_thirty_four(
        monkeypatch):
    """The measured shape, as a regression guard.

    33 full pages that do not contain the window, then the page that
    does -- which is what the live series looked like when this was
    measured. The slug route must not pay any of it.
    """
    pages = {off: [{"slug": "btc-updown-15m-filler-%d" % (off + i),
                    "startTime": "2026-10-01T00:00:00Z"} for i in range(100)]
             for off in range(0, 3300, 100)}
    pages[3300] = [{"slug": EVENT, "startTime": "2026-10-31T19:15:00Z"}]

    scan_calls = fake_gateway(monkeypatch, slug_route=None, pages=pages)
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is True and rep["source"] == "series_scan"
    scan_gets = len(scan_calls) - 1            # minus the slug attempt
    assert scan_gets == 34, scan_gets

    fast_calls = fake_gateway(monkeypatch, slug_route=event_doc(), pages=pages)
    rep = OBS.verify_alignment(WS)
    assert rep["aligned"] is True and rep["source"] == "events_slug"
    assert len(fast_calls) == 1
    assert scan_gets / len(fast_calls) == 34


@pytest.mark.parametrize("bad", [None, {}, {"event": None}, []])
def test_junk_from_the_slug_route_never_raises(monkeypatch, bad):
    fake_gateway(monkeypatch, slug_route=bad, pages={0: []})
    rep = OBS.verify_alignment(WS)          # must not raise
    assert rep["aligned"] is None
