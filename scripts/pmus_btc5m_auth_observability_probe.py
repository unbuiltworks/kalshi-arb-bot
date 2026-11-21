"""Is a BTC5m window OBSERVABLE at all on the authenticated stream?

THE QUESTION THIS EXISTS TO ANSWER, and nothing else.

BTC5m depth capture is blocked on one unmeasured fact. `l5_pmus_auth_book`
recorded, for BTC15m on 2026-09-24:

    a market-data subscription placed BEFORE a window opens stays silent
    ~3 min (0015z +184.5s, 2345z +186.7s, 0115z +184.3s); re-sending the
    subscribe on the SAME connection does not help (0115z: 51 re-subscribes
    from +0.1s, still no book until +184.3s); a FRESH connection subscribing
    to an ALREADY-LIVE market gets the full snapshot at once, 3 for 3.

A BTC15m window survives that: 900s long, so a fresh reconnect a few seconds
in loses <1% of it. A BTC5m window is 300s. If the same ~185s silence applies,
roughly 60% of every window is unobservable and the first three minutes -- where
opening behaviour lives -- are never seen. Then BTC5m depth is not obtainable on
this stream and no amount of recorder work changes that.

That latency has NEVER been measured for the 5m series. This probe measures it
and nothing else: for each window it reports the receipt time of the first
frame, the first one-sided book, the first TWO-SIDED book and the first trade,
each against the window's own start.

WHAT IT IS NOT. Not a recorder, not a service, not a collector. It runs for a
bounded number of windows and exits; it never relaunches itself.

READ-ONLY, STRUCTURALLY. It owns no order path. Every outbound frame goes
through `R.guard_outbound`, which admits subscribe/unsubscribe/heartbeat and
raises on anything else. Frame parsing and book folding are the recorder's own
`_walk_market_events` and `apply_events`, so what this measures is what the
recorder would see.

WHY THE DEPTH ROWS ARE BUILT HERE. The recorder's `emit_new_ladders` lives only
on the deployed branch `claude/l5-shadow-mode-deployment-epkz1i`; that file is
499 lines ahead of this development branch and has no ladder subsystem at all.
So the rungs are extracted and emitted here, in the EXISTING PMUS_LADDER_ROWS
schema, and a test pins the output against a row captured live from the running
recorder at 2026-11-19T04:18:34Z. Quantities are never zero-filled: a rung that
will not parse is DROPPED, because an unknown queue treated as zero puts us at
the front of a queue we cannot see.

IT TOUCHES NO PRODUCTION CONFIGURATION, and reads none: every slug it watches
it computed itself from the clock and the series registry.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

from src import l5_series_registry as REG
from src import pmus_ws_evidence_recorder as R

#: the series under test. BTC5m is the one the policy comparison needs.
DEFAULT_STRATEGY = "BTC5M"
#: windows to measure before exiting. Bounded on purpose: this answers a
#: question, it does not run continuously.
DEFAULT_WINDOWS = 4
#: fresh connection this often while the live window still has no two-sided
#: book. 3.0 is `l5_pmus_auth_book.RESUB_EVERY_S`, measured to bound the
#: observability gap to ~+3s on the 15m series.
DEFAULT_RESUB_S = 3.0
#: ... but never more than this many in one window, so a venue outage cannot
#: turn into a reconnect storm on the shared key (the 15m module's
#: RESUB_FAST_MAX, for the same reason).
RESUB_MAX_PER_WINDOW = 10
#: how long a connect attempt may hang before it is abandoned
CONNECT_TIMEOUT_S = 20.0
#: rungs retained per side, and the emission shape. These are the deployed
#: recorder's own LADDER_RUNGS default / LADDER_LOG_RUNGS / LADDER_LOG_CAP,
#: so the rows this prints are interchangeable with the ones it prints.
LADDER_RUNGS = 16
LADDER_LOG_RUNGS = 4
LADDER_LOG_CAP = 40


def series_for(strategy: str) -> REG.Series:
    """The registry row, or a refusal naming what exists."""
    key = str(strategy or "").strip().upper()
    try:
        return REG.BY_STRATEGY[key]
    except KeyError:
        raise SystemExit(
            "UNKNOWN_SERIES: %r is not in the registry; have %s"
            % (strategy, ",".join(sorted(REG.BY_STRATEGY))))


def window_bounds(now: float, series: REG.Series) -> tuple:
    """(start, end) of the window containing `now`, as epoch seconds."""
    period = float(series.horizon_s)
    start = (now // period) * period
    return start, start + period


def window_slugs(now: float, series: REG.Series, ahead: int = 0) -> List[str]:
    """The window trading NOW plus `ahead` following, as market slugs.

    Deliberately the same arithmetic and the same format string as
    `R.rolling_window_slugs`, which cannot be called here because it reads its
    prefix and period from the PRODUCTION environment variables. A test pins
    the two outputs equal so this copy can never drift.
    """
    period = float(series.horizon_s)
    start = (now // period) * period
    out = []
    for i in range(0, max(1, int(ahead) + 1)):
        t = dt.datetime.fromtimestamp(start + i * period, dt.timezone.utc)
        out.append("%s%s-%02d%02dz" % (series.prefix, t.strftime("%Y-%m-%d"),
                                       t.hour, t.minute))
    return out


class WindowObservation:
    """First-sight latencies for ONE window, measured against its own start.

    Every field is a receipt time of OURS minus the window start, or None
    because it never arrived. Nothing here is interpolated or backfilled: a
    None is the finding, not a gap to fill.
    """

    def __init__(self, slug: str, start: float) -> None:
        self.slug = slug
        self.start = float(start)
        self.first_frame: Optional[float] = None
        self.first_book: Optional[float] = None
        self.first_two_sided: Optional[float] = None
        self.first_trade: Optional[float] = None
        self.book_updates = 0
        self.trades = 0
        self.connects = 0
        self.ladder_rows = 0

    def _rel(self, now: float) -> float:
        return round(float(now) - self.start, 1)

    def note_frame(self, now: float) -> None:
        if self.first_frame is None:
            self.first_frame = self._rel(now)

    def note_book(self, now: float, *, two_sided: bool) -> None:
        self.note_frame(now)
        self.book_updates += 1
        if self.first_book is None:
            self.first_book = self._rel(now)
        if two_sided and self.first_two_sided is None:
            self.first_two_sided = self._rel(now)

    def note_trade(self, now: float) -> None:
        self.note_frame(now)
        self.trades += 1
        if self.first_trade is None:
            self.first_trade = self._rel(now)

    @property
    def observed(self) -> bool:
        """A window is OBSERVED only once both sides were served."""
        return self.first_two_sided is not None

    def row(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "window_start_utc": dt.datetime.fromtimestamp(
                self.start, dt.timezone.utc).isoformat(),
            "first_frame_s": self.first_frame,
            "first_book_s": self.first_book,
            "first_two_sided_book_s": self.first_two_sided,
            "first_trade_s": self.first_trade,
            "book_updates": self.book_updates,
            "trades": self.trades,
            "connects": self.connects,
            "ladder_rows": self.ladder_rows,
            "observed": self.observed,
        }
        #: `horizon_s` and `unobservable_fraction` are added by the caller,
        #: which knows the series. They are deliberately ABSENT here rather
        #: than present-and-None: a None would read as "measured, no value".


def unobservable_fraction(obs: WindowObservation,
                          series: REG.Series) -> Optional[float]:
    """How much of the window had no two-sided book, as a fraction.

    1.0 means the window was never observable. None is impossible here --
    a window with no two-sided book scores 1.0, which is a measurement, while
    None would read as "not measured".
    """
    if obs.first_two_sided is None:
        return 1.0
    period = float(series.horizon_s)
    if period <= 0:
        return None
    return round(min(max(obs.first_two_sided / period, 0.0), 1.0), 4)


def two_sided(state: Mapping[str, Any], slug: str) -> bool:
    """Both sides served for this slug, per the recorder's own folding."""
    s = (state or {}).get(slug) or {}
    return bool(s.get("both_sides_served"))


def emit(kind: str, payload: Mapping[str, Any]) -> None:
    print("PMUS_BTC5M_OBS " + json.dumps(
        {"kind": kind, "t": dt.datetime.now(dt.timezone.utc).isoformat(),
         **payload}, separators=(",", ":"), default=str), flush=True)


def _num(value: Any, *, lo: Any = None, hi: Any = None,
         positive: bool = False) -> Optional[str]:
    """`value` as a decimal string, or None because it is not a number.

    WHY THIS IS STRICTER THAN THE DEPLOYED RECORDER. `R._px` is a passthrough
    stringifier -- it rejects only None -- and `R._levels` requires nothing of
    `qty` beyond being present. So a venue rung of {"px": "abc", "qty": "-1"}
    is carried into the production ladder tape AS IF IT WERE A PRICE AND A
    QUEUE. Anything reading that tape to size a queue would be reading a
    string that is not a number. This probe refuses the rung instead, which
    is the same choice `_levels` already makes for a MISSING quantity and for
    the same reason: an unknown queue must stay unknown.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not d.is_finite():
        return None
    if positive and d <= 0:
        return None
    if lo is not None and d < Decimal(lo):
        return None
    if hi is not None and d > Decimal(hi):
        return None
    return str(value)


def rungs(levels: Any, cap: int) -> List[list]:
    """Up to `cap` [px, qty] pairs, in the order the venue served them.

    The deployed recorder's `_levels` shape, with its refusal made real: a
    rung whose price or quantity will not parse as a number is DROPPED rather
    than carried as a zero OR as an unparseable string.
    """
    if not isinstance(levels, list):
        return []
    out: List[list] = []
    for lv in levels[:max(0, int(cap))]:
        if not isinstance(lv, Mapping):
            continue
        px = _num(R._px(lv.get("px") or lv.get("price")), lo="0", hi="1")
        qty = _num(lv.get("qty"), positive=True)
        if px is None or qty is None:
            continue
        out.append([str(px), str(qty)])
    return out


def ladder_snapshot(ev: Mapping[str, Any], now: float,
                    cap: int = LADDER_RUNGS) -> Dict[str, Any]:
    """One depth snapshot, in the shape the recorder stores per market."""
    return {"t": now, "bids": rungs(ev.get("bids"), cap),
            "asks": rungs(ev.get("asks"), cap), "rungs_cap": cap}


def ladder_rows(snaps: List[Mapping[str, Any]], now: float,
                connected: bool = True) -> List[Dict[str, Any]]:
    """Depth snapshots as PMUS_LADDER_ROWS rows, status named explicitly."""
    out = []
    for h in snaps[-LADDER_LOG_CAP:]:
        bids = list(h.get("bids") or [])[:LADDER_LOG_RUNGS]
        asks = list(h.get("asks") or [])[:LADDER_LOG_RUNGS]
        bid = bids[0][0] if bids else None
        ask = asks[0][0] if asks else None
        out.append({
            "t": h.get("t"), "bid": bid, "ask": ask,
            "bids": bids, "asks": asks,
            "rungs_cap": h.get("rungs_cap"),
            "status": R.book_status({"bid": bid, "ask": ask, "t": h.get("t")},
                                    now, connected),
        })
    return out


def ladder_document(markets: Mapping[str, List[Mapping[str, Any]]],
                    now: float, connected: bool = True) -> Dict[str, Any]:
    """The PMUS_LADDER_ROWS document, byte-compatible with the recorder's."""
    built = {slug: ladder_rows(list(snaps), now, connected)
             for slug, snaps in (markets or {}).items()}
    built = {k: v for k, v in built.items() if v}
    return {
        "t": dt.datetime.now(dt.timezone.utc).isoformat(),
        "kind": "authenticated_ladder_rows",
        "markets": built,
        "rows": sum(len(v) for v in built.values()),
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


async def measure_window(series: REG.Series, obs: WindowObservation,
                         state: dict, *, resub_s: float,
                         clock=time.time) -> None:
    """Hold fresh connections against one window until its end.

    Reconnects every `resub_s` while the window has no two-sided book -- the
    only pattern measured to work on this venue -- then holds the connection
    that got the book for the rest of the window.
    """
    import websockets

    _, end = window_bounds(obs.start, series)
    while clock() < end:
        if obs.connects >= RESUB_MAX_PER_WINDOW and not obs.observed:
            emit("resub_budget_spent",
                 {"slug": obs.slug, "connects": obs.connects})
            return
        try:
            headers = R.auth_headers(os.environ[R.KEY_ENV].strip(),
                                     os.environ[R.SECRET_ENV].strip())
            async with websockets.connect(
                    R.WS_URL, additional_headers=headers,
                    max_size=8 * 1024 * 1024,
                    open_timeout=CONNECT_TIMEOUT_S) as ws:
                obs.connects += 1
                for frame in R.subscribe_frames([obs.slug]):
                    await ws.send(json.dumps(R.guard_outbound(frame)))
                emit("connected", {"slug": obs.slug, "connects": obs.connects,
                                   "t_in_window_s": round(clock() - obs.start, 1)})
                while clock() < end:
                    #: no book yet -> abandon this socket and come back with a
                    #: fresh one, which is the only thing measured to work
                    if not obs.observed and obs.connects < RESUB_MAX_PER_WINDOW:
                        if clock() - obs.start > resub_s * obs.connects:
                            break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    now = clock()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(msg, dict) and "heartbeat" in msg:
                        await ws.send(json.dumps(
                            R.guard_outbound({"heartbeat": {}})))
                        continue
                    R.apply_events(state, msg, now)
                    kind = R.classify_frame(msg)
                    if kind == "book":
                        obs.note_book(now, two_sided=two_sided(state, obs.slug))
                    elif kind == "trade":
                        obs.note_trade(now)
                    #: depth for OUR slug only, parsed from the same events
                    #: the fold above consumed
                    events: list = []
                    R._walk_market_events(msg, events)
                    fresh = [ladder_snapshot(ev, now) for ev in events
                             if ev.get("kind") == "book"
                             and ev.get("slug") == obs.slug]
                    if fresh:
                        doc = ladder_document({obs.slug: fresh}, now)
                        if doc["rows"]:
                            print("PMUS_LADDER_ROWS " + json.dumps(
                                doc, separators=(",", ":"), default=str),
                                flush=True)
                            obs.ladder_rows += int(doc["rows"])
        except Exception as exc:                            # noqa: BLE001
            emit("connect_failed", {"slug": obs.slug,
                                    "err": "%s:%s" % (type(exc).__name__,
                                                      str(exc)[:120])})
            await asyncio.sleep(1.0)


async def run(strategy: str, windows: int, resub_s: float) -> int:
    series = series_for(strategy)
    if not R.has_credentials():
        print(json.dumps({"verdict": "NO_CREDENTIALS",
                          "need_env_names": [R.KEY_ENV, R.SECRET_ENV],
                          "note": "read-only probe; fails closed"}))
        return 2
    emit("start", {"series": series.prefix, "horizon_s": series.horizon_s,
                   "windows": windows, "resub_s": resub_s,
                   "ws_url": R.WS_URL})
    rows: List[Dict[str, Any]] = []
    for _ in range(max(1, int(windows))):
        now = time.time()
        start, end = window_bounds(now, series)
        #: a window already most of the way through measures nothing useful
        if end - now < float(series.horizon_s) * 0.5:
            await asyncio.sleep(end - now + 0.2)
            now = time.time()
            start, end = window_bounds(now, series)
        slug = window_slugs(start, series)[0]
        state: dict = {}
        obs = WindowObservation(slug, start)
        await measure_window(series, obs, state, resub_s=resub_s)
        row = obs.row()
        row["horizon_s"] = series.horizon_s
        row["unobservable_fraction"] = unobservable_fraction(obs, series)
        rows.append(row)
        emit("window", row)
    seen = [r for r in rows if r["observed"]]
    emit("verdict", {
        "windows": len(rows), "observed": len(seen),
        "first_two_sided_book_s": [r["first_two_sided_book_s"] for r in rows],
        "unobservable_fraction": [r["unobservable_fraction"] for r in rows],
        "label": ("LATENCY ONLY. This says when a book became visible, not "
                  "that any order would have filled. DEPTH IS NOT A FILL."),
    })
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY,
                    help="registry strategy label, e.g. BTC5M or BTC15M")
    ap.add_argument("--windows", type=int, default=DEFAULT_WINDOWS)
    ap.add_argument("--resub-s", type=float, default=DEFAULT_RESUB_S)
    args = ap.parse_args(argv)
    return asyncio.run(run(args.strategy, args.windows, args.resub_s))


if __name__ == "__main__":
    raise SystemExit(main())
