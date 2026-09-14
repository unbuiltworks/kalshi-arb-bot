"""Live status handoff: infrastructure only, OFF by default.

It removes the DISK READ from the serve path. It does NOT by itself change
the ~10 s status cadence, which the measurement shows is the dominant term --
that is `PMUS_WS_STATUS_EVERY_SECONDS`. These tests pin both facts so the
change is never mistaken for the whole fix.
"""
import importlib
import os

import pytest

rec = importlib.import_module("src.pmus_ws_evidence_recorder")


@pytest.fixture(autouse=True)
def _clean():
    rec._LIVE_STATUS.update({"line": None, "utc": None, "n": 0})
    old = os.environ.get(rec.LIVE_STATUS_ENV)
    yield
    if old is None:
        os.environ.pop(rec.LIVE_STATUS_ENV, None)
    else:
        os.environ[rec.LIVE_STATUS_ENV] = old
    rec._LIVE_STATUS.update({"line": None, "utc": None, "n": 0})


def test_disabled_by_default_so_merging_changes_nothing():
    os.environ.pop(rec.LIVE_STATUS_ENV, None)
    assert rec.live_status_enabled() is False
    rec.publish_live_status({"a": 1})
    assert rec.live_status_bytes() is None
    assert rec._LIVE_STATUS["line"] is None


@pytest.mark.parametrize("val,on", [("1", True), ("true", True), ("ON", True),
                                    ("yes", True), ("0", False), ("", False),
                                    ("no", False), ("maybe", False)])
def test_enable_flag_parsing(val, on):
    os.environ[rec.LIVE_STATUS_ENV] = val
    assert rec.live_status_enabled() is on


def test_published_line_is_ndjson_bytes_matching_the_payload():
    os.environ[rec.LIVE_STATUS_ENV] = "1"
    rec.publish_live_status({"kind": "s", "n": 2})
    body = rec.live_status_bytes()
    assert isinstance(body, bytes) and body.endswith(b"\n")
    import json
    assert json.loads(body.decode()) == {"kind": "s", "n": 2}


def test_publish_never_raises_on_unserialisable_payload():
    """A handoff problem must never cost the connection or the tape."""
    os.environ[rec.LIVE_STATUS_ENV] = "1"

    class Bad:
        def __repr__(self):
            raise RuntimeError("boom")

    rec.publish_live_status({"bad": Bad()})          # must not raise
    rec.publish_live_status({"ok": 1})
    assert rec.live_status_bytes() is not None


def test_write_status_doc_publishes_the_same_payload(tmp_path):
    """The file and the live line come from ONE payload, so they cannot
    disagree about what the recorder saw."""
    os.environ[rec.LIVE_STATUS_ENV] = "1"
    rec.write_status_doc(tmp_path, {"kind": "status", "v": 7})
    import json
    on_disk = rec.status_doc_path(tmp_path).read_text().splitlines()[-1]
    assert json.loads(on_disk) == json.loads(rec.live_status_bytes().decode())


def test_publish_counter_advances_so_staleness_is_observable():
    os.environ[rec.LIVE_STATUS_ENV] = "1"
    rec.publish_live_status({"i": 1})
    rec.publish_live_status({"i": 2})
    assert rec._LIVE_STATUS["n"] == 2
    assert rec._LIVE_STATUS["utc"] is not None


def test_live_handoff_does_not_change_the_status_cadence():
    """THE POINT OF THIS TEST. The serve path getting faster does not make
    the payload newer: the payload is still built on
    PMUS_WS_STATUS_EVERY_SECONDS. Publishing twice from one build yields one
    distinct payload, exactly as the 10 s timer produces one snapshot that
    the measurement shows is reused by a median of 2 orders."""
    os.environ[rec.LIVE_STATUS_ENV] = "1"
    payload = {"kind": "status", "built_once": True}
    rec.publish_live_status(payload)
    first = rec.live_status_bytes()
    rec.publish_live_status(payload)
    assert rec.live_status_bytes() == first
    assert rec.DEFAULT_STATUS_EVERY_S == 60.0
    assert rec.STATUS_EVERY_ENV == "PMUS_WS_STATUS_EVERY_SECONDS"
