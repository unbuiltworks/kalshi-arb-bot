"""The S3 uploader must be upload-only, fail-open, and never destructive."""
import os
import time

import pytest

from src.pmus_raw_s3_uploader import (RawS3Uploader, is_closed, object_key,
                                      sha256_and_size)


class FakeS3:
    def __init__(self, *, fail=False, wrong_size=False):
        self.objects = {}
        self.fail, self.wrong_size = fail, wrong_size
        self.puts = 0

    def put_object(self, *, Bucket, Key, Body, Metadata=None):
        if self.fail:
            raise RuntimeError("s3 unavailable")
        self.puts += 1
        self.objects[(Bucket, Key)] = {"body": Body.read(),
                                       "meta": Metadata or {}}

    def head_object(self, *, Bucket, Key):
        o = self.objects[(Bucket, Key)]
        n = len(o["body"])
        return {"ContentLength": (n + 1) if self.wrong_size else n}


def _closed_file(tmp_path, name="pmus_ws_20200101_05.jsonl", body=b"x" * 100):
    p = tmp_path / name
    p.write_bytes(body)
    old = time.time() - 9999
    os.utime(p, (old, old))
    return str(p)


# ------------------------------------------------------- never destructive

def test_the_module_contains_no_delete_path():
    import inspect

    from src import pmus_raw_s3_uploader as M
    src = inspect.getsource(M)
    for forbidden in ("os.unlink", "os.remove", "shutil.rmtree", "rmtree(",
                      ".unlink(", "delete_object", "truncate("):
        assert forbidden not in src, forbidden


def test_a_successful_upload_leaves_the_local_file_byte_identical(tmp_path):
    path = _closed_file(tmp_path)
    before = open(path, "rb").read()
    u = RawS3Uploader(bucket="b", client=FakeS3())
    assert u.upload_one(path)["ok"] is True
    assert open(path, "rb").read() == before
    assert os.path.exists(path)


# --------------------------------------------------- closed files only

def test_the_file_being_written_now_is_never_uploaded(tmp_path):
    """Current-hour and recently-touched are both refused."""
    import datetime as dt
    now = time.time()
    cur = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc)
    p = tmp_path / f"pmus_ws_{cur:%Y%m%d_%H}.jsonl"
    p.write_bytes(b"partial")
    assert is_closed(str(p), now=now) is False
    # even an old mtime cannot make the CURRENT part closed
    old = now - 9999
    os.utime(p, (old, old))
    assert is_closed(str(p), now=now) is False


def test_a_recently_modified_old_part_is_still_considered_open(tmp_path):
    p = tmp_path / "pmus_ws_20200101_05.jsonl"
    p.write_bytes(b"data")
    assert is_closed(str(p), quiet_s=120.0) is False


def test_offer_skips_an_open_file_and_counts_it(tmp_path):
    p = tmp_path / "pmus_ws_20200101_05.jsonl"
    p.write_bytes(b"data")
    u = RawS3Uploader(bucket="b", client=FakeS3())
    assert u.offer(str(p)) is False
    assert u.skipped_open == 1
    assert u.q.qsize() == 0


# ------------------------------------------------------------ fail-open

def test_an_unavailable_bucket_never_raises_and_never_stalls(tmp_path):
    path = _closed_file(tmp_path)
    u = RawS3Uploader(bucket="b", client=FakeS3(fail=True))
    res = u.upload_one(path)           # must not raise
    assert res["ok"] is False
    assert u.failed == 1
    assert os.path.exists(path)


def test_absent_configuration_is_a_no_op_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("PMUS_RAW_S3_BUCKET", raising=False)
    u = RawS3Uploader()
    assert u.enabled() is False
    assert u.offer(_closed_file(tmp_path)) is False
    assert u.failed == 0               # not configured is not a failure
    assert u.start() is u              # start is a no-op


def test_offer_never_raises_even_on_a_bad_path():
    u = RawS3Uploader(bucket="b", client=FakeS3())
    assert u.offer("/nonexistent/nope.jsonl") is False


# ------------------------------------------------------ idempotent keys

def test_keys_carry_the_utc_hour_and_file_identity_and_are_idempotent():
    k1 = object_key("/state/pmus-ws/raw/pmus_ws_20260930_13.jsonl")
    k2 = object_key("/tmp/elsewhere/pmus_ws_20260930_13.jsonl")
    assert k1 == k2 == "pmus-ws/raw/20260930/13/pmus_ws_20260930_13.jsonl"


def test_a_daily_part_keeps_a_distinct_key():
    assert object_key("/r/pmus_ws_20260930.jsonl") == \
        "pmus-ws/raw/20260930/daily/pmus_ws_20260930.jsonl"


def test_re_uploading_the_same_file_reuses_the_same_object(tmp_path):
    path = _closed_file(tmp_path)
    fake = FakeS3()
    u = RawS3Uploader(bucket="b", client=fake)
    u.upload_one(path)
    u.upload_one(path)
    assert fake.puts == 2
    assert len(fake.objects) == 1, "a second copy was created"


# ----------------------------------------------------------- verification

def test_the_stored_size_is_read_back_and_a_mismatch_fails(tmp_path):
    path = _closed_file(tmp_path)
    u = RawS3Uploader(bucket="b", client=FakeS3(wrong_size=True))
    res = u.upload_one(path)
    assert res["ok"] is False
    assert "size mismatch" in res["why"]
    assert u.verified == 0 and u.failed == 1
    assert os.path.exists(path), "local file touched after a failed verify"


def test_the_checksum_and_size_are_sent_as_metadata(tmp_path):
    path = _closed_file(tmp_path)
    fake = FakeS3()
    u = RawS3Uploader(bucket="b", client=fake)
    res = u.upload_one(path)
    digest, size = sha256_and_size(path)
    meta = list(fake.objects.values())[0]["meta"]
    assert meta["sha256"] == digest == res["sha256"]
    assert meta["bytes"] == str(size) == str(res["size"])


# -------------------------------------------- backfill vs live capture

def test_backfill_offers_closed_files_only(tmp_path):
    _closed_file(tmp_path, "pmus_ws_20200101_05.jsonl")
    _closed_file(tmp_path, "pmus_ws_20200101_06.jsonl")
    fresh = tmp_path / "pmus_ws_20200101_07.jsonl"
    fresh.write_bytes(b"still being written")
    u = RawS3Uploader(bucket="b", client=FakeS3())
    res = u.backfill(str(tmp_path))
    assert res["offered"] == 2, res
    assert u.skipped_open == 1


def test_backfill_on_a_missing_directory_is_not_fatal():
    u = RawS3Uploader(bucket="b", client=FakeS3())
    res = u.backfill("/nonexistent")
    assert res["offered"] == 0


# ------------------------------------------------------- no credentials

def test_status_and_errors_carry_no_credential_material(tmp_path):
    u = RawS3Uploader(bucket="b", client=FakeS3(fail=True))
    u.upload_one(_closed_file(tmp_path))
    blob = repr(u.status())
    for leak in ("SECRET", "ACCESS_KEY", "aws_secret", "Bearer", "password"):
        assert leak not in blob
    # errors are exception CLASS names only, not messages
    assert u.errors == ["upload:RuntimeError"]
