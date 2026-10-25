"""The bounded read-only dump must be bounded, read-only, and range-correct."""
import json
from pathlib import Path

from src import pmus_book_tape as T


def _tape(tmp_path: Path, n=50, slug="cpc-btc-updown-15m-2026-10-25-1500z"):
    d = tmp_path / "pmus-btc15m"
    d.mkdir(parents=True)
    f = d / "pmus_btcupdown15m_20260927T224630Z.jsonl"
    with f.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"t": "2026-10-25T15:%02d:00+00:00" % (i % 60),
                                 "kind": "book", "slug": slug,
                                 "bids": [["0.5", "10"]],
                                 "offers": [["0.51", "10"]]}) + "\n")
    return d, f


def test_dump_emits_everything_within_the_cap(tmp_path, capsys):
    d, _ = _tape(tmp_path, n=10)
    out = T.dump_existing(d)
    assert out["emitted"] == 10
    assert out["stopped"] is None
    assert capsys.readouterr().out.count("PMUS_TAPE_DUMP ") == 10


def test_max_rows_is_a_hard_ceiling(tmp_path, capsys):
    d, _ = _tape(tmp_path, n=500)
    out = T.dump_existing(d, max_rows=7)
    assert out["emitted"] == 7
    assert out["stopped"] == "max_rows"
    assert capsys.readouterr().out.count("PMUS_TAPE_DUMP ") == 7


def test_time_range_filters_inclusively(tmp_path, capsys):
    d, _ = _tape(tmp_path, n=30)
    out = T.dump_existing(d, since="2026-10-25T15:05:00+00:00",
                          until="2026-10-25T15:09:00+00:00")
    assert out["emitted"] == 5, out
    capsys.readouterr()


def test_slug_filter_excludes_other_markets(tmp_path, capsys):
    d, f = _tape(tmp_path, n=5, slug="cpc-btc-updown-15m-2026-10-25-1500z")
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": "2026-10-25T15:00:00+00:00", "kind": "book",
                             "slug": "cpceth-eth-updown-15m-x"}) + "\n")
    out = T.dump_existing(d, slug_contains="cpc-btc-updown-15m")
    assert out["emitted"] == 5
    capsys.readouterr()


def test_dump_never_modifies_the_tape(tmp_path, capsys):
    d, f = _tape(tmp_path, n=20)
    before = f.read_bytes()
    mtime = f.stat().st_mtime
    T.dump_existing(d, max_rows=5)
    assert f.read_bytes() == before
    assert f.stat().st_mtime == mtime
    capsys.readouterr()


def test_missing_directory_is_reported_not_raised(tmp_path, capsys):
    out = T.dump_existing(tmp_path / "does-not-exist")
    assert out["emitted"] == 0
    assert out["files"] == []
    capsys.readouterr()


def test_default_ceiling_is_declared_and_finite():
    assert isinstance(T.DUMP_MAX_ROWS, int) and 0 < T.DUMP_MAX_ROWS <= 100000


def test_dump_opens_no_socket(monkeypatch, tmp_path, capsys):
    import socket
    def boom(*a, **k):
        raise AssertionError("dump_existing must not open a socket")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    d, _ = _tape(tmp_path, n=5)
    assert T.dump_existing(d)["emitted"] == 5
    capsys.readouterr()
