"""Durability wraps the recorder from OUTSIDE and cannot affect capture."""
import os
import subprocess
import time

from src import pmus_raw_s3_uploader as U
from src.pmus_ws_durability_entry import DurabilityWorker, start_durability

REVIEWED = "e48960f7"
BASE = "src/pmus_ws_evidence_recorder.py"
ENRICHED = "src/pmus_ws_evidence_recorder_enriched.py"


def _blob(rev, path):
    r = subprocess.run(["git", "rev-parse", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return r.stdout.strip()


# ------------------------------- the reviewed modules stay byte-identical

def test_the_reviewed_recorder_blobs_are_unchanged():
    """THE point of this module. If either blob moves, the lineage guard is
    being spent on a durability layer, which the owner declined."""
    for path in (BASE, ENRICHED):
        head = _blob("HEAD", path)
        reviewed = _blob(REVIEWED, path)
        assert head and reviewed, path
        assert head == reviewed, (
            f"{path} changed vs reviewed {REVIEWED}: {reviewed} -> {head}")


def test_the_durability_entry_is_not_part_of_the_reviewed_artifact():
    """It is new, so it carries no lineage delta of its own."""
    r = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", REVIEWED],
        capture_output=True, text=True).stdout.split()
    assert "src/pmus_ws_durability_entry.py" not in r
    assert "src/pmus_raw_s3_uploader.py" not in r


def test_it_touches_no_capture_or_order_code():
    """Checked over CODE, not prose: the docstring legitimately NAMES the
    things the module must not touch, so a text grep would flag itself."""
    import ast
    import inspect

    from src import pmus_ws_durability_entry as M
    tree = ast.parse(inspect.getsource(M))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            # string CONSTANTS used as values (not docstrings) still count
            if "/v1/" in n.value:
                names.add(n.value)
    forbidden = {"marketData", "subscribe", "on_trade", "apply_events",
                 "guard_outbound", "Transport", "place_limit_buy", "submit"}
    assert not (names & forbidden), names & forbidden
    assert not [n for n in names if isinstance(n, str) and "/v1/" in n]


# ------------------------------------------------------------- fail-open

def test_absent_configuration_is_a_silent_no_op(monkeypatch, capsys):
    monkeypatch.delenv("PMUS_RAW_S3_BUCKET", raising=False)
    assert start_durability() is None
    assert "\"enabled\": false" in capsys.readouterr().out


def test_a_broken_uploader_never_raises(monkeypatch, capsys):
    monkeypatch.setenv("PMUS_RAW_S3_BUCKET", "b")

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no boto3")

    monkeypatch.setattr(U, "RawS3Uploader", Boom)
    assert start_durability() is None            # must not propagate
    assert "error" in capsys.readouterr().out


def test_an_unreadable_raw_dir_is_counted_not_raised():
    up = U.RawS3Uploader(bucket="b", client=object())
    w = DurabilityWorker(up, root="/nonexistent")
    res = w.scan_once()
    assert res["offered"] == 0
    assert w.errors == 1


# ------------------------------------ closed-only, and no queue flooding

class _Fake:
    def put_object(self, **kw):
        pass

    def head_object(self, **kw):
        return {"ContentLength": 0}


def _closed(tmp_path, name, body=b"x" * 10):
    p = tmp_path / name
    p.write_bytes(body)
    old = time.time() - 9999
    os.utime(p, (old, old))
    return p


def test_only_closed_prior_hour_files_are_offered(tmp_path):
    import datetime as dt
    now = time.time()
    _closed(tmp_path, "pmus_ws_20200101_05.jsonl")
    _closed(tmp_path, "pmus_ws_20200101_06.jsonl")
    cur = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc)
    _closed(tmp_path, f"pmus_ws_{cur:%Y%m%d_%H}.jsonl")      # current hour
    up = U.RawS3Uploader(bucket="b", client=_Fake())
    w = DurabilityWorker(up, root=str(tmp_path))
    res = w.scan_once(now=now)
    assert res["offered"] == 2, res
    assert res["closed_seen"] == 2


def test_a_repeated_scan_does_not_re_queue_known_keys(tmp_path):
    _closed(tmp_path, "pmus_ws_20200101_05.jsonl")
    up = U.RawS3Uploader(bucket="b", client=_Fake())
    w = DurabilityWorker(up, root=str(tmp_path))
    first = w.scan_once()
    second = w.scan_once()
    third = w.scan_once()
    assert first["offered"] == 1
    assert second["offered"] == 0 and third["offered"] == 0
    assert second["skipped_known"] == 1
    assert up.q.qsize() == 1, "the periodic scan flooded the queue"


def test_backfill_is_a_separate_entry_point_from_the_scan(tmp_path):
    _closed(tmp_path, "pmus_ws_20200101_05.jsonl")
    up = U.RawS3Uploader(bucket="b", client=_Fake())
    w = DurabilityWorker(up, root=str(tmp_path))
    out = w.backfill_once()
    assert out["offered"] == 1
    assert w.last_backfill > 0
    assert w.scans == 0, "backfill ran the live scan path"


def test_nothing_local_is_deleted_by_a_scan(tmp_path):
    p = _closed(tmp_path, "pmus_ws_20200101_05.jsonl")
    before = p.read_bytes()
    up = U.RawS3Uploader(bucket="b", client=_Fake())
    DurabilityWorker(up, root=str(tmp_path)).scan_once()
    assert p.exists() and p.read_bytes() == before
