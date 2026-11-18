import json

from src import pmus_sports_tape as TP


def test_book_levels_keep_top3_as_served():
    msg = {"marketSlug": "aec-x", "bids": [{"px": "0.4", "qty": "10"}] * 5,
           "offers": [{"px": "0.5", "qty": "3"}]}
    (slug, b, a), = TP.book_levels(msg)
    assert slug == "aec-x" and len(b) == 3 and a[0]["qty"] == "3"


def test_tape_throttles_dedupes_and_records_events(tmp_path):
    t = [1000.0]
    tape = TP.Tape(tmp_path, clock=lambda: t[0], book_min_gap_s=1.0)
    f = {"marketSlug": "aec-x", "bids": [{"px": "0.4"}], "offers": [{"px": "0.5"}]}
    tape.book(f); tape.book(f)                     # duplicate
    t[0] += 0.2
    tape.book({**f, "bids": [{"px": "0.41"}]})    # within gap
    t[0] += 2
    tape.book({**f, "bids": [{"px": "0.42"}]})
    tape.event("FILL", {"slug": "aec-x", "px": "0.4"})
    tape.event("heartbeat", {})                    # not a tape kind
    lines = [json.loads(l) for l in open(next(tmp_path.glob("tape_*.jsonl")))]
    assert [l["k"] for l in lines] == ["book", "book", "ev"]
    assert lines[2]["kind"] == "FILL"


def test_tape_size_cap_and_never_raises(tmp_path):
    tape = TP.Tape(tmp_path, max_bytes_per_day=60)
    for i in range(10):
        tape.event("ORDER", {"i": i})
    assert tape.dropped > 0 and tape.errors == 0
    bad = TP.Tape(tmp_path / "x" / "\0bad")
    bad.event("ORDER", {})                         # unwritable: swallowed
    assert bad.errors == 1


def test_tape_prunes_old_days(tmp_path):
    for d in ("20261101", "20261102", "20261103"):
        (tmp_path / f"tape_{d}.jsonl").write_text("{}\n")
    tape = TP.Tape(tmp_path, keep_days=2)
    tape.event("ORDER", {})
    assert len(list(tmp_path.glob("tape_*.jsonl"))) == 2


# ---- 2026-11-18: the cap must never silence events or stranded markets ----
def _frame(slug, bid):
    return {"marketSlug": slug, "bids": [{"px": str(bid), "qty": "5"}],
            "offers": [{"px": str(bid + 0.02), "qty": "5"}]}


def test_ordinary_books_stop_at_their_share_but_events_continue(tmp_path):
    from src.pmus_sports_tape import Tape
    t = {"now": 1_800_000_000.0}
    tape = Tape(tmp_path, max_bytes_per_day=2000, book_min_gap_s=0,
                clock=lambda: t["now"])
    for i in range(200):
        t["now"] += 1
        tape.book(_frame("aec-a", 0.40 + (i % 50) / 1000))
    size_books = sum(p.stat().st_size for p in tmp_path.glob("tape_*"))
    assert size_books <= 2000 * Tape.BOOK_SHARE
    assert tape.dropped > 0
    before = tape.written
    tape.event("FILL", {"slug": "aec-a", "px": "0.4"})
    assert tape.written == before + 1 and tape.dropped_events == 0


def test_stranded_market_books_are_kept_after_the_book_share(tmp_path):
    from src.pmus_sports_tape import Tape
    t = {"now": 1_800_000_000.0}
    tape = Tape(tmp_path, max_bytes_per_day=4000, book_min_gap_s=0,
                clock=lambda: t["now"])
    for i in range(300):
        t["now"] += 1
        tape.book(_frame("aec-a", 0.40 + (i % 50) / 1000))
    n0 = tape.written
    tape.priority.add("aec-s")
    t["now"] += 1
    tape.book(_frame("aec-s", 0.30))
    assert tape.written == n0 + 1             # essential frame still written
    t["now"] += 1
    tape.book(_frame("aec-a", 0.33))
    assert tape.written == n0 + 1             # ordinary frame still dropped


def test_events_have_a_hard_ceiling(tmp_path):
    from src.pmus_sports_tape import Tape
    tape = Tape(tmp_path, max_bytes_per_day=500, clock=lambda: 1.8e9)
    for i in range(200):
        tape.event("FILL", {"slug": "aec-a", "i": i})
    size = sum(p.stat().st_size for p in tmp_path.glob("tape_*"))
    assert size <= 500 * Tape.EVENT_HARD_X
    assert tape.dropped_events > 0
