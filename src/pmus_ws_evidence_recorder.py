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
STATUS_EVERY_ENV = "PMUS_WS_STATUS_EVERY_SECONDS"
DEFAULT_STATUS_EVERY_S = 60.0


# ---------------- authenticated-stream state + status line ----------------

def _walk_market_events(msg: Any, out: list, slug_hint: str = "") -> None:
    """Tolerant extractor over the LIVE documented WS schema (camelCase
    like `marketData`/`marketSlug`/`lastTradePx`/`offers`, with snake_case
    equivalents accepted): collects book and trade events wherever they
    appear in the message tree."""
    if isinstance(msg, list):
        for item in msg:
            _walk_market_events(item, out, slug_hint)
        return
    if not isinstance(msg, Mapping):
        return
    slug = str(msg.get("marketSlug") or msg.get("market_slug")
               or msg.get("slug") or slug_hint or "")
    asks = msg.get("offers") if "offers" in msg else msg.get("asks")
    if slug and ("bids" in msg or "offers" in msg or "asks" in msg
                 or "bestBid" in msg or "best_bid" in msg
                 or "bestAsk" in msg or "best_ask" in msg):
        out.append({"kind": "book", "slug": slug,
                    "bids": msg.get("bids"), "asks": asks,
                    "best_bid": msg.get("bestBid") or msg.get("best_bid"),
                    "best_ask": msg.get("bestAsk") or msg.get("best_ask"),
                    "last_trade_px": msg.get("lastTradePx")
                    or msg.get("last_trade_px")})
    if slug and "price" in msg and ("qty" in msg or "quantity" in msg
                                    or "size" in msg):
        out.append({"kind": "trade", "slug": slug,
                    "price": _px(msg.get("price")),
                    "qty": _px(msg.get("qty") or msg.get("quantity")
                               or msg.get("size")),
                    "trade_time": msg.get("tradeTime")
                    or msg.get("trade_time"),
                    "maker": msg.get("maker"), "taker": msg.get("taker"),
                    "side": msg.get("side") or msg.get("takerSide")
                    or msg.get("taker_side")})
    for v in msg.values():
        _walk_market_events(v, out, slug)


def _trade_direction(ev: Mapping[str, Any],
                     market: Mapping[str, Any]) -> str:
    """Resolve taker direction: explicit side, then taker/maker side
    fields, then price vs current BBO, else unknown."""
    side = str(ev.get("side") or "").upper()
    if side in ("BUY", "SELL"):
        return side.lower()
    for part, flip in (("taker", False), ("maker", True)):
        p = ev.get(part)
        if isinstance(p, Mapping):
            ps = str(p.get("side") or "").upper()
            if ps in ("BUY", "SELL"):
                if flip:
                    return "sell" if ps == "BUY" else "buy"
                return ps.lower()
    try:
        from decimal import Decimal as D
        px = D(str(ev["price"]))
        bid = D(str(market.get("bid"))) if market.get("bid") else None
        ask = D(str(market.get("ask"))) if market.get("ask") else None
        if ask is not None and px >= ask:
            return "buy"
        if bid is not None and px <= bid:
            return "sell"
    except Exception:
        pass
    return "unknown"


def _px(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        return value.get("value") or value.get("px")
    return None if value is None else str(value)


def _level(levels: Any) -> tuple[Optional[str], Optional[str]]:
    if isinstance(levels, list) and levels and isinstance(levels[0], Mapping):
        lv = levels[0]
        return _px(lv.get("px") or lv.get("price")), \
            (str(lv.get("qty")) if lv.get("qty") is not None else None)
    return None, None


def apply_events(state: dict, msg: Any, now: float) -> None:
    """Fold one received message into per-market state (pure dict)."""
    events: list = []
    _walk_market_events(msg, events)
    for ev in events:
        s = state.setdefault(ev["slug"], {
            "bid": None, "bid_qty": None, "ask": None, "ask_qty": None,
            "both_sides_served": False, "trades": [], "book_updates": 0})
        if ev["kind"] == "book":
            bid, bid_qty = _level(ev.get("bids"))
            ask, ask_qty = _level(ev.get("asks"))
            s["bid"] = _px(ev.get("best_bid")) or bid or s["bid"]
            s["ask"] = _px(ev.get("best_ask")) or ask or s["ask"]
            if _px(ev.get("last_trade_px")):
                s["last_trade_px"] = _px(ev.get("last_trade_px"))
            if bid_qty is not None:
                s["bid_qty"] = bid_qty
            if ask_qty is not None:
                s["ask_qty"] = ask_qty
            if bid_qty is not None and ask_qty is not None:
                s["both_sides_served"] = True
            s["book_updates"] += 1
        else:
            # fail closed PER ROW: a malformed price/qty is dropped and
            # counted, never persisted, never allowed to crash the status
            # layer or the recorder
            try:
                from decimal import Decimal
                px = Decimal(str(ev.get("price")))
            except Exception:
                s["bad_trade_rows"] = s.get("bad_trade_rows", 0) + 1
                continue
            s["trades"].append({
                "t": now, "px": str(px),
                "qty": ev.get("qty"),
                "trade_time": ev.get("trade_time"),
                "dir": _trade_direction({**ev, "price": str(px)}, s)})
            del s["trades"][:-500]


def ws_gate(s: Mapping[str, Any], now: float, window_s: float = 3600.0) -> dict:
    """Sequential maker-first gate on AUTHENTICATED evidence: real trades,
    genuinely served two-sided book. Reuses the tape-report slot mechanics
    via the same fill semantics; THEORETICAL throughout."""
    from decimal import Decimal as D
    from src import pmus_status_report as rep
    trades = []
    for t in s.get("trades", []):
        if not t.get("px") or now - t["t"] > window_s:
            continue
        try:
            trades.append({"t": t["t"], "px": D(str(t["px"])), "vol": 1.0,
                           "dir_inferred": t["dir"]})
        except Exception:
            continue          # belt-and-braces: bad row never crashes status
    dirs = {t["dir_inferred"] for t in trades} - {"unknown"}
    obs = []
    if s.get("bid") and s.get("ask"):
        obs = [{"t": datetime.fromtimestamp(now - window_s,
                                            timezone.utc).isoformat(),
                "bid": s["bid"], "ask": s["ask"],
                "bid_qty": s.get("bid_qty"),
                "ask_book_missing": not s.get("both_sides_served")}]
    counts: dict = {}
    if obs and trades:
        t0 = min(t["t"] for t in trades)
        t_end = max(t["t"] for t in trades)
        t = t0
        while t <= t_end:
            row = rep.slot_replay(
                [{**obs[0], "t": datetime.fromtimestamp(
                    t, timezone.utc).isoformat()}], trades, t)
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            t += rep.SLOT_S
    matched = counts.get("MATCHED", 0)
    q1 = bool(s.get("bid_qty")) and float(s.get("bid_qty") or 0) >= 1
    if matched >= 2 and s.get("both_sides_served") and q1:
        status = "Q1_GATE_CLEARED"
    elif len(dirs) == 2 and q1:
        status = "SHADOW_CANDIDATE"
    else:
        status = "NOT_READY"
    return {"trades_1h": len(trades), "two_sided_trades": len(dirs) == 2,
            "slot_counts_theoretical": counts, "gate_status": status}


def build_status(state: Mapping[str, Any], connected: bool, msgs: int,
                 subscribed: int, now: Optional[float] = None) -> dict:
    now = now or time.time()
    tops = sorted(state.items(),
                  key=lambda kv: -len(kv[1].get("trades", [])))[:5]
    return {
        "t": datetime.now(timezone.utc).isoformat(),
        "conn": "connected" if connected else "reconnecting",
        "auth": "ok" if connected else "unknown",
        "subscribed_markets": subscribed,
        "messages_received": msgs,
        "labeling": "authenticated stream; fills/economics THEORETICAL",
        "top_markets": [
            {"slug": slug, "bid": s.get("bid"), "bid_qty": s.get("bid_qty"),
             "ask": s.get("ask"), "ask_qty": s.get("ask_qty"),
             "both_sides_served": s.get("both_sides_served", False),
             "book_updates": s.get("book_updates", 0),
             **ws_gate(s, now)}
            for slug, s in tops],
    }


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
    if leagues == ["all"]:
        # full-slate discovery: every league the venue lists (removes the
        # configured-league blind spot; incentive markets still added below)
        try:
            lg = col.http_get_json("/v2/leagues")
            rows = lg if isinstance(lg, list) else lg.get("leagues") or []
            leagues = [r["slug"] for r in rows if r.get("slug")]
        except Exception:
            leagues = ["mlb"]
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
    status_every = float(os.getenv(STATUS_EVERY_ENV,
                                   str(DEFAULT_STATUS_EVERY_S)))
    msgs = 0
    mstate: dict = {}
    last_status = 0.0
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
                    apply_events(mstate, msg, time.time())
                    if msgs % 200 == 0:
                        heartbeat_file(root, messages=msgs,
                                       subscribed=len(slugs))
                    if status_every > 0 and \
                            time.monotonic() - last_status >= status_every:
                        try:
                            print("PMUS_WS_STATUS " + json.dumps(
                                build_status(mstate, True, msgs, len(slugs)),
                                separators=(",", ":"), default=str),
                                flush=True)
                        except Exception as exc:
                            # a status bug must never cost the connection
                            # or the tape
                            print("PMUS_WS_STATUS "
                                  + json.dumps({"status_error":
                                                str(exc)[:120],
                                                "messages_received": msgs}),
                                  flush=True)
                        last_status = time.monotonic()
        except Exception as exc:
            append(root, {"kind": "error",
                          "t": datetime.now(timezone.utc).isoformat(),
                          "err": str(exc)[:150], "backoff_s": backoff})
            print("PMUS_WS_STATUS " + json.dumps(
                {**build_status(mstate, False, msgs, len(slugs)),
                 "last_error": str(exc)[:120]},
                separators=(",", ":"), default=str), flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=None)
    args = ap.parse_args()
    role = os.getenv("PMUS_SERVICE_ROLE", "").strip()
    if role and role != "ws-recorder":
        raise SystemExit(
            "SERVICE_ROLE_MISMATCH: this image runs the 'ws-recorder' "
            f"worker but the service declares PMUS_SERVICE_ROLE={role!r}")
    if not has_credentials():
        print(json.dumps({"verdict": "NO_CREDENTIALS",
                          "need_env_names": [KEY_ENV, SECRET_ENV],
                          "note": "read-only recorder; fails closed"}))
        return 2
    return asyncio.run(run(args.minutes))


if __name__ == "__main__":
    raise SystemExit(main())
