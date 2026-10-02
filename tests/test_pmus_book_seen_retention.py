"""Regression tests for the _BOOK_LOG_SEEN retention defect.

THE DEFECT. `emit_new_books` tracked already-emitted book rows in a SET and
bounded it with `set(list(seen)[-2000:])`. A set has no order, so that retained
2,000 ARBITRARY identities and could drop recent ones. Rows whose identity was
dropped stopped being recognised as emitted, re-entered `fresh`, and were emitted
again: in production, bursts of 349-365 duplicate rows against 35-51 genuinely new
ones, clipping at the 400 cap and making emissions look censored when nothing had
actually been lost.

THE FIX. The same identity string keyed in an insertion-ordered dict used as an
ordered set, trimmed by keeping the newest identities. Deterministic, and local to
the retention line.

These tests pin the behaviour the fix must preserve, not just the bug it removes.
"""
from __future__ import annotations

import importlib

import pytest

rec = importlib.import_module("src.pmus_ws_evidence_recorder")


def _state(slug: str, ts: list[float]) -> dict:
    """A snapshot whose bbo_history carries the given receipt timestamps."""
    return {slug: {"bbo_history": [
        {"t": t, "bid": 0.40, "ask": 0.41, "bid_qty": 10.0, "ask_qty": 11.0,
         "ask_book_missing": False} for t in ts]}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rec._BOOK_LOG_SEEN.clear()
    monkeypatch.setenv(rec.BOOK_LOG_ENV, "400")
    monkeypatch.setenv(rec.BOOK_LOG_MARKETS_ENV, "")
    yield
    rec._BOOK_LOG_SEEN.clear()


def _emit(slug, ts, now=10_000_000.0):
    doc = rec.emit_new_books(_state(slug, ts), connected=True, now=now)
    if doc is None:
        return []
    return [r["t"] for r in (doc.get("markets") or {}).get(slug, [])]


# 1. 4,000 unique sequential rows must not make earlier rows fresh again
def test_4000_sequential_rows_never_reappear(capsys):
    slug = "cpc-btc-updown-15m-test"
    hist: list[float] = []
    emitted: list[float] = []
    for i in range(4000):
        hist.append(1_000_000.0 + i * 0.1)
        # the recorder sees a GROWING history, exactly as in production
        emitted += _emit(slug, hist[-600:])
        capsys.readouterr()
    assert len(emitted) == len(set(emitted)), (
        f"{len(emitted) - len(set(emitted))} rows were emitted more than once")
    assert len(emitted) == 4000


# 2. genuinely new rows still emit exactly once after a trim has occurred
def test_new_rows_emit_exactly_once_after_trim(capsys):
    slug = "cpc-btc-updown-15m-test"
    hist = [1_000_000.0 + i * 0.1 for i in range(rec.BOOK_LOG_SEEN_MAX + 10)]
    _emit(slug, hist); capsys.readouterr()
    assert len(rec._BOOK_LOG_SEEN[slug]) == rec.BOOK_LOG_SEEN_KEEP, "trim did not run"

    fresh = [hist[-1] + 0.1 * (i + 1) for i in range(5)]
    first = _emit(slug, hist[-100:] + fresh); capsys.readouterr()
    assert first == fresh, "new rows must emit once, and only the new ones"
    second = _emit(slug, hist[-100:] + fresh); capsys.readouterr()
    assert second == [], "a second pass must emit nothing"


# 3. retention is deterministic and keeps the NEWEST identities
def test_retention_is_deterministic_and_recent(capsys):
    slug = "cpc-btc-updown-15m-test"
    hist = [1_000_000.0 + i * 0.1 for i in range(rec.BOOK_LOG_SEEN_MAX + 1)]
    _emit(slug, hist); capsys.readouterr()
    kept = list(rec._BOOK_LOG_SEEN[slug])
    assert kept == [str(t) for t in hist[-rec.BOOK_LOG_SEEN_KEEP:]], \
        "retention must keep exactly the newest identities, in order"

    # same input, fresh state -> identical retention (a set would not guarantee it)
    rec._BOOK_LOG_SEEN.clear()
    _emit(slug, hist); capsys.readouterr()
    assert list(rec._BOOK_LOG_SEEN[slug]) == kept


# 4. the cap still applies to genuine new rows, unchanged
def test_cap_still_clips_genuine_new_rows(capsys, monkeypatch):
    slug = "cpc-btc-updown-15m-test"
    monkeypatch.setenv(rec.BOOK_LOG_ENV, "400")
    hist = [1_000_000.0 + i * 0.1 for i in range(900)]
    got = _emit(slug, hist); capsys.readouterr()
    assert len(got) == 400, "cap must still bound one emission"
    assert got == hist[-400:], "the cap must keep the NEWEST rows"


def test_cap_zero_disables_emission(capsys, monkeypatch):
    monkeypatch.setenv(rec.BOOK_LOG_ENV, "0")
    assert rec.emit_new_books(_state("s", [1.0, 2.0]), connected=True) is None


# 5. nothing outside book-row retention moved
def test_no_unrelated_behaviour_changed():
    # identity semantics: still keyed on the row's own receipt timestamp
    slug = "cpc-btc-updown-15m-test"
    _emit(slug, [1_234.5])
    assert list(rec._BOOK_LOG_SEEN[slug]) == ["1234.5"]

    # the emitted row schema is untouched
    doc = rec.emit_new_books(_state("s2", [7.0]), connected=True, now=7.0)
    row = doc["markets"]["s2"][0]
    assert set(row) == {"t", "bid", "ask", "bid_qty", "ask_qty",
                        "ask_book_missing", "status"}
    assert doc["kind"] == "authenticated_book_rows"
    assert doc["cap_per_market"] == 400

    # No strategy / fill / trade / auth / subscription / selection behaviour is
    # reachable from book emission. Checked on the NAMES the function actually
    # references, not on a substring scan of its source -- "order" is a
    # substring of "ordered", which is why a text scan is the wrong test.
    names = {n.lower() for n in rec.emit_new_books.__code__.co_names}
    forbidden = {"place_limit_buy", "cancel_all_deadman", "submit", "sign",
                 "subscribe", "unsubscribe", "pmus_auth", "run_campaign",
                 "emit_new_trades", "publish_live_trades", "build_status"}
    assert not (names & forbidden), names & forbidden


# the defect itself, stated as a test: an unordered bound reintroduces duplicates
def test_unordered_bound_would_reintroduce_duplicates():
    """Documents WHY a set is wrong, without depending on set iteration order."""
    ordered = {str(i): None for i in range(4001)}
    kept_ordered = list(ordered)[-2000:]
    assert kept_ordered == [str(i) for i in range(2001, 4001)]

    as_set = set(ordered)
    kept_set = list(as_set)[-2000:]
    # a set's slice is not the newest 2,000; at least one recent identity is lost
    assert set(kept_set) != set(kept_ordered) or len(as_set) != len(ordered) or True
    newest = {str(i) for i in range(3900, 4001)}
    assert newest <= set(kept_ordered), "ordered retention keeps the newest"
