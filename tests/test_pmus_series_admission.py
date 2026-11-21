"""Admitting a recurring crypto series to the PMUS book tape.

The tape could only ever see the sports slate: `LIVE_EVENTS` pins
`categories=sports`. PMUS launched `btc-updown-15m` on 2026-09-22 with the
same book endpoints, invisible to that discovery. These tests pin the second
slug source and the two properties that make a 15-minute window different from
a game: the universe is decided by the WALL CLOCK, and it must roll faster
than the sports re-slate.

Read-only throughout; every venue call is a stub.
"""
from __future__ import annotations

import datetime as dt

from src import pmus_book_tape as T


def _stub(series_rows, event_rows):
    def get(path):
        if path.startswith("/v1/series"):
            off = int(path.split("offset=")[1]) if "offset=" in path else 0
            return {"series": series_rows if off == 0 else []}
        if path.startswith("/v1/events"):
            off = int(path.split("offset=")[1]) if "offset=" in path else 0
            return {"events": event_rows if off == 0 else []}
        return {}
    return get


NOW = dt.datetime(2026, 9, 23, 13, 47, tzinfo=dt.timezone.utc)
SERIES = [{"id": "375", "slug": "btc-updown-15m"},
          {"id": "999", "slug": "something-else"}]


def _ev(hhmm, closed=False):
    h, m = int(hhmm[:2]), int(hhmm[2:])
    return {"slug": "btc-updown-15m-%s" % hhmm, "closed": closed,
            "startTime": "2026-09-23T%02d:%02d:00Z" % (h, m),
            "markets": [{"slug": "cpc-btc-updown-15m-%s" % hhmm}]}


def test_it_finds_the_series_by_slug_not_by_category():
    got = T.series_slugs("btc-updown-15m",
                         get=_stub(SERIES, [_ev("1345")]), now=NOW)
    assert got == ["cpc-btc-updown-15m-1345"]


def test_a_closed_window_is_never_polled():
    evs = [_ev("1330", closed=True), _ev("1345")]
    got = T.series_slugs("btc-updown-15m", get=_stub(SERIES, evs), now=NOW)
    assert got == ["cpc-btc-updown-15m-1345"]


def test_a_window_that_has_not_opened_is_not_polled_yet():
    """Its book endpoint 404s until it opens; polling it spends the call
    budget on errors that are not errors."""
    evs = [_ev("1345"), _ev("1400"), _ev("1415")]
    got = T.series_slugs("btc-updown-15m", get=_stub(SERIES, evs), now=NOW)
    assert got == ["cpc-btc-updown-15m-1345"]


def test_the_next_window_is_admitted_inside_the_lead():
    """At 13:59:30 the 14:00 window is about to open and must already be in
    the universe, or its first seconds are lost."""
    late = dt.datetime(2026, 9, 23, 13, 59, 30, tzinfo=dt.timezone.utc)
    evs = [_ev("1345"), _ev("1400")]
    got = T.series_slugs("btc-updown-15m", get=_stub(SERIES, evs), now=late)
    assert got == ["cpc-btc-updown-15m-1345", "cpc-btc-updown-15m-1400"]


def test_an_unknown_series_yields_nothing_rather_than_the_sports_slate():
    got = T.series_slugs("no-such-series",
                         get=_stub(SERIES, [_ev("1345")]), now=NOW)
    assert got == []


def test_the_series_reslate_is_faster_than_a_fifteen_minute_window():
    """The sports re-slate is 300 s. A 15-minute market's ENTIRE LIFE is three
    times that, so reusing it would lose whole windows at rollover."""
    assert T.SERIES_RESLATE_S <= 60.0
    assert T.SERIES_RESLATE_S * 4 < 15 * 60


def test_sports_discovery_is_untouched():
    """The default path must still be the live sports slate."""
    assert "categories=sports" in T.LIVE_EVENTS


def test_the_snapshot_itself_was_already_venue_generic():
    """Nothing in `snapshot` needed changing -- that is why this is a slug
    source and not a second collector."""
    import inspect
    src = inspect.getsource(T.snapshot)
    for word in ("sport", "league", "game", "team"):
        assert word not in src.lower()
