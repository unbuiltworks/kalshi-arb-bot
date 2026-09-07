"""Deterministic tests for the authenticated read-only PMUS WS recorder."""
from __future__ import annotations

import base64
import inspect
import json

import pytest

from src import pmus_ws_evidence_recorder as rec


def test_fails_closed_without_credentials(monkeypatch, capsys):
    monkeypatch.delenv(rec.KEY_ENV, raising=False)
    monkeypatch.delenv(rec.SECRET_ENV, raising=False)
    assert rec.has_credentials() is False
    assert rec.main.__wrapped__ if hasattr(rec.main, "__wrapped__") else True
    # main() exits 2 with NO_CREDENTIALS before touching the network
    import sys
    monkeypatch.setattr(sys, "argv", ["prog"])
    assert rec.main() == 2
    out = capsys.readouterr().out
    assert "NO_CREDENTIALS" in out and rec.KEY_ENV in out


def test_auth_headers_match_documented_scheme():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    seed = b"\x07" * 32
    secret_b64 = base64.b64encode(seed).decode()
    h = rec.auth_headers("key-123", secret_b64, now_ms=1700000000000)
    assert h["X-PM-Access-Key"] == "key-123"
    assert h["X-PM-Timestamp"] == "1700000000000"
    pub = ed25519.Ed25519PrivateKey.from_private_bytes(seed).public_key()
    pub.verify(base64.b64decode(h["X-PM-Signature"]),
               b"1700000000000GET/v1/ws/markets")   # raises on mismatch


def test_subscribe_frames_are_market_data_and_trade_only():
    frames = rec.subscribe_frames(["b-slug", "a-slug"])
    assert [f["subscribe"]["subscription_type"] for f in frames] == [1, 3]
    for f in frames:
        assert set(f) == {"subscribe"}
        assert f["subscribe"]["market_slugs"] == ["a-slug", "b-slug"]
        assert f["subscribe"]["request_id"]


def test_outbound_guard_blocks_anything_but_read_only_frames():
    rec.guard_outbound({"subscribe": {}})
    rec.guard_outbound({"heartbeat": {}})
    rec.guard_outbound({"unsubscribe": {}})
    with pytest.raises(RuntimeError):
        rec.guard_outbound({"order": {"side": "buy"}})
    with pytest.raises(RuntimeError):
        rec.guard_outbound({"subscribe": {}, "cancel": {}})


def test_daily_rotation_and_heartbeat(tmp_path):
    from datetime import datetime, timezone
    d1 = datetime(2026, 9, 2, 23, 59, tzinfo=timezone.utc)
    d2 = datetime(2026, 9, 3, 0, 1, tzinfo=timezone.utc)
    assert rec.daily_path(tmp_path, d1).name == "pmus_ws_20260902.jsonl"
    assert rec.daily_path(tmp_path, d2).name == "pmus_ws_20260903.jsonl"
    rec.append(tmp_path, {"kind": "x"})
    rec.heartbeat_file(tmp_path, messages=5)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["mode"] == "READ_ONLY_WS_RECORDER" and hb["messages"] == 5


def test_structurally_no_order_or_private_paths():
    src = inspect.getsource(rec)
    for forbidden in ("/v1/orders", "/v1/ws/private", "close-position",
                      "close_position", "modify", '"POST"', "'POST'",
                      "preview", "cancel-order", "rfq"):
        assert forbidden not in src, forbidden
    # the only websocket URL is the markets stream
    import re
    assert set(re.findall(r"wss://[a-z0-9.-]+(/[a-z0-9/._-]*)?", src)) == \
        {"/v1/ws/markets"}
    # every outbound send passes through the guard
    sends = [l for l in src.splitlines() if "ws.send(" in l]
    assert sends and all("guard_outbound" in l for l in sends)


# ---- the status document the probe reads at admission time ----

def test_status_document_is_persisted_and_readable_by_the_guard(tmp_path):
    """The recorder always printed its status; only to logs, so a human
    had to copy it into an env var inside its own 120s freshness bound.
    Persisted, the probe reads it directly."""
    from src import pmus_spring_evidence_transfer as xfer
    status = {"t": 1000.0, "spring_watch": [{"slug": "mkt", "bid": "0.50"}]}
    rec.write_status_doc(tmp_path, status)
    path = rec.status_doc_path(tmp_path)
    assert path.exists()
    assert xfer.load_recorder_status(path)["spring_watch"][0]["slug"] == "mkt"


def test_the_newest_status_wins_and_the_tail_stays_bounded(tmp_path):
    for i in range(rec.STATUS_DOC_KEEP + 20):
        rec.write_status_doc(tmp_path, {"t": float(i)})
    from src import pmus_spring_evidence_transfer as xfer
    path = rec.status_doc_path(tmp_path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) <= rec.STATUS_DOC_KEEP
    # the reader takes the LAST parseable object, so it must be the newest
    assert xfer.load_recorder_status(path)["t"] == float(
        rec.STATUS_DOC_KEEP + 19)


def test_a_status_document_failure_never_raises(tmp_path):
    """A handoff-file problem must not cost the connection or the tape."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    rec.write_status_doc(blocked, {"t": 1.0})     # must not raise


def test_spring_watch_skips_frozen_books_and_market_rows_carry_the_age():
    """issue #167 comment 5571181823: a resolved market's frozen book sat
    in the spring_watch pool for hours; the selector could only refuse it
    as market_closed. Rows carry the last authenticated update age and
    spring_watch drops rows older than PMUS_WS_STALE_S."""
    import time
    now = time.time()
    live = {"bid": "0.45", "ask": "0.47", "bid_qty": "20", "ask_qty": "20",
            "both_sides_served": True, "book_updates": 30, "trades": [],
            "bbo_history": [{"t": now - 5, "bid": "0.45", "ask": "0.47"}]}
    frozen = {"bid": "0.45", "ask": "0.47", "bid_qty": "20", "ask_qty": "20",
              "both_sides_served": True, "book_updates": 5000, "trades": [],
              "bbo_history": [{"t": now - 60000, "bid": "0.45", "ask": "0.47"}]}
    rows = {"live-mkt": rec._market_row("live-mkt", live, now, l5=False),
            "frozen-mkt": rec._market_row("frozen-mkt", frozen, now, l5=False)}
    assert rows["live-mkt"]["last_authenticated_update_age_s"] < 60
    assert rows["frozen-mkt"]["last_authenticated_update_age_s"] > 50000
    picked = [r["slug"] for r in rec.spring_watch(rows)]
    assert picked == ["live-mkt"]
