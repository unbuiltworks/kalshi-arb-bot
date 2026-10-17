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
  PMUS_WS_HYDRATE_MAX_BYTES  restart hydration read bound over the tape
                        parts of today + yesterday (default 64MiB total,
                        newest first; 0 = unbounded)
  PMUS_WS_RAW_MAX_BYTES  RAW TAPE BUDGET on the volume (default 12GiB).
                        The tape is written in HOURLY parts; the oldest
                        parts are deleted at boot and every discovery
                        cycle until the raw directory fits the budget.
                        LIVE 2026-09-08 00:40Z: the unbounded daily tape
                        filled the 50GB volume (ENOSPC) and the recorder
                        crash-looped, taking the probe's evidence with it.

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
from typing import Any, Iterable, Mapping, Optional

WS_URL = "wss://api.polymarket.us/v1/ws/markets"
WS_PATH = "/v1/ws/markets"

KEY_ENV = "PMUS_API_KEY_ID"
SECRET_ENV = "PMUS_API_SECRET_KEY"
ROOT_ENV = "PMUS_WS_ROOT"
DEFAULT_ROOT = "/state/pmus-ws"
MARKETS_ENV = "PMUS_WS_MARKETS"
SERIES_ENV = "PMUS_WS_SERIES"        # recurring series, rolled per window
SERIES_LEAD_S = 60.0                 # admit the next window this early
LEAGUES_ENV = "PMUS_WS_LEAGUES"
MAX_MARKETS_ENV = "PMUS_WS_MAX_MARKETS"
DISCOVERY_ENV = "PMUS_WS_DISCOVERY_SECONDS"
PREFIXES_ENV = "PMUS_WS_PREFIXES"          # default "" = ALL markets (props too)
HYDRATE_BYTES_ENV = "PMUS_WS_HYDRATE_MAX_BYTES"
DEFAULT_HYDRATE_BYTES = 64 * 1024 * 1024   # total over the newest parts; 0 = unbounded
RAW_MAX_BYTES_ENV = "PMUS_WS_RAW_MAX_BYTES"
DEFAULT_RAW_MAX_BYTES = 12 * 1024 ** 3      # raw tape budget on the volume
TAPE_WRITE_ERRORS = {"n": 0, "last_alarm": 0.0}
EXPAND_PAGES_ENV = "PMUS_WS_EXPAND_PAGES"  # 0 disables global enumeration
# Slugs that must never lose a seat, comma-separated. The markets the executor
# holds risk in: the settlement auditor and residual accounting both read those
# streams, so eviction there is the one that can actually hurt.
#
# HONEST LIMIT: this is OPERATOR-SET, not derived. The recorder does not read
# portfolio positions and deliberately does not start doing so here -- adding an
# authenticated call to this service's hot path while doubling its subscription
# load would be the wrong thing to change in the same step. `plan(protected=...)`
# is implemented and tested; automatic wiring from live positions is NOT done.
PROTECTED_ENV = "PMUS_WS_PROTECTED_SLUGS"

#: Resubscribe the instant a fixed-period window rolls over, instead of
#: waiting out the rediscovery timer.
#:
#: WHY. The websocket session runs for PMUS_WS_DISCOVERY_SECONDS (default
#: 300) before reconnecting with fresh targets, so a BTC15m window that opens
#: at :45:00 can wait up to five minutes to be subscribed at all. Measured on
#: the raw tape: the authenticated two-sided book first appeared 429.4s and
#: 513.5s into two consecutive 900s windows, while the venue traded 73 times
#: in one of them. That is not the venue being quiet and it is not the public
#: gateway -- it is us arriving late to a market we are entitled to watch
#: from its first second.
#:
#: Set to the window period in seconds (900 for BTC15m). 0 or unset is off,
#: and the recorder behaves exactly as before.
ROLLOVER_ENV = "PMUS_WS_ROLLOVER_SECONDS"

#: the series whose live window must always keep its subscription seat
ROLLOVER_SLUG_ENV = "PMUS_WS_ROLLOVER_SLUG_PREFIX"


def rollover_period_s() -> float:
    try:
        v = float(str(os.getenv(ROLLOVER_ENV, "")).strip() or "0")
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0


def next_window_boundary(now: Optional[float] = None,
                         period_s: Optional[float] = None) -> Optional[float]:
    """The next wall-clock instant a window opens, or None when off."""
    period = rollover_period_s() if period_s is None else period_s
    if not period:
        return None
    now = time.time() if now is None else now
    return (int(now // period) + 1) * period


def rolling_window_slugs(now: Optional[float] = None,
                         period_s: Optional[float] = None,
                         ahead: int = 1) -> list:
    """The window trading NOW plus the next `ahead`, as market slugs.

    A static PMUS_WS_PROTECTED_SLUGS cannot express "the window trading now"
    for a product that rotates every fifteen minutes, so the live window is
    computed from the clock and protected alongside the static list.
    """
    prefix = str(os.getenv(ROLLOVER_SLUG_ENV, "") or "").strip()
    period = rollover_period_s() if period_s is None else period_s
    if not prefix or not period:
        return []
    now = time.time() if now is None else now
    start = int(now // period) * period
    out = []
    for i in range(0, max(1, ahead + 1)):
        t = datetime.fromtimestamp(start + i * period, timezone.utc)
        out.append("%s%s-%02d%02dz" % (prefix, t.strftime("%Y-%m-%d"),
                                       t.hour, t.minute))
    return out
PLANNER_ENV = "PMUS_WS_PLANNER"            # "priority" (default) | "rotation"

# last discovery diagnostics (issue #167 comment 5520693117): merged into
# the status line so an incomplete embedded page can never hide again
DISCOVERY_DIAG: dict = {}

SUB_MARKET_DATA = 1
SUB_TRADE = 3
# LIVE 2026-09-07 ~09:35Z: the authenticated stream went from ~1,000
# frames/min to ~1 frame per connection while the venue kept trading
# (keyless gateway BBO moved, our socket book froze). The stream is now
# accounted frame by frame and kept alive per the venue's own guidance
# (docs api-reference/websocket/overview: keep-alive, reconnect when
# heartbeats stop, limit subscriptions). Read-only; never evidence.
SUB_CHUNK_ENV = "PMUS_WS_SUB_CHUNK"            # slugs per subscribe frame
DEFAULT_SUB_CHUNK = 32
IDLE_HEARTBEAT_S = float(os.getenv("PMUS_WS_IDLE_HEARTBEAT_S", "20"))
STARVED_S = float(os.getenv("PMUS_WS_STARVED_S", "120"))
STARVED_RECONNECT_MAX = int(os.getenv("PMUS_WS_STARVED_RECONNECT_MAX", "5"))
STREAM_ALARM_S = float(os.getenv("PMUS_WS_STREAM_ALARM_S", "300"))
FRAME_LOG_FIRST = 3                            # frames echoed per connection
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


#: Which markets retain the FULL ladder, as a comma-separated list of exact
#: slugs. EMPTY BY DEFAULT, which keeps the memory profile byte-for-byte what
#: it is today. This is deliberately not a pattern and not "all": `prune_state`
#: exists because `mstate` reached 8.27 GB RSS holding top-of-book alone, and a
#: 16-rung two-sided ladder over 240 snapshots is ~300 KB per market -- fine for
#: the handful of markets we actually quote, 460 MB over a 1,500-market COLD
#: rotation. The bound lands before the cap, not after.
LADDER_MARKETS_ENV = "PMUS_WS_LADDER_MARKETS"
LADDER_RUNGS_ENV = "PMUS_WS_LADDER_RUNGS"
LADDER_HISTORY_ENV = "PMUS_WS_LADDER_HISTORY"
LADDER_RUNGS_DEFAULT = 16
LADDER_HISTORY_DEFAULT = 240      # ~one 15m window at a 4s cadence
LADDER_RUNGS_CAP = 64
LADDER_HISTORY_CAP = 1000


#: Follow the rotating window. A 15m product retires its slug every quarter
#: hour, so a STATIC exact list goes stale within one window and retains a
#: ladder for a market that no longer trades. This resolves the live slug from
#: the clock, which keeps the selection EXACT -- never a pattern -- while
#: still tracking the rotation. `ahead` of 1 covers the boundary, where the
#: next window opens before the current one settles.
LADDER_ROLLING_ENV = "PMUS_WS_LADDER_ROLLING"
LADDER_ROLLING_AHEAD_ENV = "PMUS_WS_LADDER_ROLLING_AHEAD"
LADDER_ROLLING_AHEAD_DEFAULT = 1
LADDER_ROLLING_AHEAD_CAP = 3


def ladder_rolling() -> bool:
    return str(os.getenv(LADDER_ROLLING_ENV) or "").strip().upper() == "YES"


def ladder_markets(now: Optional[float] = None) -> frozenset:
    """Exact slugs retaining a ladder. Unreadable or unset means NONE.

    Two sources, both exact: the static allowlist, and -- when
    PMUS_WS_LADDER_ROLLING is YES -- the slug of the window trading now plus
    the next one, computed from the clock by `rolling_window_slugs`. A pattern
    is never matched, so the number of markets holding rungs stays small and
    countable, which is what keeps the memory bound meaningful.
    """
    raw = os.getenv(LADDER_MARKETS_ENV) or ""
    out = set(p.strip() for p in raw.split(",") if p.strip())
    if ladder_rolling():
        ahead = _bounded_int(LADDER_ROLLING_AHEAD_ENV,
                             LADDER_ROLLING_AHEAD_DEFAULT,
                             LADDER_ROLLING_AHEAD_CAP)
        try:
            out.update(rolling_window_slugs(now, ahead=ahead) or ())
        except Exception:
            # a bad prefix or period must not take the recorder down, and
            # must not silently widen the selection either
            pass
    return frozenset(out)


#: SHADOW POLICY EVALUATION, OFF BY DEFAULT. Built lazily and only when
#: L5_SHADOW_POLICY_EVAL is YES, so an unset or unreadable variable leaves this
#: process byte-identical to what it is today. Read-only: it scores decisions
#: against evidence this process already holds, opens no socket, signs nothing
#: and has no path to an order.
_SHADOW_POLICY_LOG = None
_SHADOW_POLICY_BROKEN = False


def shadow_policy_log():
    """The per-process shadow log, or None when the feature is off.

    THE FLAG IS RE-READ EVERY CALL, not cached with the object. Caching the
    answer would mean unsetting L5_SHADOW_POLICY_EVAL did nothing until a
    restart, and the documented rollback for this feature is exactly that
    unset. A disable therefore takes effect on the next event.

    A construction that raises sets `_SHADOW_POLICY_BROKEN` and is never
    retried, so a broken evaluator costs one attempt rather than one per
    event, and can never take the recorder down.
    """
    global _SHADOW_POLICY_LOG
    global _SHADOW_POLICY_BROKEN
    if _SHADOW_POLICY_BROKEN:
        return None
    try:
        from . import l5_shadow_policy_eval as SPE
        if not SPE.enabled():
            return None
        if _SHADOW_POLICY_LOG is None:
            from decimal import Decimal
            _SHADOW_POLICY_LOG = SPE.ShadowPolicyLog(
                tick=Decimal("0.01"), min_qty=Decimal("0.01"),
                depth_ticks=1, cash_envelope=Decimal("2.00"),
                max_worst_case_loss=Decimal("2.00"))
    except Exception:
        _SHADOW_POLICY_BROKEN = True
        _SHADOW_POLICY_LOG = None
        return None
    return _SHADOW_POLICY_LOG


def drop_stale_ladders(state: dict, keep: frozenset) -> int:
    """Release rungs for markets no longer selected.

    WHY THIS AND NOT prune_state. prune_state evicts a whole market only
    after it has been idle for two hours, because admission reads trades_1h
    off it. A rotating 15m product leaves eight retired windows inside that
    grace period, each still holding a ladder -- so the ladders have to be
    released on their own schedule, by selection rather than by idleness.
    Only the ladder is dropped; every other piece of that market's evidence
    is left exactly where it is.
    """
    dropped = 0
    for slug, s in state.items():
        if slug in keep:
            continue
        if isinstance(s, dict) and s.pop("ladder_history", None) is not None:
            dropped += 1
    return dropped


def _bounded_int(env: str, default: int, cap: int) -> int:
    """A positive int at or under `cap`. Anything unreadable reads as the
    default, never as unbounded."""
    try:
        v = int(str(os.getenv(env) or default).strip())
    except (TypeError, ValueError):
        return default
    if v < 1:
        return default
    return min(v, cap)


def ladder_rungs() -> int:
    return _bounded_int(LADDER_RUNGS_ENV, LADDER_RUNGS_DEFAULT, LADDER_RUNGS_CAP)


def ladder_history() -> int:
    return _bounded_int(LADDER_HISTORY_ENV, LADDER_HISTORY_DEFAULT,
                        LADDER_HISTORY_CAP)


def _levels(levels: Any, rungs: int) -> list:
    """Up to `rungs` [px, qty] pairs, in the order the venue served them.

    WHY THIS EXISTS. `_level` below returns levels[0] and nothing else, so the
    35-40 rung ladder the venue sends is discarded at ingestion. Our maker
    intents rest a median 8 ticks behind the touch, so top-of-book depth cannot
    describe the level we would join, and `l5_maker_fill_model.Ladder` -- which
    exists precisely to answer "what was ahead of us at the level we joined?"
    -- has nothing to read. A rung whose price or quantity will not parse is
    DROPPED rather than carried as a zero: an unknown queue must stay unknown,
    because treating it as zero puts us at the front of a queue we cannot see.
    """
    if not isinstance(levels, list):
        return []
    out = []
    for lv in levels[:max(0, int(rungs))]:
        if not isinstance(lv, Mapping):
            continue
        px = _px(lv.get("px") or lv.get("price"))
        qty = lv.get("qty")
        if px is None or qty is None:
            continue
        out.append([str(px), str(qty)])
    return out


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
            # ask_qty is stamped HERE, alongside bid_qty, for the same reason
            # the prices are: the faithful translation caps a crossing at the
            # depth that existed AT THE ACTION, and reading the live snapshot
            # later would staple present depth onto a past state.
            s.setdefault("bbo_history", []).append({
                "t": now, "bid": s["bid"], "ask": s["ask"],
                "bid_qty": s["bid_qty"], "ask_qty": s["ask_qty"],
                "ask_book_missing": not s["both_sides_served"]})
            del s["bbo_history"][:-2000]
            # THE RUNGS, for the handful of markets we actually quote. Kept in
            # its own bounded history rather than widening bbo_history, so the
            # existing slot replay is untouched and the memory this adds is
            # confined to the allowlisted slugs.
            if ev["slug"] in ladder_markets(now):
                rungs = ladder_rungs()
                s.setdefault("ladder_history", []).append({
                    "t": now,
                    "bids": _levels(ev.get("bids"), rungs),
                    "asks": _levels(ev.get("asks"), rungs),
                    "rungs_cap": rungs,
                })
                del s["ladder_history"][:-ladder_history()]
                _spl = shadow_policy_log()
                if _spl is not None:
                    # the touch named EXACTLY as emit_new_ladders names it --
                    # the top rung of each side -- so the evaluator sees the
                    # same book the emitted evidence rows show.
                    _h = s["ladder_history"][-1]
                    _b = list(_h.get("bids") or [])
                    _a = list(_h.get("asks") or [])
                    _spl.observe_book(ev["slug"], dict(
                        _h, bid=(_b[0][0] if _b else None),
                        ask=(_a[0][0] if _a else None)))
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
            # THE BOOK AT THE INSTANT OF THE TRADE, stamped onto the row.
            #
            # Without it, `dir` cannot be interpreted. Proving what "buy" and
            # "sell" mean requires comparing the print against the book it
            # executed into, and the only book a reader can otherwise reach is
            # the PUBLIC gateway's ~30-second cache -- which is not that book.
            # Measured over 184 resolvable rows: the forward reading
            # (buy lifts the ask) held 46.2% of the time and the inverted one
            # 35.3%, with 18.5% of prints strictly inside the quoted spread
            # where neither reading can even apply. That is not a close call
            # between two hypotheses, it is the wrong book.
            #
            # `s["bid"]`/`s["ask"]` are the authenticated top of book this
            # same handler maintains, and `_trade_direction` already consults
            # them, so recording them costs nothing and makes the direction
            # field checkable instead of a matter of belief.
            s["trades"].append({
                "t": now, "px": str(px),
                "qty": ev.get("qty"),
                "trade_time": ev.get("trade_time"),
                "bid_at_trade": s.get("bid"),
                "ask_at_trade": s.get("ask"),
                "dir": _trade_direction({**ev, "price": str(px)}, s)})
            del s["trades"][:-500]
            _spl = shadow_policy_log()
            if _spl is not None:
                _spl.observe_trade(ev["slug"], now, str(px), ev.get("qty"))


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


def _market_row(slug: str, s: Mapping[str, Any], now: float,
                l5: bool = True) -> dict:
    gate = ws_gate(s, now)
    if isinstance(s, dict):
        # persist the latest slot verdicts so the priority planner's
        # completion-probability component scores on real evidence
        s["_slot_counts"] = gate.get("slot_counts_theoretical")
    from src import pmus_priority_queue as pq
    last_t = pq.last_authenticated_t(s)
    return {"slug": slug, "bid": s.get("bid"), "bid_qty": s.get("bid_qty"),
            "ask": s.get("ask"), "ask_qty": s.get("ask_qty"),
            "both_sides_served": s.get("both_sides_served", False),
            "book_updates": s.get("book_updates", 0),
            # age of the last authenticated update, from the recorder's own
            # bounded history (the enrichment layer may overwrite it)
            "last_authenticated_update_age_s": (round(now - last_t, 1)
                                                if last_t is not None else None),
            **gate,
            # expensive per-market L5 state only for the allocated top
            # candidates (issue #167 comment 5521210994 funnel step 4)
            "l5_shadow": (_l5_compact(s, now, gate) if l5
                          else {"skipped": "not_allocated"})}


SPRING_WATCH_LIMIT = int(os.getenv("PMUS_SPRING_WATCH_LIMIT", "10"))


def pq_stale_env() -> str:
    from src import pmus_priority_queue as pq
    return pq.STALE_S_ENV


POOL_ALARM_S = float(os.getenv("PMUS_WS_POOL_ALARM_S", "600"))
_POOL_EMPTY_SINCE: dict = {"t": None}


def pool_alarm(discovery: Mapping[str, Any], fresh_rows: Mapping[str, Any],
               watch: list, now: float) -> dict:
    """24/7 requirement (issue #167 comment 5571884826): when the venue
    lists live events but the served candidate pool is stale or empty
    for longer than POOL_ALARM_S, say so with the exact reason instead of
    idling silently. Pure bookkeeping; never changes admission."""
    live_events = int(discovery.get("v1_live_events") or discovery.get("live_events") or 0)
    fresh_live = int(discovery.get("fresh_live_subscribed") or len(fresh_rows))
    empty = (live_events > 0) and (fresh_live == 0 or not watch)
    since = _POOL_EMPTY_SINCE["t"]
    if empty and since is None:
        _POOL_EMPTY_SINCE["t"] = now
        since = now
    if not empty:
        _POOL_EMPTY_SINCE["t"] = None
    dur = (now - since) if (empty and since is not None) else 0.0
    reason = None
    if empty:
        reason = ("no fresh authenticated market in the subscription set" if fresh_live == 0
                  else "no fresh two-sided market inside the 0.35-0.65 spring band")
    out = {"live_events": live_events, "fresh_subscribed": fresh_live,
           "spring_watch_rows": len(watch), "pool_empty_for_s": round(dur, 1),
           "alarm": bool(empty and dur >= POOL_ALARM_S), "reason": reason,
           "labels": "alarm = live events exist but no fresh candidate served for >= POOL_ALARM_S; logging only"}
    if out["alarm"]:
        print("PMUS_WS_ALARM " + json.dumps({"kind": "pool_stale_or_empty", **out},
                                             separators=(",", ":"), default=str), flush=True)
    return out
def snapshot_state(state: Mapping[str, Any]) -> dict:
    """A copy of the per-market state safe to hand to a worker thread:
    the reader keeps mutating the live dicts and lists; the copy never
    aliases them (entries themselves are append-only records)."""
    return {slug: {**s, "trades": list(s.get("trades") or []),
                   "bbo_history": list(s.get("bbo_history") or [])}
            for slug, s in list(state.items())}


def rss_gb() -> Optional[float]:
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(kb / (1024.0 * 1024.0), 3)
    except Exception:                                  # noqa: BLE001
        return None


def live_events_of(discovery: Optional[Mapping[str, Any]]) -> int:
    d = discovery or {}
    return int(d.get("v1_live_events") or d.get("live_events") or 0)


def classify_frame(msg: Any) -> str:
    """heartbeat / error / trade / book / ack / other for one received
    frame (market data only; nothing here is ever sent)."""
    if isinstance(msg, Mapping):
        if "heartbeat" in msg:
            return "heartbeat"
        if "error" in msg:
            return "error"
    events: list = []
    _walk_market_events(msg, events)
    if any(e.get("kind") == "trade" for e in events):
        return "trade"
    if any(e.get("kind") == "book" for e in events):
        return "book"
    if isinstance(msg, Mapping) and "request_id" in msg:
        return "ack"
    return "other"


class StreamHealth:
    """Frame accounting that survives reconnects (pure; clock injected):
    per-connection counts plus a 300 s window of frame kinds so a starved
    socket is visible across the 300 s rediscovery cadence."""
    KINDS = ("book", "trade", "heartbeat", "ack", "error", "other")

    def __init__(self, now: float):
        self.recent: list = []
        self.last_book_t: Optional[float] = None
        self.last_frame_t: Optional[float] = None
        self.new_session(now)

    def new_session(self, now: float) -> None:
        self.session_start = now
        self.counts = {k: 0 for k in self.KINDS}
        self.max_proc_s = 0.0
        self.sum_proc_s = 0.0

    def note_proc(self, seconds: float) -> None:
        """Wall time the reader spent OFF ws.recv() handling one frame:
        LIVE 2026-09-07 the in-process shadow report and the status build
        held the reader for ~90 s per tick, which starved the socket."""
        self.max_proc_s = max(self.max_proc_s, seconds)
        self.sum_proc_s += seconds

    def note(self, kind: str, now: float) -> None:
        self.counts[kind if kind in self.counts else "other"] += 1
        self.recent.append((now, kind))
        self.last_frame_t = now
        if kind in ("book", "trade"):
            self.last_book_t = now
        cut = now - 300.0
        while self.recent and self.recent[0][0] < cut:
            self.recent.pop(0)
        del self.recent[:-5000]

    def snapshot(self, now: float) -> dict:
        r60 = [k for t, k in self.recent if t >= now - 60.0]
        r300 = [k for t, k in self.recent if t >= now - 300.0]
        return {"conn_age_s": round(now - self.session_start, 1),
                "frames_60s": len(r60),
                "books_300s": sum(1 for k in r300 if k == "book"),
                "trades_300s": sum(1 for k in r300 if k == "trade"),
                "last_book_age_s": (None if self.last_book_t is None
                                    else round(now - self.last_book_t, 1)),
                "last_frame_age_s": (None if self.last_frame_t is None
                                     else round(now - self.last_frame_t, 1)),
                "max_frame_proc_s": round(getattr(self, "max_proc_s", 0.0), 3),
                "sum_frame_proc_s": round(getattr(self, "sum_proc_s", 0.0), 3),
                "counts": dict(self.counts)}


def should_reconnect_starved(health: StreamHealth, live_events: int,
                             now: float, starved_reconnects: int) -> bool:
    """Reconnect (fresh subscriptions) when live events exist but no
    book/trade frame arrived for STARVED_S; bounded to
    STARVED_RECONNECT_MAX consecutive attempts, then the normal cadence."""
    if live_events <= 0 or starved_reconnects >= STARVED_RECONNECT_MAX:
        return False
    ref = health.last_book_t if health.last_book_t is not None \
        else health.session_start
    return (now - ref) >= STARVED_S


_STREAM_STARVED_SINCE: dict = {"t": None, "last_alarm": None}


def stream_alarm(snapshot: Mapping[str, Any], live_events: int,
                 subscribed: int, now: float) -> dict:
    """24/7 requirement: live events exist, seats are subscribed, and the
    authenticated socket delivered no market-data frame for
    STREAM_ALARM_S -> PMUS_WS_ALARM stream_starved (at most once per
    window). Logging only; never changes admission."""
    books = int(snapshot.get("books_300s") or 0) + \
        int(snapshot.get("trades_300s") or 0)
    starved = live_events > 0 and subscribed > 0 and books == 0
    st = _STREAM_STARVED_SINCE
    if starved and st["t"] is None:
        st["t"] = now
    if not starved:
        st["t"] = None
        st["last_alarm"] = None
    dur = (now - st["t"]) if (starved and st["t"] is not None) else 0.0
    alarm = bool(starved and dur >= STREAM_ALARM_S)
    out = {"live_events": live_events, "subscribed": subscribed,
           "market_data_frames_300s": books,
           "starved_for_s": round(dur, 1), "alarm": alarm,
           "reason": ("no book/trade frame on the authenticated stream "
                      "while live events exist") if starved else None,
           "labels": "alarm = live events exist but the socket delivered "
                     "no market-data frame for >= STREAM_ALARM_S; "
                     "logging only"}
    if alarm and (st["last_alarm"] is None
                  or now - st["last_alarm"] >= STREAM_ALARM_S):
        st["last_alarm"] = now
        print("PMUS_WS_ALARM " + json.dumps({"kind": "stream_starved", **out},
                                             separators=(",", ":"),
                                             default=str), flush=True)
    return out


def _gw_value(v: Any) -> Optional[str]:
    if isinstance(v, Mapping):
        v = v.get("value")
    return None if v is None else str(v)


def gateway_xcheck(slugs: list, state: Mapping[str, Any], http_get_json,
                   now: float, limit: int = 3) -> list[dict]:
    """DIAGNOSTIC ONLY: the keyless public gateway BBO for a few served
    slugs next to our authenticated socket book, so a frozen socket is
    provable in the log. Never written into market state or evidence."""
    out = []
    for slug in list(slugs)[:limit]:
        row: dict = {"slug": slug}
        try:
            md = (http_get_json(f"/v1/markets/{slug}/bbo") or {}).get(
                "marketData") or {}
            row.update({
                "gateway_bid": _gw_value(md.get("bestBid")),
                "gateway_ask": _gw_value(md.get("bestAsk")),
                "gateway_last_trade_px": _gw_value(md.get("lastTradePx")),
                "gateway_shares_traded": md.get("sharesTraded"),
                "gateway_sample_ts": (md.get("lastPriceSample") or {}).get("ts"),
                "gateway_state": md.get("state")})
        except Exception as exc:                       # noqa: BLE001
            row["gateway_error"] = f"{type(exc).__name__}:{str(exc)[:80]}"
        s = state.get(slug) or {}
        hist = s.get("bbo_history") or []
        row.update({
            "ws_bid": None if s.get("bid") is None else str(s.get("bid")),
            "ws_ask": None if s.get("ask") is None else str(s.get("ask")),
            "ws_book_updates": s.get("book_updates"),
            "ws_trades_total": len(s.get("trades") or []),
            "ws_last_update_age_s": (round(now - float(hist[-1]["t"]), 1)
                                     if hist else None)})
        try:
            if "gateway_error" in row:
                raise ValueError("gateway unavailable")
            gb, ga = row.get("gateway_bid"), row.get("gateway_ask")
            row["book_diverged"] = bool(
                gb is not None and row["ws_bid"] is not None
                and (float(gb) != float(row["ws_bid"])
                     or (ga is not None and row["ws_ask"] is not None
                         and float(ga) != float(row["ws_ask"]))))
        except (TypeError, ValueError):
            row["book_diverged"] = None
        row["labels"] = ("DIAGNOSTIC ONLY: keyless gateway vs authenticated "
                         "socket; never evidence")
        out.append(row)
    return out


SPRING_WATCH_LO = 0.35   # OWNER DECISION 5533476555: observation 35-65
SPRING_WATCH_HI = 0.65


def spring_watch(rows: Mapping[str, Mapping[str, Any]],
                 l5_alloc: Optional[set] = None,
                 limit: Optional[int] = None) -> list[dict]:
    """Bounded spring prefilter over ALL currently tracked markets (issue
    #167 comment 5521430953), so a 45-55c spring-fit market stays visible
    to the selector even when higher-activity out-of-band markets occupy
    the display rows. Prefilters ONLY on spring-relevant observational
    facts — YES bid inside the band with a genuinely served two-sided
    book — then ranks by two-sided trade flow, trades_1h, displayed
    depth. Advisory visibility only: NOT an execution admission; the
    canonical spring gate + micro_eligible stay authoritative."""
    picked = []
    stale_s = float(os.getenv(pq_stale_env(), "900"))
    for slug, r in rows.items():
        try:
            b = float(r.get("bid"))
        except (TypeError, ValueError):
            continue
        if not (SPRING_WATCH_LO <= b <= SPRING_WATCH_HI):
            continue
        if not r.get("both_sides_served"):
            continue
        # a frozen book (resolved market, or a dead feed) is not a spring
        # candidate: the selector would only refuse it as market_closed
        # (issue #167 comment 5571181823)
        try:
            age = float(r.get("last_authenticated_update_age_s"))
        except (TypeError, ValueError):
            age = None
        if age is not None and age > stale_s:
            continue
        try:
            depth = min(float(r.get("bid_qty") or 0),
                        float(r.get("ask_qty") or 0))
        except (TypeError, ValueError):
            depth = 0.0
        shadow = r.get("l5_shadow") or {}
        picked.append(({
            "slug": slug, "bid": r.get("bid"), "ask": r.get("ask"),
            "bid_qty": r.get("bid_qty"), "ask_qty": r.get("ask_qty"),
            "trades_1h": r.get("trades_1h"),
            "two_sided_trades": r.get("two_sided_trades"),
            "gate_status": r.get("gate_status"),
            "l5_allocated": (slug in l5_alloc) if l5_alloc is not None
            else None,
            "off_distribution": shadow.get("off_distribution"),
            "labels": "SHADOW/THEORETICAL; advisory only, not an "
                      "execution admission",
        }, depth))
    picked.sort(key=lambda rd: (not rd[0].get("two_sided_trades"),
                                -int(rd[0].get("trades_1h") or 0),
                                -rd[1], str(rd[0]["slug"])))
    lim = SPRING_WATCH_LIMIT if limit is None else limit
    return [r for r, _ in picked[:max(0, lim)]]


def build_status(state: Mapping[str, Any], connected: bool, msgs: int,
                 subscribed: int, now: Optional[float] = None,
                 discovery: Optional[Mapping[str, Any]] = None,
                 l5_alloc: Optional[set] = None,
                 stream_health: Optional[Mapping[str, Any]] = None) -> dict:
    now = now or time.time()
    # Gate every tracked market once per cycle: a Q1_GATE_CLEARED market
    # outside the five-row display must still be visible to the supervisor.
    rows = {slug: _market_row(slug, s, now,
                              l5=(l5_alloc is None or slug in l5_alloc))
            for slug, s in state.items()}
    stale_s = float(os.getenv(pq_stale_env(), "900"))
    def _fresh(slug: str) -> bool:
        age = rows[slug].get("last_authenticated_update_age_s")
        return age is None or float(age) <= stale_s
    fresh_rows = {slug: r for slug, r in rows.items() if _fresh(slug)}
    tops = sorted(((slug, state[slug]) for slug in fresh_rows),
                  key=lambda kv: -len(kv[1].get("trades", [])))[:5]
    eligible = sorted(
        (r for r in fresh_rows.values() if r.get("gate_status") == "Q1_GATE_CLEARED"),
        key=lambda r: (-int(r.get("trades_1h") or 0), str(r.get("slug"))))
    alarm = pool_alarm(discovery or {}, fresh_rows, spring_watch(fresh_rows, l5_alloc), now)
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
        "fresh_markets": len(fresh_rows),
        "stale_markets": len(rows) - len(fresh_rows),
        "pool_alarm": alarm,
        "spring_watch": spring_watch(fresh_rows, l5_alloc),
    }
    if stream_health is not None:
        status["stream_health"] = dict(stream_health)
        status["stream_alarm"] = stream_alarm(
            stream_health, live_events_of(discovery), subscribed, now)
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


def subscribe_frames(slugs: list[str],
                     chunk: Optional[int] = None) -> list[dict]:
    """One subscribe frame per subscription type per chunk of slugs.
    chunk None / <= 0 keeps the original single frame per type."""
    ordered = sorted(slugs)
    if not chunk or chunk <= 0 or len(ordered) <= chunk:
        parts = [ordered]
    else:
        parts = [ordered[i:i + chunk] for i in range(0, len(ordered), chunk)]
    out = []
    for kind, st in (("mkt", SUB_MARKET_DATA), ("trd", SUB_TRADE)):
        for ci, part in enumerate(parts):
            out.append({"subscribe": {
                "request_id": f"{kind}{ci}-{uuid.uuid4().hex[:10]}",
                "subscription_type": st,
                "market_slugs": list(part)}})
    return out


def guard_outbound(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    """Every frame leaving this process passes through here."""
    bad = set(frame) - OUTBOUND_FRAME_KEYS
    if bad:
        raise RuntimeError(f"blocked non-read-only outbound frame keys: {bad}")
    return frame


def root_dir() -> Path:
    return Path(os.getenv(ROOT_ENV, DEFAULT_ROOT))


def daily_path(root: Path, now: Optional[datetime] = None) -> Path:
    """The tape part being written NOW: hourly parts since 2026-09-08
    (`pmus_ws_YYYYMMDD_HH.jsonl`) so retention can drop whole old parts
    without ever rewriting a file. The pre-existing daily files
    (`pmus_ws_YYYYMMDD.jsonl`) stay readable and sweepable."""
    now = now or datetime.now(timezone.utc)
    return root / "raw" / f"pmus_ws_{now:%Y%m%d_%H}.jsonl"


def tape_parts(root: Path) -> list:
    """Every raw tape part, oldest first (daily file of a day sorts before
    that day's hourly parts: '.' < '_')."""
    raw = root / "raw"
    if not raw.exists():
        return []
    return sorted(p for p in raw.glob("pmus_ws_*.jsonl") if p.is_file())


def raw_max_bytes() -> int:
    return int(os.getenv(RAW_MAX_BYTES_ENV, str(DEFAULT_RAW_MAX_BYTES)))


def sweep_raw_tapes(root: Path, max_bytes: Optional[int] = None,
                    now: Optional[datetime] = None) -> dict:
    """Delete the OLDEST tape parts until the raw directory fits
    `max_bytes`; the part being written now is never deleted. Returns an
    audit row (deleted parts, bytes freed, bytes kept). Errors never
    propagate: a sweep that cannot delete reports it and the write path
    raises its own alarm."""
    budget = raw_max_bytes() if max_bytes is None else int(max_bytes)
    current = daily_path(root, now)
    parts = tape_parts(root)
    sizes = {}
    for p in parts:
        try:
            sizes[p] = p.stat().st_size
        except OSError:
            sizes[p] = 0
    total = sum(sizes.values())
    deleted, freed, errors = [], 0, []
    for p in parts:
        if budget <= 0 or total <= budget:
            break
        if p == current:
            continue
        try:
            p.unlink()
            deleted.append(p.name)
            freed += sizes[p]
            total -= sizes[p]
        except OSError as exc:
            errors.append(f"{p.name}:{type(exc).__name__}")
    return {"budget_bytes": budget, "parts_before": len(parts),
            "deleted": deleted, "freed_bytes": freed, "kept_bytes": total,
            "kept_parts": len(parts) - len(deleted), "errors": errors}


def append(root: Path, row: Mapping[str, Any]) -> None:
    """Append one tape row. Fail-SOFT: on a full volume (ENOSPC) the
    sweep runs once and the write is retried; a second failure is
    counted and alarmed (PMUS_WS_ALARM tape_write_failed, at most once a
    minute) instead of crashing the recorder -- the probe reads the
    status document, and a crash-looping recorder starves it."""
    path = daily_path(root)
    line = json.dumps(row, separators=(",", ":"), default=str) + "\n"
    for attempt in (0, 1):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as sink:
                sink.write(line)
            return
        except OSError as exc:
            if attempt == 0:
                sw = sweep_raw_tapes(root)
                if sw["deleted"]:
                    print("PMUS_WS_STATUS " + json.dumps(
                        {"raw_retention": {**sw, "trigger": "write_error",
                                           "err": f"{type(exc).__name__}:{str(exc)[:80]}"}},
                        separators=(",", ":"), default=str), flush=True)
                continue
            TAPE_WRITE_ERRORS["n"] += 1
            now = time.time()
            if now - TAPE_WRITE_ERRORS["last_alarm"] >= 60.0:
                TAPE_WRITE_ERRORS["last_alarm"] = now
                print("PMUS_WS_ALARM " + json.dumps(
                    {"kind": "tape_write_failed", "errors": TAPE_WRITE_ERRORS["n"],
                     "err": f"{type(exc).__name__}:{str(exc)[:120]}",
                     "part": path.name,
                     "labels": "tape row dropped; stream/status unaffected; logging only"},
                    separators=(",", ":"), default=str), flush=True)


# The status document the spring probe's admission guard reads
# (PMUS_SPRING_EVIDENCE_STATUS_PATH). The recorder has always PRINTED its
# status; printing it only to logs meant the payload had to be copied by
# hand into an env var inside its own 120s freshness bound. Persisting it
# costs one small write per status tick and removes the copy.
STATUS_DOC_NAME = "pmus_ws_status.jsonl"
STATUS_DOC_KEEP = 64          # newest N documents; this is a handoff, not a tape


def status_doc_path(root: Path) -> Path:
    return root / "status" / STATUS_DOC_NAME


def write_status_doc(root: Path, status: Mapping[str, Any]) -> None:
    """Persist the newest status atomically, keeping a short tail.

    Never raises: a handoff-file problem must not cost the connection or
    the tape, exactly like the status print it accompanies.
    """
    try:
        path = status_doc_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(status, separators=(",", ":"), default=str)
        prev: list = []
        if path.exists():
            prev = path.read_text(encoding="utf-8").splitlines()[
                -(STATUS_DOC_KEEP - 1):]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join([*prev, line]) + "\n", encoding="utf-8")
        tmp.replace(path)       # readers never see a half-written document
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Per-market authenticated trade tail for the PMUS passive L5 surface.
# OFF by default; GET-only; same recorder process/port; no order capability.
TRADES_HTTP_PATH = "/trades"
TRADES_ENV = "PMUS_WS_TRADES_HTTP"
TRADES_PER_MARKET = 200
_LIVE_TRADES: dict[str, Any] = {"line": None, "utc": None, "n": 0}


def trades_http_enabled() -> bool:
    """OFF unless explicitly turned on."""
    return str(os.getenv(TRADES_ENV, "")).strip().lower() in (
        "1", "true", "yes", "on")


def trades_payload(state: Mapping[str, Any], now: float) -> dict:
    """Per-market trade tail: {t, px, qty, dir}, newest last."""
    markets: dict = {}
    for slug, s in (state or {}).items():
        raw = list((s or {}).get("trades") or [])
        markets[slug] = {
            "trades": [{"t": t.get("t"), "px": t.get("px"),
                        "qty": t.get("qty"), "dir": t.get("dir")}
                       for t in raw[-TRADES_PER_MARKET:]],
            "ring_full": len(raw) >= 500,
            "bad_trade_rows": (s or {}).get("bad_trade_rows", 0),
        }
    return {"t": datetime.now(timezone.utc).isoformat(), "now": now,
            "markets": markets,
            "labels": ("AUTHENTICATED trade tape, read-only. ring_full means "
                       "older trades in an interval may have been dropped, so "
                       "an empty result no longer means nobody traded.")}


def publish_live_trades(state: Mapping[str, Any], now: float) -> None:
    """Publish the newest trade tail for the HTTP handler. Never raises."""
    if not trades_http_enabled():
        return
    try:
        line = json.dumps(trades_payload(state, now),
                          separators=(",", ":"), default=str)
        _LIVE_TRADES["line"] = (line + "\n").encode("utf-8")
        _LIVE_TRADES["utc"] = datetime.now(timezone.utc).isoformat()
        _LIVE_TRADES["n"] = int(_LIVE_TRADES.get("n") or 0) + 1
    except Exception:
        pass


#: Bounded INCREMENTAL emission of authenticated trade rows to the existing
#: log stream. OFF by default; the value is the per-market cap per emission.
#:
#: WHY THE LOG AND NOT THE /trades ENDPOINT. The rows are already held in this
#: process and the analysis only needs to READ them. Serving them over HTTP
#: means a port, a route and -- to be reachable from outside Railway's private
#: network -- a public domain, which is a new access-control surface for data
#: that the existing log stream can carry with no surface at all. The narrowest
#: safe method is the one that adds nothing.
#:
#: INCREMENTAL, so the log is a TAPE and not a repeated snapshot. Each emission
#: carries only rows not already emitted, keyed on the row's own identity, so a
#: consumer can concatenate emissions without deduplicating and without holes.
TRADES_LOG_ENV = "PMUS_WS_TRADES_LOG"
_TRADES_LOG_SEEN: dict = {}


def trades_log_cap() -> int:
    """0 (off) unless set to a positive integer."""
    try:
        v = int(str(os.getenv(TRADES_LOG_ENV, "")).strip() or "0")
    except ValueError:
        return 0
    return max(0, min(v, 200))


def _trade_row_key(slug: str, t: Mapping[str, Any]) -> str:
    return "|".join((slug, str(t.get("t")), str(t.get("px")),
                     str(t.get("qty")), str(t.get("trade_time")),
                     str(t.get("dir"))))


def emit_new_trades(state: Mapping[str, Any]) -> Optional[dict]:
    """Print rows first seen since the last emission. Never raises.

    Read-only by construction: it formats data this process already holds and
    sends it to stdout. It opens no socket, serves no request and cannot reach
    an order path.
    """
    cap = trades_log_cap()
    if not cap:
        return None
    out = {}
    total = 0
    for slug, s in (state or {}).items():
        rows = list((s or {}).get("trades") or [])
        fresh = []
        seen = _TRADES_LOG_SEEN.setdefault(slug, set())
        for t in rows:
            k = _trade_row_key(slug, t)
            if k in seen:
                continue
            seen.add(k)
            fresh.append({"t": t.get("t"), "px": t.get("px"),
                          "qty": t.get("qty"),
                          "trade_time": t.get("trade_time"),
                          "bid_at_trade": t.get("bid_at_trade"),
                          "ask_at_trade": t.get("ask_at_trade"),
                          "dir": t.get("dir")})
        if len(seen) > 4000:                      # bounded memory
            _TRADES_LOG_SEEN[slug] = set(list(seen)[-2000:])
        if fresh:
            out[slug] = fresh[-cap:]
            total += len(out[slug])
    if not out:
        return None
    doc = {"t": datetime.now(timezone.utc).isoformat(),
           "kind": "authenticated_trade_rows",
           "markets": out, "rows": total, "cap_per_market": cap,
           "schema": "t=receipt epoch, px=trade price, qty=size, "
                     "trade_time=venue time, bid_at_trade/ask_at_trade="
                     "AUTHENTICATED top of book at the instant of the trade, "
                     "dir=taker direction from _trade_direction "
                     "(buy|sell|unknown)",
           "labels": "AUTHENTICATED read-only trade tape. Not a fill of ours; "
                     "queue position is not implied."}
    try:
        print("PMUS_TRADE_ROWS " + json.dumps(
            doc, separators=(",", ":"), default=str), flush=True)
    except Exception:                                    # noqa: BLE001
        return None
    return doc


#: Bounded INCREMENTAL emission of AUTHENTICATED BOOK state to the log.
#:
#: WHY THIS EXISTS. The L5 observer reads the PUBLIC gateway, which exposes a
#: BTC15m market only PARTWAY INTO its own window: probed at 21:36Z the LIVE
#: 21:30 window returned HTTP 404 while 21:15 had shown a deep book at 21:28Z,
#: thirteen minutes into itself. The authenticated stream sees the same market
#: trading from +6.8s. So the observer was STRUCTURALLY blind exactly while L5
#: opens his sequence, and NO_BOOK was being recorded as venue illiquidity
#: when it was our own blindness.
#:
#: This process already holds the authenticated book. It formats what it holds
#: and writes it to stdout -- no socket, no route, no credential, no new
#: access surface, and no order path. OFF by default; the value is the cap of
#: snapshots per market per emission.
#:
#: NOTHING IS FABRICATED. Each row is a book state the venue actually sent,
#: stamped with OUR receipt time. No BBO is derived from trades, no price is
#: interpolated and no gap is backfilled. A market with no usable book emits
#: its STATUS, not a guessed price.
BOOK_LOG_ENV = "PMUS_WS_BOOK_LOG"

#: comma-separated substrings; a market is emitted only if its slug contains
#: one of them. Empty means every watched market, which is what the first
#: deployment did -- and the platform TRUNCATED the result at 81920 bytes,
#: splitting one document across several log entries and leaving every
#: fragment unparseable. A tape that cannot be parsed is not evidence, so the
#: study names the series it wants and the sports slate stays out of it.
BOOK_LOG_MARKETS_ENV = "PMUS_WS_BOOK_LOG_MARKETS"

#: hard ceiling on markets per PRINTED document, so no single log line can
#: approach the platform's limit again even with no filter set. Emissions are
#: split across several documents, each self-contained and independently
#: parseable, rather than one oversized line.
BOOK_LOG_MARKETS_PER_DOC = 8

_BOOK_LOG_SEEN: dict = {}

#: a snapshot older than this at emission is reported STALE rather than fresh
BOOK_STALE_S = 15.0


def book_log_cap() -> int:
    """0 (off) unless set to a positive integer."""
    try:
        v = int(str(os.getenv(BOOK_LOG_ENV, "")).strip() or "0")
    except ValueError:
        return 0
    return max(0, min(v, 400))


def book_log_markets() -> list:
    """Substrings a market slug must contain to be emitted; [] means all."""
    raw = str(os.getenv(BOOK_LOG_MARKETS_ENV, "") or "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def book_log_wanted(slug: str, wanted: list) -> bool:
    return (not wanted) or any(w in str(slug) for w in wanted)


def book_status(snap: Mapping[str, Any], now: float,
                connected: bool = True) -> str:
    """Explicit state. Absence is always named, never implied by a null."""
    if not connected:
        return "DISCONNECTED"
    bid, ask = snap.get("bid"), snap.get("ask")
    if bid is None and ask is None:
        return "NO_BOOK"
    if bid is None or ask is None:
        return "ONE_SIDED"
    t = snap.get("t")
    if t is not None and (now - float(t)) > BOOK_STALE_S:
        return "STALE"
    return "OK"


#: THE LADDER TAPE. `ladder_history` is captured for the markets
#: PMUS_WS_LADDER_ROLLING selects, but nothing ever printed it, so the depth
#: at the level our maker intents would JOIN was recorded and unreadable:
#: emit_new_books carries top-of-book only, the status document carries
#: ladder_release only, and the service has no domain. This prints it, using
#: the same incremental-tape discipline as emit_new_books so a consumer can
#: concatenate emissions without deduplicating and without holes.
#:
#: BOUNDED THREE WAYS, because a 16-rung ladder at ~1 snapshot/second would
#: otherwise dwarf every other line: rungs per side (LADDER_LOG_RUNGS),
#: snapshots per emission (LADDER_LOG_CAP) and markets per document. It adds
#: no capture, no subscription and no state -- it only reads what is already
#: there.
LADDER_LOG_RUNGS = 4
LADDER_LOG_CAP = 40
LADDER_LOG_MARKETS_PER_DOC = 2
_LADDER_LOG_SEEN: dict = {}


def emit_new_ladders(state: Mapping[str, Any], *, connected: bool = True,
                     now: Optional[float] = None) -> Optional[dict]:
    """Print ladder snapshots first seen since the last emission.

    Selection is EXACTLY `ladder_markets()`: a market that is not retaining a
    ladder is not emitted, so this can never widen what is captured. Never
    raises, for the same reason emit_new_books does not: an evidence
    convenience must not be able to stop the capture it observes.
    """
    try:
        keep = ladder_markets(now)
        if not keep:
            return None
        now = time.time() if now is None else now
        out: dict = {}
        total = 0
        for slug, s in (state or {}).items():
            if slug not in keep or not isinstance(s, Mapping):
                continue
            hist = list((s or {}).get("ladder_history") or [])
            if not hist:
                continue
            seen = _LADDER_LOG_SEEN.setdefault(slug, set())
            fresh = []
            for h in hist:
                ts = h.get("t")
                if ts is None or ts in seen:
                    continue
                seen.add(ts)
                bids = list(h.get("bids") or [])[:LADDER_LOG_RUNGS]
                asks = list(h.get("asks") or [])[:LADDER_LOG_RUNGS]
                # the touch, named explicitly, so a consumer never has to
                # infer which rung was best from ordering alone
                fresh.append({
                    "t": ts,
                    "bid": (bids[0][0] if bids else None),
                    "ask": (asks[0][0] if asks else None),
                    "bids": bids, "asks": asks,
                    "rungs_cap": h.get("rungs_cap"),
                    "status": book_status(
                        {"bid": (bids[0][0] if bids else None),
                         "ask": (asks[0][0] if asks else None),
                         "t": ts}, now, connected),
                })
            if len(seen) > 4000:
                _LADDER_LOG_SEEN[slug] = set(list(seen)[-2000:])
            if fresh:
                out[slug] = fresh[-LADDER_LOG_CAP:]
                total += len(out[slug])
        if not out:
            return None

        def document(markets: dict) -> dict:
            return {
                "t": datetime.now(timezone.utc).isoformat(),
                "kind": "authenticated_ladder_rows",
                "markets": markets,
                "rows": sum(len(v) for v in markets.values()),
                "rungs_per_side": LADDER_LOG_RUNGS,
                "cap_per_market": LADDER_LOG_CAP,
                "connected": connected,
                "schema": ("t=OUR receipt epoch for that book state; bids/asks"
                           "=[[price, quantity], ...] best-first, AUTHENTICATED"
                           " as the venue served them, truncated to "
                           "rungs_per_side; bid/ask repeat rung 0 for "
                           "convenience; status=OK|STALE|ONE_SIDED|NO_BOOK|"
                           "DISCONNECTED"),
                "labels": ("AUTHENTICATED read-only ladder tape. DEPTH IS NOT "
                           "A FILL: these rungs say what was resting ahead of "
                           "a joiner at that price, not that any order of ours "
                           "executed. Nothing here is derived, interpolated or "
                           "backfilled, and the same marketSlug keys the book "
                           "and trade tapes."),
            }

        slugs = list(out)
        for i in range(0, len(slugs), LADDER_LOG_MARKETS_PER_DOC):
            chunk = {s: out[s] for s in slugs[i:i + LADDER_LOG_MARKETS_PER_DOC]}
            print("PMUS_LADDER_ROWS " + json.dumps(
                document(chunk), separators=(",", ":"), default=str),
                flush=True)
        doc = document(out)
        doc["rows"] = total
        return doc
    except Exception:                                    # noqa: BLE001
        return None


def emit_new_books(state: Mapping[str, Any], *, connected: bool = True,
                   now: Optional[float] = None) -> Optional[dict]:
    """Print authenticated book snapshots first seen since the last emission.

    INCREMENTAL, so the log is a TAPE and not a repeated snapshot: each
    emission carries only history entries not already emitted, keyed on their
    own receipt timestamp, so a consumer can concatenate emissions without
    deduplicating and without holes. Never raises: an evidence convenience
    must not be able to stop the capture it observes.
    """
    cap = book_log_cap()
    if not cap:
        return None
    now = time.time() if now is None else now
    out: dict = {}
    total = 0
    wanted = book_log_markets()
    try:
        for slug, s in (state or {}).items():
            if not book_log_wanted(slug, wanted):
                continue
            hist = list((s or {}).get("bbo_history") or [])
            if not hist:
                continue
            seen = _BOOK_LOG_SEEN.setdefault(slug, set())
            fresh = []
            for h in hist:
                k = str(h.get("t"))
                if k in seen:
                    continue
                seen.add(k)
                fresh.append({
                    # every field comes from THIS history entry. Reading depth
                    # from the live snapshot `s` would backdate present depth
                    # onto a past book state. A state captured before the
                    # ask_qty field existed reports null depth, which the
                    # faithful rule refuses as NO_DEPTH -- it never guesses.
                    "t": h.get("t"),
                    "bid": h.get("bid"), "ask": h.get("ask"),
                    "bid_qty": h.get("bid_qty"), "ask_qty": h.get("ask_qty"),
                    "ask_book_missing": h.get("ask_book_missing"),
                    "status": book_status(
                        {"bid": h.get("bid"), "ask": h.get("ask"),
                         "t": h.get("t")}, now, connected),
                })
            if len(seen) > 4000:
                _BOOK_LOG_SEEN[slug] = set(list(seen)[-2000:])
            if fresh:
                out[slug] = fresh[-cap:]
                total += len(out[slug])
        if not out:
            return None

        def document(markets: dict) -> dict:
            return {
                "t": datetime.now(timezone.utc).isoformat(),
                "kind": "authenticated_book_rows",
                "markets": markets,
                "rows": sum(len(v) for v in markets.values()),
                "cap_per_market": cap,
                "connected": connected,
                "identity": {slug: {"marketSlug": slug,
                                    "book_updates": (state.get(slug) or {}).get(
                                        "book_updates"),
                                    "ws_trades": len((state.get(slug) or {}).get(
                                        "trades") or [])}
                             for slug in markets},
                "schema": ("t=OUR receipt epoch for that book state; bid/ask="
                           "AUTHENTICATED top of book as the venue sent it; "
                           "bid_qty/ask_qty=displayed depth AT THAT STATE, "
                           "null when the venue had not served that side; "
                           "status=OK|STALE|ONE_SIDED|NO_BOOK|DISCONNECTED"),
                "labels": ("AUTHENTICATED read-only book tape. Nothing is "
                           "derived from trades, interpolated or backfilled. "
                           "The same marketSlug keys the trade tape, so book "
                           "and trades resolve to ONE market identity."),
            }

        # SPLIT across several self-contained documents. One oversized line is
        # truncated by the log platform and every fragment of it is
        # unparseable, which loses the whole emission; several smaller lines
        # lose nothing.
        slugs = list(out)
        for i in range(0, len(slugs), BOOK_LOG_MARKETS_PER_DOC):
            chunk = {s: out[s] for s in slugs[i:i + BOOK_LOG_MARKETS_PER_DOC]}
            print("PMUS_BOOK_ROWS " + json.dumps(
                document(chunk), separators=(",", ":"), default=str),
                flush=True)
        doc = document(out)
        doc["rows"] = total
        return doc
    except Exception:                                    # noqa: BLE001
        return None


def live_trades_bytes() -> Optional[bytes]:
    """Newest published trade tail, or None when unset/disabled."""
    if not trades_http_enabled():
        return None
    v = _LIVE_TRADES.get("line")
    return v if isinstance(v, (bytes, bytearray)) else None


# Read-only status handoff over Railway's private network. The spring
# probe and the recorder sit on different volumes, so the status document
# cannot be shared as a file; this serves the SAME document the recorder
# already writes, byte for byte. GET only, no state, no credentials, no
# order path. "0" disables it.
STATUS_PORT_ENV = "PMUS_WS_STATUS_PORT"
DEFAULT_STATUS_PORT = 8080
STATUS_HTTP_PATHS = ("/status", "/status/pmus_ws_status.jsonl")
# OWNER (maximize legitimate evidence turnover): the recorder runs the
# read-only multi-market L5 shadow over EVERY subscribed market and serves
# its report beside the status document. "0" disables the shadow.
SHADOW_HTTP_PATH = "/l5_shadow"
#: set once the recorder is running; None before that
LIVE_MSTATE: Optional[dict] = None

SHADOW_ENV = "PMUS_L5_MULTI_SHADOW"
SHADOW_TICK_S = 1.0
SHADOW_MARKET_STATE_OF = None     # enrichment may install a cache reader


def shadow_report_path(root: Path) -> Path:
    from src import pmus_l5_multi_shadow as ms
    return root / "l5_shadow" / ms.REPORT_FILE


def start_multi_shadow(root: Path):
    """The multi-market shadow, or None when disabled / unavailable. A
    shadow failure can never block the recorder."""
    if os.getenv(SHADOW_ENV, "1").strip() == "0":
        return None
    try:
        from src import pmus_l5_multi_shadow as ms
        sh = ms.MultiShadow(root / "l5_shadow",
                            market_state_of=SHADOW_MARKET_STATE_OF)
        print("PMUS_L5_SHADOW " + json.dumps(
            {"kind": "shadow_start", "restore": sh.restore, "model_sha": sh.model_sha,
             "code_sha": ms.CODE_SHA, "lookup_version": ms.LOOKUP_VERSION,
             "labels": ms.LABELS}, separators=(",", ":"), default=str), flush=True)
        return sh
    except Exception as exc:                          # noqa: BLE001
        print("PMUS_L5_SHADOW " + json.dumps(
            {"kind": "shadow_unavailable", "err": f"{type(exc).__name__}:{str(exc)[:120]}"}),
            flush=True)
        return None


def shadow_compact(rep: Mapping[str, Any]) -> dict:
    b, e = rep.get("behavioral_layer") or {}, rep.get("executable_layer") or {}
    return {"kind": "shadow_report",
            **{k: rep.get(k) for k in ("markets_shadowed", "markets_with_decisions",
                                       "unique_decisions", "post_fill_decisions",
                                       "theoretical_fills")},
            "funnel": rep.get("funnel"),
            "behavioral": {k: b.get(k) for k in ("scored_decisions", "l1_sampled_vs_l5",
                                                 "off_distribution_rate_pct")},
            "behavioral_post_fill": (b.get("post_fill") or {}).get("complete_freq_gap_pp_sampled"),
            "executable": {k: e.get(k) for k in ("execution_eligible", "eligible_share",
                                                 "refusals_by_reason")},
            "complete_counterfactual": e.get("complete_counterfactual"),
            "lookup_provenance": rep.get("lookup_provenance")}


def status_port() -> int:
    raw = os.getenv(STATUS_PORT_ENV, "").strip() or os.getenv(
        "PORT", "").strip() or str(DEFAULT_STATUS_PORT)
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


#: the bounded shadow-records path. ABSENT unless a token is configured:
#: with no token the handler 404s, so the default deployment serves nothing
#: new at all. The existing status paths carry NO authentication and are
#: protected only by this service having no public domain -- which is a
#: network fact, not a control, and is not good enough for records that
#: carry inventory and cost. This path therefore brings its own.
SHADOW_RECORDS_PATH = "/shadow-policy"
SHADOW_RECORDS_TOKEN_ENV = "L5_SHADOW_POLICY_TOKEN"
SHADOW_RECORDS_MAX_LIMIT = 200


def shadow_records_token() -> str:
    """The bearer token, or empty when the path is disabled."""
    return str(os.getenv(SHADOW_RECORDS_TOKEN_ENV) or "").strip()


def shadow_records_authorized(header: Any) -> bool:
    """Constant-time bearer check. Empty token => never authorized.

    The token is never logged, never echoed and never compared with `==`.
    """
    import hmac
    tok = shadow_records_token()
    if not tok:
        return False
    got = str(header or "")
    prefix = "Bearer "
    if not got.startswith(prefix):
        return False
    return hmac.compare_digest(got[len(prefix):], tok)


def serve_status_doc(root: Path, port: int):
    """Serve the status document read-only on a daemon thread.

    Returns the server (so tests can learn the bound port and shut it
    down) or None when disabled/unbindable. Never raises: a handoff
    failure must not cost the connection or the tape.
    """
    if port <= 0:
        return None
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    doc = status_doc_path(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a: Any) -> None:     # keep stdout for status
            pass

        def do_GET(self) -> None:                     # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == TRADES_HTTP_PATH:
                blob = live_trades_bytes()
                if blob is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return
            if path == SHADOW_RECORDS_PATH:
                if not shadow_records_token():
                    self.send_response(404)       # the path does not exist
                    self.end_headers()
                    return
                if not shadow_records_authorized(
                        self.headers.get("Authorization")):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", "Bearer")
                    self.end_headers()
                    return
                _spl = shadow_policy_log()
                if _spl is None:
                    self.send_response(503)
                    self.end_headers()
                    return
                try:
                    import urllib.parse as _up
                    q = _up.parse_qs(
                        (self.path.split("?", 1) + [""])[1])
                    lim = int((q.get("limit") or ["50"])[0])
                except Exception:
                    lim = 50
                lim = max(1, min(lim, SHADOW_RECORDS_MAX_LIMIT))
                try:
                    blob = json.dumps(_spl.records_view(limit=lim),
                                      separators=(",", ":"),
                                      default=str).encode()
                except Exception:
                    self.send_response(503)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(blob)
                return
            if path == SHADOW_HTTP_PATH:
                # the read-only multi-market L5 shadow report (same
                # handoff pattern as the status document)
                src_path = shadow_report_path(root)
            elif path in STATUS_HTTP_PATHS:
                src_path = doc
            else:
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = src_path.read_bytes()
            except OSError:
                self.send_response(503)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    class DualStack(HTTPServer):
        # Railway private networking is IPv6-only; "::" also accepts v4.
        address_family = socket.AF_INET6
        allow_reuse_address = True

        def server_bind(self) -> None:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6,
                                       socket.IPV6_V6ONLY, 0)
            except (OSError, AttributeError):
                pass
            super().server_bind()

    try:
        try:
            srv = DualStack(("::", port), Handler)
        except OSError:
            srv = HTTPServer(("0.0.0.0", port), Handler)
    except OSError as exc:
        print("PMUS_WS_STATUS " + json.dumps(
            {"status_http": "unbound", "port": port, "err": str(exc)[:80]}),
            flush=True)
        return None
    threading.Thread(target=srv.serve_forever, name="pmus-ws-status-http",
                     daemon=True).start()
    print("PMUS_WS_STATUS " + json.dumps(
        {"status_http": "serving", "port": srv.server_address[1],
         "paths": list(STATUS_HTTP_PATHS), "read_only": True}), flush=True)
    return srv


def heartbeat_file(root: Path, **fields: Any) -> None:
    tmp = root / "heartbeat.tmp"
    root.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps({"t": datetime.now(timezone.utc).isoformat(),
                               "mode": "READ_ONLY_WS_RECORDER", **fields},
                              indent=1))
    os.replace(tmp, root / "heartbeat.json")


def series_slugs(series_slug: str) -> list:
    """The window of a recurring series that is trading NOW, plus the next.

    A recurring 15-minute series creates every window hours ahead and expires
    each one on a hard clock, so "which market is this series right now" is a
    wall-clock question, not a `live=true` flag the venue only sets for
    sports. A static PMUS_WS_MARKETS list cannot express it: each window is
    its own market, so the list goes dead within fifteen minutes -- and
    because explicit discovery returns EARLY, it would take the sports slate
    down with it while it lived.

    Closed windows are excluded. A window whose start is within
    SERIES_LEAD_S is admitted so its first seconds are not lost at rollover.

    Inlined rather than imported: `src/pmus_book_tape.py` is not in this
    image's COPY set, and this needs no dependency the recorder lacks.
    """
    from datetime import datetime, timedelta, timezone
    from src import pmus_sports_collector as col
    now = datetime.now(timezone.utc)
    try:
        sid = None
        off = 0
        while off < 2000 and sid is None:
            d = col.http_get_json("/v1/series?limit=100&offset=%d" % off) or {}
            rows = d.get("series") or []
            if not rows:
                break
            for r in rows:
                if str(r.get("slug")) == series_slug:
                    sid = str(r.get("id"))
                    break
            off += len(rows)
        if sid is None:
            return []
        evs, off = [], 0
        while off < 4000:
            d = col.http_get_json(
                "/v1/events?series_id=%s&limit=100&offset=%d" % (sid, off)) or {}
            rows = d.get("events") or []
            if not rows:
                break
            evs += rows
            off += len(rows)
            if len(rows) < 100:
                break
    except Exception:                                    # noqa: BLE001
        return []
    live = []
    for e in evs:
        if e.get("closed"):
            continue
        try:
            start = datetime.fromisoformat(
                str(e.get("startTime")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        live.append((start, e))
    live.sort(key=lambda r: r[0])
    out = []
    for start, e in live:
        if start > now + timedelta(seconds=SERIES_LEAD_S):
            break
        for m in (e.get("markets") or []):
            if m.get("slug"):
                out.append(str(m["slug"]))
    return out


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
    series = [s.strip() for s in os.getenv(SERIES_ENV, "").split(",")
              if s.strip()]
    if series:
        out = []
        for name in series:
            for slug in series_slugs(name):
                out.append({"slug": slug, "league": None,
                            "why": "series:%s" % name,
                            "event_state": None, "prop": False,
                            "market_meta": {"series": name}})
        return out
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
    # PRIMARY live surface (issue #167 comments 5525261365/5525293221/
    # 5525317831): the server-side /v1/events?live=true set carries the
    # complete live slate with complete embedded market sets (measured
    # live; the legacy global-market join below buried live rows beyond
    # its page cap). Failure is surfaced in diagnostics and behavior
    # degrades to the legacy league path — never silently.
    v1_diag: dict = {"v1_live_events": 0, "v1_live_error": "not_run"}
    v1_added = 0
    try:
        live_events, v1_diag = col.fetch_live_events(col.http_get_json)
        seen = {t["slug"] for t in targets}
        for row in col.live_event_targets(live_events, prefixes):
            if row["slug"] not in seen:
                seen.add(row["slug"])
                targets.append(row)
                v1_added += 1
    except Exception as exc:
        v1_diag = {"v1_live_events": 0,
                   "v1_live_error": f"{type(exc).__name__}:{str(exc)[:80]}"}
    # Legacy full-universe expansion kept as supplemental/fallback; its
    # (measured) emptiness is now explained by the join diagnostics
    # instead of hidden. PMUS_WS_EXPAND_PAGES=0 disables it.
    pages = 0
    expanded: list[dict] = []
    open_rows: list = []
    max_pages = 0
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
    try:
        join_diag = col.enumeration_join_diagnostics(
            events_by_league, open_rows, pages >= max_pages > 0)
    except Exception:
        join_diag = {}
    DISCOVERY_DIAG.clear()
    DISCOVERY_DIAG.update({
        "events_discovered": sum(len(v) for v in events_by_league.values()),
        "markets_embedded": embedded_n,
        "markets_expanded": len(targets) - embedded_n - v1_added,
        "v1_live_markets_added": v1_added,
        "enumeration_pages_fetched": pages,
        **v1_diag,
        **join_diag,
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


STATE_MAX_MARKETS_ENV = "PMUS_WS_STATE_MAX_MARKETS"   # default 1500
STATE_MAX_IDLE_ENV = "PMUS_WS_STATE_MAX_IDLE_S"       # default 7200 s


def prune_state(mstate: dict, keep: Iterable[str], now: Optional[float] = None,
                max_idle_s: Optional[float] = None,
                max_markets: Optional[int] = None) -> dict:
    """Bound the IN-MEMORY market state, the way sweep_raw_tapes bounds disk.

    WHY THIS EXISTS. `mstate` was never pruned: it kept one entry per market
    EVER observed, each holding up to 2,000 bbo_history snapshots and 500
    trades. COLD rotation samples the whole discovered universe by design
    (11,201 live markets measured 2026-09-13), so the dict grows monotonically
    for the life of the process. Measured RSS before this change: 8.27 GB. That
    is the memory analogue of the 2026-09-08 ENOSPC, and raising the
    subscription cap doubles the rate markets enter the dict -- so the bound has
    to land BEFORE the cap does, not after.

    WHAT IT WILL NOT DO. Admission reads trades_1h / two_sided_trades off this
    state, so evicting a market throws away the evidence that makes it
    admissible. Eviction is therefore deliberately conservative:
      * never evicts anything in `keep` (currently subscribed + protected),
        whatever its age;
      * only evicts markets IDLE beyond max_idle_s (default 2 h -- comfortably
        longer than the 1 h window trades_1h needs), so a market cannot lose
        its evidence while that evidence is still live;
      * only then, if still over max_markets, drops the oldest-idle first.
    A never-observed entry has no authenticated timestamp and is treated as
    maximally idle -- it carries no evidence to lose."""
    now = time.time() if now is None else now
    if max_idle_s is None:
        max_idle_s = float(os.getenv(STATE_MAX_IDLE_ENV, "7200"))
    if max_markets is None:
        max_markets = int(os.getenv(STATE_MAX_MARKETS_ENV, "1500"))
    keep = set(keep or ())
    from src import pmus_priority_queue as _pq
    ages: list[tuple[float, str]] = []
    for slug, st in mstate.items():
        if slug in keep:
            continue
        t = _pq.last_authenticated_t(st)
        ages.append(((now - t) if t is not None else float("inf"), slug))
    evicted_idle = [s for age, s in ages if age > max_idle_s]
    for slug in evicted_idle:
        mstate.pop(slug, None)
    evicted_cap: list[str] = []
    if len(mstate) > max_markets:
        remaining = sorted(((age, s) for age, s in ages if s in mstate),
                           reverse=True)
        for _age, slug in remaining:
            if len(mstate) <= max_markets:
                break
            mstate.pop(slug, None)
            evicted_cap.append(slug)
    return {"kept": len(mstate), "evicted_idle": len(evicted_idle),
            "evicted_over_cap": len(evicted_cap), "protected": len(keep),
            "max_idle_s": max_idle_s, "max_markets": max_markets}


def discover_slugs() -> list[str]:
    """Back-compat single-shot discovery: full universe, cycle-0 plan."""
    cap = int(os.getenv(MAX_MARKETS_ENV, "24"))
    return subscription_plan(discover_universe(), {}, cap, 0)["subscribe"]


# ----------------- restart hydration from the persisted tape -----------------

def iter_recent_tape_rows(root: Path, now: Optional[float] = None,
                          max_bytes: int = 0):
    """Yield (epoch_t, msg) from the persisted tape parts of yesterday and
    today, in order, bounded to the NEWEST `max_bytes` in total (the tail
    of the newest parts). Malformed lines are skipped -- hydration can
    never crash startup."""
    now_dt = datetime.fromtimestamp(now or time.time(), timezone.utc)
    from datetime import timedelta
    days = {f"{d:%Y%m%d}" for d in (now_dt - timedelta(days=1), now_dt)}
    parts = [p for p in tape_parts(root)
             if p.name[len("pmus_ws_"):len("pmus_ws_") + 8] in days]
    chunks = []                      # newest first, then reversed
    budget = max_bytes if max_bytes else None
    for path in reversed(parts):
        if budget is not None and budget <= 0:
            break
        try:
            with path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                take = size if budget is None else min(size, budget)
                f.seek(size - take)
                data = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        lines = data.splitlines()
        if take < size and lines:
            lines = lines[1:]                     # drop the partial first line
        chunks.append(lines)
        if budget is not None:
            budget -= take
    for lines in reversed(chunks):
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
    # Retention FIRST: a full volume must be freed before the first write
    # or the first hydration read (LIVE 2026-09-08 00:40Z ENOSPC crash loop)
    try:
        sweep = sweep_raw_tapes(root)
    except Exception as exc:                      # noqa: BLE001
        sweep = {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}
    print("PMUS_WS_STATUS " + json.dumps(
        {"raw_retention": {**sweep, "trigger": "boot"}},
        separators=(",", ":"), default=str), flush=True)
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
    plan_state: dict = {}
    l5_alloc: Optional[set] = None
    fast_realloc_count = 0
    serve_status_doc(root, status_port())
    shadow = start_multi_shadow(root)
    # P0.1: a reference (not a copy) to the live per-market state, so an
    # in-process reader can take a consistent snapshot whenever it needs one
    # instead of replaying the whole tape minutes later. Reading it never
    # mutates it; writers are unaffected.
    global LIVE_MSTATE
    LIVE_MSTATE = mstate
    last_shadow_tick = 0.0
    health = StreamHealth(time.time())
    starved_reconnects = 0
    import concurrent.futures
    loop = asyncio.get_running_loop()
    worker = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="pmus-side")
    side_busy = {"status": False, "tick": False, "settle": False}
    side_state = {"slugs": [], "discovery": {}, "l5_alloc": None}

    def _status_job(snap: dict, connected: bool, msgs_now: int,
                    n_slugs: int, disc: dict, alloc: Optional[set],
                    sh: dict) -> None:
        """Runs on the worker thread over a snapshot: the reader never
        waits for the status build, the report or the gateway check."""
        try:
            try:
                st = build_status(snap, connected, msgs_now, n_slugs,
                                  discovery=disc, l5_alloc=alloc,
                                  stream_health=sh)
                print("PMUS_WS_STATUS " + json.dumps(
                    st, separators=(",", ":"), default=str), flush=True)
                write_status_doc(root, st)
                publish_live_trades(snap, time.time())
                emit_new_trades(snap)
                emit_new_books(snap, connected=connected)
                emit_new_ladders(snap, connected=connected)
                try:
                    from src import pmus_sports_collector as col
                    xs = gateway_xcheck(
                        [r["slug"] for r in (st.get("spring_watch") or [])]
                        or [r["slug"] for r in (st.get("top_markets") or [])],
                        snap, col.http_get_json, time.time())
                    if xs:
                        print("PMUS_WS_XCHECK " + json.dumps(
                            {"t": st["t"], "rows": xs},
                            separators=(",", ":"), default=str), flush=True)
                except Exception as exc:              # noqa: BLE001
                    print("PMUS_WS_XCHECK " + json.dumps(
                        {"error": str(exc)[:120]}), flush=True)
            except Exception as exc:                  # noqa: BLE001
                # a status bug must never cost the connection or the tape
                print("PMUS_WS_STATUS " + json.dumps(
                    {"status_error": str(exc)[:120],
                     "messages_received": msgs_now}), flush=True)
            if shadow is not None:
                try:
                    rep = shadow.write_report(time.time())
                    if rep:
                        print("PMUS_L5_SHADOW " + json.dumps(
                            shadow_compact(rep) if "error" not in rep
                            else {"kind": "shadow_report_error", **rep},
                            separators=(",", ":"), default=str), flush=True)
                except Exception as exc:              # noqa: BLE001
                    print("PMUS_L5_SHADOW " + json.dumps(
                        {"kind": "shadow_report_error",
                         "err": f"{type(exc).__name__}:{str(exc)[:120]}"}),
                        flush=True)
        finally:
            side_busy["status"] = False

    def schedule_status(connected: bool) -> bool:
        """Due and not already running -> snapshot on the reader thread
        (milliseconds) and hand the work to the worker. Called after a
        frame AND on a silent socket so a starved stream still reports
        and alarms."""
        nonlocal last_status
        if status_every <= 0 or side_busy["status"] or \
                time.monotonic() - last_status < status_every:
            return False
        last_status = time.monotonic()
        side_busy["status"] = True
        snap = snapshot_state(mstate)
        sh = {**health.snapshot(time.time()),
              "starved_reconnects": starved_reconnects,
              "rss_gb": rss_gb(),
              "shadow_enabled": shadow is not None}
        loop.run_in_executor(
            worker, _status_job, snap, connected, msgs,
            len(side_state["slugs"]), dict(side_state["discovery"]),
            side_state["l5_alloc"], sh)
        return True

    def schedule_settlement(spl: Any) -> None:
        """Reconcile pending residuals on the side worker, never inline.

        Never overlaps itself (`side_busy["settle"]`), never raises into the
        loop, and each read carries SETTLEMENT_HTTP_TIMEOUT_S rather than the
        collector's 15s default. If the reader is unavailable the pass is
        abandoned and capture continues untouched.
        """
        if spl is None or side_busy.get("settle"):
            return
        side_busy["settle"] = True

        def _settle() -> None:
            try:
                from functools import partial
                from src import l5_shadow_policy_eval as _SPE
                from src import pmus_sports_collector as _col
                _get = partial(_col.http_get_json,
                               timeout=_SPE.SETTLEMENT_HTTP_TIMEOUT_S)
                done = spl.reconcile_pending(_get)
                if done:
                    print("PMUS_SHADOW_SETTLED " + json.dumps(
                        {"rows": len(done),
                         "markets": [{"market": r.get("market"),
                                      "reconciled": r.get("reconciled"),
                                      "basis": _SPE.settlement_basis(r),
                                      "reason": r.get("reconcile_reason"),
                                      "attempts": r.get("reconcile_attempts"),
                                      "net_usd": {
                                          k: (r.get("net") or {}).get(
                                              k, {}).get("net_usd")
                                          for k in ("HOLD", "REPRICE",
                                                    "CROSS", "BLIND")},
                                      # the variants' SETTLED net. Without
                                      # this the depth comparison resolves
                                      # into the volume store and never
                                      # reaches any readable surface, which
                                      # is the section 41 observability gap
                                      # recurring in the settlement path.
                                      "variants": {
                                          lbl: {kk: vv.get(kk) for kk in (
                                              "net_usd",
                                              "matched_pair_net_usd",
                                              "residual_settlement_net_usd",
                                              "matched_qty", "residual_qty",
                                              "split_basis", "priced")}
                                          for lbl, vv in (
                                              r.get("variants") or {}).items()
                                      } or None}
                                     for r in done],
                         "labels": ("venue-confirmed OUTCOME only; the fills "
                                    "remain MODELLED")},
                        separators=(",", ":"), default=str), flush=True)
                    # THE SCORECARD IS A PURE FOLD, NOT A SECOND STORE.
                    # spl.latest already holds every record, rebuilt from the
                    # append-only store at construction (last line wins per
                    # market), so this needs no file, no network and no state
                    # of its own: idempotent because it is a recomputation,
                    # restart-safe because what it recomputes from is. Emitted
                    # only when a settlement actually resolved, on this same
                    # side worker, so it is off the capture path; and wrapped
                    # separately so a scorecard fault can never cost the
                    # settlement line that was already printed.
                    try:
                        print("PMUS_SHADOW_SCORECARD " + json.dumps(
                            _SPE.depth_scorecard(list(spl.latest.values())),
                            separators=(",", ":"), default=str), flush=True)
                    except Exception:                      # noqa: BLE001
                        pass
            except Exception:                              # noqa: BLE001
                try:
                    spl.reconcile_errors += 1
                except Exception:                          # noqa: BLE001
                    pass
            finally:
                side_busy["settle"] = False

        loop.run_in_executor(worker, _settle)

    def schedule_shadow_tick() -> None:
        nonlocal last_shadow_tick
        if shadow is None or side_busy["tick"] or \
                time.monotonic() - last_shadow_tick < SHADOW_TICK_S:
            return
        last_shadow_tick = time.monotonic()
        side_busy["tick"] = True
        snap = snapshot_state(mstate)
        now = time.time()

        def _tick() -> None:
            try:
                shadow.tick(snap, now)
            except Exception as exc:                  # noqa: BLE001
                print("PMUS_L5_SHADOW " + json.dumps(
                    {"kind": "shadow_tick_error",
                     "err": f"{type(exc).__name__}:{str(exc)[:120]}"}),
                    flush=True)
            finally:
                side_busy["tick"] = False
        loop.run_in_executor(worker, _tick)

    while deadline is None or time.monotonic() < deadline:
        cap = int(os.getenv(MAX_MARKETS_ENV, "24"))
        try:
            sweep = sweep_raw_tapes(root)
            if sweep["deleted"] or sweep["errors"]:
                print("PMUS_WS_STATUS " + json.dumps(
                    {"raw_retention": {**sweep, "trigger": "discovery"}},
                    separators=(",", ":"), default=str), flush=True)
        except Exception as exc:                  # noqa: BLE001
            print("PMUS_WS_STATUS " + json.dumps(
                {"raw_retention": {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}}),
                flush=True)
        targets = discover_universe()
        protected_now = [x.strip() for x in
                         os.getenv(PROTECTED_ENV, "").split(",") if x.strip()]
        # the live window and the one about to open never lose their seat
        for _slug in rolling_window_slugs():
            if _slug not in protected_now:
                protected_now.append(_slug)
        if os.getenv(PLANNER_ENV, "priority").strip() == "rotation":
            plan = subscription_plan(targets, mstate, cap, cycle)
            slugs = plan["subscribe"]
            l5_alloc = None
            discovery_diag = {**plan["diagnostics"], **DISCOVERY_DIAG}
        else:
            # HOT/WARM/COLD priority tiers (issue #167 comment 5521210994)
            from src import pmus_priority_queue as pq
            p = pq.plan(targets, mstate, cap, cycle, plan_state,
                        protected=protected_now)
            plan_state = p["state"]
            slugs = p["subscribe"]
            l5_alloc = set(p["l5_allocated"])
            discovery_diag = {**p["diagnostics"], **DISCOVERY_DIAG}
            append(root, {"kind": "priority_plan",
                          "t": datetime.now(timezone.utc).isoformat(),
                          "diagnostics": p["diagnostics"],
                          "per_market": p["per_market"]})
        # P0.2: the BTC15m cadence is DETERMINISTIC, so the live window and the
        # one about to open are subscribed BY NAME rather than waited for.
        # Generic discovery resolves this series through the public gateway,
        # which does not expose a window until minutes into it -- so discovery
        # cannot name the market at T=0 even when the venue is already
        # trading it. Measured: first authenticated two-sided book at +545.6s
        # into a 900s window. Computing the slug removes that dependency
        # entirely; it costs one subscription seat and no lookup.
        _forced = [x for x in rolling_window_slugs() if x not in slugs]
        if _forced:
            slugs = list(slugs) + _forced
            print("PMUS_WS_STATUS " + json.dumps(
                {"forced_window_subscribe": _forced,
                 "reason": "deterministic slug; discovery cannot name a "
                           "BTC15m window at its open"},
                separators=(",", ":")), flush=True)
        cycle += 1
        # Bound memory the same way and at the same cadence as disk. Runs AFTER
        # the plan so the newly-chosen seats are protected from their own sweep.
        try:
            # RUNS BEFORE prune_state, on its own schedule. prune_state keeps a
            # market for two hours after its last frame because admission reads
            # trades_1h off it; a 15m product leaves eight retired windows
            # inside that grace period, and each would still be holding a
            # ladder. Selection, not idleness, is what releases rungs.
            ladder_keep = ladder_markets()
            if ladder_keep or any("ladder_history" in (v or {})
                                  for v in mstate.values() if isinstance(v, dict)):
                _spl = shadow_policy_log()
                if _spl is not None:
                    _scored = _spl.rollover(keep=ladder_keep)
                    if _scored:
                        print("PMUS_SHADOW_POLICY " + json.dumps(
                            _spl.emit_document(_scored),
                            separators=(",", ":"), default=str), flush=True)
                    # SETTLEMENT, OFF THE EVENT LOOP. This block runs
                    # inside `async def run`, so a synchronous GET here
                    # blocks the websocket exactly as long as it takes --
                    # the collector's default timeout is 15s and four
                    # pending windows would be a minute of lost capture.
                    # It therefore goes to the SAME side worker and the
                    # SAME busy-flag pattern that schedule_status and
                    # schedule_shadow_tick already use, with a short
                    # explicit timeout. Capture outranks reconciliation.
                    schedule_settlement(_spl)
                released = drop_stale_ladders(mstate, ladder_keep)
                if released:
                    print("PMUS_WS_STATUS " + json.dumps(
                        {"ladder_release": {"markets": released,
                                            "retaining": sorted(ladder_keep)}},
                        separators=(",", ":"), default=str), flush=True)
            pruned = prune_state(mstate, set(slugs) | set(protected_now))
            if pruned["evicted_idle"] or pruned["evicted_over_cap"]:
                print("PMUS_WS_STATUS " + json.dumps(
                    {"state_prune": pruned}, separators=(",", ":"),
                    default=str), flush=True)
        except Exception as exc:                  # noqa: BLE001
            print("PMUS_WS_STATUS " + json.dumps(
                {"state_prune": {"error": f"{type(exc).__name__}:{str(exc)[:120]}"}}),
                flush=True)
        side_state.update({"slugs": list(slugs), "discovery": dict(discovery_diag),
                           "l5_alloc": l5_alloc})
        append(root, {"kind": "recorder_start",
                      "t": datetime.now(timezone.utc).isoformat(),
                      "subscribed": slugs, "read_only": True,
                      "discovery": discovery_diag,
                      "note": "subscribe/heartbeat frames only; no order "
                              "path exists in this worker"})
        try:
            headers = auth_headers(os.environ[KEY_ENV].strip(),
                                   os.environ[SECRET_ENV].strip())
            async with websockets.connect(
                    WS_URL, additional_headers=headers,
                    max_size=8 * 1024 * 1024) as ws:
                backoff = 1.0
                chunk = int(os.getenv(SUB_CHUNK_ENV, str(DEFAULT_SUB_CHUNK)))
                frames = subscribe_frames(slugs, chunk)
                for frame in frames:
                    await ws.send(json.dumps(guard_outbound(frame)))
                session_start = time.monotonic()
                health.new_session(time.time())
                session_reason = "rediscover"
                last_hb_sent = time.monotonic()
                frames_logged = 0
                print("PMUS_WS_CONN " + json.dumps(
                    {"kind": "connect", "subscribed": len(slugs),
                     "subscribe_frames": len(frames), "chunk": chunk,
                     "starved_reconnects": starved_reconnects},
                    separators=(",", ":")), flush=True)
                rollover_at = next_window_boundary()
                while deadline is None or time.monotonic() < deadline:
                    if time.monotonic() - session_start > rediscover_s:
                        break                     # reconnect w/ fresh targets
                    if rollover_at is not None and time.time() >= rollover_at:
                        # a new window just opened: resubscribe NOW rather
                        # than arriving minutes into a market that is already
                        # trading. O(1) per frame, exact at the boundary.
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                    except asyncio.TimeoutError:
                        heartbeat_file(root, messages=msgs,
                                       subscribed=len(slugs))
                        if time.monotonic() - last_hb_sent >= IDLE_HEARTBEAT_S:
                            # keep-alive on an idle socket (venue guidance)
                            await ws.send(json.dumps(guard_outbound({"heartbeat": {}})))
                            last_hb_sent = time.monotonic()
                        schedule_status(True)
                        if should_reconnect_starved(
                                health, live_events_of(discovery_diag),
                                time.time(), starved_reconnects):
                            starved_reconnects += 1
                            session_reason = "starved"
                            break                 # fresh subscriptions
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        msg = {"raw": str(raw)[:2000]}
                    if isinstance(msg, dict) and "heartbeat" in msg:
                        await ws.send(json.dumps(guard_outbound({"heartbeat": {}})))
                        last_hb_sent = time.monotonic()
                    kind = classify_frame(msg)
                    health.note(kind, time.time())
                    if kind in ("book", "trade"):
                        starved_reconnects = 0
                    if frames_logged < FRAME_LOG_FIRST or kind == "error":
                        frames_logged += 1
                        print("PMUS_WS_FRAME " + json.dumps(
                            {"kind": kind, "n": msgs + 1,
                             "frame": json.dumps(msg, default=str)[:600]},
                            separators=(",", ":")), flush=True)
                    append(root, {"kind": "ws",
                                  "t": datetime.now(timezone.utc).isoformat(),
                                  "msg": msg})
                    msgs += 1
                    proc_t0 = time.monotonic()
                    apply_events(mstate, msg, time.time())
                    schedule_shadow_tick()
                    if msgs % 200 == 0:
                        heartbeat_file(root, messages=msgs,
                                       subscribed=len(slugs))
                    if status_every > 0 and not side_busy["status"] and \
                            time.monotonic() - last_status >= status_every:
                        # fast intra-cycle L5 allocation (issue #167
                        # comment 5524827113): re-rank the CURRENT
                        # subscribed slugs on CURRENT evidence every
                        # status tick — the expensive per-market shadow
                        # follows activity between discovery cycles. No
                        # resubscribe, no planner-state mutation.
                        if l5_alloc is not None:
                            try:
                                from src import pmus_priority_queue as pq
                                new_alloc = set(pq.allocate_l5(
                                    slugs, mstate, targets=targets))
                                fast_realloc_count += 1
                                discovery_diag = {
                                    **discovery_diag,
                                    "l5_fast_realloc_count":
                                    fast_realloc_count,
                                    "l5_fast_realloc_changed":
                                    new_alloc != l5_alloc}
                                l5_alloc = new_alloc
                            except Exception as exc:
                                discovery_diag = {
                                    **discovery_diag,
                                    "l5_fast_realloc_error":
                                    str(exc)[:80]}
                            side_state.update({"discovery": dict(discovery_diag),
                                               "l5_alloc": l5_alloc})
                        schedule_status(True)
                    health.note_proc(time.monotonic() - proc_t0)
                # venue guidance: release subscriptions explicitly before
                # the socket closes so a key never accumulates orphaned
                # subscriptions across the reconnect cadence
                unsubscribed = 0
                for frame in frames:
                    try:
                        await ws.send(json.dumps(guard_outbound({"unsubscribe": {"request_id": frame["subscribe"]["request_id"]}})))
                        unsubscribed += 1
                    except Exception:                 # noqa: BLE001
                        break
                print("PMUS_WS_CONN " + json.dumps(
                    {"kind": "disconnect", "reason": session_reason,
                     "unsubscribed": unsubscribed,
                     "duration_s": round(time.monotonic() - session_start, 1),
                     "counts": health.counts,
                     "starved_reconnects": starved_reconnects},
                    separators=(",", ":"), default=str), flush=True)
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
