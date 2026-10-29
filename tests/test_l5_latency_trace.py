"""The latency decomposition: honest absences, no invented publication time."""
from __future__ import annotations

from src import l5_latency_trace as LT


def t(**kw):
    base = dict(action_key="a1", source_event_t=1000.0, feed_served_t=1016.0,
                first_seen_t=1017.0, receipt_t=1018.1, evaluated_t=1018.3,
                submitted_t=1018.5, venue_ack_t=1018.9, poll_interval_s=3.0)
    base.update(kw)
    return LT.trace(**base)


def test_the_segments_add_up_to_the_measured_latency():
    r = t()
    assert r["source_event_to_receipt_s"] == 18.1
    s = r["segments"]
    assert round(s["index_lag_s"] + s["poll_lag_s"], 3) == 18.1


def test_a_missing_endpoint_makes_the_segment_none_not_zero():
    """A stage whose boundary is unknown has an unknown duration. Zero is a
    claim, and the claim would be that there was no delay."""
    r = t(venue_ack_t=None)
    assert r["segments"]["ack_lag_s"] is None
    assert r["segments"]["submit_lag_s"] == 0.2
    r2 = t(feed_served_t=None)
    assert r2["segments"]["serve_lag_s"] is None
    assert r2["segments"]["index_lag_s"] == 17.0


def test_no_publication_timestamp_is_ever_invented():
    """The activity feed publishes none. The record must say so."""
    r = t(feed_served_t=None, first_seen_t=None)
    assert r["at"]["feed_served_utc"] is None
    assert r["at"]["first_seen_utc"] is None
    assert "publication" in r["labels"]
    assert "never synthesised" in r["labels"]["feed_served_utc"]
    src = open("src/l5_latency_trace.py").read()
    for bad in ("time.time()", "datetime.now", "utcnow"):
        assert bad not in src, bad


def test_first_seen_is_labelled_as_a_bound_not_a_publication_time():
    r = t()
    lab = r["labels"]["first_seen_utc"]
    assert "MEASURED BY US" in lab and "NOT a publication timestamp" in lab
    assert r["first_seen_bound_s"] == 3.0


def test_every_segment_names_its_owner():
    r = t()
    for name, _a, _b in LT.SEGMENTS:
        assert r["segment_owner"][name] in (
            "SOURCE_OR_POLL_PHASE", "NETWORK", "OURS", "VENUE")
    assert r["segment_owner"]["poll_lag_s"] == "OURS"
    assert r["segment_owner"]["index_lag_s"] == "SOURCE_OR_POLL_PHASE"


def test_clock_skew_is_reported_not_clamped():
    """Three machines, three clocks. A negative segment is evidence."""
    r = t(feed_served_t=1020.0)
    assert r["segments"]["serve_lag_s"] == -3.0


def test_an_unparseable_or_absent_date_header_is_none():
    for bad in (None, "", "not a date", 12345, "Wed, 99 Zzz 2026"):
        assert LT.parse_http_date(bad) is None
    assert LT.parse_http_date("Wed, 29 Oct 2026 15:03:31 GMT") == 1793286211.0


def test_a_naive_date_is_read_as_utc_not_local():
    a = LT.parse_http_date("Wed, 29 Oct 2026 15:03:31 GMT")
    b = LT.parse_http_date("Wed, 29 Oct 2026 15:03:31 -0000")
    assert a == b


# ------------------------------------------------- the first-seen tracker
def test_first_seen_keeps_the_first_sighting_not_the_latest():
    """Reporting a later poll would understate the very lag this measures."""
    m = LT.first_seen_tracker()
    assert m("k1", 100.0) == 100.0
    assert m("k1", 140.0) == 100.0
    assert m.get("k1") == 100.0


def test_an_unseen_key_is_none_rather_than_now():
    m = LT.first_seen_tracker()
    assert m.get("never") is None


def test_the_tracker_is_bounded_and_evicts_rather_than_lying():
    m = LT.first_seen_tracker(cap=10)
    for i in range(30):
        m("k%d" % i, float(i))
    assert m.size() <= 10
    # an evicted key reports None, never a later poll's time
    assert m.get("k0") is None
    assert m.get("k29") == 29.0


# ------------------------------------------------------------- summarise
def test_the_summary_carries_n_beside_every_median():
    """venue_ack exists only for submitted orders; a median over 3 rows and
    one over 300 must not look alike."""
    traces = [t(), t(venue_ack_t=None), t(venue_ack_t=None)]
    s = LT.summarise(traces)
    assert s["ack_lag_s"]["n"] == 1
    assert s["poll_lag_s"]["n"] == 3
    assert s["source_event_to_receipt_s"]["median_s"] == 18.1


def test_the_summary_survives_an_empty_set():
    s = LT.summarise([])
    for name, _a, _b in LT.SEGMENTS:
        assert s[name]["n"] == 0 and s[name]["median_s"] is None


def test_the_module_cannot_act():
    """No network and no order path. It formats timestamps callers hold.

    The test names IMPORTS and CALLS rather than substrings: an earlier
    version banned the word "order" and failed on the tracker's own
    insertion-order list and on the phrase "in order" in a docstring. A
    guard that fires on prose teaches people to delete the guard.
    """
    import ast
    tree = ast.parse(open("src/l5_latency_trace.py").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "datetime", "email", "typing"}, imported
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    # NOT "get": the module calls dict.get, and banning it would be the
    # same mistake as banning the word "order" -- a guard that fires on
    # ordinary code is a guard that gets deleted.
    for forbidden in ("submit", "place", "cancel", "urlopen", "request",
                      "post", "send"):
        assert forbidden not in called, forbidden
