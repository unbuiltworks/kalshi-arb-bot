"""The activity pager: bounded, deduplicating, paced, restart-safe.

Nothing here touches the network. A fake fetcher records every request so the
request COUNT and the offsets are assertable, which is the only way to check
that the pager stops.
"""
from __future__ import annotations

import pytest

from src.l5_book_shadow import PolymarketVenue, activity_key


class FakeFetcher:
    """Serves a synthetic newest-first feed and records every call."""

    def __init__(self, total: int):
        self.calls = []
        self.rows = [{"transactionHash": "0x%04x" % i, "timestamp": 1000 + i,
                      "asset": "tok", "size": "1", "slug": "s",
                      "outcome": "Up", "type": "TRADE"}
                     for i in range(total)]

    def get(self, url, params):
        self.calls.append(dict(params))
        off = int(params.get("offset") or 0)
        lim = int(params["limit"])
        return self.rows[off:off + lim]


def venue(total):
    f = FakeFetcher(total)
    return PolymarketVenue(f), f


# ---------------------------------------------------------- bounded paging

def test_paging_is_bounded_by_max_pages():
    v, f = venue(100000)
    known = {"a-key-we-will-never-see"}
    rows = v.l5_activity(limit=10, pages=999, known=known)
    assert len(f.calls) == PolymarketVenue.MAX_ACTIVITY_PAGES
    assert len(rows) == 10 * PolymarketVenue.MAX_ACTIVITY_PAGES


def test_a_cold_start_takes_ONE_page_exactly_as_before_this_change():
    """seen_activity is in-memory and empty after a restart. Deep paging there
    would re-emit thousands of rows onto an append-only tape and the shadow
    would apply L5's inventory twice."""
    for known in (set(), None):
        v, f = venue(100000)
        rows = v.l5_activity(limit=500, pages=4, known=known)
        assert len(f.calls) == 1
        # one page AND the old page size: bounding pages alone still let a
        # cold start re-emit 489 rows in production (deployment 588b6e48)
        assert f.calls[0]["limit"] == 100
        assert len(rows) == 100


def test_no_configuration_can_walk_the_whole_history():
    v, f = venue(100000)
    v.l5_activity(limit=500, pages=10 ** 6, known={"never-seen"})
    assert len(f.calls) <= PolymarketVenue.MAX_ACTIVITY_PAGES


def test_paging_stops_at_the_first_page_that_overlaps_what_we_hold():
    v, f = venue(1000)
    known = {activity_key(v.http.rows[25])}        # sits on page 3 of 10
    v.l5_activity(limit=10, pages=4, known=known)
    assert len(f.calls) == 3
    assert [c.get("offset") for c in f.calls] == [None, 10, 20]


def test_a_short_page_stops_paging():
    v, f = venue(7)
    rows = v.l5_activity(limit=10, pages=4, known={"x"})
    assert len(f.calls) == 1 and len(rows) == 7


def test_an_empty_page_stops_paging():
    v, f = venue(10)
    v.l5_activity(limit=10, pages=4, known={"x"})
    assert len(f.calls) == 2            # full page, then the empty one


def test_offsets_advance_by_the_page_size_and_never_repeat():
    v, f = venue(1000)
    v.l5_activity(limit=50, pages=4, known={"x"})
    assert [c.get("offset") for c in f.calls] == [None, 50, 100, 150]


def test_one_page_is_still_the_default():
    v, f = venue(1000)
    v.l5_activity(limit=10)
    assert len(f.calls) == 1


# -------------------------------------------------------------- dedup

def test_the_pager_and_the_emitter_share_ONE_key_definition():
    a = {"transactionHash": "0xab", "timestamp": 7, "asset": "t", "size": "3"}
    assert activity_key(a) == "0xab|7|t|3"
    assert activity_key(dict(a, size="4")) != activity_key(a)
    assert activity_key(dict(a, timestamp=8)) != activity_key(a)


def test_missing_fields_do_not_collide_into_one_key():
    assert activity_key({"transactionHash": "0xab"}) != activity_key({})


def test_overlapping_pages_do_not_duplicate_tape_rows():
    """The pager may return a row twice; the emitter must still write it once."""
    from src.l5_book_shadow import BookShadowCollector
    seen, written = set(), []

    class Stub:
        pass

    st = Stub()
    st.seen_activity = seen
    st.emit = written.append
    row = {"transactionHash": "0x1", "timestamp": 5, "asset": "t",
           "size": "2", "slug": "s", "outcome": "Up"}
    BookShadowCollector._emit_activity(st, [row, dict(row), row])
    assert len(written) == 1


# ---------------------------------------------------- rate limit / pacing

def test_the_extra_requests_are_bounded_per_poll():
    """At most MAX_ACTIVITY_PAGES GETs per poll, so the added load is capped."""
    v, f = venue(100000)
    v.l5_activity(limit=500, pages=99, known={"x"})
    assert len(f.calls) <= 4
    # 4 GETs per ~89 s cycle is under 3 rpm against an observed 52.8 rpm
    assert 4 * (60.0 / 89.1) < 3.0


def test_a_feed_that_always_overlaps_costs_exactly_one_request():
    """Steady state: the first page overlaps, so the common case is 1 GET."""
    v, f = venue(1000)
    known = {activity_key(r) for r in v.http.rows[:10]}
    v.l5_activity(limit=10, pages=4, known=known)
    assert len(f.calls) == 1


# -------------------------------------------------------- restart safety

def test_a_restart_with_an_empty_seen_set_still_terminates():
    v, f = venue(100000)
    rows = v.l5_activity(limit=500, pages=4, known=set())
    assert len(f.calls) == 1 and len(rows) == 100


def test_a_warm_poll_keeps_the_large_page():
    v, f = venue(100000)
    v.l5_activity(limit=500, pages=4, known={"never-seen"})
    assert all(c["limit"] == 500 for c in f.calls)


def test_known_may_be_None_and_paging_still_terminates():
    v, f = venue(100000)
    v.l5_activity(limit=10, pages=4, known=None)
    assert len(f.calls) == 1


def test_a_feed_error_propagates_rather_than_being_swallowed_here():
    """The caller already wraps this in the activity_error branch; the pager
    must not invent an empty success that would read as 'no new fills'."""
    class Boom:
        def get(self, url, params):
            raise RuntimeError("502")
    with pytest.raises(RuntimeError):
        PolymarketVenue(Boom()).l5_activity(pages=4, known={"x"})


def test_a_non_list_response_yields_nothing_rather_than_raising():
    class Odd:
        def get(self, url, params):
            return {"error": "nope"}
    assert PolymarketVenue(Odd()).l5_activity(pages=4, known={"x"}) == []
