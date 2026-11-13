"""THEORETICAL liquidity-incentive recorder for the PMUS sports collector.

READ ONLY. Every venue read goes through the base collector's GET-only,
keyless ``http_get_json`` (public https://gateway.polymarket.us). Nothing in
this module can place, amend or withdraw an order, holds a credential, or
talks to any other host. Every number it writes is THEORETICAL: a quote we
never placed, scored against a book we only observed. Nothing here is
submitted, filled, credited, settled-for-us or realized.

The question it answers: would a small resting maker quote at the touch of a
liquidity-rewarded sports market earn more in program rewards than it loses
to adverse fills? It records, per rewarded market being polled:

1. ``program_map``  -- the market's liquidity-program periods (programId,
   rewardPool, period, discountFactor, targetSize, maxSpread, status) and the
   event grouping used for the PER_EVENT reading. Fetched with repeated
   ``symbols=`` query params in batches, because ``GET /v1/incentives``
   pagination LOOPS (its nextPageToken serves page 1 again; see
   analysis/l5/pivot_pmus/RESULT.md section B0).
2. ``incentive_share`` -- the hypothetical score share of OUR quote of Q
   contracts (Q in QUOTE_SIZES) resting at the best bid and at the best ask,
   honouring the Target Size walk and maxSpread, and the expected reward
   under THREE pool readings (the basis is unknown; none is picked):
       PER_MARKET  rewardPool paid to each market          (upper bound)
       PER_EVENT   rewardPool / markets of the event carrying the program
       PER_PROGRAM rewardPool / markets carrying the program that we mapped
                   (we only map live-slate markets, so M_program is a LOWER
                   bound and PER_PROGRAM an UPPER bound within its reading)
3. ``touch_fill`` / ``markout`` -- hypothetical fills of one touch quote per
   side and the mid move after each fill (rule in :func:`detect_fill`).
4. ``settlement`` -- the venue's settlementPx once a tracked market expires,
   so lone-leg P&L can be valued offline.

Scoring (docs.polymarket.us/incentives/liquidity.md; copy in
data/pmus/sports_incentives_2026-09-10/doc_incentives_liquidity.md):
per snapshot, per side, ``score = DF ** ticks_from_best * size`` for orders
inside the range where cumulative RAW size walked from the best price first
reaches Target Size; each side is normalised to 1.0 when Target Size is met.
Our reward share on a side is our score over the total in-range score; each
side carries half of the period pool per second (sides are normalised
independently). Payouts under $1 per period are not paid (flagged, not
applied: the period total is not known until it ends).

ASSUMPTIONS (all recorded in rows, none silent):
* The walk is at PRICE-LEVEL granularity (the public book is aggregated by
  level): whole levels are included until cumulative size reaches Target
  Size, and the level that reaches it is included in full. Our order joins
  the back of the best level, so it is always inside the range.
* maxSpread: a side's "size-adjusted price" is the size-weighted average
  price of its in-range levels (ours included); mid is (best bid + best ask)
  / 2 of the observed book. If the program has maxSpread, BOTH sides must
  meet Target Size AND have |size-adjusted price - mid| <= maxSpread, or
  neither side scores.
* Period pacing: ``daily``/``daily_event`` pools are spread over 24h;
  ``day_of`` over 6h; ``early`` from its start to event start - 6h; ``live``
  over PMUS_INCENTIVE_LIVE_PERIOD_HOURS (default 3h; the true length is not
  known until settlement). A live period is treated as paying while the
  market is on the venue's live slate.
* Tick: the market's ``orderPriceMinTickSize`` if the target metadata
  carries it, else the coarsest of 0.01/0.005/0.001 that every observed
  level price sits on.

Failure model: every public entry point catches its own exceptions, counts
them and returns. The collector additionally wraps each call, so a defect
here can cost recorder rows, never collector behaviour.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

LABEL = "THEORETICAL_NOT_REALIZED"

ENABLE_ENV = "PMUS_INCENTIVE_RECORDER"                 # "0" disables
MAP_REFRESH_ENV = "PMUS_INCENTIVE_MAP_REFRESH_SECONDS"
MAP_REFRESH_DEFAULT_S = 600.0
STATUS_ENV = "PMUS_INCENTIVE_STATUS_SECONDS"
STATUS_DEFAULT_S = 300.0
LIVE_HOURS_ENV = "PMUS_INCENTIVE_LIVE_PERIOD_HOURS"
LIVE_HOURS_DEFAULT = 3.0
SHARE_ROW_ENV = "PMUS_INCENTIVE_SHARE_ROW_SECONDS"
SHARE_ROW_DEFAULT_S = 60.0
DAILY_ROW_CAP_ENV = "PMUS_INCENTIVE_DAILY_ROW_CAP"
DAILY_ROW_CAP_DEFAULT = 200_000

QUOTE_SIZES = (10, 50)
READINGS = ("PER_MARKET", "PER_EVENT", "PER_PROGRAM")
MARKOUT_HORIZONS_S = (30, 120, 600)
MARKOUT_SLACK_S = 60.0          # a horizon's mid must come from <= h + slack
GAP_CAP_S = 60.0                # time-integration step cap between polls
SYMBOLS_BATCH = 60
SYMBOLS_CAP_GUARD = 100         # a batch returning >= this is split
MAP_MAX_SLUGS = 600
MAP_BATCH_GAP_S = 0.2
SETTLE_STALE_S = 900.0          # unseen this long -> check settlement
SETTLE_RECHECK_S = 600.0
SETTLE_GIVE_UP_S = 72 * 3600.0
SETTLE_GETS_PER_CYCLE = 3
SETTLE_QUEUE_MAX = 500
STATUS_TOP_N = 20
EPS = 1e-9

TERMINAL_STATE_TOKENS = ("EXPIRED", "RESOLVED", "SETTLED")


def enabled() -> bool:
    return os.getenv(ENABLE_ENV, "1").strip().lower() not in (
        "0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _num(v: Any) -> Optional[float]:
    if isinstance(v, Mapping):
        v = v.get("value") if v.get("value") is not None else v.get("px")
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ts(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- book ----

def parse_levels(levels: Any) -> list:
    """[(px, qty)] from the public book's ``bids``/``offers`` arrays.

    Each level is ``{"px": {"value": "0.38"} | "0.38" | 0.38, "qty": ...}``.
    Unparseable or non-positive levels are dropped, never guessed.
    """
    out = []
    for lv in levels or ():
        if not isinstance(lv, Mapping):
            continue
        px = _num(lv.get("px") if "px" in lv else lv.get("price"))
        qty = _num(lv.get("qty") if "qty" in lv else lv.get("size"))
        if px is None or qty is None or qty <= 0 or not (0.0 < px < 1.0):
            continue
        out.append((px, qty))
    return out


def book_sides(book_md: Mapping[str, Any]) -> tuple:
    """(bids, asks) sorted best-first. ``offers`` is authoritative when
    present (same rule as the base collector's ``bbo_book_row``)."""
    bids = parse_levels(book_md.get("bids"))
    if "offers" in book_md:
        asks = parse_levels(book_md.get("offers"))
    else:
        asks = parse_levels(book_md.get("asks"))
    bids.sort(key=lambda x: -x[0])
    asks.sort(key=lambda x: x[0])
    return bids, asks


TICK_CANDIDATES = (0.01, 0.005, 0.001)


def infer_tick(meta: Optional[Mapping[str, Any]], *sides: list) -> tuple:
    """(tick, source). Venue field first, else the coarsest grid that every
    observed level price sits on, else 0.01 (the sports default)."""
    t = _num((meta or {}).get("orderPriceMinTickSize"))
    if t is not None and 0 < t < 1:
        return t, "orderPriceMinTickSize"
    prices = [p for side in sides for p, _ in side or ()]
    if prices:
        for cand in TICK_CANDIDATES:
            if all(abs(p / cand - round(p / cand)) < 1e-6 for p in prices):
                return cand, "inferred_from_levels"
        return TICK_CANDIDATES[-1], "inferred_finest"
    return 0.01, "default_0.01"


# ------------------------------------------------------------- scoring ----

def walk_side(levels: list, tick: float, df: float, target: float,
              our_qty: float = 0.0) -> dict:
    """Target-Size walk of ONE side with our ``our_qty`` joined at the back
    of the best level. ``levels`` best-first [(px, qty)] WITHOUT our order.

    Returns the in-range levels, others' discounted score, our score, the
    side's size-adjusted price (size-weighted, ours included), whether the
    side meets Target Size, and our share (0 when it does not)."""
    if not levels:
        if our_qty <= 0:
            return {"qualifies": False, "reason": "empty_side", "share": 0.0,
                    "others_score": 0.0, "our_score": 0.0,
                    "in_range_size": 0.0, "levels_in_range": 0,
                    "size_adj_px": None, "best": None}
        return {"qualifies": our_qty >= target, "reason": "only_us",
                "share": 1.0 if our_qty >= target else 0.0,
                "others_score": 0.0, "our_score": float(our_qty),
                "in_range_size": float(our_qty), "levels_in_range": 0,
                "size_adj_px": None, "best": None}
    best = levels[0][0]
    cum = float(our_qty)
    others = 0.0
    notional = 0.0
    used = 0
    for i, (px, qty) in enumerate(levels):
        # level 0 (the best) is always in range: our order sits there
        if i > 0 and cum >= target:
            break
        k = int(round(abs(px - best) / tick)) if tick > 0 else 0
        others += (df ** k) * qty
        notional += px * qty
        cum += qty
        used += 1
    notional += best * our_qty
    qualifies = cum >= target - EPS
    our_score = float(our_qty)
    total = our_score + others
    return {"qualifies": qualifies,
            "reason": None if qualifies else "target_not_met",
            "share": (our_score / total) if (qualifies and total > 0) else 0.0,
            "others_score": others, "our_score": our_score,
            "in_range_size": cum, "levels_in_range": used,
            "size_adj_px": (notional / cum) if cum > 0 else None,
            "best": best}


def score_levels(levels: list, tick: float, df: float) -> list:
    """Per-level discounted score (docs example helper): [(px, qty, k, s)]."""
    if not levels:
        return []
    best = levels[0][0]
    out = []
    for px, qty in levels:
        k = int(round(abs(px - best) / tick))
        out.append((px, qty, k, (df ** k) * qty))
    return out


def quote_shares(bids: list, asks: list, tick: float, df: float,
                 target: float, q: float,
                 max_spread: Optional[float] = None) -> dict:
    """Our share on each side for Q resting at the best bid and best ask.

    Without maxSpread the sides are independent. With maxSpread, both sides
    must meet Target Size with their size-adjusted price within maxSpread of
    the mid, or neither side is paid."""
    b = walk_side(bids, tick, df, target, q)
    a = walk_side(asks, tick, df, target, q)
    mid = ((bids[0][0] + asks[0][0]) / 2.0) if (bids and asks) else None
    ms_ok = None
    if max_spread is not None:
        ms_ok = bool(
            mid is not None and b["qualifies"] and a["qualifies"]
            and b["size_adj_px"] is not None and a["size_adj_px"] is not None
            and abs(b["size_adj_px"] - mid) <= max_spread + EPS
            and abs(a["size_adj_px"] - mid) <= max_spread + EPS)
        if not ms_ok:
            b = {**b, "share": 0.0, "reason": b.get("reason") or "max_spread"}
            a = {**a, "share": 0.0, "reason": a.get("reason") or "max_spread"}
    return {"bid": b, "ask": a, "mid": mid, "max_spread_ok": ms_ok}


# ---------------------------------------------------------- programs -----

def _max_spread(tp: Mapping[str, Any]) -> Optional[float]:
    v = _num(tp.get("maxSpread"))
    return v if (v is not None and v > 0) else None


def period_pacing(tp: Mapping[str, Any], event_start: Optional[datetime],
                  now: float, event_live: bool,
                  live_hours: float) -> tuple:
    """(paying_now, period_seconds, basis) for one liquidity time period."""
    per = str(tp.get("period") or "")
    st = _ts(tp.get("start"))
    nowd = datetime.fromtimestamp(now, timezone.utc)
    if str(tp.get("status") or "active") != "active":
        return False, None, "status_not_active"
    if st and st > nowd:
        return False, None, "not_started"
    if per in ("daily", "daily_event"):
        return True, 86400.0, "daily_24h"
    if per == "live":
        live = event_live or (event_start is not None and nowd >= event_start)
        return live, live_hours * 3600.0, "live_assumed_%gh" % live_hours
    if per == "day_of":
        if event_start is None:
            return False, None, "day_of_no_event_start"
        ok = event_start - timedelta(hours=6) <= nowd < event_start
        return ok, 6 * 3600.0, "day_of_6h"
    if per == "early":
        if event_start is None or st is None:
            return False, None, "early_no_bounds"
        end = event_start - timedelta(hours=6)
        return (nowd < end), max(3600.0, (end - st).total_seconds()), \
            "early_start_to_event_minus_6h"
    return False, None, "unknown_period:%s" % per


def fetch_programs(get_json: Callable, slugs: Iterable[str],
                   batch: int = SYMBOLS_BATCH,
                   gap_s: float = MAP_BATCH_GAP_S,
                   sleep: Callable = time.sleep) -> tuple:
    """{slug: program_row} via repeated ``symbols=`` params. Returns
    (found, diag). Batches that hit the 100-row cap are split."""
    found: dict = {}
    diag = {"requests": 0, "errors": [], "split": 0}
    slugs = sorted({str(s) for s in slugs if s})

    def query(chunk: list) -> None:
        if diag["requests"]:
            sleep(gap_s)
        diag["requests"] += 1
        d = get_json("/v1/incentives?" + "&".join(
            "symbols=" + urllib.parse.quote(s, safe="") for s in chunk))
        progs = (d or {}).get("programs") if isinstance(d, Mapping) else None
        progs = progs if isinstance(progs, list) else []
        if len(progs) >= SYMBOLS_CAP_GUARD and len(chunk) > 1:
            diag["split"] += 1
            h = len(chunk) // 2
            query(chunk[:h])
            query(chunk[h:])
            return
        for p in progs:
            if isinstance(p, Mapping) and p.get("marketSlug"):
                found[str(p["marketSlug"])] = dict(p)

    for i in range(0, len(slugs), batch):
        try:
            query(slugs[i:i + batch])
        except Exception as exc:                              # noqa: BLE001
            diag["errors"].append("%s:%s" % (type(exc).__name__,
                                              str(exc)[:80]))
    return found, diag


def build_program_map(found: Mapping[str, Mapping[str, Any]],
                      event_of: Mapping[str, str]) -> dict:
    """{slug: {"event": ..., "instrumentState": ..., "eventStartTime": ...,
    "periods": [{programId, pool, period, df, target, maxSpread, status,
    start, M_event, M_program}]}} for liquidity periods only."""
    by_prog: dict = defaultdict(set)
    by_prog_ev: dict = defaultdict(set)
    for slug, row in found.items():
        for tp in row.get("timePeriods") or ():
            if not isinstance(tp, Mapping):
                continue
            if tp.get("programType") not in (None, "liquidityProgram"):
                continue
            key = (tp.get("programId"), tp.get("start"))
            by_prog[key].add(slug)
            by_prog_ev[key + (event_of.get(slug, slug),)].add(slug)
    out = {}
    for slug, row in found.items():
        periods = []
        ev = event_of.get(slug, slug)
        for tp in row.get("timePeriods") or ():
            if not isinstance(tp, Mapping):
                continue
            if tp.get("programType") not in (None, "liquidityProgram"):
                continue
            pool = _num(tp.get("rewardPool"))
            df = _num(tp.get("discountFactor"))
            target = _num(tp.get("targetSize"))
            if pool is None or df is None or target is None:
                continue
            key = (tp.get("programId"), tp.get("start"))
            periods.append({
                "programId": tp.get("programId"), "pool": pool,
                "period": tp.get("period"), "df": df, "target": target,
                "maxSpread": _max_spread(tp),
                "maxSpread_raw": tp.get("maxSpread"),
                "status": tp.get("status"), "start": tp.get("start"),
                "M_event": max(1, len(by_prog_ev[key + (ev,)])),
                "M_program": max(1, len(by_prog[key])),
            })
        if periods:
            out[slug] = {"event": ev,
                         "instrumentState": row.get("instrumentState"),
                         "eventStartTime": row.get("eventStartTime") or None,
                         "periods": periods}
    return out


# ------------------------------------------------------------- fills -----

def detect_fill(side: str, px: float, best_bid: Optional[float],
                best_ask: Optional[float], last_trade_px: Optional[float],
                new_trade: bool) -> Optional[str]:
    """Hypothetical fill rule for one touch quote resting at ``px``.

    Evaluated only on a snapshot strictly LATER than the one the quote was
    placed on. A BID at ``px`` is FILLED when either
      (a) ``trade_through``: best ask < px (the offer side has moved strictly
          through our price -- a resting bid at px could not survive that), or
      (b) ``level_vanished_with_trade``: the best bid is now below px or the
          bid side is empty, AND there is evidence of a new trade since the
          quote was placed (cumulative sharesTraded rose, or lastTradePx
          changed when sharesTraded is unavailable), AND lastTradePx <= px.
    An ASK at ``px`` mirrors it: best bid > px, or (best ask above px / ask
    side empty, a new trade, and lastTradePx >= px).
    A locked touch (best ask == our bid) is NOT a fill: our order joined the
    back of the queue, so the queue ahead absorbs it first. The whole Q is
    assumed filled (no partials). Returns the rule name or None."""
    if side == "bid":
        if best_ask is not None and best_ask < px - EPS:
            return "trade_through"
        if (best_bid is None or best_bid < px - EPS) and new_trade \
                and last_trade_px is not None and last_trade_px <= px + EPS:
            return "level_vanished_with_trade"
        return None
    if best_bid is not None and best_bid > px + EPS:
        return "trade_through"
    if (best_ask is None or best_ask > px + EPS) and new_trade \
            and last_trade_px is not None and last_trade_px >= px - EPS:
        return "level_vanished_with_trade"
    return None


def markout(side: str, fill_px: float, mid_later: float) -> float:
    """Per-contract markout: P&L of the filled quote marked at a later mid.
    Negative = the mid moved against us. bid (bought YES): mid - px;
    ask (sold YES): px - mid."""
    return (mid_later - fill_px) if side == "bid" else (fill_px - mid_later)


# ------------------------------------------------------------ recorder ---

def _new_acc() -> dict:
    return {"snaps": 0, "obs_s": 0.0, "qual_s": {q: 0.0 for q in QUOTE_SIZES},
            "qual_snaps": {q: 0 for q in QUOTE_SIZES},
            "both_s": {q: 0.0 for q in QUOTE_SIZES},
            "usd": {q: {r: 0.0 for r in READINGS} for q in QUOTE_SIZES},
            "fills": {"bid": 0, "ask": 0}, "requotes": {"bid": 0, "ask": 0},
            "mo": {h: [] for h in MARKOUT_HORIZONS_S},
            "event": None, "programs": []}


class IncentiveRecorder:
    """Stateful, fail-soft recorder. Public methods never raise."""

    def __init__(self, root: Path, get_json: Optional[Callable] = None,
                 clock: Callable = time.time, sleep: Callable = time.sleep,
                 printer: Callable = print):
        if get_json is None:
            from src import pmus_sports_collector as col
            get_json = col.http_get_json
        self.root = Path(root)
        self.dir = self.root / "incentive"
        self.get_json = get_json
        self.clock = clock
        self.sleep = sleep
        self.printer = printer
        self.errors = 0
        self.last_error: Optional[str] = None
        self.map: dict = {}
        self.map_t = 0.0
        self.map_diag: dict = {}
        self.last_status = clock()
        self.mk: dict = {}                    # per-market live state
        self.acc: dict = {}                   # (day, slug) -> accumulator
        self.settle_pending: dict = {}        # slug -> {first, last_check}
        self.settled: set = set()
        self.rows_today = 0
        self.rows_day = None
        self.rows_dropped = 0
        self.map_refresh_s = _env_float(MAP_REFRESH_ENV, MAP_REFRESH_DEFAULT_S)
        self.status_s = _env_float(STATUS_ENV, STATUS_DEFAULT_S)
        self.live_hours = _env_float(LIVE_HOURS_ENV, LIVE_HOURS_DEFAULT)
        self.share_row_s = _env_float(SHARE_ROW_ENV, SHARE_ROW_DEFAULT_S)
        self.row_cap = int(_env_float(DAILY_ROW_CAP_ENV,
                                      DAILY_ROW_CAP_DEFAULT))
        self._load_state()

    # ------------------------------------------------------------ io --
    def _err(self, where: str, exc: BaseException) -> None:
        self.errors += 1
        self.last_error = "%s:%s:%s" % (where, type(exc).__name__,
                                        str(exc)[:100])

    def path_for(self, t: float) -> Path:
        return self.dir / ("pmus_incentive_%s.jsonl"
                           % datetime.fromtimestamp(t, timezone.utc)
                           .strftime("%Y%m%d"))

    def write(self, rows: list, t: Optional[float] = None) -> None:
        if not rows:
            return
        t = self.clock() if t is None else t
        day = _day(t)
        if day != self.rows_day:
            self.rows_day, self.rows_today = day, 0
        room = self.row_cap - self.rows_today
        if room <= 0:
            self.rows_dropped += len(rows)
            return
        if len(rows) > room:
            self.rows_dropped += len(rows) - room
            rows = rows[:room]
        path = self.path_for(t)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as sink:
            for r in rows:
                sink.write(json.dumps({"label": LABEL, **r},
                                      separators=(",", ":"),
                                      default=str) + "\n")
        self.rows_today += len(rows)

    def _state_path(self) -> Path:
        return self.dir / "recorder_state.json"

    def _load_state(self) -> None:
        try:
            p = self._state_path()
            if p.exists():
                d = json.loads(p.read_text())
                self.settle_pending = dict(d.get("settle_pending") or {})
                self.settled = set(d.get("settled") or [])
        except Exception as exc:                              # noqa: BLE001
            self._err("load_state", exc)

    def _save_state(self) -> None:
        try:
            p = self._state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {"settle_pending": self.settle_pending,
                 "settled": sorted(self.settled)[-2000:]}, default=str))
            os.replace(tmp, p)
        except Exception as exc:                              # noqa: BLE001
            self._err("save_state", exc)

    # ---------------------------------------------------- program map --
    def refresh_map(self, targets: Iterable[Mapping[str, Any]],
                    universe: Iterable[Mapping[str, Any]] = (),
                    now: Optional[float] = None) -> None:
        """Polled markets first, then same-event siblings of the REWARDED
        polled markets (needed for M_event), capped at MAP_MAX_SLUGS."""
        now = self.clock() if now is None else now
        try:
            targets = [t for t in targets or () if t.get("slug")]
            event_of = {}
            for t in list(universe or ()) + targets:
                if t.get("slug"):
                    event_of[str(t["slug"])] = str(
                        t.get("event_slug") or t.get("slug"))
            polled = [str(t["slug"]) for t in targets][:MAP_MAX_SLUGS]
            found, d1 = fetch_programs(self.get_json, polled,
                                       sleep=self.sleep)
            rewarded_events = {event_of.get(s, s) for s in found}
            siblings = sorted({s for s, ev in event_of.items()
                               if ev in rewarded_events and s not in polled})
            siblings = siblings[:max(0, MAP_MAX_SLUGS - len(polled))]
            found2, d2 = fetch_programs(self.get_json, siblings,
                                        sleep=self.sleep) if siblings \
                else ({}, {"requests": 0, "errors": [], "split": 0})
            found.update(found2)
            full = build_program_map(found, event_of)
            polled_set = set(polled)
            self.map = {s: v for s, v in full.items() if s in polled_set}
            self.map_t = now
            self.map_diag = {
                "requests": d1["requests"] + d2["requests"],
                "errors": (d1["errors"] + d2["errors"])[:5],
                "n_errors": len(d1["errors"]) + len(d2["errors"]),
                "polled": len(polled), "siblings_queried": len(siblings),
                "rewarded_polled": len(self.map)}
            self.write([{
                "kind": "program_map", "t": _iso(now), **self.map_diag,
                "pool_readings": list(READINGS),
                "markets": [{"slug": s, **v} for s, v in
                            sorted(self.map.items())]}], now)
        except Exception as exc:                              # noqa: BLE001
            self._err("refresh_map", exc)
            self.map_t = now      # back off a full interval after a failure

    # -------------------------------------------------------- observe --
    def observe(self, slug: str, target: Mapping[str, Any],
                bbo_md: Mapping[str, Any], book_md: Mapping[str, Any],
                now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        try:
            self._observe(str(slug), target or {}, bbo_md or {},
                          book_md or {}, now)
        except Exception as exc:                              # noqa: BLE001
            self._err("observe", exc)

    def _observe(self, slug: str, target: Mapping[str, Any],
                 bbo: Mapping[str, Any], book: Mapping[str, Any],
                 now: float) -> None:
        state = str(bbo.get("state") or "")
        if any(tok in state for tok in TERMINAL_STATE_TOKENS) and \
                (slug in self.map or slug in self.mk):
            self._maybe_settle_from_bbo(slug, bbo, now)
        prog = self.map.get(slug)
        if not prog:
            return
        self.settle_pending.pop(slug, None)
        bids, asks = book_sides(book)
        bb = bids[0][0] if bids else None
        ba = asks[0][0] if asks else None
        mid = ((bb + ba) / 2.0) if (bb is not None and ba is not None) \
            else None
        tick, tick_src = infer_tick(target.get("market_meta"), bids, asks)
        ltp = _num(bbo.get("lastTradePx"))
        cum = _num(bbo.get("sharesTraded"))

        m = self.mk.setdefault(slug, {"orders": {"bid": None, "ask": None},
                                      "pending": [], "last_t": None,
                                      "last_row_t": 0.0})
        m["last_seen"] = now
        rows: list = []
        acc = self._acc(now, slug)
        acc["event"] = prog.get("event")
        acc["programs"] = sorted({p["programId"] for p in prog["periods"]})

        # 1. markouts maturing on this snapshot
        rows += self._resolve_markouts(slug, m, mid, now, acc)

        # 2. fills of the resting hypothetical quotes, then re-join
        for side, best in (("bid", bb), ("ask", ba)):
            o = m["orders"][side]
            if o is not None and now > o["t"]:
                new_trade = False
                if cum is not None and o.get("cum") is not None:
                    new_trade = cum > o["cum"] + EPS
                elif ltp is not None and o.get("ltp") is not None:
                    new_trade = abs(ltp - o["ltp"]) > EPS
                rule = detect_fill(side, o["px"], bb, ba, ltp, new_trade)
                if rule:
                    acc["fills"][side] += 1
                    fill = {"kind": "touch_fill", "t": _iso(now),
                            "slug": slug, "event": prog.get("event"),
                            "side": side, "px": o["px"], "rule": rule,
                            "placed_t": _iso(o["t"]),
                            "rest_s": round(now - o["t"], 3),
                            "best_bid": bb, "best_ask": ba, "mid": mid,
                            "last_trade_px": ltp, "shares_traded_cum": cum,
                            "quote_sizes": list(QUOTE_SIZES)}
                    rows.append(fill)
                    m["pending"].append({"side": side, "px": o["px"],
                                         "t": now, "mid0": mid,
                                         "marks": {}})
                    o = m["orders"][side] = None
                elif best is not None and abs(best - o["px"]) > EPS:
                    acc["requotes"][side] += 1
                    o = m["orders"][side] = None
            if o is None and best is not None:
                m["orders"][side] = {"px": best, "t": now,
                                     "cum": cum, "ltp": ltp}
            elif best is None:
                m["orders"][side] = None

        # 3. incentive share for each paying period, both Q sizes
        dt = 0.0
        if m["last_t"] is not None:
            dt = max(0.0, min(GAP_CAP_S, now - m["last_t"]))
        m["last_t"] = now
        event_live = bool(target.get("why") == "v1_live_event_market"
                          or (target.get("event_state") or {}).get("live"))
        est = _ts(prog.get("eventStartTime"))
        per_rows = []
        rate = {q: {r: 0.0 for r in READINGS} for q in QUOTE_SIZES}
        any_q = {q: False for q in QUOTE_SIZES}
        both_q = {q: False for q in QUOTE_SIZES}
        for p in prog["periods"]:
            paying, secs, basis = period_pacing(
                p, est, now, event_live, self.live_hours)
            pr = {"programId": p["programId"], "period": p["period"],
                  "paying": paying, "pacing": basis, "pool": p["pool"],
                  "M_event": p["M_event"], "M_program": p["M_program"],
                  "df": p["df"], "target": p["target"],
                  "maxSpread": p["maxSpread"]}
            if paying and secs:
                pr["q"] = {}
                for q in QUOTE_SIZES:
                    s = quote_shares(bids, asks, tick, p["df"], p["target"],
                                     q, p["maxSpread"])
                    sb, sa = s["bid"]["share"], s["ask"]["share"]
                    pools = {"PER_MARKET": p["pool"],
                             "PER_EVENT": p["pool"] / p["M_event"],
                             "PER_PROGRAM": p["pool"] / p["M_program"]}
                    for r in READINGS:
                        rate[q][r] += pools[r] / secs * 0.5 * (sb + sa)
                    any_q[q] = any_q[q] or (sb > 0 or sa > 0)
                    both_q[q] = both_q[q] or (sb > 0 and sa > 0)
                    pr["q"][q] = {
                        "bid_share": round(sb, 6), "ask_share": round(sa, 6),
                        "bid_target_met": s["bid"]["qualifies"],
                        "ask_target_met": s["ask"]["qualifies"],
                        "bid_others_score": round(s["bid"]["others_score"], 3),
                        "ask_others_score": round(s["ask"]["others_score"], 3),
                        "max_spread_ok": s["max_spread_ok"],
                        "usd_per_day": {r: round(pools[r] / secs * 0.5 *
                                                 (sb + sa) * 86400.0, 4)
                                        for r in READINGS}}
            per_rows.append(pr)

        acc["snaps"] += 1
        acc["obs_s"] += dt
        for q in QUOTE_SIZES:
            if any_q[q]:
                acc["qual_s"][q] += dt
                acc["qual_snaps"][q] += 1
            if both_q[q]:
                acc["both_s"][q] += dt
            for r in READINGS:
                acc["usd"][q][r] += rate[q][r] * dt

        if now - m["last_row_t"] >= self.share_row_s:
            m["last_row_t"] = now
            rows.append({
                "kind": "incentive_share", "t": _iso(now), "slug": slug,
                "event": prog.get("event"), "best_bid": bb, "best_ask": ba,
                "mid": mid, "tick": tick, "tick_source": tick_src,
                "bid_touch_qty": bids[0][1] if bids else None,
                "ask_touch_qty": asks[0][1] if asks else None,
                "periods": per_rows,
                "cum_day": self._acc_summary(acc),
                "assumptions": {"walk": "price_level_granularity",
                                "live_period_hours": self.live_hours,
                                "under_1usd_payout_floor": "not_applied"}})
        self.write(rows, now)

    def _acc(self, now: float, slug: str) -> dict:
        key = (_day(now), slug)
        a = self.acc.get(key)
        if a is None:
            a = self.acc[key] = _new_acc()
        return a

    def _resolve_markouts(self, slug: str, m: dict, mid: Optional[float],
                          now: float, acc: dict) -> list:
        rows, keep = [], []
        for f in m["pending"]:
            for h in MARKOUT_HORIZONS_S:
                if h in f["marks"]:
                    continue
                age = now - f["t"]
                if age >= h:
                    if age <= h + MARKOUT_SLACK_S and mid is not None:
                        f["marks"][h] = (mid, round(age, 3))
                    else:
                        f["marks"][h] = (None, round(age, 3))
            if len(f["marks"]) == len(MARKOUT_HORIZONS_S):
                rows.append(self._markout_row(slug, f, acc, now))
            else:
                keep.append(f)
        m["pending"] = keep
        return rows

    def _markout_row(self, slug: str, f: dict, acc: dict,
                     now: float) -> dict:
        out = {"kind": "markout", "t": _iso(now), "slug": slug,
               "side": f["side"], "fill_px": f["px"], "fill_t": _iso(f["t"]),
               "mid_at_fill": f["mid0"],
               "sign": "markout<0 means mid moved against the quote"}
        for h in MARKOUT_HORIZONS_S:
            mid_h, lag = f["marks"].get(h, (None, None))
            mo = markout(f["side"], f["px"], mid_h) if mid_h is not None \
                else None
            out["mid_%ds" % h] = mid_h
            out["lag_%ds" % h] = lag
            out["markout_%ds" % h] = round(mo, 6) if mo is not None else None
            if mo is not None:
                acc["mo"][h].append(mo)
        return out

    def _flush_stale_markouts(self, now: float) -> None:
        horizon = max(MARKOUT_HORIZONS_S) + MARKOUT_SLACK_S
        for slug, m in self.mk.items():
            if not m["pending"]:
                continue
            rows, keep = [], []
            for f in m["pending"]:
                if now - f["t"] > horizon:
                    for h in MARKOUT_HORIZONS_S:
                        f["marks"].setdefault(h, (None, None))
                    rows.append(self._markout_row(slug, f,
                                                  self._acc(now, slug), now))
                else:
                    keep.append(f)
            m["pending"] = keep
            self.write(rows, now)

    # ---------------------------------------------------- settlement ---
    def _maybe_settle_from_bbo(self, slug: str, bbo: Mapping[str, Any],
                               now: float, source: str = "poll") -> bool:
        if slug in self.settled:
            return True
        state = str(bbo.get("state") or "")
        px = _num(bbo.get("settlementPx"))
        if not any(tok in state for tok in TERMINAL_STATE_TOKENS) \
                or px is None or not (0.0 <= px <= 1.0):
            return False
        self.settled.add(slug)
        self.settle_pending.pop(slug, None)
        self.write([{"kind": "settlement", "t": _iso(now), "slug": slug,
                     "state": state, "settlement_px": px,
                     "binary": px in (0.0, 1.0), "source": source,
                     "route": "/v1/markets/{slug}/bbo"}], now)
        self._save_state()
        return True

    def _queue_stale(self, now: float) -> None:
        for slug, m in self.mk.items():
            if slug in self.settled or slug in self.settle_pending:
                continue
            if now - m.get("last_seen", now) >= SETTLE_STALE_S:
                self.settle_pending[slug] = {"first": now, "last_check": 0.0}
        if len(self.settle_pending) > SETTLE_QUEUE_MAX:
            for s in sorted(self.settle_pending,
                            key=lambda k: self.settle_pending[k]["first"]
                            )[:len(self.settle_pending) - SETTLE_QUEUE_MAX]:
                self.settle_pending.pop(s, None)

    def check_settlements(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        try:
            self._queue_stale(now)
            done = 0
            changed = False
            for slug in sorted(self.settle_pending,
                               key=lambda k: self.settle_pending[k]
                               ["last_check"]):
                if done >= SETTLE_GETS_PER_CYCLE:
                    break
                ent = self.settle_pending[slug]
                if now - ent["first"] > SETTLE_GIVE_UP_S:
                    self.settle_pending.pop(slug, None)
                    self.write([{"kind": "settlement", "t": _iso(now),
                                 "slug": slug, "state": None,
                                 "settlement_px": None,
                                 "status": "gave_up_after_72h"}], now)
                    changed = True
                    continue
                if now - ent["last_check"] < SETTLE_RECHECK_S:
                    continue
                ent["last_check"] = now
                done += 1
                changed = True
                try:
                    r = self.get_json("/v1/markets/%s/bbo"
                                      % urllib.parse.quote(slug, safe=""))
                    md = (r or {}).get("marketData") or r or {}
                    self._maybe_settle_from_bbo(slug, md, now, "settle_check")
                except Exception as exc:                      # noqa: BLE001
                    self._err("settle_get", exc)
            if changed:
                self._save_state()
            for slug in list(self.mk):
                if slug in self.settled:
                    self.mk.pop(slug, None)
        except Exception as exc:                              # noqa: BLE001
            self._err("check_settlements", exc)

    # ----------------------------------------------------- per cycle ---
    def after_cycle(self, targets: Iterable[Mapping[str, Any]] = (),
                    universe: Iterable[Mapping[str, Any]] = (),
                    now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        try:
            if not self.map_t or now - self.map_t >= self.map_refresh_s:
                self.refresh_map(targets, universe, now)
            self._flush_stale_markouts(now)
            self.check_settlements(now)
            self._roll_days(now)
            if now - self.last_status >= self.status_s:
                self.emit_status(now)
        except Exception as exc:                              # noqa: BLE001
            self._err("after_cycle", exc)

    def _roll_days(self, now: float) -> None:
        today = _day(now)
        old = [k for k in self.acc if k[0] != today]
        rows = []
        for k in old:
            a = self.acc.pop(k)
            rows.append({"kind": "incentive_share", "summary": "day_close",
                         "day": k[0], "t": _iso(now), "slug": k[1],
                         "event": a["event"], "programs": a["programs"],
                         "cum_day": self._acc_summary(a)})
        self.write(rows, now)
        # bound per-market state to markets seen in the last day
        for slug in [s for s, m in self.mk.items()
                     if now - m.get("last_seen", now) > 86400 and
                     not m["pending"]]:
            self.mk.pop(slug, None)

    # -------------------------------------------------------- status ---
    @staticmethod
    def _acc_summary(a: dict) -> dict:
        obs = a["obs_s"]
        per_day = (86400.0 / obs) if obs > 0 else None

        def mean(xs):
            return round(sum(xs) / len(xs), 5) if xs else None
        out = {"snaps": a["snaps"], "obs_s": round(obs, 1),
               "fills": dict(a["fills"]), "requotes": dict(a["requotes"])}
        for q in QUOTE_SIZES:
            out["qual_frac_q%d" % q] = (round(a["qual_s"][q] / obs, 4)
                                        if obs > 0 else None)
            out["both_sides_frac_q%d" % q] = (round(a["both_s"][q] / obs, 4)
                                              if obs > 0 else None)
            out["usd_accrued_q%d" % q] = {r: round(a["usd"][q][r], 4)
                                          for r in READINGS}
            out["usd_day_q%d" % q] = ({r: round(a["usd"][q][r] * per_day, 3)
                                       for r in READINGS} if per_day else None)
        hours = obs / 3600.0
        out["fills_hr"] = ({s: round(n / hours, 3) for s, n in
                            a["fills"].items()} if hours > 0 else None)
        for h in MARKOUT_HORIZONS_S:
            out["mo%d" % h] = mean(a["mo"][h])
            out["mo%d_n" % h] = len(a["mo"][h])
        # reward minus markout cost at Q=10, PER_EVENT, 120s markouts,
        # both on the same observed-time basis
        if per_day:
            cost = -sum(a["mo"][120]) * QUOTE_SIZES[0]
            out["net_ev_q10_day"] = round(
                (a["usd"][QUOTE_SIZES[0]]["PER_EVENT"] - cost) * per_day, 3)
        else:
            out["net_ev_q10_day"] = None
        return out

    def status_payload(self, now: Optional[float] = None) -> dict:
        now = self.clock() if now is None else now
        today = _day(now)
        mkts = []
        for (day, slug), a in self.acc.items():
            if day != today:
                continue
            s = self._acc_summary(a)
            d10, d50 = s["usd_day_q10"] or {}, s["usd_day_q50"] or {}
            mkts.append({
                "slug": slug, "ev": a["event"], "obs_s": s["obs_s"],
                "qual10": s["qual_frac_q10"],
                "q10": [d10.get(r) for r in READINGS],
                "q50": [d50.get(r) for r in READINGS],
                "fills_hr": s["fills_hr"],
                "mo30": s["mo30"], "mo120": s["mo120"],
                "n_mo120": s["mo120_n"],
                "net_ev_q10_day": s["net_ev_q10_day"]})
        mkts.sort(key=lambda r: -((r["q10"][1] or 0.0) if r["q10"] else 0.0))
        return {"label": LABEL, "t": _iso(now), "day": today,
                "readings": "q10/q50=[PER_MARKET,PER_EVENT,PER_PROGRAM] usd/day",
                "net": "net_ev_q10_day=PER_EVENT reward - Q10*120s markout cost",
                "map": {"age_s": round(now - self.map_t, 1) if self.map_t
                        else None, **{k: self.map_diag.get(k) for k in
                                      ("rewarded_polled", "requests",
                                       "n_errors")}},
                "settle_pending": len(self.settle_pending),
                "settled": len(self.settled),
                "errors": self.errors, "last_error": self.last_error,
                "rows_today": self.rows_today,
                "rows_dropped": self.rows_dropped,
                "markets_total": len(mkts),
                "markets": mkts[:STATUS_TOP_N]}

    def emit_status(self, now: Optional[float] = None) -> Optional[dict]:
        now = self.clock() if now is None else now
        self.last_status = now
        try:
            p = self.status_payload(now)
            self.printer("PMUS_INCENTIVE_STATUS " + json.dumps(
                p, separators=(",", ":"), default=str), flush=True)
            return p
        except Exception as exc:                              # noqa: BLE001
            self._err("emit_status", exc)
            return None
