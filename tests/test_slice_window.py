"""The slicer must be a verbatim copy, bounded exactly by the window."""
import gzip
import json
import subprocess
import sys


def _tape(tmp_path, rows):
    p = tmp_path / "tape.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def _run(tape, out, slug="S", start="2026-09-30T13:30:00+00:00"):
    return subprocess.run(
        [sys.executable, "scripts/l5_slice_window.py", str(tape), slug,
         start, str(out)], capture_output=True, text=True)


def _row(secs, slug="S"):
    return {"t": f"2026-09-30T13:{30 + secs // 60:02d}:{secs % 60:02d}+00:00",
            "kind": "book", "msg": {"marketData": {"marketSlug": slug}}}


def test_rows_outside_the_window_are_excluded_and_inside_kept_verbatim(tmp_path):
    rows = [_row(0), _row(300), _row(899)]
    late = {"t": "2026-09-30T13:45:00+00:00", "kind": "book",
            "msg": {"marketData": {"marketSlug": "S"}}}
    early = {"t": "2026-09-30T13:29:59+00:00", "kind": "book",
             "msg": {"marketData": {"marketSlug": "S"}}}
    tape = _tape(tmp_path, [early] + rows + [late])
    out = tmp_path / "o.jsonl.gz"
    r = _run(tape, out)
    assert r.returncode == 0, r.stdout + r.stderr
    got = [json.loads(l) for l in gzip.open(out, "rt")]
    assert len(got) == 3
    assert got == rows                      # verbatim, in order


def test_another_market_in_the_same_window_is_excluded(tmp_path):
    tape = _tape(tmp_path, [_row(10, "S"), _row(20, "OTHER")])
    out = tmp_path / "o.jsonl.gz"
    _run(tape, out)
    got = [json.loads(l) for l in gzip.open(out, "rt")]
    assert len(got) == 1


def test_a_truncated_tail_line_is_counted_not_hidden(tmp_path):
    p = tmp_path / "tape.jsonl"
    p.write_text(json.dumps(_row(5)) + "\n" + '{"t": "2026-09-30T13:3')
    out = tmp_path / "o.jsonl.gz"
    r = _run(p, out)
    assert r.returncode == 0
    assert "(1 unparsable)" in r.stdout


def test_a_tape_with_no_matching_rows_is_refused(tmp_path):
    tape = _tape(tmp_path, [_row(10, "OTHER")])
    out = tmp_path / "o.jsonl.gz"
    r = _run(tape, out)
    assert r.returncode == 1
    assert "REFUSED" in r.stdout
