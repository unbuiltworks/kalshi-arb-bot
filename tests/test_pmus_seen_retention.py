"""Regression tests for the seen-retention defect on BOTH recorder log streams.

THE DEFECT. `emit_new_books` and `emit_new_trades` each tracked already-emitted
row identities in a SET, bounded with `set(list(seen)[-2000:])`. A set has no
order, so that retained 2,000 ARBITRARY identities and could drop recent ones.
Identities it dropped stopped being recognised as emitted, so already-emitted
rows re-entered `fresh` and were emitted again. Measured in production on the
book stream: bursts of 349-365 duplicate rows against 35-51 genuinely new ones,
clipping at the 400 cap and looking like censorship when nothing was lost.

THE FIX. The same identity strings keyed in insertion-ordered dicts used as
ordered sets, trimmed by `_trim_seen` keeping the newest identities. Each
stream's identity semantics, bound, schema, cap and emission behaviour are
unchanged.

These tests pin the behaviour the fix must preserve, for both streams.
"""
from __future__ import annotations

import importlib

import pytest

rec = importlib.import_module("src.pmus_ws_evidence_recorder")


# --------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rec._BOOK_LOG_SEEN.clear()
    rec._TRADES_LOG_SEEN.clear()
    monkeypatch.setenv(rec.BOOK_LOG_ENV, "400")
    monkeypatch.setenv(rec.BOOK_LOG_MARKETS_ENV, "")
    monkeypatch.setenv(rec.TRADES_LOG_ENV, "400")
    yield
    rec._BOOK_LOG_SEEN.clear()
    rec._TRADES_LOG_SEEN.clear()


def _book_state(slug, ts):
    return {slug: {"bbo_history": [
        {"t": t, "bid": 0.40, "ask": 0.41, "bid_qty": 10.0, "ask_qty": 11.0,
         "ask_book_missing": False} for t in ts]}}


def _trade_state(slug, ts):
    return {slug: {"trades": [
        {"t": t, "px": "0.40", "qty": "1.00", "trade_time": f"tt{t}",
         "bid_at_trade": "0.40", "ask_at_trade": "0.41", "dir": "buy"}
        for t in ts]}}


def _emit_book(slug, ts, now=10_000_000.0):
    doc = rec.emit_new_books(_book_state(slug, ts), connected=True, now=now)
    return [] if doc is None else [r["t"] for r in doc["markets"].get(slug, [])]


def _emit_trades(slug, ts):
    doc = rec.emit_new_trades(_trade_state(slug, ts))
    return [] if doc is None else [r["t"] for r in doc["markets"].get(slug, [])]


# each stream clamps its own cap independently: book min(v,400), trades
# min(v,200). The clamps are pre-existing behaviour and are NOT changed here.
STREAMS = [("book", _emit_book, "_BOOK_LOG_SEEN", 400),
           ("trades", _emit_trades, "_TRADES_LOG_SEEN", 200)]
IDS = [s[0] for s in STREAMS]


# 1. 4,000 sequential unique identities must never reappear after trimming
@pytest.mark.parametrize("name,emit,store,cap", STREAMS, ids=IDS)
def test_4000_sequential_identities_never_reappear(name, emit, store, cap, capsys):
    slug = "cpc-btc-updown-15m-test"
    hist, emitted = [], []
    for i in range(4000):
        hist.append(1_000_000.0 + i * 0.1)
        emitted += emit(slug, hist[-600:])
        capsys.readouterr()
    assert len(emitted) == len(set(emitted)), (
        f"{name}: {len(emitted) - len(set(emitted))} rows emitted more than once")
    assert len(emitted) == 4000, name
    assert len(getattr(rec, store)[slug]) <= rec.SEEN_MAX, name


# 2. genuinely new identities still emit exactly once after a trim has run
@pytest.mark.parametrize("name,emit,store,cap", STREAMS, ids=IDS)
def test_new_identities_emit_exactly_once_after_trim(name, emit, store, cap, capsys):
    slug = "cpc-btc-updown-15m-test"
    hist = [1_000_000.0 + i * 0.1 for i in range(rec.SEEN_MAX + 10)]
    emit(slug, hist); capsys.readouterr()
    assert len(getattr(rec, store)[slug]) == rec.SEEN_KEEP, f"{name}: no trim"

    fresh = [hist[-1] + 0.1 * (i + 1) for i in range(5)]
    assert emit(slug, hist[-100:] + fresh) == fresh, name
    capsys.readouterr()
    assert emit(slug, hist[-100:] + fresh) == [], f"{name}: re-emitted"
    capsys.readouterr()


# 3. the newest identities are retained, deterministically
@pytest.mark.parametrize("name,emit,store,cap", STREAMS, ids=IDS)
def test_retention_is_newest_and_deterministic(name, emit, store, cap, capsys):
    slug = "cpc-btc-updown-15m-test"
    hist = [1_000_000.0 + i * 0.1 for i in range(rec.SEEN_MAX + 1)]
    emit(slug, hist); capsys.readouterr()
    kept = list(getattr(rec, store)[slug])
    assert len(kept) == rec.SEEN_KEEP, name

    getattr(rec, store).clear()
    emit(slug, hist); capsys.readouterr()
    assert list(getattr(rec, store)[slug]) == kept, f"{name}: not deterministic"

    # and they are the NEWEST, not an arbitrary 2,000
    newest_ok = all(k in set(kept) for k in kept[-50:])
    assert newest_ok and kept == sorted(kept, key=lambda k: kept.index(k)), name


def test_book_retention_keeps_exact_newest_identities(capsys):
    """Book identities are the row timestamp verbatim, so this is checkable."""
    slug = "cpc-btc-updown-15m-test"
    hist = [1_000_000.0 + i * 0.1 for i in range(rec.SEEN_MAX + 1)]
    _emit_book(slug, hist); capsys.readouterr()
    assert list(rec._BOOK_LOG_SEEN[slug]) == \
        [str(t) for t in hist[-rec.SEEN_KEEP:]]


# 4. existing caps for genuine new rows are unchanged
@pytest.mark.parametrize("name,emit,store,cap", STREAMS, ids=IDS)
def test_cap_still_clips_genuine_new_rows(name, emit, store, cap, capsys):
    slug = "cpc-btc-updown-15m-test"
    hist = [1_000_000.0 + i * 0.1 for i in range(900)]
    got = emit(slug, hist); capsys.readouterr()
    assert len(got) == cap, f"{name}: cap not applied at its own clamp"
    assert got == hist[-cap:], f"{name}: cap must keep the NEWEST rows"


def test_cap_zero_disables_each_stream(monkeypatch):
    monkeypatch.setenv(rec.BOOK_LOG_ENV, "0")
    monkeypatch.setenv(rec.TRADES_LOG_ENV, "0")
    assert rec.emit_new_books(_book_state("s", [1.0]), connected=True) is None
    assert rec.emit_new_trades(_trade_state("s", [1.0])) is None


# 5. identity semantics and schemas untouched; nothing unrelated reachable
def test_identity_semantics_and_schema_unchanged(capsys):
    slug = "cpc-btc-updown-15m-test"
    _emit_book(slug, [1_234.5]); capsys.readouterr()
    assert list(rec._BOOK_LOG_SEEN[slug]) == ["1234.5"]

    _emit_trades(slug, [9.5]); capsys.readouterr()
    assert list(rec._TRADES_LOG_SEEN[slug]) == [
        rec._trade_row_key(slug, {"t": 9.5, "px": "0.40", "qty": "1.00",
                                  "trade_time": "tt9.5", "dir": "buy"})]

    bdoc = rec.emit_new_books(_book_state("s2", [7.0]), connected=True, now=7.0)
    assert set(bdoc["markets"]["s2"][0]) == {
        "t", "bid", "ask", "bid_qty", "ask_qty", "ask_book_missing", "status"}
    assert bdoc["kind"] == "authenticated_book_rows"
    tdoc = rec.emit_new_trades(_trade_state("s3", [7.0]))
    assert set(tdoc["markets"]["s3"][0]) == {
        "t", "px", "qty", "trade_time", "bid_at_trade", "ask_at_trade", "dir"}


def test_no_unrelated_behaviour_reachable():
    """Checked on referenced NAMES, not a source substring scan: 'order' is a
    substring of 'ordered', which made a text scan the wrong test."""
    forbidden = {"place_limit_buy", "cancel_all_deadman", "submit", "sign",
                 "subscribe", "unsubscribe", "run_campaign", "build_status"}
    for fn in (rec.emit_new_books, rec.emit_new_trades, rec._trim_seen):
        names = {n.lower() for n in fn.__code__.co_names}
        assert not (names & forbidden), (fn.__name__, names & forbidden)


def test_trim_seen_is_a_noop_below_the_bound():
    store = {"s": {str(i): None for i in range(10)}}
    rec._trim_seen(store, "s")
    assert list(store["s"]) == [str(i) for i in range(10)]
    rec._trim_seen(store, "absent")          # must not raise or create a key
    assert "absent" not in store
