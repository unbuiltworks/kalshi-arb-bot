"""Venue attestation of the cost basis of settled positions this ledger did
not open -- and the bounded, auditable retroactive correction it drives.

THE DEFECT (2026-11-13, owner-approved fix: "Yes, and correct today too").
The account-wide ledger adopts positions it did not open -- another
strategy's legs, e.g. BTC5m's in `cpc-btc-updown-5m-*` -- at a $1.00/share
upper bound, because it never saw their price. BTC5m rests at $0.08, so a
lone losing leg books 12.5x its real cost and a completed pair books 0
instead of its gain. On 2026-11-13 that put the published daily figure near
-39.53 against a $40 halt, on losses that never happened.

WHAT THIS DOES. After a market has settled AT THE BOUND (its retired row
says `UNKNOWN_COST_UPPER_BOUND`), it reads the VENUE's own record of the
account's fills -- `/v1/portfolio/activities`, through the service's
existing signed transport (`l5_cost_basis_audit.walk_activities`, the same
reader and the same production-validated row parser the cost-basis audit
uses) -- and hands the per-side fills to
`InventoryStore.attest_retired_market_cost`, which re-derives the cost and
replaces the bound only where the venue's quantity equals the held quantity
exactly. Everything else keeps the $1 bound.

WHY AFTER SETTLEMENT, NOT BEFORE. Doing it on the retired row leaves every
OPEN position, and therefore every exposure cap, risk floor and $1 bound
the submission path reads, exactly as it was. The only thing that moves is
the realized figure of a market that has already settled -- on the UTC day
it settled, through a correction record that keeps the old row verbatim.

WHEN THE WALK IS ENOUGH. The feed is account-scoped and newest-first. A walk
is SUFFICIENT for a set of markets when either it reached the end of the
feed, or (cheaper) every dated row it read was in non-increasing time order
and it has passed a row older than every market's floor -- the market's
slug time minus `LOOKBACK_S`. The walk is started only AFTER the markets
were retired, so their fills cannot be newer than its first page. A walk
that stops short can only MISS fills, and a missed fill makes the quantity
fall short of what the ledger held, which refuses: truncation fails closed.

BOUNDED. A few pages per step, a minimum interval between steps, a few
corrections per step, a bounded number of attempts per market (recorded
durably, so a restart does not re-run a market forever), and a hard floor
on how far back it corrects: `RETRO_FLOOR_UTC_DAY`, the day the owner
approved. Older bound rows are reported and never touched.

FAIL CLOSED. Any exception in this path is caught by the caller; the bound
stays and trading is not affected.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import l5_cost_basis_audit as CBA

UPPER_BOUND = "UNKNOWN_COST_UPPER_BOUND"
STATE_KEY = "venue_cost_attestation"

#: the owner approved correcting "today" (2026-11-13) and forward. Rows that
#: settled before this UTC day are reported and never corrected here. An
#: environment value can only move the floor LATER, never earlier.
RETRO_FLOOR_UTC_DAY = "2026-11-13"
RETRO_FLOOR_ENV = "L5_VENUE_ATTEST_FLOOR_UTC_DAY"

#: how long before a market's slug time a fill of it is assumed impossible
#: for the cheaper time-floor proof. If it is wrong the fill is simply
#: missed and the quantity check refuses -- the bound stays.
LOOKBACK_S = 12 * 3600.0
PAGES_PER_STEP = 2
STEP_INTERVAL_S = 5.0
ERROR_BACKOFF_S = 60.0
MAX_WALK_PAGES = 400
MAX_MARKETS_PER_WALK = 64
MAX_CORRECTIONS_PER_STEP = 8
MAX_ATTEMPTS = 3
#: a market is first read this long after it settled, so the venue's feed has
#: caught up with its last fill; a refused market is re-read no sooner than
#: RETRY_S later. Both are wall-clock.
SETTLE_AGE_S = 120.0
RETRY_S = 600.0

#: THE SIBLING-LEDGER FORM. OFF unless this is exactly the token below.
#:
#: A market in another registered strategy's series (the 5-minute lane)
#: is traded two-sided and fast; this ledger adopts one net snapshot of it
#: and retires it at the $1 bound, so the exact forms can never match. With
#: the switch on, a refusal `VENUE_QTY_NE_LEDGER_QTY` on such a market is
#: retried ONCE per pass with that strategy's own order ledger as second
#: evidence -- see `InventoryStore._sibling_refusal` for everything that
#: must then agree. Off, nothing here changes.
SIBLING_ENV = "L5_SIBLING_LEDGER_ATTESTATION"
SIBLING_TOKEN = "VENUE_FILLS_AND_SIBLING_LEDGER"
#: where each other strategy publishes its order ledger (read-only GET)
SIBLING_FEED_ENVS = {"BTC5M": "L5_BTC5M_FILLS_URL",
                     "SPORTS": "L5_SPORTS_FILLS_URL"}
#: THE SPORTS CONTROLLER'S MARKETS ARE IN SCOPE ONLY FROM A DAY THE OWNER
#: NAMES. Owner 2026-11-22: "never simply subtract $10.95 from a safety
#: ledger". Registering the sports series must not, by being deployed,
#: restate rows that are already booked. So for a strategy listed here the
#: sibling form is OFF unless its feed is configured AND this variable holds
#: a UTC day, and it then applies only to markets this ledger settled on or
#: after that day. Unset or malformed: off, and nothing about a sports row
#: changes (it keeps the exact forms and the $1 bound it has today).
SIBLING_FLOOR_ENVS = {"SPORTS": "L5_SPORTS_ATTEST_FLOOR_UTC_DAY"}
SIBLING_TIMEOUT_S = 3.0
#: a market already exhausted under the exact forms gets this many tries
#: under the sibling form, recorded durably, then is left alone again
MAX_SIBLING_ATTEMPTS = 3
QTY_REASON = "VENUE_QTY_NE_LEDGER_QTY"


def sibling_enabled() -> bool:
    return (os.environ.get(SIBLING_ENV) or "").strip() == SIBLING_TOKEN


def _strategy_of(market: str) -> Optional[str]:
    try:
        from src import l5_live_execution as LX
        owner = LX.ORIGIN.ledger_owner_of_market(market)
        return None if owner in (None, LX.OWN_STRATEGY) else str(owner)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return None


def sibling_in_scope(market: str, settled_t: Any) -> bool:
    """May the sibling form be used for this market, settled at this time?

    A strategy with no entry in `SIBLING_FLOOR_ENVS` (the 5-minute lane) is
    exactly as before: in scope whenever the form is on. One with an entry
    needs its feed configured and an owner-named floor day, and the row's
    own settlement day must not be before it.
    """
    owner = _strategy_of(market)
    if owner is None:
        return False
    env = SIBLING_FLOOR_ENVS.get(owner)
    if env is None:
        return True
    feed = SIBLING_FEED_ENVS.get(owner)
    if not feed or not (os.environ.get(feed) or "").strip():
        return False
    floor = (os.environ.get(env) or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", floor):
        return False
    day = str(settled_t or "")[:10]
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)) and day >= floor


def sibling_evidence(market: str, *, fetch: Any = None) -> Dict[str, Any]:
    """The other strategy's own orders for `market`, or why not. GET only.

    {"ok": True, "evidence": {...}} | {"ok": False, "reason": ...}. Never
    raises. An order with no venue id, or the same venue id twice, refuses:
    the store matches by venue order id and must not be handed a guess.
    """
    if not sibling_enabled():
        return {"ok": False, "reason": "SIBLING_FORM_OFF"}
    owner = _strategy_of(market)
    if owner is None:
        return {"ok": False, "reason": "NOT_ANOTHER_STRATEGYS_MARKET"}
    env = SIBLING_FEED_ENVS.get(owner)
    url = (os.environ.get(env) or "").strip() if env else ""
    if not url:
        return {"ok": False, "reason": "SIBLING_FEED_NOT_CONFIGURED"}
    try:
        if fetch is None:
            import json as _json
            import urllib.request as _ur
            with _ur.urlopen(url, timeout=SIBLING_TIMEOUT_S) as r:  # noqa: S310
                doc = _json.loads(r.read().decode("utf-8"))
        else:
            doc = fetch(url)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False,
                "reason": "SIBLING_FEED_UNREACHABLE:%s" % type(exc).__name__}
    if not isinstance(doc, Mapping) or doc.get("readable") is not True:
        return {"ok": False, "reason": "SIBLING_FEED_NOT_READABLE"}
    if str(doc.get("strategy") or "") != owner:
        return {"ok": False, "reason": "SIBLING_FEED_IS_ANOTHER_STRATEGYS"}
    orders: Dict[str, Dict[str, Any]] = {}
    for o in doc.get("orders") or ():
        if not isinstance(o, Mapping):
            return {"ok": False, "reason": "SIBLING_ORDER_UNREADABLE"}
        if str(o.get("market") or "") != market:
            continue
        q = CBA._d(o.get("filled_qty"))
        if q is None:
            return {"ok": False, "reason": "SIBLING_ORDER_UNREADABLE"}
        if q == 0:
            continue
        vid = str(o.get("venue_order_id") or "").strip()
        if not vid or vid in orders:
            return {"ok": False,
                    "reason": "SIBLING_ORDER_ID_MISSING_OR_REPEATED"}
        orders[vid] = {"side": o.get("side"),
                       "filled_qty": o.get("filled_qty"),
                       "filled_cost": o.get("filled_cost")}
    if not orders:
        return {"ok": False, "reason": "SIBLING_HAS_NO_FILLS_IN_MARKET"}
    return {"ok": True, "evidence": {
        "strategy": owner, "orders": orders,
        "as_of_utc": doc.get("as_of_utc"),
        "source": "%s_ORDER_LEDGER_FEED" % owner}}


_SLUG_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})z$")
_ROW_TIME_KEYS = ("time", "createTime", "transactTime", "tradeTime",
                  "executionTime", "createdAt", "created_at", "timestamp",
                  "lastTransactTime")


def retro_floor_day() -> str:
    raw = str(os.getenv(RETRO_FLOOR_ENV, "") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) and raw > RETRO_FLOOR_UTC_DAY:
        return raw
    return RETRO_FLOOR_UTC_DAY


def slug_time(market: str) -> Optional[dt.datetime]:
    m = _SLUG_TIME.search(str(market or ""))
    if not m:
        return None
    try:
        y, mo, d, hh, mm = (int(x) for x in m.groups())
        return dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def parse_time(raw: Any) -> Optional[dt.datetime]:
    """An ISO-8601 instant (nanoseconds and `Z` tolerated), or None."""
    if raw is None or isinstance(raw, bool):
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$", s)
    if m:
        frac = (m.group(2) or "")[:7]
        s = m.group(1) + frac + m.group(3)
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t


def row_time(row: Mapping[str, Any]) -> Optional[dt.datetime]:
    body = CBA._payload(row) if isinstance(row, Mapping) else {}
    for src in (body, row):
        if not isinstance(src, Mapping):
            continue
        for k in _ROW_TIME_KEYS:
            t = parse_time(src.get(k))
            if t is not None:
                return t
    return None


def _row_id(row: Mapping[str, Any]) -> Optional[str]:
    body = CBA._payload(row)
    for k in ("tradeId", "id"):
        v = body.get(k) if isinstance(body, Mapping) else None
        if v not in (None, ""):
            return str(v)
    return None


def candidates(store: Any, *, now: Optional[dt.datetime] = None
               ) -> List[Tuple[str, str]]:
    """(retired_t, market) still at the bound, in scope, not exhausted, old
    enough and not refused too recently -- oldest settlement first."""
    now = now or dt.datetime.now(dt.timezone.utc)
    st = getattr(store, "state", {}) or {}
    floor = retro_floor_day()
    book = ((st.get(STATE_KEY) or {}).get("markets") or {})
    out = []
    for m, row in (st.get("retired_markets") or {}).items():
        if not isinstance(row, Mapping):
            continue
        if row.get("pnl_provenance") != UPPER_BOUND:
            continue
        if row.get("cost_superseded") or row.get("venue_cost_attested"):
            continue
        t = str(row.get("t") or "")
        if not t or t[:10] < floor:
            continue
        rec = book.get(m) or {}
        if rec.get("status") == "CORRECTED":
            continue
        if rec.get("status") == "UPPER_BOUND_KEPT" and not sibling_retry(
                m, rec, settled_t=t) and not round_trip_retry(rec):
            continue
        rt = parse_time(t)
        if rt is not None and (now - rt).total_seconds() < SETTLE_AGE_S:
            continue
        last = parse_time(rec.get("t"))
        if last is not None and (now - last).total_seconds() < RETRY_S:
            continue
        out.append((t, str(m)))
    out.sort()
    return out


def sibling_retry(market: str, rec: Mapping[str, Any], *,
                  settled_t: Any = None) -> bool:
    """May an EXHAUSTED market be read again under the sibling form?

    Only with the switch on, only for another strategy's market, only when
    what exhausted it was the quantity rule (the one thing the sibling form
    addresses), and only `MAX_SIBLING_ATTEMPTS` times, counted durably.
    """
    if not sibling_enabled() or _strategy_of(market) is None:
        return False
    if not sibling_in_scope(market, settled_t):
        return False
    if not str(rec.get("last_reason") or "").startswith(QTY_REASON) \
            and not int(rec.get("sibling_attempts") or 0):
        return False
    return int(rec.get("sibling_attempts") or 0) < MAX_SIBLING_ATTEMPTS


class Walk:
    """One paced walk of the activity feed for a fixed set of markets."""

    def __init__(self, markets: List[str], *, started_at: dt.datetime) -> None:
        self.markets = list(markets)
        self.mset = set(self.markets)
        self.started_at = started_at
        floors = [slug_time(m) for m in self.markets]
        self.floor: Optional[dt.datetime] = (
            None if (not floors or any(f is None for f in floors))
            else min(floors) - dt.timedelta(seconds=LOOKBACK_S))
        self.rows: List[Dict[str, Any]] = []
        self.seen_ids: set = set()
        self.cursor: Optional[str] = None
        self.pages = 0
        self.ended: Optional[str] = None      # None while still walking
        self.last_t: Optional[dt.datetime] = None
        self.monotonic = True
        self.dated_rows = 0
        self.sufficient = False
        self.by: Optional[str] = None
        self.evaluated: set = set()

    def step(self, transport: Any, pages: int) -> None:
        for _ in range(max(1, int(pages))):
            if self.ended is not None or self.sufficient:
                return
            if self.pages >= MAX_WALK_PAGES:
                self.ended = "max_pages"
                return
            got = CBA.walk_activities(transport, max_pages=self.pages + 1,
                                      cursor=self.cursor, rows=[],
                                      pages_done=self.pages)
            ended = str(got.get("ended") or "")
            if ended.startswith("error"):
                self.ended = ended
                return
            self.pages = int(got.get("pages") or self.pages + 1)
            for r in got.get("rows") or ():
                if not isinstance(r, Mapping):
                    continue
                t = row_time(r)
                if t is not None:
                    self.dated_rows += 1
                    if self.last_t is not None and t > self.last_t:
                        self.monotonic = False
                    self.last_t = t
                if CBA._row_market(r) not in self.mset:
                    continue
                rid = _row_id(r)
                if rid is not None:
                    if rid in self.seen_ids:
                        continue          # a re-served row is not a new fill
                    self.seen_ids.add(rid)
                self.rows.append(dict(r))
            if ended in ("eof", "no_cursor"):
                self.ended = ended
                self.sufficient, self.by = True, "END_OF_FEED"
                return
            nxt = got.get("next_cursor")
            if not nxt:
                self.ended = "no_continuation"
                return
            self.cursor = str(nxt)
            if (self.floor is not None and self.monotonic
                    and self.last_t is not None and self.last_t < self.floor):
                self.sufficient, self.by = True, "TIME_FLOOR"
                return

    def proof(self) -> Dict[str, Any]:
        return {"sufficient": bool(self.sufficient), "by": self.by,
                "pages": self.pages, "ended": self.ended,
                "started_at": self.started_at.isoformat(),
                "floor": None if self.floor is None else self.floor.isoformat(),
                "monotonic": self.monotonic,
                "oldest_dated_row": (None if self.last_t is None
                                     else self.last_t.isoformat())}


def legs_from_rows(rows: List[Mapping[str, Any]], market: str
                   ) -> Dict[str, Any]:
    """Per-side venue fills for one market, or a refusal. NO RECONCILIATION
    HERE: the quantity rule lives in exactly one place, the store's verb.

    Every fill row of the market must have a readable outcome side (an
    unreadable one could be either leg's), and each side's fills must pass
    `establish_basis` -- quantity, price, fee and trade id present, and the
    venue's own per-fill `cost` (where the row carries one) agreeing with
    qty x own price + fee to within a cent of rounding. All origins count:
    the ledger's own fills are in the feed too, and the result REPLACES the
    market's cost, so nothing is counted twice.
    """
    mine = [r for r in rows if CBA._row_market(r) == market
            and str(r.get("kind") or r.get("type") or "").strip().lower()
            in CBA.FILL_KINDS]
    sideless = [r for r in mine if CBA._row_side(r) is None]
    if sideless:
        return {"ok": False, "reason": "FILL_SIDE_UNREADABLE",
                "rows": len(sideless)}
    if not mine:
        return {"ok": False, "reason": "NO_VENUE_FILLS_FOUND"}
    legs: Dict[str, Any] = {}
    for side in ("UP", "DOWN"):
        fills = CBA.classify_fills(mine, market=market, side=side,
                                   our_coids=set(),
                                   our_venue_orders=set())["fills"]
        if not fills:
            continue
        qsum = D(0)
        for f in fills:
            q = CBA._d(f.get("qty"))
            if q is None or q <= 0:
                return {"ok": False, "reason": "%s:FILL_WITHOUT_QUANTITY"
                        % side}
            qsum += q
        basis = CBA.establish_basis(fills, expect_qty=qsum, side=side)
        if not basis.get("established"):
            return {"ok": False,
                    "reason": "%s:%s" % (side, basis.get("reason")),
                    "detail": {k: v for k, v in basis.items()
                               if k in ("worst_gap_usd", "trade_id")}}
        if any(not f.get("trade_id") for f in fills):
            return {"ok": False, "reason": "FILL_WITHOUT_TRADE_ID",
                    "side": side}
        legs[side] = {
            "qty": basis["qty"], "cost": basis["actual_cost_usd"],
            "fees": basis.get("fees_usd"),
            "fills": [{"trade_id": f.get("trade_id"),
                       "venue_order_id": f.get("venue_order_id"),
                       "qty": f.get("qty"), "price": f.get("price"),
                       "fee": f.get("fee")} for f in fills]}
    return {"ok": True, "legs": legs}


#: THE ROUND-TRIP FORM (owner 2026-11-26: "Fix the underlying accounting
#: mechanism"). LIVE 2026-11-26: the owner bought 469 NO in
#: `aec-cbb-duke-uconn-2026-11-25` at 02:07 and sold all 469 at 02:14. The
#: ledger adopted the venue's 469 at the $1/share bound at 02:12, never saw
#: the position close, and at settlement (02:26) booked -469.00. The venue's
#: own result was -20.63. The exact forms above could never correct it:
#: `establish_basis` reads every fill as a purchase, so the SALE failed the
#: per-fill cross-check (COST_CROSSCHECK_FAILED) and the bound stayed.
#:
#: A market whose venue fills buy and SELL the same outcome is evaluated
#: here instead: every trade row's own direction (the order intent:
#: BUY_LONG / SELL_LONG / BUY_SHORT / SELL_SHORT) and our executed shares
#: are read, and the form applies ONLY when the venue's net position on
#: every side is exactly zero -- the position was closed before settlement,
#: so settlement redeemed nothing and the market's whole result is the sum
#: of the venue's own per-trade `realizedPnl` (fee-inclusive). Anything
#: unreadable, or a position still open at settlement, refuses: the bound
#: stays. Its attempts are counted separately and bounded.
ROUND_TRIP_FORM = "VENUE_ROUND_TRIP_CLOSED_BEFORE_SETTLEMENT"
MAX_ROUND_TRIP_ATTEMPTS = 3
_INTENTS = {"ORDER_INTENT_BUY_LONG": ("UP", 1),
            "ORDER_INTENT_SELL_LONG": ("UP", -1),
            "ORDER_INTENT_BUY_SHORT": ("DOWN", 1),
            "ORDER_INTENT_SELL_SHORT": ("DOWN", -1)}


def round_trip_from_rows(rows: List[Mapping[str, Any]], market: str
                         ) -> Dict[str, Any]:
    """{"sells": bool, "ok": bool, ...}: the market's venue trades read as
    signed per-side quantities with the venue's own realized result.

    `sells` False: no sale was found and the round-trip form does not apply
    (the exact forms decide). `ok` True only when every trade row is
    readable (intent, executed shares, realizedPnl, trade id), no trade id
    repeats, and the net position on both sides is exactly zero."""
    mine = [r for r in rows if CBA._row_market(r) == market
            and str(r.get("kind") or r.get("type") or "").strip().lower()
            in CBA.FILL_KINDS]
    fills: List[Dict[str, Any]] = []
    net = {"UP": D(0), "DOWN": D(0)}
    realized = D(0)
    sells = False
    problem: Optional[str] = None
    seen: set = set()
    for r in mine:
        b = CBA._payload(r)
        ours = CBA._exec_objects(b) if isinstance(b, Mapping) else []
        order = (ours[0].get("order") if ours and isinstance(
            ours[0].get("order"), Mapping) else None)
        intent = str((order or {}).get("intent") or "").strip().upper()
        side_dir = _INTENTS.get(intent)
        if side_dir and side_dir[1] < 0:
            sells = True
        qty = CBA._d(CBA._first_present(ours, CBA.EXEC_QTY_KEYS)) \
            if ours else None
        rp = CBA._d(b.get("realizedPnl")) if isinstance(b, Mapping) else None
        tid = str((b or {}).get("id") or (b or {}).get("tradeId") or "")
        if problem is None:
            if side_dir is None:
                problem = "TRADE_INTENT_UNREADABLE"
            elif qty is None or qty <= 0:
                problem = "TRADE_QTY_UNREADABLE"
            elif rp is None:
                problem = "TRADE_REALIZED_PNL_UNREADABLE"
            elif not tid:
                problem = "TRADE_ID_MISSING"
            elif tid in seen:
                problem = "TRADE_ID_REPEATED"
        if problem is not None:
            continue
        seen.add(tid)
        side, sign = side_dir
        net[side] += sign * qty
        realized += rp
        fills.append({
            "trade_id": tid,
            "venue_order_id": str((order or {}).get("id") or "") or None,
            "intent": intent, "side": side, "qty": str(qty),
            "realized_pnl": str(rp),
            "manual_indicator": (order or {}).get("manualOrderIndicator")})
    if not sells:
        return {"sells": False, "ok": False, "reason": "NO_SALE_FOUND"}
    if problem is not None:
        return {"sells": True, "ok": False, "reason": problem}
    if net["UP"] != 0 or net["DOWN"] != 0:
        return {"sells": True, "ok": False,
                "reason": "POSITION_OPEN_AT_SETTLEMENT",
                "venue_net": {k: str(v) for k, v in net.items()}}
    return {"sells": True, "ok": True, "realized": str(realized),
            "fills": fills, "venue_net": {k: "0" for k in net},
            "all_manual": bool(fills) and all(
                f["manual_indicator"] == "MANUAL_ORDER_INDICATOR_MANUAL"
                for f in fills)}


def round_trip_retry(rec: Mapping[str, Any]) -> bool:
    """An exhausted market is read again for the round-trip form until that
    form has had its own bounded attempts or has ruled itself out."""
    return (not rec.get("round_trip_final")
            and int(rec.get("round_trip_attempts") or 0)
            < MAX_ROUND_TRIP_ATTEMPTS)


class Attestor:
    """The paced pass. Holds only the in-flight walk in memory; everything
    that must survive a restart is in the store's durable record."""

    def __init__(self) -> None:
        self.walk: Optional[Walk] = None
        self.last_step = 0.0
        self.next_allowed = 0.0
        self.last_error: Optional[str] = None
        self.steps = 0
        self.last_result: List[Dict[str, Any]] = []

    def step(self, store: Any, transport: Any, now: float) -> Dict[str, Any]:
        if transport is None:
            return {"idle": "NO_TRANSPORT"}
        if now < self.next_allowed or now - self.last_step < STEP_INTERVAL_S:
            return {"idle": "PACED"}
        self.last_step = now
        if self.walk is None:
            cands = candidates(store)
            if not cands:
                return {"idle": "NO_CANDIDATES"}
            # WALL CLOCK, not the tick's `now`: the walk's start is compared
            # with the rows' retirement stamps, which are wall-clock.
            self.walk = Walk([m for _t, m in cands[:MAX_MARKETS_PER_WALK]],
                             started_at=dt.datetime.now(dt.timezone.utc))
        w = self.walk
        self.steps += 1
        if not w.sufficient and w.ended is None:
            w.step(transport, PAGES_PER_STEP)
        if not w.sufficient:
            if w.ended is None:
                return {"walking": w.proof()}
            if str(w.ended).startswith("error"):
                # a transport fault (a 429, a timeout) is not evidence about
                # any market: no attempt is spent, the walk is dropped and
                # retried after a back-off.
                self.last_error = w.ended
                self.walk = None
                self.next_allowed = now + ERROR_BACKOFF_S
                return {"walk_failed": w.ended}
            # stopped short with no proof: every market keeps its bound and
            # spends one attempt.
            out = []
            for m in w.markets:
                out.append(self._refuse(store, m, "WALK_INSUFFICIENT:%s"
                                        % w.ended))
            self.walk = None
            self.last_result = out
            return {"refused": out}
        out = []
        for m in w.markets:
            if m in w.evaluated:
                continue
            if len(out) >= MAX_CORRECTIONS_PER_STEP:
                break
            w.evaluated.add(m)
            out.append(self._evaluate(store, w, m))
        if all(m in w.evaluated for m in w.markets):
            self.walk = None
        self.last_result = out
        return {"evaluated": out}

    def _refuse(self, store: Any, market: str, reason: str) -> Dict[str, Any]:
        rec = (((getattr(store, "state", {}) or {}).get(STATE_KEY) or {})
               .get("markets") or {}).get(market) or {}
        final = int(rec.get("attempts") or 0) + 1 >= MAX_ATTEMPTS
        store.note_venue_cost_attempt(market, reason=reason, final=final)
        return {"market": market, "attested": False, "reason": reason,
                "final": final}

    def _evaluate(self, store: Any, w: Walk, market: str) -> Dict[str, Any]:
        try:
            row = ((store.state.get("retired_markets") or {}).get(market))
            if not isinstance(row, Mapping) or \
                    row.get("pnl_provenance") != UPPER_BOUND:
                return {"market": market, "skipped": "NO_LONGER_AT_BOUND"}
            rt = parse_time(row.get("t"))
            if rt is None:
                return self._refuse(store, market, "RETIRED_T_UNREADABLE")
            if rt > w.started_at:
                # retired after the walk began: its fills may be newer than
                # the first page. Not an attempt; the next walk covers it.
                return {"market": market, "skipped": "RETIRED_AFTER_WALK"}
            rt = round_trip_from_rows(w.rows, market)
            if rt.get("sells"):
                return self._evaluate_round_trip(store, w, market, rt)
            rec0 = (((getattr(store, "state", {}) or {}).get(STATE_KEY) or {})
                    .get("markets") or {}).get(market) or {}
            if rec0.get("status") == "UPPER_BOUND_KEPT" and \
                    not sibling_retry(market, rec0, settled_t=row.get("t")):
                # re-read only for the round-trip form, which does not
                # apply: rule it out for good, spend nothing else
                store.note_venue_cost_attempt(
                    market, reason=None, final=True,
                    count_attempt=False, round_trip_final=True)
                return {"market": market, "attested": False,
                        "reason": "ROUND_TRIP:NO_SALE_FOUND", "final": True}
            got = legs_from_rows(w.rows, market)
            if not got.get("ok"):
                return self._refuse(store, market, str(got.get("reason")))
            out = store.attest_retired_market_cost(
                market, legs=got["legs"], walk=w.proof(),
                correction_id="venue-attest|%s" % market)
            if out.get("attested"):
                return {"market": market, "attested": True,
                        "old_pnl": out.get("old_pnl"),
                        "new_pnl": out.get("new_pnl"),
                        "delta": out.get("delta")}
            if str(out.get("reason")) == QTY_REASON and sibling_enabled() \
                    and _strategy_of(market) is not None \
                    and sibling_in_scope(market, row.get("t")):
                return self._evaluate_sibling(store, w, market, got)
            return self._refuse(store, market, str(out.get("reason")))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                return self._refuse(store, market,
                                    "EXCEPTION:%s" % type(exc).__name__)
            except BaseException:                              # noqa: BLE001
                return {"market": market, "attested": False,
                        "reason": "EXCEPTION:%s" % type(exc).__name__}

    def _evaluate_round_trip(self, store: Any, w: Walk, market: str,
                             rt: Mapping[str, Any]) -> Dict[str, Any]:
        """See ROUND_TRIP_FORM. Every outcome but a correction spends one
        ROUND-TRIP attempt and leaves the bound exactly where it was."""
        out: Mapping[str, Any] = {}
        if rt.get("ok"):
            out = store.attest_retired_round_trip(
                market, round_trip=rt, walk=w.proof(),
                correction_id="venue-round-trip|%s" % market)
            if out.get("attested"):
                return {"market": market, "attested": True,
                        "form": ROUND_TRIP_FORM,
                        "old_pnl": out.get("old_pnl"),
                        "new_pnl": out.get("new_pnl"),
                        "delta": out.get("delta")}
        reason = "ROUND_TRIP:%s" % (out.get("reason") or rt.get("reason"))
        rec = (((getattr(store, "state", {}) or {}).get(STATE_KEY) or {})
               .get("markets") or {}).get(market) or {}
        n = int(rec.get("round_trip_attempts") or 0) + 1
        final = n >= MAX_ROUND_TRIP_ATTEMPTS
        store.note_venue_cost_attempt(market, reason=reason, final=True,
                                      count_attempt=False,
                                      round_trip_attempts=n,
                                      round_trip_final=final)
        return {"market": market, "attested": False, "reason": reason,
                "round_trip_attempts": n, "final": final}

    def _evaluate_sibling(self, store: Any, w: Walk, market: str,
                          got: Mapping[str, Any]) -> Dict[str, Any]:
        """The same venue fills, with the other strategy's ledger beside
        them. Every outcome but a correction spends one SIBLING attempt and
        leaves the bound exactly where it was."""
        ev = sibling_evidence(market)
        if ev.get("ok"):
            out = store.attest_retired_market_cost(
                market, legs=got["legs"], walk=w.proof(),
                correction_id="venue-attest|%s" % market,
                sibling=ev["evidence"])
            if out.get("attested"):
                return {"market": market, "attested": True,
                        "form": "VENUE_FILLS_EQUAL_SIBLING_LEDGER",
                        "old_pnl": out.get("old_pnl"),
                        "new_pnl": out.get("new_pnl"),
                        "delta": out.get("delta")}
            reason = "%s:SIBLING:%s" % (QTY_REASON, out.get("sibling")
                                        or out.get("reason"))
        else:
            reason = "%s:SIBLING:%s" % (QTY_REASON, ev.get("reason"))
        rec = (((getattr(store, "state", {}) or {}).get(STATE_KEY) or {})
               .get("markets") or {}).get(market) or {}
        n = int(rec.get("sibling_attempts") or 0) + 1
        final = n >= MAX_SIBLING_ATTEMPTS
        store.note_venue_cost_attempt(market, reason=reason, final=final,
                                      count_attempt=False,
                                      sibling_attempts=n)
        return {"market": market, "attested": False, "reason": reason,
                "sibling_attempts": n, "final": final}

    def heartbeat(self, store: Any) -> Dict[str, Any]:
        st = getattr(store, "state", {}) or {}
        floor = retro_floor_day()
        book = ((st.get(STATE_KEY) or {}).get("markets") or {})
        recs = [r for r in (st.get("cost_basis_corrections") or [])
                if isinstance(r, Mapping)
                and r.get("kind") == "VENUE_ATTESTED_MARKET_COST"]
        delta = D(0)
        for r in recs:
            d = CBA._d(r.get("delta"))
            if d is not None:
                delta += d
        reasons: Dict[str, int] = {}
        pending = kept = before_floor = 0
        for m, row in (st.get("retired_markets") or {}).items():
            if not isinstance(row, Mapping) or \
                    row.get("pnl_provenance") != UPPER_BOUND:
                continue
            if str(row.get("t") or "")[:10] < floor:
                before_floor += 1
                continue
            rec = book.get(m) or {}
            if rec.get("status") == "UPPER_BOUND_KEPT":
                kept += 1
            else:
                pending += 1
            why = str(rec.get("last_reason") or "NOT_YET_ATTEMPTED")
            reasons[why] = reasons.get(why, 0) + 1
        return {
            "attested": sum(1 for r in book.values()
                            if isinstance(r, Mapping)
                            and r.get("status") == "CORRECTED"),
            "corrections_applied": len(recs),
            "corrections_delta_usd": str(delta),
            "corrections": [{"market": r.get("market"),
                             "settlement_utc_day": r.get(
                                 "settlement_utc_day"),
                             "old_pnl": r.get("old_pnl"),
                             "new_pnl": r.get("new_pnl"),
                             "delta": r.get("delta")} for r in recs[-12:]],
            "still_upper_bound": {"count": pending + kept,
                                  "pending": pending,
                                  "kept_after_max_attempts": kept,
                                  "reasons": reasons},
            "upper_bound_before_floor_not_in_scope": before_floor,
            "retro_floor_utc_day": floor,
            "walk": None if self.walk is None else self.walk.proof(),
            "last_error": self.last_error,
            "labels": ("venue-attested cost basis for settled positions this "
                       "ledger did not open. A correction replaces the $1/"
                       "share bound only where the venue's fills reconcile "
                       "exactly; it stays on the market's settlement day and "
                       "is account-wide realized, NOT verified L5. Anything "
                       "not attested keeps the bound."),
        }
