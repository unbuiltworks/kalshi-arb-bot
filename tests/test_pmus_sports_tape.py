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
