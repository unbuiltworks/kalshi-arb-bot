"""Every CANDIDATE's field paths must actually resolve against that venue's
documented order-book shape.

This exists because the V1 probe rejected all four feeds on
`4_book_with_sizes`, and a path TYPO produces exactly the same symptom as a
venue that genuinely publishes no sizes. Without this test the two are
indistinguishable from the artifact, and a fixable defect reads as a venue
limitation.
"""
import time

from src.gemini_research.leadlag_probe import (CANDIDATES, _dig,
                                               classify_unit)

NOW = time.time()

FIXTURES = {
    "coinbase": {
        "bids": [["85896.13", "0.5", 2]], "asks": [["85896.14", "1.25", 3]],
        "sequence": 1102756342, "time": "2026-10-05T21:41:30.285766262Z"},
    "kraken": {"result": {"XXBTZUSD": {
        "bids": [["85893.30000", "1.000", int(NOW)]],
        "asks": [["85893.40000", "2.000", int(NOW)]]}}},
    "gemini_spot": {
        "bids": [{"price": "85906.09", "amount": "3.1",
                  "timestamp": str(int(NOW))}],
        "asks": [{"price": "85906.10", "amount": "0.7",
                  "timestamp": str(int(NOW))}]},
    "bitstamp": {
        "timestamp": str(int(NOW)), "microtimestamp": str(int(NOW * 1e6)),
        "bids": [["85900.00", "0.9"]], "asks": [["85901.00", "1.1"]]},
    "binance": {"lastUpdateId": 77, "bids": [["85890.00", "2.0"]],
                "asks": [["85891.00", "2.5"]]},
}


def test_every_candidate_resolves_price_and_size():
    for c in CANDIDATES:
        j = FIXTURES[c["venue"]]
        for field in ("bid", "ask", "bid_size", "ask_size"):
            got = _dig(j, c[field])
            assert got not in (None, ""), (
                "%s: path %r for %s resolved to %r -- a typo here is "
                "indistinguishable from a venue with no sizes"
                % (c["venue"], c[field], field, got))


def test_timestamp_paths_resolve_and_unit_is_identifiable():
    for c in CANDIDATES:
        if not c.get("ts_fields"):
            # binance depth genuinely publishes none; that is a fact about
            # the feed, and the probe must report it rather than invent one
            assert c["venue"] == "binance"
            continue
        j = FIXTURES[c["venue"]]
        raw = None
        for f in c["ts_fields"]:
            raw = _dig(j, (f,)) if isinstance(f, str) else _dig(j, f)
            if raw not in (None, ""):
                break
        assert raw not in (None, ""), "%s: no ts_fields path resolved" % (
            c["venue"],)
        cl = classify_unit(raw, NOW)
        assert cl.get("identified"), "%s: unit not identified from %r (%s)" % (
            c["venue"], raw, cl.get("why"))


def test_the_admissibility_rule_itself_was_not_weakened():
    """The four requirements are the point of the study; only the SOURCE
    list was allowed to change."""
    import inspect

    from src.gemini_research import leadlag_probe as P
    src = inspect.getsource(P.probe_one)
    for req in ("1_reachable", "2_source_timestamp_present",
                "3_timestamp_unit_identified", "4_book_with_sizes"):
        assert req in src, "requirement %s disappeared" % req
    # sizes must still be required, not merely recorded
    assert 'all(x not in (None, "") for x in' in src
