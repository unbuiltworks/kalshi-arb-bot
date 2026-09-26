"""Joined BTC15m evidence: L5's actions beside the authenticated PMUS tape.

READ-ONLY BY CONSTRUCTION. This module imports no transport that can POST,
builds no order body, holds no arming variable and calls no mutation verb.
The only socket it opens is the authenticated websocket, on which it sends
the recorder's own subscribe frames through the recorder's own outbound
guard and nothing else. Tests assert all of that on the parse tree.

WHY IT EXISTS
-------------
The repair adverse-selection study needs two streams joined per window, and
the existing evidence had neither completely:

  * prints -- the account-wide recorder exports a CAPPED, deduplicated view
    (cap 200 per market per emission, in-memory state trimmed to the last
    500) spread across 758 markets, so the best-covered BTC15m window held
    862 seconds of 900 and most held a handful of seconds.
  * L5 actions -- not being recorded continuously at all.

A window that was never fully watched cannot be told apart from a quiet one
unless coverage is measured, so coverage is measured first and the study is
told which windows earned their way into it.

DURABILITY
----------
Deployment logs are ephemeral -- Probe 07's were already gone when they were
wanted. So every window is written to the volume as a self-contained record:
raw frames verbatim, book states, prints, L5 actions, and the coverage
metrics, each with its own receipt timestamp and provenance. A window can be
reconstructed months later from the file alone.

AN OBSERVED TRADE IS NOT A FILL
-------------------------------
Nothing recorded here is a fill of ours. Prints are the venue's tape, book
states are the venue's book, and queue position is neither known nor
modelled. The record carries what was observed; what a resting order of ours
would have achieved is not in it.

NOT A ONE-OFF
-------------
The record is the joined sequential history -- every L5 action in window
order with the book and the tape around it. The repair study is one question
asked of it; the sequential-reconstruction programme is the reason it is
shaped this way rather than as a repair-specific extract.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

WINDOW_S = 900.0

#: a window enters the PREREGISTERED primary study only if it clears all of
#: these. They are properties of the OBSERVATION, never of the result.
PRIMARY_MIN_OBSERVED_FRACTION = 0.95
PRIMARY_MAX_GAP_S = 20.0
PRIMARY_MIN_PRINTS = 1
PRIMARY_REQUIRE_L5_FEED_OK = True


def _now() -> float:
    return time.time()


#: the boundary file between the old collector and this one. Written ONCE,
#: never rewritten, so a restart cannot move the line it marks.
PRE_CUTOVER = "PRE_CUTOVER_MANIFEST.json"

#: everything this process is permitted to create, relative to the root. It
#: appends and it creates; it never deletes, truncates, renames or rewrites
#: anything that was already on the volume.
WRITES_UNDER = ("windows/", PRE_CUTOVER)


def sha256_file(path: Path) -> Dict[str, Any]:
    import hashlib
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            n += len(b)
            h.update(b)
    return {"sha256": h.hexdigest(), "bytes": n}


def inventory(root: str, *, skip=(PRE_CUTOVER,)) -> List[Dict[str, Any]]:
    """Hash every file already on the volume. Opens nothing for writing."""
    base = Path(root)
    out: List[Dict[str, Any]] = []
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(base))
        if rel in skip:
            continue
        try:
            rec = sha256_file(p)
        except OSError as exc:
            out.append({"path": rel, "error": str(exc)[:120]})
            continue
        rec["path"] = rel
        out.append(rec)
    return out


def snapshot_pre_cutover(root: str, *, note: str = "",
                         action_count: Optional[int] = None) -> Dict[str, Any]:
    """Record what was on the volume BEFORE this collector wrote anything.

    Written once. If the manifest already exists it is read back and
    returned unchanged, because the boundary between the old collector and
    this one is a fact about a moment, not about the latest restart.
    """
    import datetime as _dt
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    p = base / PRE_CUTOVER
    if p.exists():
        try:
            doc = json.loads(p.read_text())
            doc["reused_existing_manifest"] = True
            return doc
        except ValueError:
            pass                       # unreadable: fall through and write
    files = inventory(root)
    doc = {
        "schema": "l5_window_collector/pre_cutover/v1",
        "taken_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "root": str(root),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(f.get("bytes") or 0) for f in files),
        "prior_collector_action_count": action_count,
        "note": note,
        "labels": ("the boundary between the previous observer and the "
                   "joined collector. Historical files are preserved "
                   "byte-for-byte; this process only ever creates new ones "
                   "under windows/."),
    }
    tmp = base / (PRE_CUTOVER + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(doc, fh, indent=1, default=str)
    tmp.replace(p)
    doc["reused_existing_manifest"] = False
    return doc


def verify_pre_cutover(root: str) -> Dict[str, Any]:
    """Re-hash every file the manifest recorded and compare. A measurement."""
    base = Path(root)
    p = base / PRE_CUTOVER
    if not p.exists():
        return {"checked": 0, "drift": [], "all_unchanged": None,
                "reason": "NO_MANIFEST"}
    doc = json.loads(p.read_text())
    before = {f["path"]: f for f in doc.get("files") or [] if "sha256" in f}
    now = {f["path"]: f for f in inventory(root) if "sha256" in f}
    drift = []
    for rel, rec in sorted(before.items()):
        cur = now.get(rel)
        if cur is None:
            drift.append({"path": rel, "problem": "MISSING"})
        elif cur["sha256"] != rec["sha256"] or cur["bytes"] != rec["bytes"]:
            drift.append({"path": rel, "problem": "CHANGED",
                          "before": rec["sha256"], "after": cur["sha256"]})
    return {"checked": len(before), "drift": drift,
            "all_unchanged": not drift,
            "taken_utc": doc.get("taken_utc"),
            "prior_collector_action_count":
                doc.get("prior_collector_action_count"),
            "labels": "historical evidence re-hashed after the run, not assumed"}


class Coverage:
    """What fraction of the 900 seconds we actually watched, and the gaps.

    A gap is wall-clock time inside the window during which the socket was
    not connected. Missing tape is recorded as missing tape; it never
    becomes an absence of trading.
    """

    def __init__(self, window_start: float, window_end: float) -> None:
        self.window_start = float(window_start)
        self.window_end = float(window_end)
        self.segments: List[List[float]] = []       # [connected_from, to]
        self.gaps: List[Dict[str, float]] = []
        self._open: Optional[float] = None
        self.first_frame_t: Optional[float] = None
        self.last_frame_t: Optional[float] = None
        self.frames = 0
        self.reconnects = 0

    def connected(self, t: float) -> None:
        if self._open is None:
            if self.segments:
                self.reconnects += 1
                prev_end = self.segments[-1][1]
                if t > prev_end:
                    self.gaps.append({"from": prev_end, "to": t,
                                      "seconds": t - prev_end})
            self._open = t
            self.segments.append([t, t])

    def disconnected(self, t: float) -> None:
        if self._open is not None and self.segments:
            self.segments[-1][1] = max(self.segments[-1][1], t)
        self._open = None

    def frame(self, t: float) -> None:
        self.frames += 1
        if self.first_frame_t is None:
            self.first_frame_t = t
        self.last_frame_t = t
        if self.segments:
            self.segments[-1][1] = max(self.segments[-1][1], t)

    def observed_seconds(self) -> float:
        total = 0.0
        for a, b in self.segments:
            lo = max(a, self.window_start)
            hi = min(b, self.window_end)
            if hi > lo:
                total += hi - lo
        return total

    def observed_fraction(self) -> float:
        span = self.window_end - self.window_start
        return 0.0 if span <= 0 else min(1.0, self.observed_seconds() / span)

    def max_gap_s(self) -> float:
        inside = [g["seconds"] for g in self.gaps
                  if g["to"] > self.window_start and g["from"] < self.window_end]
        lead = (0.0 if not self.segments
                else max(0.0, min(self.segments[0][0], self.window_end)
                         - self.window_start))
        tail = (self.window_end - self.window_start if not self.segments
                else max(0.0, self.window_end
                         - min(max(b for _, b in self.segments),
                               self.window_end)))
        return max([0.0, lead, tail] + inside)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "window_start": self.window_start, "window_end": self.window_end,
            "observed_seconds": round(self.observed_seconds(), 3),
            "observed_fraction": round(self.observed_fraction(), 6),
            "max_gap_s": round(self.max_gap_s(), 3),
            "gaps": [{k: round(v, 3) for k, v in g.items()} for g in self.gaps],
            "segments": [[round(a, 3), round(b, 3)] for a, b in self.segments],
            "reconnects": self.reconnects,
            "frames": self.frames,
            "first_frame_t": self.first_frame_t,
            "last_frame_t": self.last_frame_t,
            "labels": ("observed_fraction is wall-clock socket coverage of "
                       "the window. Missing tape is missing tape and is "
                       "never reported as an absence of trading."),
        }


def primary_eligible(cov: Mapping[str, Any], prints: int,
                     l5_feed_ok: bool) -> Dict[str, Any]:
    """Does this window enter the preregistered primary analysis?

    Every criterion is about the OBSERVATION. Nothing here looks at what the
    window showed, so a window cannot be admitted or excluded for being
    convenient.
    """
    reasons: List[str] = []
    if float(cov.get("observed_fraction") or 0) < PRIMARY_MIN_OBSERVED_FRACTION:
        reasons.append("OBSERVED_FRACTION_BELOW_%.2f"
                       % PRIMARY_MIN_OBSERVED_FRACTION)
    if float(cov.get("max_gap_s") or 0) > PRIMARY_MAX_GAP_S:
        reasons.append("GAP_ABOVE_%.0fs" % PRIMARY_MAX_GAP_S)
    if int(prints) < PRIMARY_MIN_PRINTS:
        reasons.append("NO_PRINTS")
    if PRIMARY_REQUIRE_L5_FEED_OK and not l5_feed_ok:
        reasons.append("L5_FEED_NOT_OK")
    return {"primary": not reasons, "excluded_for": reasons,
            "thresholds": {
                "min_observed_fraction": PRIMARY_MIN_OBSERVED_FRACTION,
                "max_gap_s": PRIMARY_MAX_GAP_S,
                "min_prints": PRIMARY_MIN_PRINTS,
                "require_l5_feed_ok": PRIMARY_REQUIRE_L5_FEED_OK},
            "labels": ("an incomplete window is KEPT and labelled, never "
                       "dropped silently and never read as no interaction")}


class RawTapeFeed:
    """The authenticated websocket, with every frame written down verbatim.

    Reuses the recorder's URL, subscribe frames, outbound guard and event
    fold -- the proven primitives -- rather than a second interpretation of
    the same stream. It adds one thing: the raw frame reaches the volume
    before it reaches any state, so the record does not depend on our parse
    being right.
    """

    def __init__(self, market: str, sink, *, clock=_now) -> None:
        self.market = market
        self.sink = sink                      # callable(dict) -> None
        self.clock = clock
        self.state: Dict[str, Any] = {}
        self.connected = False
        self.errors: List[str] = []
        self.coverage: Optional[Coverage] = None
        self.stop_reason = "not_started"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.book_states: List[Dict[str, Any]] = []
        self._last_book = None

    # --------------------------------------------------------------- read

    def market_state(self) -> Dict[str, Any]:
        return dict(self.state.get(self.market) or {})

    def prints(self) -> List[Dict[str, Any]]:
        return list((self.state.get(self.market) or {}).get("trades") or [])

    def ingest(self, msg: Any) -> None:
        from src import pmus_ws_evidence_recorder as R
        t = self.clock()
        self.sink({"kind": "ws", "t": t, "market": self.market, "msg": msg})
        if self.coverage is not None:
            self.coverage.frame(t)
        R.apply_events(self.state, msg, t)
        s = self.state.get(self.market)
        if not s:
            return
        key = (s.get("bid"), s.get("ask"), s.get("bid_qty"), s.get("ask_qty"))
        if key != self._last_book:
            self._last_book = key
            with self._lock:
                self.book_states.append({
                    "t": t, "bid": s.get("bid"), "ask": s.get("ask"),
                    "bid_qty": s.get("bid_qty"), "ask_qty": s.get("ask_qty"),
                    "state": s.get("state")})

    # -------------------------------------------------------------- drive

    def start(self, coverage: Coverage) -> None:
        self.coverage = coverage
        self.stop_reason = "running"
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="l5-window-collector-feed")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=8.0)
        if self.coverage is not None:
            self.coverage.disconnected(self.clock())

    def _run(self) -> None:                                # pragma: no cover
        while not self._stop.is_set():
            try:
                asyncio.run(self._session())
            except BaseException as exc:                   # noqa: BLE001
                self.errors.append("%s: %s"
                                   % (type(exc).__name__, str(exc)[:160]))
            self.connected = False
            if self.coverage is not None:
                self.coverage.disconnected(self.clock())
            if self._stop.is_set():
                break
            time.sleep(1.0)                 # self-recovering, 24/7
        self.stop_reason = ("local_cancellation" if self._stop.is_set()
                            else "remote_close")

    async def _session(self) -> None:                      # pragma: no cover
        import websockets

        from src import pmus_ws_evidence_recorder as R
        headers = R.auth_headers(os.environ[R.KEY_ENV].strip(),
                                 os.environ[R.SECRET_ENV].strip())
        async with websockets.connect(R.WS_URL, additional_headers=headers,
                                      max_size=8 * 1024 * 1024) as ws:
            self.connected = True
            if self.coverage is not None:
                self.coverage.connected(self.clock())
            for frame in R.subscribe_frames([self.market]):
                await ws.send(json.dumps(R.guard_outbound(frame)))
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    self.ingest(json.loads(raw))
                except ValueError:
                    continue
        self.connected = False


class WindowRecord:
    """One BTC15m window, written to the volume as a self-contained file."""

    def __init__(self, root: str, slug: str, window_start: float) -> None:
        self.root = Path(root)
        self.slug = slug
        self.window_start = float(window_start)
        self.window_end = self.window_start + WINDOW_S
        self.dir = self.root / "windows" / slug
        self.dir.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.dir / "frames.jsonl"
        self._raw = None
        self.raw_frames = 0

    def open_raw(self):
        self._raw = open(self.raw_path, "a", buffering=1)
        return self.sink

    def sink(self, row: Mapping[str, Any]) -> None:
        if self._raw is None:
            return
        self._raw.write(json.dumps(row, default=str) + "\n")
        self.raw_frames += 1

    def close_raw(self) -> None:
        if self._raw is not None:
            self._raw.close()
            self._raw = None

    def write(self, doc: Mapping[str, Any]) -> str:
        p = self.dir / "window.json"
        tmp = self.dir / "window.json.tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=1, default=str)
        tmp.replace(p)                 # atomic: a half-written record is not
        return str(p)                  # a record
