"""Authenticated READ-ONLY Polymarket US market-data WS recorder.

Issue #167 comment 5515986195. Subscribes to the authenticated markets
stream (`wss://api.polymarket.us/v1/ws/markets`) — full order book,
market stats, and the real-time TRADE tape that the public gateway
structurally withholds — and appends every received message verbatim to
daily JSONL under a persistent root. This closes the PMUS evidence-gate
blocker (true two-sided book + trade tape).

STRUCTURALLY READ-ONLY: the only frames this process ever sends are
subscribe, unsubscribe, and heartbeat echoes (see OUTBOUND_FRAME_KEYS —
enforced at send time AND by structural tests). No order endpoint of any
kind, no HTTP mutation, and no private-stream subscription exists in
this worker. Credentials grant read access to market data only as used
here.

Environment (names only; never commit values):
  PMUS_API_KEY_ID       API key identifier from polymarket.us/developer
                        (opaque string, e.g. UUID-like) — sent as
                        X-PM-Access-Key
  PMUS_API_SECRET_KEY   the portal's Secret Key, base64; decoded and its
                        first 32 bytes used as the Ed25519 seed (exactly
                        the scheme in the official auth docs)
  PMUS_WS_ROOT          state root (default /state/pmus-ws)
  PMUS_WS_MARKETS       optional comma list of market slugs; when empty,
                        targets are discovered from the public gateway
                        (live league markets + active-incentive markets,
                        same rules as the keyless collector)
  PMUS_WS_LEAGUES       discovery leagues (default "mlb")
  PMUS_WS_MAX_MARKETS   subscription cap (default 24)
  PMUS_WS_DISCOVERY_SECONDS  rediscovery interval (default 300)

Fails closed with NO_CREDENTIALS (exit 2) when the two key envs are
absent. Reconnects with exponential backoff (1..30s).

    python3 -m src.pmus_ws_evidence_recorder            # runs forever
    python3 -m src.pmus_ws_evidence_recorder --minutes 30
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

WS_URL = "wss://api.polymarket.us/v1/ws/markets"
WS_PATH = "/v1/ws/markets"

KEY_ENV = "PMUS_API_KEY_ID"
SECRET_ENV = "PMUS_API_SECRET_KEY"
ROOT_ENV = "PMUS_WS_ROOT"
DEFAULT_ROOT = "/state/pmus-ws"
MARKETS_ENV = "PMUS_WS_MARKETS"
LEAGUES_ENV = "PMUS_WS_LEAGUES"
MAX_MARKETS_ENV = "PMUS_WS_MAX_MARKETS"
DISCOVERY_ENV = "PMUS_WS_DISCOVERY_SECONDS"

SUB_MARKET_DATA = 1
SUB_TRADE = 3
OUTBOUND_FRAME_KEYS = frozenset({"subscribe", "unsubscribe", "heartbeat"})


def has_credentials() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip()) and \
        bool(os.getenv(SECRET_ENV, "").strip())


def auth_headers(key_id: str, secret_b64: str,
                 path: str = WS_PATH,
                 now_ms: Optional[int] = None) -> dict:
    """Ed25519 signature over `{ts}GET{path}` — the documented scheme."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    seed = base64.b64decode(secret_b64)[:32]
    key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    sig = base64.b64encode(key.sign(f"{ts}GET{path}".encode())).decode()
    return {"X-PM-Access-Key": key_id, "X-PM-Timestamp": ts,
            "X-PM-Signature": sig}


def subscribe_frames(slugs: list[str]) -> list[dict]:
    return [{"subscribe": {"request_id": f"{kind}-{uuid.uuid4().hex[:10]}",
                           "subscription_type": st,
                           "market_slugs": sorted(slugs)}}
            for kind, st in (("mkt", SUB_MARKET_DATA), ("trd", SUB_TRADE))]


def guard_outbound(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    """Every frame leaving this process passes through here."""
    bad = set(frame) - OUTBOUND_FRAME_KEYS
    if bad:
        raise RuntimeError(f"blocked non-read-only outbound frame keys: {bad}")
    return frame


def root_dir() -> Path:
    return Path(os.getenv(ROOT_ENV, DEFAULT_ROOT))


def daily_path(root: Path, now: Optional[datetime] = None) -> Path:
    now = now or datetime.now(timezone.utc)
    return root / "raw" / f"pmus_ws_{now:%Y%m%d}.jsonl"


def append(root: Path, row: Mapping[str, Any]) -> None:
    path = daily_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def heartbeat_file(root: Path, **fields: Any) -> None:
    tmp = root / "heartbeat.tmp"
    root.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps({"t": datetime.now(timezone.utc).isoformat(),
                               "mode": "READ_ONLY_WS_RECORDER", **fields},
                              indent=1))
    os.replace(tmp, root / "heartbeat.json")


def discover_slugs() -> list[str]:
    """Public-gateway discovery, same selection rules as the collector."""
    explicit = [s.strip() for s in os.getenv(MARKETS_ENV, "").split(",")
                if s.strip()]
    if explicit:
        return explicit
    from src import pmus_sports_collector as col
    leagues = [x.strip() for x in
               os.getenv(LEAGUES_ENV, "mlb").split(",") if x.strip()]
    events_by_league = {}
    for lg in leagues:
        try:
            ev = col.http_get_json(f"/v2/leagues/{lg}/events")
            events_by_league[lg] = ev if isinstance(ev, list) \
                else ev.get("events") or []
        except Exception:
            events_by_league[lg] = []
    try:
        incentives = col.http_get_json("/v1/incentives")
    except Exception:
        incentives = {}
    targets = col.discover_targets(events_by_league, incentives,
                                   ["aec-", "a5c-"], True)
    cap = int(os.getenv(MAX_MARKETS_ENV, "24"))
    return [t["slug"] for t in targets[:cap]]


async def run(minutes: Optional[float]) -> int:
    import websockets
    root = root_dir()
    deadline = time.monotonic() + minutes * 60 if minutes else None
    backoff = 1.0
    rediscover_s = float(os.getenv(DISCOVERY_ENV, "300"))
    msgs = 0
    while deadline is None or time.monotonic() < deadline:
        slugs = discover_slugs()
        append(root, {"kind": "recorder_start",
                      "t": datetime.now(timezone.utc).isoformat(),
                      "subscribed": slugs, "read_only": True,
                      "note": "subscribe/heartbeat frames only; no order "
                              "path exists in this worker"})
        try:
            headers = auth_headers(os.environ[KEY_ENV].strip(),
                                   os.environ[SECRET_ENV].strip())
            async with websockets.connect(
                    WS_URL, additional_headers=headers,
                    max_size=8 * 1024 * 1024) as ws:
                backoff = 1.0
                for frame in subscribe_frames(slugs):
                    await ws.send(json.dumps(guard_outbound(frame)))
                session_start = time.monotonic()
                while deadline is None or time.monotonic() < deadline:
                    if time.monotonic() - session_start > rediscover_s:
                        break                     # reconnect w/ fresh targets
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                    except asyncio.TimeoutError:
                        heartbeat_file(root, messages=msgs,
                                       subscribed=len(slugs))
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        msg = {"raw": str(raw)[:2000]}
                    if isinstance(msg, dict) and "heartbeat" in msg:
                        await ws.send(json.dumps(guard_outbound({"heartbeat": {}})))
                    append(root, {"kind": "ws",
                                  "t": datetime.now(timezone.utc).isoformat(),
                                  "msg": msg})
                    msgs += 1
                    if msgs % 200 == 0:
                        heartbeat_file(root, messages=msgs,
                                       subscribed=len(slugs))
        except Exception as exc:
            append(root, {"kind": "error",
                          "t": datetime.now(timezone.utc).isoformat(),
                          "err": str(exc)[:150], "backoff_s": backoff})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=None)
    args = ap.parse_args()
    if not has_credentials():
        print(json.dumps({"verdict": "NO_CREDENTIALS",
                          "need_env_names": [KEY_ENV, SECRET_ENV],
                          "note": "read-only recorder; fails closed"}))
        return 2
    return asyncio.run(run(args.minutes))


if __name__ == "__main__":
    raise SystemExit(main())
