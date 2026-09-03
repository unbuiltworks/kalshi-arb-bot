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
  PMUS_WS_LEAGUES       discovery leagues (default "mlb"; "all" = every
                        league the venue lists)
  PMUS_WS_PREFIXES      optional slug-prefix filter; default EMPTY = every
                        market on live events, props included
  PMUS_WS_MAX_MARKETS   venue subscription cap (default 24). Above the
                        cap a core of the most active observed markets is
                        pinned and the remainder rotates every discovery
                        cycle — nothing is silently excluded
  PMUS_WS_DISCOVERY_SECONDS  rediscovery interval (default 300)
  PMUS_WS_HYDRATE_MAX_BYTES  restart hydration read bound per daily tape
                        file (default 64MiB; 0 = unbounded)

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
PREFIXES_ENV = "PMUS_WS_PREFIXES"          # default "" = ALL markets (props too)
HYDRATE_BYTES_ENV = "PMUS_WS_HYDRATE_MAX_BYTES"
DEFAULT_HYDRATE_BYTES = 64 * 1024 * 1024   # per daily tape file; 0 = unbounded
EXPAND_PAGES_ENV = "PMUS_WS_EXPAND_PAGES"  # 0 disables global enumeration

# last discovery diagnostics (issue #167 comment 5520693117): merged into
# the status line so an incomplete embedded page can never hide again
DISCOVERY_DIAG: dict = {}

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
            # bounded TIME-ALIGNED snapshot history: slot replay must use
            # the book that actually existed at each slot, never the
            # current BBO (issue #167 comment 5517600205)
            s.setdefault("bbo_history", []).append({
                "t": now, "bid": s["bid"], "ask": s["ask"],
                "bid_qty": s["bid_qty"],
                "ask_book_missing": not s["both_sides_served"]})
            del s["bbo_history"][:-2000]
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
    history = [h for h in s.get("bbo_history", [])
               if h.get("bid") and h.get("ask")
               and now - h["t"] <= window_s + rep.SLOT_S]

    def prevailing(slot_start: float, slot_end: float) -> Optional[dict]:
        """The book that actually existed for this slot: the last
        snapshot at/before slot_start, else the first one appearing
        inside the slot. No snapshot -> no verdict (NO_QUOTE), never the
        current BBO (issue #167 comment 5517600205)."""
        before = [h for h in history if h["t"] <= slot_start]
        if before:
            return before[-1]
        inside = [h for h in history if slot_start < h["t"] <= slot_end]
        return inside[0] if inside else None

    counts: dict = {}
    if history and trades:
        # start the slot grid 1ms BEFORE the first trade: slot_replay's
        # in-slot filter is strictly '>', so a grid anchored exactly on
        # the first trade would drop that trade from its own slot
        t0 = min(t["t"] for t in trades) - 0.001
        t_end = max(t["t"] for t in trades)
        t = t0
        while t <= t_end:
            snap = prevailing(t, t + rep.SLOT_S)
            if snap is None:
                counts["NO_QUOTE"] = counts.get("NO_QUOTE", 0) + 1
                t += rep.SLOT_S
                continue
            # +1ms guard: isoformat() truncates to microseconds, which can
            # land the observation a hair BEFORE slot_start and fail
            # slot_replay's >= entry lookup
            row = rep.slot_replay(
                [{"t": datetime.fromtimestamp(
                    t + 0.001, timezone.utc).isoformat(),
                  "bid": snap["bid"], "ask": snap["ask"],
                  "bid_qty": snap.get("bid_qty"),
                  "ask_book_missing": snap.get("ask_book_missing", True)}],
                trades, t)
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


def _l5_compact(s: Mapping[str, Any], now: float,
                gate: Mapping[str, Any]) -> dict:
    """Compact per-market L5 action-shadow fields (issue #167 comments
    5519672916/5519746304): THEORETICAL clone state + ranked next
    relation from the frozen canonical model, alongside the pair gate.
    A shadow failure is contained to this field — never the status line,
    never the gate."""
    try:
        from src import pmus_l5_action_shadow as l5s
        row = l5s.market_shadow_row(s, now, gate)
        v2 = row.get("persistence_v2") or {}
        return {"l5_state": row["l5_state"],
                "last_rel": row["last_rel"],
                "sim_inventory": row["sim_inventory"],
                "next_relation_ranked": row["next_relation_ranked"][:3],
                "l5_next_relation": row["comparison"]["l5_next_relation"],
                "off_distribution": row["off_distribution"],
                "divergent_vs_pair_gate": row["comparison"]["divergent"],
                "model_sha256_16": row["model_sha256_16"],
                "v2": ({"recommended_relation": v2.get(
                            "recommended_relation"),
                        "persist_extend_p": v2.get("persist_extend_p"),
                        "switch_complete_p": v2.get("switch_complete_p"),
                        "side": v2.get("side"),
                        "price_zone": v2.get("price_zone"),
                        "next_reevaluation_s": v2.get(
                            "next_reevaluation_s"),
                        "off_distribution": v2.get("off_distribution"),
                        "model_sha256_16": v2.get("model_sha256_16")}
                       if not v2.get("error") else v2),
                "labels": "THEORETICAL"}
    except Exception as exc:
        return {"error": f"l5_shadow_unavailable:{type(exc).__name__}"}


def _market_row(slug: str, s: Mapping[str, Any], now: float) -> dict:
    gate = ws_gate(s, now)
    return {"slug": slug, "bid": s.get("bid"), "bid_qty": s.get("bid_qty"),
            "ask": s.get("ask"), "ask_qty": s.get("ask_qty"),
            "both_sides_served": s.get("both_sides_served", False),
            "book_updates": s.get("book_updates", 0),
            **gate,
            "l5_shadow": _l5_compact(s, now, gate)}


def build_status(state: Mapping[str, Any], connected: bool, msgs: int,
                 subscribed: int, now: Optional[float] = None,
                 discovery: Optional[Mapping[str, Any]] = None) -> dict:
    now = now or time.time()
    # Gate every tracked market once per cycle: a Q1_GATE_CLEARED market
    # outside the five-row display must still be visible to the supervisor.
    rows = {slug: _market_row(slug, s, now) for slug, s in state.items()}
    tops = sorted(state.items(),
                  key=lambda kv: -len(kv[1].get("trades", [])))[:5]
    eligible = sorted(
        (r for r in rows.values() if r.get("gate_status") == "Q1_GATE_CLEARED"),
        key=lambda r: (-int(r.get("trades_1h") or 0), str(r.get("slug"))))
    status = {
        "t": datetime.now(timezone.utc).isoformat(),
        "conn": "connected" if connected else "reconnecting",
        "auth": "ok" if connected else "unknown",
        "subscribed_markets": subscribed,
        "messages_received": msgs,
        "labeling": "authenticated stream; fills/economics THEORETICAL",
        "top_markets": [rows[slug] for slug, _ in tops],
        "eligible_markets": eligible,
        "eligible_total": len(eligible),
    }
    if discovery:
        status["discovery"] = dict(discovery)
    return status


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


def discover_universe() -> list[dict]:
    """Public-gateway discovery of the FULL live sports universe — props
    included. No hardcoded market-type/prefix assumption is applied
    unless the operator sets PMUS_WS_PREFIXES; market/event/league
    metadata is preserved so the engine stays market-agnostic
    (issue #167 comment 5519422461)."""
    explicit = [s.strip() for s in os.getenv(MARKETS_ENV, "").split(",")
                if s.strip()]
    if explicit:
        return [{"slug": s, "league": None, "why": "explicit_env",
                 "event_state": None, "prop": False, "market_meta": {}}
                for s in explicit]
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
            # limit=500: the default events page is capped at 10/league
            # (issue #167 comment 5520693117 — coverage audit)
            ev = col.http_get_json(
                f"/v2/leagues/{lg}/events?limit={col.EVENTS_PAGE_LIMIT}")
            events_by_league[lg] = ev if isinstance(ev, list) \
                else ev.get("events") or []
        except Exception:
            events_by_league[lg] = []
    try:
        incentives = col.http_get_json("/v1/incentives")
    except Exception:
        incentives = {}
    prefixes = [p.strip() for p in
                os.getenv(PREFIXES_ENV, "").split(",") if p.strip()]
    targets = col.discover_targets(events_by_league, incentives, prefixes,
                                   True)
    embedded_n = len(targets)
    # Full-universe expansion: embedded event.markets carry at most ~15
    # rows while marketCounts reports 47-162+ per event. Enumerate the
    # global open-market listing and join to LIVE events on the exact
    # gameStartTime key; failure degrades to embedded-only, with the
    # gap visible in diagnostics rather than hidden.
    pages = 0
    expanded: list[dict] = []
    try:
        max_pages = int(os.getenv(EXPAND_PAGES_ENV,
                                  str(col.ENUM_MAX_PAGES_DEFAULT)))
        if max_pages > 0:
            open_rows, pages = col.enumerate_open_markets(
                col.http_get_json, max_pages)
            expanded = col.expand_live_event_markets(events_by_league,
                                                     open_rows)
    except Exception:
        expanded = []
    seen = {t["slug"] for t in targets}
    for row in expanded:
        if prefixes and not row["slug"].startswith(tuple(prefixes)):
            continue
        if row["slug"] not in seen:
            seen.add(row["slug"])
            targets.append(row)
    DISCOVERY_DIAG.clear()
    DISCOVERY_DIAG.update({
        "events_discovered": sum(len(v) for v in events_by_league.values()),
        "markets_embedded": embedded_n,
        "markets_expanded": len(targets) - embedded_n,
        "enumeration_pages_fetched": pages,
    })
    return targets


def _activity_score(s: Mapping[str, Any], now: float) -> float:
    """Observed-evidence priority for subscription planning: real recent
    trades dominate, a genuinely served two-sided book and book flow
    break ties. Unobserved markets score 0 and are covered by rotation."""
    trades = sum(1 for t in s.get("trades", [])
                 if now - t.get("t", 0) <= 3600.0)
    return (trades * 10.0
            + (5.0 if s.get("both_sides_served") else 0.0)
            + min(float(s.get("book_updates", 0)), 500.0) / 100.0)


def subscription_plan(targets: list[dict], state: Mapping[str, Any],
                      cap: int, cycle: int,
                      now: Optional[float] = None) -> dict:
    """Deterministic full-coverage plan under the venue subscription cap.

    <= cap markets: subscribe everything. Above cap: pin a CORE of the
    most active observed markets and fill the rest of the cap with a
    ROTATING window over the remainder, advancing every discovery cycle —
    so no market (props included) is ever silently excluded; each is
    observed within a bounded number of cycles. Never loosens the gate."""
    now = now or time.time()
    cap = max(1, int(cap))
    slugs_seen: set = set()
    uniq = []
    for t in targets:
        if t["slug"] not in slugs_seen:
            slugs_seen.add(t["slug"])
            uniq.append(t)
    props_discovered = sum(1 for t in uniq if t.get("prop"))
    if len(uniq) <= cap:
        subs = [t["slug"] for t in uniq]
        excluded: list[dict] = []
    else:
        ranked = sorted(
            uniq, key=lambda t: (-_activity_score(
                state.get(t["slug"], {}), now), t["slug"]))
        rotate_n = max(1, cap // 4)
        core = ranked[:cap - rotate_n]
        rest = ranked[cap - rotate_n:]
        start = (cycle * rotate_n) % len(rest)
        window = (rest + rest)[start:start + rotate_n]
        subs = [t["slug"] for t in core] + [t["slug"] for t in window]
        chosen = set(subs)
        excluded = [{"slug": t["slug"], "reason": "rotation_pending"}
                    for t in uniq if t["slug"] not in chosen]
    chosen = set(subs)
    return {
        "subscribe": subs,
        "excluded": excluded,
        "diagnostics": {
            "discovered_total": len(uniq),
            "subscribed_total": len(subs),
            "excluded_total": len(excluded),
            "excluded_reasons": sorted({e["reason"] for e in excluded}),
            "props_discovered": props_discovered,
            "props_subscribed": sum(1 for t in uniq
                                    if t.get("prop") and t["slug"] in chosen),
            "rotation_cycle": cycle,
        },
    }


def discover_slugs() -> list[str]:
    """Back-compat single-shot discovery: full universe, cycle-0 plan."""
    cap = int(os.getenv(MAX_MARKETS_ENV, "24"))
    return subscription_plan(discover_universe(), {}, cap, 0)["subscribe"]


# ----------------- restart hydration from the persisted tape -----------------

def iter_recent_tape_rows(root: Path, now: Optional[float] = None,
                          max_bytes: int = 0):
    """Yield (epoch_t, msg) from the persisted daily JSONL tape (today and
    yesterday, in order), bounded to the last `max_bytes` per file.
    Malformed lines are skipped — hydration can never crash startup."""
    now_dt = datetime.fromtimestamp(now or time.time(), timezone.utc)
    from datetime import timedelta
    for day in (now_dt - timedelta(days=1), now_dt):
        path = daily_path(root, day)
        if not path.exists():
            continue
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes) if max_bytes else 0)
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        if max_bytes and size > max_bytes and lines:
            lines = lines[1:]                     # drop the partial first line
        for line in lines:
            try:
                row = json.loads(line)
                if not isinstance(row, Mapping) or row.get("kind") != "ws":
                    continue
                dt = datetime.fromisoformat(
                    str(row["t"]).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                yield dt.timestamp(), row.get("msg")
            except Exception:
                continue


def hydrate_state(state: dict, rows, now: Optional[float] = None,
                  window_s: float = 3600.0) -> dict:
    """Rebuild in-memory gate evidence from persisted authenticated tape
    rows so a restart/deploy does not reset MATCHED history to zero.
    Each row is folded at ITS RECORDED timestamp, so time-alignment and
    provenance are exactly what the live fold would have produced; rows
    older than the gate horizon are skipped and thus still age out
    (issue #167 comment 5519422461)."""
    from src import pmus_status_report as rep
    now = now or time.time()
    cutoff = now - (window_s + 2 * rep.SLOT_S)
    used = skipped = 0
    for t, msg in rows:
        if t < cutoff or t > now:
            skipped += 1
            continue
        apply_events(state, msg, t)
        used += 1
    return {"hydrated_rows": used, "skipped_rows": skipped,
            "hydrated_markets": len(state)}


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
    cycle = 0
    # Restart hydration: rebuild gate evidence from the persisted tape so
    # a deploy does not cold-start MATCHED history to zero. Failure here
    # can never block startup.
    try:
        hyd_bytes = int(os.getenv(HYDRATE_BYTES_ENV,
                                  str(DEFAULT_HYDRATE_BYTES)))
        hydration = hydrate_state(
            mstate, iter_recent_tape_rows(root, max_bytes=hyd_bytes))
    except Exception as exc:
        hydration = {"hydration_error": str(exc)[:120]}
    print("PMUS_WS_STATUS " + json.dumps(
        {"hydration": hydration}, separators=(",", ":"), default=str),
        flush=True)
    while deadline is None or time.monotonic() < deadline:
        cap = int(os.getenv(MAX_MARKETS_ENV, "24"))
        plan = subscription_plan(discover_universe(), mstate, cap, cycle)
        cycle += 1
        slugs = plan["subscribe"]
        discovery_diag = {**plan["diagnostics"], **DISCOVERY_DIAG}
        append(root, {"kind": "recorder_start",
                      "t": datetime.now(timezone.utc).isoformat(),
                      "subscribed": slugs, "read_only": True,
                      "discovery": discovery_diag,
                      "excluded": plan["excluded"][:200],
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
                                build_status(mstate, True, msgs, len(slugs),
                                             discovery=discovery_diag),
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
