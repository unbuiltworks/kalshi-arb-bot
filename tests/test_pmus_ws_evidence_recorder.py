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
    # LIVE 2026-09-08 00:40Z (ENOSPC): the tape is written in HOURLY parts
    assert rec.daily_path(tmp_path, d1).name == "pmus_ws_20260902_23.jsonl"
    assert rec.daily_path(tmp_path, d2).name == "pmus_ws_20260903_00.jsonl"
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


def test_status_serves_only_fresh_rows_and_raises_the_pool_alarm(monkeypatch):
    """OWNER GO 5571884826: stale markets leave top/eligible/spring_watch,
    and when live events exist but no fresh candidate is served for
    POOL_ALARM_S the status carries an alarm with the exact reason."""
    import time
    now = time.time()
    frozen = {"bid": "0.45", "ask": "0.47", "bid_qty": "20", "ask_qty": "20",
              "both_sides_served": True, "book_updates": 5000,
              "trades": [{"t": now - 60000, "px": "0.45", "dir": "buy"}] * 9,
              "bbo_history": [{"t": now - 60000, "bid": "0.45", "ask": "0.47"}]}
    state = {"frozen-a": dict(frozen), "frozen-b": dict(frozen)}
    rec._POOL_EMPTY_SINCE["t"] = None
    disc = {"v1_live_events": 41, "fresh_live_subscribed": 0}
    st = rec.build_status(state, True, 10, 2, now=now, discovery=disc)
    assert st["top_markets"] == [] and st["eligible_markets"] == [] and st["spring_watch"] == []
    assert st["stale_markets"] == 2 and st["fresh_markets"] == 0
    a = st["pool_alarm"]
    assert a["live_events"] == 41 and a["alarm"] is False and a["pool_empty_for_s"] == 0.0
    assert a["reason"].startswith("no fresh authenticated market")
    st2 = rec.build_status(state, True, 10, 2, now=now + rec.POOL_ALARM_S + 1, discovery=disc)
    assert st2["pool_alarm"]["alarm"] is True
    # a fresh live row clears it
    state["live-c"] = {"bid": "0.50", "ask": "0.52", "bid_qty": "10", "ask_qty": "10",
                       "both_sides_served": True, "book_updates": 3, "trades": [],
                       "bbo_history": [{"t": now + rec.POOL_ALARM_S, "bid": "0.50", "ask": "0.52"}]}
    st3 = rec.build_status(state, True, 10, 3, now=now + rec.POOL_ALARM_S + 2,
                           discovery={"v1_live_events": 41, "fresh_live_subscribed": 1})
    assert st3["pool_alarm"]["alarm"] is False and st3["spring_watch"][0]["slug"] == "live-c"


# --- LIVE 2026-09-07: starved authenticated stream (frame accounting) ---

def test_subscribe_frames_chunk_and_cover_every_slug():
    slugs = [f"m-{i:03d}" for i in range(70)]
    frames = rec.subscribe_frames(slugs, chunk=32)
    assert len(frames) == 6                      # 3 chunks x 2 types
    types = sorted(f["subscribe"]["subscription_type"] for f in frames)
    assert types == [1, 1, 1, 3, 3, 3]
    ids = [f["subscribe"]["request_id"] for f in frames]
    assert len(set(ids)) == 6
    for st in (1, 3):
        got = sorted(sum((f["subscribe"]["market_slugs"] for f in frames
                          if f["subscribe"]["subscription_type"] == st), []))
        assert got == sorted(slugs)
    assert all(len(f["subscribe"]["market_slugs"]) <= 32 for f in frames)
    for f in frames:
        rec.guard_outbound(f)
    # chunk None keeps the original single frame per type
    assert len(rec.subscribe_frames(slugs, None)) == 2
    assert len(rec.subscribe_frames(slugs[:10], chunk=32)) == 2


def test_classify_frame_names_every_kind():
    assert rec.classify_frame({"heartbeat": {}}) == "heartbeat"
    assert rec.classify_frame({"request_id": "mkt0-x", "error": "bad"}) == "error"
    assert rec.classify_frame({"request_id": "mkt0-x",
                               "market_data_subscription_snapshot": {"eof": True}}) == "ack"
    book = {"market_data": {"marketSlug": "m-1", "bids": [{"px": {"value": "0.45"}, "qty": "1"}],
                            "offers": [{"px": {"value": "0.47"}, "qty": "1"}]}}
    assert rec.classify_frame(book) == "book"
    trade = {"trade": {"marketSlug": "m-1", "price": {"value": "0.46"}, "qty": "2",
                       "tradeTime": "2026-09-07T17:00:00Z"}}
    assert rec.classify_frame(trade) == "trade"
    assert rec.classify_frame({"unknown": 1}) == "other"
    assert rec.classify_frame([]) == "other"


def test_stream_health_spans_reconnects_and_drives_a_bounded_starvation_reconnect(monkeypatch):
    monkeypatch.setattr(rec, "STARVED_S", 120.0)
    monkeypatch.setattr(rec, "STARVED_RECONNECT_MAX", 2)
    h = rec.StreamHealth(1000.0)
    h.note("ack", 1001.0)
    h.note("book", 1002.0)
    h.note("trade", 1003.0)
    snap = h.snapshot(1010.0)
    assert snap["frames_60s"] == 3 and snap["books_300s"] == 1 and snap["trades_300s"] == 1
    assert snap["last_book_age_s"] == 7.0 and snap["counts"]["ack"] == 1
    # no book for 120 s while live events exist -> reconnect; not without live events
    assert rec.should_reconnect_starved(h, 40, 1122.0, 0) is False   # 119 s since trade
    assert rec.should_reconnect_starved(h, 40, 1123.5, 0) is True
    assert rec.should_reconnect_starved(h, 0, 1200.0, 0) is False
    assert rec.should_reconnect_starved(h, 40, 1200.0, 2) is False   # bounded
    # a new session keeps the window (books_300s spans connections)
    h.new_session(1200.0)
    assert h.counts["book"] == 0
    assert h.snapshot(1250.0)["books_300s"] == 1
    assert h.snapshot(1350.0)["books_300s"] == 0
    # a session with no frame at all is measured from its start
    h2 = rec.StreamHealth(2000.0)
    assert rec.should_reconnect_starved(h2, 5, 2100.0, 0) is False
    assert rec.should_reconnect_starved(h2, 5, 2121.0, 0) is True


def test_stream_alarm_fires_once_per_window_and_clears_on_market_data(capsys, monkeypatch):
    monkeypatch.setattr(rec, "STREAM_ALARM_S", 300.0)
    rec._STREAM_STARVED_SINCE.update({"t": None, "last_alarm": None})
    dead = {"books_300s": 0, "trades_300s": 0}
    a = rec.stream_alarm(dead, live_events=53, subscribed=128, now=1000.0)
    assert a["alarm"] is False and a["starved_for_s"] == 0.0
    assert a["reason"].startswith("no book/trade frame")
    a = rec.stream_alarm(dead, 53, 128, now=1301.0)
    assert a["alarm"] is True
    a = rec.stream_alarm(dead, 53, 128, now=1400.0)
    assert a["alarm"] is True                    # still alarmed, not re-printed
    out = capsys.readouterr().out
    assert out.count("PMUS_WS_ALARM") == 1 and "stream_starved" in out
    # a book frame clears it; no live events never alarms
    a = rec.stream_alarm({"books_300s": 1, "trades_300s": 0}, 53, 128, now=1500.0)
    assert a["alarm"] is False and a["reason"] is None
    a = rec.stream_alarm(dead, 0, 128, now=2000.0)
    assert a["alarm"] is False
    rec._STREAM_STARVED_SINCE.update({"t": None, "last_alarm": None})


def test_gateway_xcheck_shows_a_frozen_socket_book_and_never_touches_state():
    now = 1000.0
    state = {"cs2": {"bid": "0.6000", "ask": "0.6100", "book_updates": 1, "trades": [],
                     "bbo_history": [{"t": now - 259.0, "bid": "0.6000", "ask": "0.6100"}]}}
    calls = []
    def http_get_json(path):
        calls.append(path)
        return {"marketData": {"marketSlug": "cs2",
                               "bestBid": {"value": "0.7000", "currency": "USD"},
                               "bestAsk": {"value": "0.7100", "currency": "USD"},
                               "lastTradePx": {"value": "0.7000", "currency": "USD"},
                               "sharesTraded": "8122.0600",
                               "lastPriceSample": {"ts": "2026-09-07T17:21:19Z"},
                               "state": "MARKET_STATE_OPEN"}}
    before = json.dumps(state, sort_keys=True)
    rows = rec.gateway_xcheck(["cs2", "missing"], state, http_get_json, now, limit=3)
    assert calls == ["/v1/markets/cs2/bbo", "/v1/markets/missing/bbo"]
    r = rows[0]
    assert r["gateway_bid"] == "0.7000" and r["gateway_ask"] == "0.7100"
    assert r["gateway_last_trade_px"] == "0.7000" and r["gateway_shares_traded"] == "8122.0600"
    assert r["ws_bid"] == "0.6000" and r["ws_book_updates"] == 1
    assert r["ws_last_update_age_s"] == 259.0 and r["book_diverged"] is True
    assert "never evidence" in r["labels"]
    assert rows[1]["ws_bid"] is None and rows[1]["book_diverged"] is False
    assert json.dumps(state, sort_keys=True) == before
    # a gateway failure is a field, never an exception
    def boom(path):
        raise OSError("timed out")
    rows = rec.gateway_xcheck(["cs2"], state, boom, now)
    assert rows[0]["gateway_error"].startswith("OSError") and rows[0]["book_diverged"] is None


def test_status_carries_stream_health_and_the_stream_alarm(monkeypatch):
    import time
    now = time.time()
    rec._STREAM_STARVED_SINCE.update({"t": None, "last_alarm": None})
    state = {"live-c": {"bid": "0.50", "ask": "0.52", "bid_qty": "10", "ask_qty": "10",
                        "both_sides_served": True, "book_updates": 3, "trades": [],
                        "bbo_history": [{"t": now - 5, "bid": "0.50", "ask": "0.52"}]}}
    disc = {"v1_live_events": 53, "fresh_live_subscribed": 128}
    sh = {"books_300s": 0, "trades_300s": 0, "frames_60s": 0, "starved_reconnects": 1}
    st = rec.build_status(state, True, 90, 128, now=now, discovery=disc, stream_health=sh)
    assert st["stream_health"]["starved_reconnects"] == 1
    assert st["stream_alarm"]["live_events"] == 53 and st["stream_alarm"]["alarm"] is False
    st2 = rec.build_status(state, True, 90, 128, now=now + rec.STREAM_ALARM_S + 1,
                           discovery=disc, stream_health=sh)
    assert st2["stream_alarm"]["alarm"] is True
    assert "stream_health" not in rec.build_status(state, True, 90, 128, now=now, discovery=disc)
    rec._STREAM_STARVED_SINCE.update({"t": None, "last_alarm": None})


def test_snapshot_state_never_aliases_the_live_lists_and_health_tracks_processing_time():
    live = {"m": {"bid": "0.45", "trades": [{"t": 1.0}], "bbo_history": [{"t": 1.0}],
                  "book_updates": 1}}
    snap = rec.snapshot_state(live)
    live["m"]["trades"].append({"t": 2.0})
    live["m"]["bbo_history"].append({"t": 2.0})
    live["m"]["book_updates"] = 2
    assert len(snap["m"]["trades"]) == 1 and len(snap["m"]["bbo_history"]) == 1
    assert snap["m"]["book_updates"] == 1
    h = rec.StreamHealth(0.0)
    h.note_proc(0.2)
    h.note_proc(1.5)
    s = h.snapshot(10.0)
    assert s["max_frame_proc_s"] == 1.5 and s["sum_frame_proc_s"] == 1.7
    h.new_session(20.0)
    assert h.snapshot(21.0)["max_frame_proc_s"] == 0.0
    r = rec.rss_gb()
    assert r is None or (isinstance(r, float) and r >= 0.0)


def test_raw_tape_retention_frees_the_volume_oldest_first(tmp_path, capsys):
    """LIVE 2026-09-08 00:40Z: the unbounded daily tape filled the 50GB
    volume; the recorder crash-looped on ENOSPC and the probe lost its
    evidence source. The raw directory is now bounded: oldest parts go
    first, the part being written is never deleted, and the reader still
    hydrates across daily files + hourly parts (newest bytes first)."""
    from datetime import datetime, timezone
    raw = tmp_path / "raw"
    raw.mkdir()
    now = datetime(2026, 9, 8, 1, 5, tzinfo=timezone.utc)
    # legacy daily files + hourly parts, 100 bytes each
    names = ["pmus_ws_20260905.jsonl", "pmus_ws_20260906.jsonl",
             "pmus_ws_20260907.jsonl", "pmus_ws_20260908.jsonl",
             "pmus_ws_20260908_00.jsonl", "pmus_ws_20260908_01.jsonl"]
    for n in names:
        (raw / n).write_bytes(b"x" * 100)
    assert [p.name for p in rec.tape_parts(tmp_path)] == names
    sw = rec.sweep_raw_tapes(tmp_path, max_bytes=250, now=now)
    assert sw["deleted"] == names[:4]           # oldest first, until it fits
    assert sw["freed_bytes"] == 400 and sw["kept_bytes"] == 200
    assert sw["kept_parts"] == 2 and sw["errors"] == []
    # the current part is never deleted, even when it alone exceeds the budget
    sw = rec.sweep_raw_tapes(tmp_path, max_bytes=50, now=now)
    assert sw["deleted"] == ["pmus_ws_20260908_00.jsonl"]
    assert [p.name for p in rec.tape_parts(tmp_path)] == ["pmus_ws_20260908_01.jsonl"]
    # budget 0 = unbounded (nothing deleted)
    (raw / "pmus_ws_20260908_00.jsonl").write_bytes(b"y" * 100)
    assert rec.sweep_raw_tapes(tmp_path, max_bytes=0, now=now)["deleted"] == []
    # the default budget is the env / 12GiB constant
    assert rec.raw_max_bytes() == rec.DEFAULT_RAW_MAX_BYTES == 12 * 1024 ** 3
    # append goes to the hourly part of NOW and never raises on a bad root:
    # a write error sweeps once, retries, then alarms instead of crashing
    rec.append(tmp_path, {"kind": "x"})
    assert rec.daily_path(tmp_path).exists()
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    before = rec.TAPE_WRITE_ERRORS["n"]
    rec.TAPE_WRITE_ERRORS["last_alarm"] = 0.0
    rec.append(blocked, {"kind": "x"})          # raw/ cannot be created
    assert rec.TAPE_WRITE_ERRORS["n"] == before + 1
    out = capsys.readouterr().out
    assert "PMUS_WS_ALARM" in out and "tape_write_failed" in out


def test_hydration_reads_across_daily_and_hourly_parts_newest_first(tmp_path):
    from datetime import datetime, timezone
    now = datetime(2026, 9, 8, 1, 5, tzinfo=timezone.utc).timestamp()
    raw = tmp_path / "raw"
    raw.mkdir()

    def row(t, tag):
        return json.dumps({"kind": "ws",
                           "t": datetime.fromtimestamp(t, timezone.utc).isoformat(),
                           "msg": {"tag": tag}})
    (raw / "pmus_ws_20260906_23.jsonl").write_text(row(now - 90000, "old") + "\n")
    (raw / "pmus_ws_20260907.jsonl").write_text(row(now - 3000, "daily") + "\n")
    (raw / "pmus_ws_20260908_00.jsonl").write_text(row(now - 2000, "h00") + "\n")
    (raw / "pmus_ws_20260908_01.jsonl").write_text(row(now - 100, "h01") + "\n")
    tags = [m["tag"] for _, m in rec.iter_recent_tape_rows(tmp_path, now)]
    assert tags == ["daily", "h00", "h01"]      # two days back is not read
    # the byte bound takes the NEWEST bytes across parts
    one = list(rec.iter_recent_tape_rows(tmp_path, now, max_bytes=90))
    assert [m["tag"] for _, m in one] == ["h01"]
