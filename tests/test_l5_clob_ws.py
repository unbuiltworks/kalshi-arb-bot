"""Offline tests for the strict-timing websocket arm.

No socket is opened: a fake client replays frames, so the frame codec,
the outbound guard, the token attribution and the availability marking are
all tested without the venue.
"""

import json

import pytest

from src import l5_clob_ws as ws


# ------------------------------------------------------------- RFC 6455

def _reader(data: bytes):
    buf = bytearray(data)

    def read(n):
        take = bytes(buf[:n])
        del buf[:len(take)]
        return take
    return read


def test_client_frames_are_masked_and_round_trip():
    frame = ws.encode_frame(ws.OP_TEXT, b"hello")
    # RFC 6455 s5.3: a client frame MUST set the mask bit.
    assert frame[1] & 0x80
    got = ws.decode_frame(_reader(frame))
    assert got["payload"] == b"hello"
    assert got["fin"] is True
    assert got["opcode"] == ws.OP_TEXT


@pytest.mark.parametrize("n", [0, 125, 126, 70000])
def test_all_three_length_encodings_round_trip(n):
    payload = b"x" * n
    got = ws.decode_frame(_reader(ws.encode_frame(ws.OP_BINARY, payload)))
    assert got["payload"] == payload


def test_oversized_frame_is_refused_before_allocation():
    header = bytes([0x82, 127]) + (ws.MAX_FRAME_BYTES + 1).to_bytes(8, "big")
    with pytest.raises(ws.ProtocolError):
        ws.decode_frame(_reader(header))


def test_url_parsing_defaults_the_port_by_scheme():
    assert ws.parse_ws_url("wss://h/ws/market") == {
        "secure": True, "host": "h", "port": 443, "path": "/ws/market"}
    assert ws.parse_ws_url("ws://h:7/x")["port"] == 7
    with pytest.raises(ValueError):
        ws.parse_ws_url("https://h/x")


# ------------------------------------------------------- outbound guard

def test_only_subscribe_pong_and_close_may_be_sent():
    for kind in ws.ALLOWED_OUTBOUND_KINDS:
        assert ws.guard_outbound({"kind": kind})["kind"] == kind


@pytest.mark.parametrize("kind", ["order", "post", "cancel", "", None])
def test_any_order_shaped_frame_is_structurally_refused(kind):
    with pytest.raises(ws.OutboundRefused):
        ws.guard_outbound({"kind": kind})


# ------------------------------------------------------------- recorder

TOKENS = {"tokA": "UP", "tokB": "DOWN"}


class FakeClient:
    """Replays a scripted frame list; records what was sent."""

    def __init__(self, messages, handshake_ok=True):
        self._messages = list(messages)
        self.sent = []
        self.closed = False
        self._ok = handshake_ok

    def connect(self):
        if not self._ok:
            raise ConnectionError("handshake refused: HTTP/1.1 403")
        return {"status_line": "HTTP/1.1 101 Switching Protocols",
                "switching_protocols": True, "url": "wss://fake/ws/market"}

    def send(self, frame):
        ws.guard_outbound(frame)
        self.sent.append(frame)

    def recv(self):
        if not self._messages:
            return None
        obj = self._messages.pop(0)
        data = json.dumps(obj).encode()
        return {"opcode": ws.OP_TEXT, "bytes": len(data), "data": data}

    def close(self):
        self.closed = True


def _collector(tmp_path, messages, **kw):
    client = FakeClient(messages, **kw)
    col = ws.ClobWsCollector(str(tmp_path), market="m1", tokens=TOKENS,
                             url="wss://fake/ws/market",
                             client_factory=lambda u: client,
                             log=lambda m: None)
    return col, client


def test_both_tokens_are_recorded_independently(tmp_path):
    col, client = _collector(tmp_path, [
        {"asset_id": "tokA", "bids": [["0.49", "10"]]},
        {"asset_id": "tokB", "bids": [["0.50", "10"]]},
    ])
    rep = col.session()
    assert rep["both_tokens_streaming"] is True
    assert rep["status"] == "PROVEN"
    rows = [r for r in col.tape.read_all() if r["kind"] == "ws_book_frame"]
    assert [r["token"] for r in rows] == ["tokA", "tokB"]
    # Outcome comes from the venue's token id, never from a complement.
    assert [r["outcome"] for r in rows] == ["UP", "DOWN"]
    assert client.closed is True


def test_rows_are_labelled_strict_timing_and_carry_monotonic_order(tmp_path):
    col, _ = _collector(tmp_path, [{"asset_id": "tokA"},
                                   {"asset_id": "tokB"}])
    col.session()
    rows = [r for r in col.tape.read_all() if r["kind"] == "ws_book_frame"]
    assert all(r["timing_authority"] == ws.TIMING_WEBSOCKET for r in rows)
    assert ws.TIMING_WEBSOCKET != ws.TIMING_REST
    ns = [r["recv_monotonic_ns"] for r in rows]
    assert ns == sorted(ns)
    assert all(r["schema_version"] == "l5_clob_ws/1" for r in rows)


def test_the_tape_stream_is_separate_from_the_rest_arm(tmp_path):
    col, _ = _collector(tmp_path, [{"asset_id": "tokA"}])
    col.session()
    names = sorted(p.name for p in (tmp_path / "raw").glob("*.jsonl"))
    # REST parts are tape_*.jsonl; nothing here may touch them.
    assert names and all(n.startswith("wstape_") for n in names)


def test_a_token_is_never_inferred_from_a_price(tmp_path):
    col, _ = _collector(tmp_path, [{"price": "0.51", "bids": []}])
    col.session()
    row = [r for r in col.tape.read_all()
           if r["kind"] == "ws_book_frame"][0]
    assert row["token"] is None
    assert row["outcome"] is None


def test_one_token_streaming_is_not_proven(tmp_path):
    col, _ = _collector(tmp_path, [{"asset_id": "tokA"}] * 3)
    rep = col.session()
    assert rep["both_tokens_streaming"] is False
    assert rep["status"] == ws.WS_UNPROVEN_LABEL
    assert "UNMEASURED" in rep["MEANING"]


def test_unparsable_payload_is_kept_verbatim_not_dropped(tmp_path):
    class Garbage(FakeClient):
        def recv(self):
            if self._messages:
                self._messages.pop()
                return {"opcode": ws.OP_TEXT, "bytes": 3, "data": b"{ox"}
            return None

    client = Garbage([1])
    col = ws.ClobWsCollector(str(tmp_path), market="m1", tokens=TOKENS,
                             url="wss://fake/ws/market",
                             client_factory=lambda u: client,
                             log=lambda m: None)
    col.session()
    row = [r for r in col.tape.read_all()
           if r["kind"] == "ws_book_frame"][0]
    assert "UNPARSED" in row["payload"]


def test_a_failed_session_opens_an_unavailability_window(tmp_path):
    client = FakeClient([], handshake_ok=False)
    col = ws.ClobWsCollector(str(tmp_path), market="m1", tokens=TOKENS,
                             url="wss://fake/ws/market",
                             client_factory=lambda u: client,
                             log=lambda m: None)
    rep = ws.probe("m1", TOKENS, str(tmp_path), collector=col,
                   forbidden=())
    assert rep["error_class"] == "ConnectionError"
    assert rep["status"] == ws.WS_UNPROVEN_LABEL
    win = [r for r in col.tape.read_all() if r["kind"] == "ws_availability"]
    assert win and win[0]["state"] == ws.WS_UNAVAILABLE_LABEL
    assert "UNMEASURED" in win[0]["MEANING"]


def test_subscribe_names_both_tokens_on_one_socket(tmp_path):
    col, client = _collector(tmp_path, [{"asset_id": "tokA"}])
    col.session()
    sub = [f for f in client.sent if f["kind"] == "subscribe"]
    assert len(sub) == 1
    assert sorted(sub[0]["payload"]["assets_ids"]) == ["tokA", "tokB"]


def test_the_default_url_is_only_a_candidate_until_proven():
    """The first hostname I wrote did not exist (NXDOMAIN).

    So the default is recorded as one of several CANDIDATES, each a
    resolving host, and nothing downstream may treat a websocket row as a
    strict-timing observation until a deployed SubscriptionProof reads
    PROVEN. A plausible-looking constant is not a venue contract.
    """
    assert ws.DEFAULT_WS_URL in ws.WS_URL_CANDIDATES
    assert len(ws.WS_URL_CANDIDATES) >= 2
    for url in ws.WS_URL_CANDIDATES:
        parsed = ws.parse_ws_url(url)
        assert parsed["secure"] is True
        assert parsed["host"].endswith("polymarket.com")
    # The dead hostname must not come back.
    assert not any("ws-subscribe.polymarket.com" in u
                   for u in ws.WS_URL_CANDIDATES)


def test_discovery_unpacks_the_selection_tuple(monkeypatch):
    """Regression: select_l5_active returns (selections, actions).

    Iterating the tuple itself yields two lists, neither with .tokens, so
    every market reads as single-token and the probe refuses -- which is
    exactly what happened on the first Railway probe.
    """
    import scripts.l5_clob_ws_run as runner
    from src.l5_book_shadow import Selection

    sel = Selection(venue="polymarket", market="btc-15m-x",
                    tokens=[{"token": "A", "outcome": "Up"},
                            {"token": "B", "outcome": "Down"}],
                    selector="l5_active", why="test")
    monkeypatch.setattr(runner, "select_l5_active",
                        lambda *a, **k: ([sel], []), raising=False)
    import src.l5_book_shadow as _bs
    monkeypatch.setattr(_bs, "select_l5_active",
                        lambda *a, **k: ([sel], []))
    monkeypatch.setattr(_bs.PolymarketVenue, "__init__",
                        lambda self, *a, **k: None)

    market, tokens = runner.discover_two_token_market()
    assert market == "btc-15m-x"
    assert tokens == {"A": "Up", "B": "Down"}
