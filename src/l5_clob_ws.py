"""Dual-token CLOB websocket tape. STRICT-TIMING SOURCE. READ-ONLY.

Why this exists
---------------
`src/l5_book_shadow.py` polls REST. A REST row proves REACHABILITY and
SAME-SECOND co-occurrence and nothing finer: two rows one poll apart cannot
order two book changes that happened inside that poll. The EXTEND->COMPLETE
turn is a question about ORDER of arrival on the two tokens, so REST cannot
answer it even in principle.

This module is the strict-timing arm. It holds one websocket per outcome
token, stamps every frame on receipt, and appends it verbatim.

Hard boundaries
---------------
* It is a SEPARATE tape stream (`wstape`) under a SEPARATE schema
  (`l5_clob_ws/1`). Nothing here rewrites, re-labels or re-interprets an
  existing REST row. The two arms are joined downstream, by market/token,
  never merged in place.
* Every row carries `timing_authority`. REST rows say
  `rest_same_second_reachability`; rows from here say `websocket_strict`.
  A downstream consumer that mixes them without reading that field is
  making a claim this collector never made.
* Read-only. The only frames it ever sends are the subscribe payload, a
  pong, and a close. `guard_outbound()` refuses anything else structurally,
  so there is no order verb to reach with or without an env var.
* No credentials. The market channel is public; the handshake sends no
  authentication material and `boot_gate()` (reused from the REST collector)
  still fails closed if an order-capable module is importable.

The venue contract is NOT guessed
---------------------------------
`DEFAULT_WS_URL` and `subscribe_payload()` are the venue's documented
market-channel shape, but this module treats them as UNCONFIRMED until a
live session proves them: `probe()` returns the first frames verbatim and
`SubscriptionProof` records what was actually accepted. Until a proof row
exists, no analysis may treat an absent frame as an absent book change --
the same rule as BOOK_ONLY_ACTIVITY_UNAVAILABLE on the REST arm.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from src.l5_book_shadow import (
    Pacer,
    TapeWriter,
    boot_gate,
    now_utc_iso,
)

SCHEMA_VERSION = "l5_clob_ws/1"

#: The strict-timing label. The REST arm's label is deliberately different.
TIMING_WEBSOCKET = "websocket_strict"
TIMING_REST = "rest_same_second_reachability"

#: UNVERIFIED. Overridable so a venue change is a configuration change,
#: never a code guess.
#:
#: DNS evidence, this container, 2026-10-07T04:4xZ:
#:   ws-subscribe.polymarket.com        NXDOMAIN  <- my first guess. WRONG.
#:   ws-subscribe-clob.polymarket.com   NXDOMAIN
#:   ws.polymarket.com                  NXDOMAIN
#:   stream.polymarket.com              NXDOMAIN
#:   ws-live-data.polymarket.com        172.64.153.51   resolves
#:   clob.polymarket.com                172.64.153.51   resolves
#: clob/gamma/github all resolve, so this is the HOSTNAME being wrong
#: rather than the container's DNS being restricted.
#:
#: A handshake against BOTH resolving candidates returns HTTP 403 from this
#: container's egress proxy -- the same 403 it returns for
#: data-api.polymarket.com, which works fine from Railway. So the endpoint
#: CANNOT be settled from here and must be probed from the deployed host.
#:
#: Until SubscriptionProof.status reads PROVEN on a deployed probe this is
#: a CANDIDATE, not a contract, and no websocket row may be treated as a
#: strict-timing observation.
#: 2026-10-07: the venue's OWN documentation names the market channel
#: endpoint, and it is none of the three hosts I guessed. Two deploys of
#: evidence before looking it up:
#:   ws-subscribe.polymarket.com           NXDOMAIN
#:   ws-live-data.polymarket.com           resolves, handshake 403 FROM
#:                                         RAILWAY -- so not a proxy issue
#:   ws-subscriptions-clob.polymarket.com  resolves, DOCUMENTED  <- this one
#: Source: docs.polymarket.com/developers/CLOB/websocket/wss-overview
WS_URL_CANDIDATES = (
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
    "wss://ws-live-data.polymarket.com/ws/market",
    "wss://clob.polymarket.com/ws/market",
)
DEFAULT_WS_URL = WS_URL_CANDIDATES[0]
ENV_WS_URL = "CLOB_WS_URL"

#: Until this is proven live, a gap in frames is UNMEASURED, not quiet.
WS_UNPROVEN_LABEL = "WS_SUBSCRIPTION_UNPROVEN"
WS_UNAVAILABLE_LABEL = "BOOK_ONLY_WS_UNAVAILABLE"

MAX_FRAME_BYTES = 8 << 20
PING_INTERVAL_S = 20.0
RECV_TIMEOUT_S = 30.0

#: The complete set of frames this collector is permitted to send.
ALLOWED_OUTBOUND_KINDS = ("subscribe", "pong", "close")


# ============================================================ outbound guard

class OutboundRefused(RuntimeError):
    """Raised instead of sending a frame this collector may not send."""


def guard_outbound(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    """Structural refusal, not a flag.

    A collector that cannot form an order frame cannot send one by mistake,
    by configuration, or by a future edit that forgets why this is here.
    """
    kind = frame.get("kind")
    if kind not in ALLOWED_OUTBOUND_KINDS:
        raise OutboundRefused(
            "websocket collector may only send %s; refused kind=%r"
            % (", ".join(ALLOWED_OUTBOUND_KINDS), kind))
    return frame


# =============================================================== RFC 6455

class ProtocolError(RuntimeError):
    pass


OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def encode_frame(opcode: int, payload: bytes,
                 mask_key: Optional[bytes] = None) -> bytes:
    """A single final, client-masked frame.

    A client MUST mask (RFC 6455 s5.3); a server that sees an unmasked
    client frame is required to drop the connection, so this is not
    optional politeness.
    """
    if mask_key is None:
        mask_key = os.urandom(4)
    if len(mask_key) != 4:
        raise ProtocolError("mask key must be 4 bytes")
    n = len(payload)
    out = bytearray([0x80 | opcode])
    if n < 126:
        out.append(0x80 | n)
    elif n < (1 << 16):
        out.append(0x80 | 126)
        out += struct.pack("!H", n)
    else:
        out.append(0x80 | 127)
        out += struct.pack("!Q", n)
    out += mask_key
    out += bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(out)


def _read_exactly(read: Callable[[int], bytes], n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = read(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed mid-frame")
        buf += chunk
    return bytes(buf)


def decode_frame(read: Callable[[int], bytes]) -> Dict[str, Any]:
    """One frame off the wire. Returns {fin, opcode, payload}."""
    b0, b1 = _read_exactly(read, 2)
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exactly(read, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exactly(read, 8))[0]
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("frame of %d bytes exceeds cap" % length)
    key = _read_exactly(read, 4) if masked else b""
    payload = _read_exactly(read, length) if length else b""
    if masked:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return {"fin": fin, "opcode": opcode, "payload": payload}


def parse_ws_url(url: str) -> Dict[str, Any]:
    if url.startswith("wss://"):
        secure, rest = True, url[6:]
    elif url.startswith("ws://"):
        secure, rest = False, url[5:]
    else:
        raise ValueError("not a websocket url: %r" % url)
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return {"secure": secure, "host": host,
            "port": int(port) if port else (443 if secure else 80),
            "path": "/" + path}


class WebSocketClient:
    """The smallest client that is actually correct on the market channel.

    Deliberately stdlib-only: the deployed image is python:3.11-slim with
    two files in it, and adding a dependency to the collector would widen
    the image that the boot gate exists to keep narrow.
    """

    def __init__(self, url: str, *, timeout: float = RECV_TIMEOUT_S,
                 ca_bundle: Optional[str] = None,
                 connect: Optional[Callable[..., Any]] = None) -> None:
        self.url = url
        self.parts = parse_ws_url(url)
        self.timeout = timeout
        self.ca_bundle = ca_bundle
        self._connect = connect or socket.create_connection
        self.sock: Any = None
        self.handshake: Dict[str, Any] = {}
        self._buf = bytearray()

    # -- lifecycle ------------------------------------------------------
    def connect(self) -> Dict[str, Any]:
        p = self.parts
        raw = self._connect((p["host"], p["port"]), timeout=self.timeout)
        if p["secure"]:
            ctx = ssl.create_default_context(cafile=self.ca_bundle)
            raw = ctx.wrap_socket(raw, server_hostname=p["host"])
        self.sock = raw
        nonce = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: l5-clob-ws (read-only research collector)\r\n"
            "\r\n" % (p["path"], p["host"], nonce)
        )
        self.sock.sendall(req.encode("ascii"))
        head = self._read_http_head()
        status = head.split("\r\n", 1)[0]
        self.handshake = {"status_line": status,
                          "switching_protocols": " 101 " in status,
                          "url": self.url}
        if not self.handshake["switching_protocols"]:
            raise ConnectionError("handshake refused: %s" % status)
        return self.handshake

    def _read_http_head(self) -> str:
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed during handshake")
            self._buf += chunk
        head, _, rest = bytes(self._buf).partition(b"\r\n\r\n")
        self._buf = bytearray(rest)
        return head.decode("latin-1")

    def _read(self, n: int) -> bytes:
        if self._buf:
            take = bytes(self._buf[:n])
            del self._buf[:len(take)]
            return take
        return self.sock.recv(n)

    # -- frames ---------------------------------------------------------
    def send(self, frame: Mapping[str, Any]) -> None:
        """Only `subscribe`, `pong` and `close` get past the guard."""
        guard_outbound(frame)
        kind = frame["kind"]
        if kind == "close":
            self.sock.sendall(encode_frame(OP_CLOSE, b"\x03\xe8"))
            return
        opcode = OP_PONG if kind == "pong" else OP_TEXT
        payload = frame.get("payload", b"")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        elif isinstance(payload, (dict, list)):
            payload = json.dumps(payload, separators=(",", ":")).encode()
        self.sock.sendall(encode_frame(opcode, payload))

    def recv(self) -> Optional[Dict[str, Any]]:
        """One application message, reassembled, with control frames
        handled. Returns None once the peer closes."""
        chunks: List[bytes] = []
        opcode = None
        while True:
            f = decode_frame(self._read)
            op = f["opcode"]
            if op == OP_PING:
                self.send({"kind": "pong", "payload": f["payload"]})
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                return None
            if op in (OP_TEXT, OP_BINARY):
                opcode = op
                chunks = [f["payload"]]
            elif op == OP_CONT:
                chunks.append(f["payload"])
            else:
                raise ProtocolError("unexpected opcode %r" % op)
            if f["fin"]:
                data = b"".join(chunks)
                return {"opcode": opcode, "bytes": len(data), "data": data}

    def close(self) -> None:
        try:
            if self.sock is not None:
                self.send({"kind": "close"})
        except Exception:                                      # noqa: BLE001
            pass
        finally:
            try:
                if self.sock is not None:
                    self.sock.close()
            finally:
                self.sock = None


# ========================================================== subscription

def subscribe_payload(token_ids: Iterable[str]) -> Dict[str, Any]:
    """The market-channel subscribe body.

    UNCONFIRMED until a live `SubscriptionProof` exists. Both tokens are
    named in ONE subscription on purpose: a single stream gives both
    tokens the same receive clock, which is the entire point of the
    strict-timing arm -- two sockets would reintroduce the cross-stream
    skew this module exists to remove.
    """
    return {"assets_ids": list(token_ids), "type": "market",
            "custom_feature_enabled": True}


class SubscriptionProof:
    """What the venue actually accepted, recorded rather than assumed."""

    def __init__(self) -> None:
        self.handshake: Dict[str, Any] = {}
        self.sent: Dict[str, Any] = {}
        self.first_frames: List[dict] = []
        self.tokens_seen: Dict[str, int] = {}

    def observe(self, obj: Any, token_ids: Iterable[str]) -> None:
        if len(self.first_frames) < 5:
            self.first_frames.append(
                {"preview": json.dumps(obj, sort_keys=True)[:600]})
        for tid in token_ids:
            if tid and tid in json.dumps(obj):
                self.tokens_seen[tid] = self.tokens_seen.get(tid, 0) + 1

    def report(self, token_ids: Iterable[str]) -> Dict[str, Any]:
        ids = [t for t in token_ids if t]
        both = len(ids) >= 2 and all(self.tokens_seen.get(t) for t in ids)
        return {
            "handshake": self.handshake,
            "subscribe_sent": self.sent,
            "first_frames": self.first_frames,
            "frames_per_token": dict(self.tokens_seen),
            "both_tokens_streaming": both,
            "status": "PROVEN" if both else WS_UNPROVEN_LABEL,
            "MEANING": "Until both_tokens_streaming is true, an absent "
                       "frame on a token is UNMEASURED and must not be "
                       "analysed as an absent book change.",
        }


# ================================================================ recorder

def ws_envelope(*, market: str, token: Optional[str],
                outcome: Optional[str], kind: str,
                url: str) -> Dict[str, Any]:
    """Provenance for a strict-timing row.

    `recv_monotonic_ns` is the ordering authority; wall clock can step and
    would silently reorder two frames microseconds apart.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "recv_utc": now_utc_iso(),
        "recv_unix": time.time(),
        "recv_monotonic_ns": time.monotonic_ns(),
        "venue": "polymarket",
        "market": market,
        "token": token,
        "outcome": outcome,
        "feed": "clob_ws_market",
        "ws_url": url,
        "timing_authority": TIMING_WEBSOCKET,
        "labels": "BOOK OBSERVATION ONLY -- never a fill, never realized, "
                  "never an order",
    }


class ClobWsCollector:
    """One socket, both tokens, append-only, strict receive timestamps."""

    def __init__(self, root: str, *, market: str,
                 tokens: Mapping[str, str],
                 url: Optional[str] = None,
                 pacer: Optional[Pacer] = None,
                 client_factory: Optional[Callable[[str], Any]] = None,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.market = market
        #: {token_id: outcome}. Both are recorded INDEPENDENTLY; a row
        #: never carries a derived complement price.
        self.tokens = dict(tokens)
        self.url = url or os.environ.get(ENV_WS_URL, DEFAULT_WS_URL)
        self.tape = TapeWriter(root, stream="wstape")
        self.pacer = pacer or Pacer()
        self.client_factory = client_factory or (lambda u: WebSocketClient(u))
        self._log = log or (lambda m: print(m, flush=True))
        self.proof = SubscriptionProof()
        self.frames = 0
        self.reconnects = 0
        self._down_since: Optional[float] = None

    # -- availability windows, same rule as the REST arm ----------------
    def _emit_window(self, edge: str) -> None:
        row = ws_envelope(market=self.market, token=None, outcome=None,
                          kind="ws_availability", url=self.url)
        row["state"] = (WS_UNAVAILABLE_LABEL if edge == "OPEN"
                        else "WS_RECOVERED")
        row["edge"] = edge
        row["since_unix"] = self._down_since
        row["reconnects"] = self.reconnects
        row["MEANING"] = ("Frames missing in this interval are UNMEASURED, "
                          "NOT an absence of book change, and must not be "
                          "analysed as one.")
        self.tape.write(row)

    def token_of(self, obj: Any) -> Optional[str]:
        """Which token a frame belongs to, by the venue's own id only.

        Never inferred from a price or a complement: an inferred token is
        exactly the 1-X ambiguity that cost this program an attempt.
        """
        if isinstance(obj, Mapping):
            for key in ("asset_id", "assets_id", "token_id"):
                v = obj.get(key)
                if isinstance(v, str) and v in self.tokens:
                    return v
        return None

    def record(self, obj: Any, raw_len: int) -> Dict[str, Any]:
        token = self.token_of(obj)
        row = ws_envelope(market=self.market, token=token,
                          outcome=self.tokens.get(token or ""),
                          kind="ws_book_frame", url=self.url)
        row["raw_bytes"] = raw_len
        row["payload"] = obj
        self.tape.write(row)
        self.frames += 1
        return row

    # -- session --------------------------------------------------------
    def session(self, max_frames: Optional[int] = None,
                deadline: Optional[float] = None) -> Dict[str, Any]:
        """One connect/subscribe/read cycle. Returns what it observed."""
        client = self.client_factory(self.url)
        ids = [t for t in self.tokens]
        try:
            self.proof.handshake = client.connect()
            payload = subscribe_payload(ids)
            self.proof.sent = payload
            client.send({"kind": "subscribe", "payload": payload})
            if self._down_since is not None:
                self._emit_window("CLOSE")
                self._down_since = None
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if max_frames is not None and self.frames >= max_frames:
                    break
                msg = client.recv()
                if msg is None:
                    break
                try:
                    obj = json.loads(msg["data"].decode("utf-8"))
                except ValueError:
                    obj = {"UNPARSED": msg["data"][:600].decode(
                        "utf-8", "replace")}
                for item in (obj if isinstance(obj, list) else [obj]):
                    self.proof.observe(item, ids)
                    self.record(item, msg["bytes"])
        finally:
            client.close()
        return self.proof.report(ids)

    def write_proof(self) -> Dict[str, Any]:
        rep = self.proof.report(list(self.tokens))
        self.tape.write_derived("ws_subscription_proof.json", rep)
        return rep


def probe(market: str, tokens: Mapping[str, str], root: str,
          *, seconds: float = 20.0,
          collector: Optional[ClobWsCollector] = None,
          forbidden: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Prove the venue contract before anything downstream trusts it.

    `forbidden` is scoped only so an offline test can assert the failure
    path in the full repo, where the order modules ARE importable by
    design. The deployed image passes the default, and there the gate is
    the same structural refusal the REST collector boots behind.
    """
    col = collector or ClobWsCollector(root, market=market, tokens=tokens)
    gate = (boot_gate() if forbidden is None else boot_gate(forbidden))
    if not gate["ok"]:
        return {"ok": False, "boot_gate": gate}
    try:
        rep = col.session(deadline=time.monotonic() + seconds)
    except Exception as exc:                                   # noqa: BLE001
        rep = col.proof.report(list(tokens))
        rep["error_class"] = type(exc).__name__
        rep["error"] = str(exc)[:400]
        col._down_since = col._down_since or time.time()
        col._emit_window("OPEN")
    rep["frames_recorded"] = col.frames
    col.tape.write_derived("ws_subscription_proof.json", rep)
    return rep
