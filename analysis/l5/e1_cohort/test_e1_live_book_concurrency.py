"""The live book under a real collector thread, not a single-threaded one.

In production `observe` is driven by the websocket collector's thread while
`book_fn`/`health_fn` are called from the cohort runner's thread. Every
existing test calls them from ONE thread, so consistency held by accident:
`_set_top` copied-then-rebound, and each reader happened to do one lookup.
Nothing asserted it, and the component deciding whether a live book is
fresh should not rest on that.

THE TEAR THESE TESTS CATCH. A real `price_change` carries one entry PER
TOKEN. Published per-entry without a lock, a reader can see the UP leg of
frame N beside the DOWN leg of frame N-1. Both legs look well-formed and
`book_unix` is the max of the two, so the stale leg is served under the
fresh timestamp -- the same shape as the bug that superseded
`cdc63519...`, where a frozen book read as healthy.

HOW IT IS DETECTED RATHER THAN HOPED FOR. The writer publishes frames whose
two legs always sum to exactly 1.00 (UP 0.30 / DOWN 0.70, then UP 0.70 /
DOWN 0.30, alternating). Any single frame is consistent; any MIX of two
frames sums to 0.60 or 1.40. So `up.best_bid + down.best_bid != 1.00` is
proof of a torn read, with no timing assumptions and no sleeps.

Verified to fail without the fix: with the lock removed from `observe` and
`book`, this test reports torn reads.

Run:
    PYTHONPATH=. python3 -m pytest \
        analysis/l5/e1_cohort/test_e1_live_book_concurrency.py -q
"""
from __future__ import annotations

import threading

from analysis.l5.e1_cohort import live_book as LB

CONDITION = "0x436a83c8d1"
TOK_UP = "3746189131500"
TOK_DOWN = "1880892653400"

PIN = {
    "slug": "btc-updown-15m-concurrency",
    "condition_id": CONDITION,
    "token_up": TOK_UP,
    "token_down": TOK_DOWN,
}


def _frame(generation: int) -> dict:
    """A real-shaped price_change whose two legs always sum to 1.00."""
    up_bid = 0.30 if generation % 2 == 0 else 0.70
    down_bid = 1.00 - up_bid
    return {
        "event_type": "price_change",
        "market": CONDITION,
        "price_changes": [
            {"asset_id": TOK_UP, "best_bid": "%.2f" % up_bid,
             "best_ask": "%.2f" % (up_bid + 0.01), "price": "%.2f" % up_bid,
             "side": "BUY", "size": "3133.01"},
            {"asset_id": TOK_DOWN, "best_bid": "%.2f" % down_bid,
             "best_ask": "%.2f" % (down_bid + 0.01),
             "price": "%.2f" % down_bid, "side": "BUY", "size": "2044.50"},
        ],
    }


def _feed() -> LB.TwoTokenFeed:
    """A feed with no collector and no thread of its own."""
    return LB.TwoTokenFeed(dict(PIN), root="/tmp/e1-concurrency-not-used")


def test_both_legs_always_come_from_one_frame():
    feed = _feed()
    feed.observe(_frame(0))  # so neither leg is ever absent

    writes = 20000
    stop = threading.Event()
    torn = []
    reads = [0]

    def writer():
        for g in range(writes):
            feed.observe(_frame(g))
        stop.set()

    def reader():
        while not stop.is_set():
            b = feed.book()
            up = b["up"].get("best_bid")
            down = b["down"].get("best_bid")
            if up is None or down is None:
                continue
            reads[0] += 1
            total = round(float(up) + float(down), 2)
            if total != 1.00:
                torn.append({"up": up, "down": down, "sum": total,
                             "book_unix": b.get("book_unix")})

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    r.start()
    w.start()
    w.join()
    r.join()

    # the test must have actually exercised the race
    assert reads[0] > 0, "the reader never observed a populated book"
    assert not torn, (
        "%d torn reads: the two legs came from different frames, so a stale "
        "leg was served under a fresh book_unix. First: %r"
        % (len(torn), torn[0]))


def test_health_and_book_agree_under_concurrent_writes():
    """Health certifies freshness for the book that is actually served."""
    feed = _feed()
    feed.observe(_frame(0))

    stop = threading.Event()
    bad = []

    def writer():
        for g in range(20000):
            feed.observe(_frame(g))
        stop.set()

    def reader():
        while not stop.is_set():
            h = feed.health()
            # both tokens must be reported streaming once both have been
            # seen, and the per-token map must never disagree with itself
            if not h["both_tokens_streaming"]:
                bad.append({"why": "both_tokens_streaming went False",
                            "health": h})
                continue
            seen = h["tokens_last_seen_unix"]
            upd = h["tokens_last_book_update_unix"]
            for tok in (TOK_UP, TOK_DOWN):
                if tok not in seen or tok not in upd:
                    bad.append({"why": "token missing from one map",
                                "token": tok, "health": h})
                elif seen[tok] != upd[tok]:
                    # tokens_last_seen_unix IS the last book update; the two
                    # views are read in one critical section and must match
                    bad.append({"why": "two views of the same instant "
                                       "disagree", "token": tok,
                                "health": h})

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    r.start()
    w.start()
    w.join()
    r.join()

    assert not bad, "inconsistent health under concurrent writes: %r" % bad[0]


def test_counters_are_not_lost_under_concurrent_writers():
    """`frames += 1` is a read-modify-write and is not atomic.

    Honest scope: this one PASSES without the lock too at this scale -- the
    GIL switches on bytecode boundaries that this loop rarely lands on, so
    no loss was observed. It is a GUARD against a future writer, not a
    demonstrated bug, unlike the two tests above which fail unlocked.
    """
    feed = _feed()
    per_thread = 2000
    threads = 4

    def writer():
        for g in range(per_thread):
            feed.observe(_frame(g))

    ts = [threading.Thread(target=writer) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert feed.frames == per_thread * threads, (
        "lost increments: %d of %d frames counted"
        % (feed.frames, per_thread * threads))
    # every frame matches the pinned market and carries two usable legs
    assert feed.frames_refused_other_market == 0
    assert feed.frames_unrecognised == 0
    assert feed.frames_applied == per_thread * threads * 2


def test_the_registry_lock_is_still_its_own_lock():
    """The per-feed lock must not have replaced the registry lock."""
    feed = _feed()
    assert feed._book_lock is not LB._LOCK
    # and the registry still serialises one-feed-per-slug
    assert isinstance(LB._LOCK, type(threading.Lock()))


def test_a_frame_for_another_market_still_cannot_update_the_cache():
    """The refusal survives the locking change (no feed behaviour change)."""
    feed = _feed()
    feed.observe(_frame(0))
    before = feed.book()

    stray = _frame(1)
    stray["market"] = "0xdeadbeef"
    feed.observe(stray)

    after = feed.book()
    assert after["up"]["best_bid"] == before["up"]["best_bid"]
    assert after["down"]["best_bid"] == before["down"]["best_bid"]
    assert feed.frames_refused_other_market == 1
