"""`live_book.observe()` against the REAL captured frame shapes.

THE FRAMES BELOW ARE NOT SYNTHESISED. They are the shapes the proven
websocket probe actually captured (deployment 90fa9013, market
btc-updown-15m-1791372600, 11,523 frames: two book snapshots and 11,438
`price_change` events), pasted as fixtures so a parser change cannot drift
away from what the venue sends.

THE DEFECT THESE TESTS LOCK OUT. The first `observe()` looked for a
top-level `asset_id` with top-level `bids`/`asks`. A `price_change` has
none of those -- the payload is nested in `price_changes`, one entry per
token -- so `observe()` resolved an empty token id and returned early on
99.3% of frames. The top of book populated once at subscription and then
never moved, and because `last_seen` also stopped advancing only the
silence timeout would eventually catch it. In the interval, `health_fn`
reported HEALTHY while serving a FROZEN book, and a maker price would have
been chosen from it. That is the only failure mode in this build that
pointed the wrong way, and these tests exist to keep it that way.

Run:
    PYTHONPATH=. python3 -m pytest analysis/l5/e1_cohort/test_e1_live_book.py -q
"""
from __future__ import annotations

import os

import pytest

from analysis.l5.e1_cohort import live_book as LB
from analysis.l5.e1_cohort import provider as PV

# ----------------------------------------------------- the REAL identities

COND = ("0x436a8368a605f5ab3d849705e01d774ed5d3454ed6383c0226520716838"
        "1c8d1")
TOK_A = ("3746189497465202193658251021114119316613992948091300902313751"
         "3062057072471315")
TOK_B = ("1880892345327373131518540333256322389933461680742187077221659"
         "6004232689206534")

PIN = {"slug": "btc-updown-15m-1791372600",
       "condition_id": COND, "market_id": COND,
       "token_up": TOK_A, "token_down": TOK_B,
       "window_start": 1791372600, "window_end": 1791373500}


# ------------------------------------------- the REAL frames, verbatim

#: a `price_change`, exactly as captured. NO top-level asset_id, NO bids,
#: NO asks. One entry per token, each carrying best_bid/best_ask directly.
PRICE_CHANGE = {
    "event_type": "price_change",
    "market": COND,
    "price_changes": [
        {"asset_id": TOK_A,
         "best_ask": "0.68", "best_bid": "0.67",
         "hash": "e5a17cfb0dc00a425d98e69ece718ef5901f270d",
         "price": "0.01", "side": "BUY", "size": "3133.01"},
        {"asset_id": TOK_B,
         "best_ask": "0.33", "best_bid": "0.32", "price": "0.99",
         "side": "SELL", "size": "3133."}],
}

#: a `price_change` whose changed price IS the best bid on the buy side --
#: the only case in which the entry's `size` is the queue depth we want.
PRICE_CHANGE_AT_BEST = {
    "event_type": "price_change",
    "market": COND,
    "price_changes": [
        {"asset_id": TOK_A, "best_ask": "0.68", "best_bid": "0.67",
         "price": "0.67", "side": "BUY", "size": "412.5"}],
}

#: a book snapshot: top-level asset_id PLUS ladders. Two of 11,523.
BOOK_SNAPSHOT = {
    "event_type": "book",
    "market": COND,
    "asset_id": TOK_A,
    "bids": [{"price": "0.65", "size": "100.0"},
             {"price": "0.66", "size": "250.0"},
             {"price": "0.67", "size": "412.5"}],
    "asks": [{"price": "0.69", "size": "80.0"},
             {"price": "0.68", "size": "140.0"}],
}

BOOK_SNAPSHOT_B = dict(BOOK_SNAPSHOT, asset_id=TOK_B,
                       bids=[{"price": "0.32", "size": "900.0"}],
                       asks=[{"price": "0.33", "size": "700.0"}])


@pytest.fixture
def feed(tmp_path):
    return LB.TwoTokenFeed(PIN, str(tmp_path))


# ============ 1. a price_change updates BOTH tokens =====================

def test_a_real_price_change_updates_both_tokens(feed):
    """THE DEFECT, directly. This frame used to be dropped entirely."""
    feed.observe(PRICE_CHANGE)
    book = feed.book()
    assert book["up"]["best_bid"] == "0.67"
    assert book["up"]["best_ask"] == "0.68"
    assert book["down"]["best_bid"] == "0.32"
    assert book["down"]["best_ask"] == "0.33"
    assert book["up"]["token_id"] == TOK_A
    assert book["down"]["token_id"] == TOK_B
    assert book["up"]["frame_source"] == "price_change"
    # BOTH tokens got a top-of-book update from ONE frame
    assert set(feed.last_update) == {TOK_A, TOK_B}
    assert feed.frames_applied == 2
    assert feed.frames_refused_other_market == 0
    assert feed.frames_unrecognised == 0


def test_only_price_change_frames_still_reach_both_tokens_streaming(feed):
    """A feed that NEVER receives a snapshot -- 11,438 of 11,523 real
    frames -- must still be healthy."""
    assert feed.health()["both_tokens_streaming"] is False
    for _ in range(50):
        feed.observe(PRICE_CHANGE)
    h = feed.health()
    assert h["both_tokens_streaming"] is True
    assert h["frames"] == 50
    assert h["frames_applied"] == 100
    assert h["frames_unrecognised"] == 0
    assert set(h["tokens_last_seen_unix"]) == {TOK_A, TOK_B}


def test_the_top_of_book_actually_MOVES_across_price_changes(feed):
    """The frozen-book defect: prove successive frames change the cache."""
    feed.observe(PRICE_CHANGE)
    first = feed.book()["up"]["best_bid"]
    moved = {"event_type": "price_change", "market": COND,
             "price_changes": [dict(PRICE_CHANGE["price_changes"][0],
                                    best_bid="0.71", best_ask="0.72")]}
    feed.observe(moved)
    second = feed.book()["up"]["best_bid"]
    assert first == "0.67" and second == "0.71"
    assert first != second, "the top of book is frozen"


# ============ 2. a frame for ANOTHER market is refused ==================

def test_a_frame_for_another_market_is_refused(feed):
    feed.observe(PRICE_CHANGE)
    before = dict(feed.book()["up"])
    stray = {"event_type": "price_change",
             "market": "0xdeadbeef" + "0" * 56,
             "price_changes": [dict(PRICE_CHANGE["price_changes"][0],
                                    best_bid="0.01", best_ask="0.02")]}
    feed.observe(stray)
    assert feed.book()["up"]["best_bid"] == before["best_bid"]
    assert feed.frames_refused_other_market == 1
    assert feed.frames_applied == 2          # only the first frame's two
    # a stray SNAPSHOT is refused too
    feed.observe(dict(BOOK_SNAPSHOT, market="0xdeadbeef" + "0" * 56,
                      bids=[{"price": "0.02", "size": "1"}]))
    assert feed.book()["up"]["best_bid"] == before["best_bid"]
    assert feed.frames_refused_other_market == 2


def test_a_token_that_is_not_ours_is_ignored(feed):
    feed.observe({"event_type": "price_change", "market": COND,
                  "price_changes": [{"asset_id": "999", "best_bid": "0.90",
                                     "best_ask": "0.91", "price": "0.9",
                                     "side": "BUY", "size": "1"}]})
    assert feed.book()["up"] == {} and feed.book()["down"] == {}
    assert feed.frames_applied == 0


# ============ 3. the snapshot path still works ==========================

def test_the_book_snapshot_path_still_works(feed):
    feed.observe(BOOK_SNAPSHOT)
    up = feed.book()["up"]
    assert up["best_bid"] == "0.67"           # best of 0.65/0.66/0.67
    assert up["best_ask"] == "0.68"           # best of 0.69/0.68
    assert up["frame_source"] == "book_snapshot"
    # the ladder DOES give real queue depth at the best bid
    assert up["size_at_our_price"] == "412.5"
    assert up["size_ahead_of_us"] == "412.5"
    assert up["size_source"] == "book_snapshot"
    assert "size_unknown_reason" not in up
    feed.observe(BOOK_SNAPSHOT_B)
    assert feed.health()["both_tokens_streaming"] is True


def test_a_snapshot_then_price_changes_keeps_updating(feed):
    feed.observe(BOOK_SNAPSHOT)
    feed.observe(BOOK_SNAPSHOT_B)
    assert feed.book()["up"]["size_at_our_price"] == "412.5"
    feed.observe(PRICE_CHANGE)
    # the price moved and the size is now correctly reported as unknown
    assert feed.book()["up"]["best_bid"] == "0.67"
    assert feed.book()["up"]["size_at_our_price"] is None


# ============ 4. SIZE IS NEVER INVENTED =================================

def test_a_price_change_does_not_invent_a_size(feed):
    """The entry's `size` is at the CHANGED price (0.01), not at the best
    bid (0.67). Using it as queue depth would be a fabrication."""
    feed.observe(PRICE_CHANGE)
    up = feed.book()["up"]
    assert up["size_at_our_price"] is None
    assert up["size_ahead_of_us"] is None
    assert "3133.01" not in str(up)
    assert "CHANGED price" in up["size_unknown_reason"]
    assert "0.01" in up["size_unknown_reason"]


def test_size_is_used_when_the_changed_price_IS_the_best_bid(feed):
    feed.observe(PRICE_CHANGE_AT_BEST)
    up = feed.book()["up"]
    assert up["best_bid"] == "0.67"
    assert up["size_at_our_price"] == "412.5"
    assert "size_unknown_reason" not in up


def test_a_sell_side_change_at_the_best_bid_price_is_not_queue_depth(feed):
    feed.observe({"event_type": "price_change", "market": COND,
                  "price_changes": [
                      {"asset_id": TOK_A, "best_bid": "0.67",
                       "best_ask": "0.68", "price": "0.67",
                       "side": "SELL", "size": "9999"}]})
    assert feed.book()["up"]["size_at_our_price"] is None
    assert "9999" not in str(feed.book()["up"])


# ============ 5. STALENESS DOES NOT DEPEND ON SILENCE ALONE =============

def test_one_busy_token_does_not_make_the_other_look_healthy(feed,
                                                             monkeypatch):
    """Snapshot both, then 60s of price_change for ONE token only. The
    OTHER token must read unhealthy -- this is the stale-book case that
    used to pass."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(LB.time, "time", lambda: clock["t"])
    feed.observe(BOOK_SNAPSHOT)
    feed.observe(BOOK_SNAPSHOT_B)
    assert feed.health()["both_tokens_streaming"] is True
    assert PV.assert_ws_both_tokens_healthy(
        feed.health(), PIN, now=clock["t"])["ok"] is True

    # 60 seconds of traffic on TOK_A ONLY
    only_a = {"event_type": "price_change", "market": COND,
              "price_changes": [dict(PRICE_CHANGE["price_changes"][0])]}
    for i in range(60):
        clock["t"] = 1000.0 + i + 1
        feed.observe(only_a)

    h = feed.health()
    # frames ARE arriving, and both tokens were seen at some point...
    assert h["frames"] == 62
    assert set(h["tokens_last_book_update_unix"]) == {TOK_A, TOK_B}
    # ...but TOK_B's BOOK has not moved for 60s, so the provider refuses
    assert h["tokens_last_seen_unix"][TOK_A] == clock["t"]
    assert h["tokens_last_seen_unix"][TOK_B] == 1000.0
    assert h["staleness_depends_on_silence_alone"] is False
    with pytest.raises(PV.WindowUnavailable) as ei:
        PV.assert_ws_both_tokens_healthy(h, PIN, now=clock["t"])
    assert "TOKEN_SILENT" in str(ei.value)
    assert "token_down" in str(ei.value)


def test_a_frozen_book_cannot_read_healthy(feed, monkeypatch):
    """The exact defect: one snapshot, then nothing but dropped frames."""
    clock = {"t": 2000.0}
    monkeypatch.setattr(LB.time, "time", lambda: clock["t"])
    feed.observe(BOOK_SNAPSHOT)
    feed.observe(BOOK_SNAPSHOT_B)
    clock["t"] = 2000.0 + PV.MAX_TOKEN_SILENCE_S + 1
    with pytest.raises(PV.WindowUnavailable):
        PV.assert_ws_both_tokens_healthy(feed.health(), PIN,
                                         now=clock["t"])


def test_refused_frames_do_not_advance_freshness(feed, monkeypatch):
    clock = {"t": 3000.0}
    monkeypatch.setattr(LB.time, "time", lambda: clock["t"])
    feed.observe(PRICE_CHANGE)
    t0 = dict(feed.last_update)
    clock["t"] = 3100.0
    feed.observe(dict(PRICE_CHANGE, market="0xdead" + "0" * 60))
    feed.observe({"event_type": "tick_size_change", "market": COND})
    assert feed.last_update == t0, \
        "a refused or unusable frame advanced the freshness clock"


# ============ 6. unrecognised frames are counted, not guessed ===========

def test_an_unrecognised_frame_is_counted_not_guessed(feed):
    for junk in ({"event_type": "tick_size_change", "market": COND},
                 {"event_type": "book", "market": COND,
                  "asset_id": TOK_A},
                 {"market": COND, "price_changes": []}):
        feed.observe(junk)
    assert feed.frames_applied == 0
    assert feed.frames_unrecognised >= 2
    assert feed.book()["up"] == {}


def test_a_non_dict_frame_is_ignored(feed):
    for junk in (None, [], "frame", 7):
        feed.observe(junk)
    assert feed.frames == 0 and feed.frames_applied == 0


# ============ 7. the provider consumes this book correctly ==============

def test_the_provider_prices_the_first_leg_from_the_real_frame(feed):
    feed.observe(PRICE_CHANGE)
    price = PV.first_leg_price_from_book(feed.book()["up"])
    assert str(price) == "0.67"
    # and the complement side is INDEPENDENT, never 1 - up
    assert feed.book()["down"]["best_bid"] == "0.32"
    assert feed.health()["derived_complement"] is False


def test_the_feed_never_derives_a_complement():
    """CODE only -- the docstring says "1 - UP" in order to forbid it."""
    import ast
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "live_book.py")
    src = open(path, encoding="utf8").read()
    code = src
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                code = code.replace(d, "")
    code = "\n".join(ln for ln in code.splitlines()
                     if not ln.strip().startswith("#"))
    # arithmetic derivations only; "e1-two-token-feed" is a thread name
    for derivation in ("1 - ", "1.0 -", "ONE -", "1 -0", "- up", "- bb"):
        assert derivation not in code, derivation
    assert "best_bid" in code
    # both tokens are read independently, by their own asset_id
    assert code.count("token_up") >= 1 and code.count("token_down") >= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
