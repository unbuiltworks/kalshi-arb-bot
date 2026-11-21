"""REPORT-ONLY: the ledger's booked results beside the venue's executions.

WHY. The account ledger learns of positions it did not open from the
venue's NET position, read every few seconds. A pair whose legs fill between
two reads is never booked; a pair read in the gap between its legs is booked
as a one-sided loss at $1 a share. See `l5_venue_basis` for the measured
case. This pass reads the venue's activity feed -- the executions themselves
-- and states, market by market, what the fills say next to what the ledger
booked, INCLUDING markets the ledger never saw.

WHAT IT CANNOT DO, which is the design:

  * It calls NO method of the store and never assigns into `store.state`.
    No booked result, position, cost, attempt counter, correction record or
    limit is written. Nothing here is read by any gate.
  * Its only transport call is the activity feed's GET, through
    `l5_cost_basis_audit.walk_activities`, at the attestation's own pace
    (`VCA.PAGES_PER_STEP` pages no more often than `VCA.STEP_INTERVAL_S`),
    and a whole pass is repeated no more often than `REFRESH_S`.
  * It applies no difference. An upward one is reported as pending the
    owner's existing correction path, which it names; a downward one is
    reported as `downward_adjustment_usd`.
  * OFF unless `L5_VENUE_BASIS` is exactly `REPORT`. There is no other
    mode. Its state lives in memory; a restart repeats the pass.

COMPLETENESS IS THE VENUE'S EXPLICIT END OF FEED AND NOTHING LESS, as in
`l5_equity_feed`: the feed is newest-first but not strictly ordered, so no
row's date proves that every fill of a market has been served. A pass that
does not end on `eof` reports every market VENUE_INCOMPLETE with no figure.

WHAT THE FEED DOES AND DOES NOT CARRY (see `l5_cost_basis_audit`):
  * trade rows (`activity_type_trade`): our execution's outcome side,
    shares, YES-quoted price, commission and the trade id. The venue has no
    separate "sell to close": buying DOWN IS selling YES, and it nets the
    position. Every readable fill is therefore an acquisition of its own
    outcome at its own price; a row whose direction and outcome contradict
    each other, or that states a SELL intent, is left UNREADABLE.
  * resolution rows (`activity_type_position_resolution`): the venue's own
    `afterPosition.realized` for the netted position. That is a REALIZED
    figure, not a cash credit, and its exact convention against a gross
    payout is not established -- so it is reported BESIDE the result as a
    cross-check and never substituted for it.
  * NO settlement-cash credit row. So `settlement_cash` is NOT_CHECKED for
    every market this pass reports, and every figure is labelled settled
    arithmetic on confirmed fills, not realized cash.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import l5_cost_basis_audit as CBA
from src import l5_venue_basis as VB
from src import l5_venue_cost_attestation as VCA

MODE_ENV = "L5_VENUE_BASIS"
MODE_REPORT = "REPORT"
MODE_OFF = "OFF"

#: a finished pass is not repeated sooner than this (seconds)
REFRESH_S = 3600.0
MAX_PAGES = 800
#: feed rows kept in memory for one pass; more makes the pass INCOMPLETE
MAX_ROWS = 20000
#: a market the ledger has no row for is reported only once its slug time is
#: this far behind the pass's start, so a window still trading is not
#: reported as a finished result
UNBOOKED_AGE_S = 3600.0
UNBOOKED_AGE_NO_SLUG_S = 86400.0
#: |venue realized - result| at or below this is agreement
RESOLUTION_TOLERANCE = D("0.005")

#: READ-ONLY DETAIL: exact market slugs whose venue execution rows are
#: printed (one `L5_VENUE_BASIS_DETAIL` line each) when a pass completes.
DETAIL_ENV = "L5_VENUE_BASIS_DETAIL_MARKETS"
DETAIL_MAX_MARKETS = 12
DETAIL_MAX_ROWS = 60
DETAIL_CLIP = 80
DETAIL_MAX_IDENTITY_FIELDS = 40
_SLUG = re.compile(r"[a-z0-9-]{12,120}")
#: THE ONLY identity keys ever printed, matched on the whole key name
#: (case-insensitive): venue order id, trade/fill/execution id, client order
#: id and the manual-order indicator. `id` is NOT here: a bare id is printed
#: only through the named readers in `detail_row`.
IDENTITY_KEYS = frozenset((
    "orderid", "order_id", "venueorderid", "venue_order_id",
    "tradeid", "trade_id", "fillid", "fill_id",
    "executionid", "execution_id", "execid", "exec_id",
    "clientorderid", "client_order_id",
    "manualorderindicator", "manual_order_indicator"))
#: a key containing any of these is never printed and never descended into,
#: at any depth -- no credential and no account identifier reaches the log.
FORBIDDEN_KEY_PARTS = ("account", "user", "owner", "wallet", "address",
                       "email", "key", "secret", "token", "signature",
                       "auth", "session", "ip")

RESOLUTION_KINDS = frozenset(("activity_type_position_resolution",
                              "position_resolution"))


def mode(env: Optional[Mapping[str, str]] = None) -> str:
    """REPORT for the exact token, OFF for everything else."""
    e = os.environ if env is None else env
    return MODE_REPORT if str(e.get(MODE_ENV) or "").strip() == MODE_REPORT \
        else MODE_OFF


def detail_markets(env: Optional[Mapping[str, str]] = None) -> List[str]:
    """The EXACT slugs listed, in order, at most DETAIL_MAX_MARKETS. A token
    that is not a plain lowercase slug is dropped: no wildcard, no prefix."""
    e = os.environ if env is None else env
    out: List[str] = []
    for tok in str(e.get(DETAIL_ENV) or "").split(","):
        tok = tok.strip()
        if _SLUG.fullmatch(tok) and tok not in out:
            out.append(tok)
        if len(out) >= DETAIL_MAX_MARKETS:
            break
    return out


def _clip(x: Any) -> Any:
    if x is None or isinstance(x, (bool, int)):
        return x
    return str(x)[:DETAIL_CLIP]


def identity_fields(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The ALLOWLISTED identity scalars on the row and on OUR execution, by
    dotted path, verbatim, clipped. Bounded.

    Never printed: any key that is not in IDENTITY_KEYS; anything under a
    key containing a FORBIDDEN_KEY_PARTS fragment; and anything under the
    COUNTERPARTY's execution -- where `isAggressor` does not say which
    execution is ours, neither is read.
    """
    out: Dict[str, Any] = {}
    body = CBA._payload(row)
    ours = CBA._exec_objects(body) if isinstance(body, Mapping) else []

    def visit(obj: Any, path: str, depth: int) -> None:
        if depth > 5 or not isinstance(obj, Mapping):
            return
        for k, v in obj.items():
            if len(out) >= DETAIL_MAX_IDENTITY_FIELDS:
                return
            low = str(k).lower()
            if any(part in low for part in FORBIDDEN_KEY_PARTS):
                continue
            here = "%s.%s" % (path, k) if path else str(k)
            if isinstance(v, Mapping):
                if k in CBA.EXEC_KEYS and not any(v is o for o in ours):
                    continue            # not ours, or not known to be
                visit(v, here, depth + 1)
            elif low in IDENTITY_KEYS and not isinstance(v, (list, tuple)):
                out[here[:160]] = _clip(v)

    visit(row, "", 0)
    return out


def detail_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One venue row as served: time, side, direction words, our
    execution's shares/price/fee, and every identity field. Verbatim
    strings; nothing is derived or attributed."""
    body = CBA._payload(row)
    body = body if isinstance(body, Mapping) else {}
    ours = CBA._exec_objects(body)
    ex = ours[0] if ours else {}
    order = ex.get("order") if isinstance(ex.get("order"), Mapping) else {}
    t = VCA.row_time(row)
    qty = CBA._d(CBA._first_present(ours, CBA.EXEC_QTY_KEYS))
    if qty is None:
        qty = CBA._d(body.get("qty"))
    px = CBA._d(CBA._first_present(ours, CBA.EXEC_PX_KEYS))
    if px is None:
        px = CBA._d(body.get("price"))
    fee = CBA._d(CBA._first_present([body], CBA.FEE_KEYS))
    if fee is None:
        fee = CBA._d(CBA._first_present(ours, CBA.FEE_KEYS, deep=True))
    vid_ours, _vid_all, vid_src = CBA._row_venue_order_candidates(body)
    return {
        "kind": _kind(row)[:DETAIL_CLIP],
        "time": None if t is None else t.isoformat(),
        "side": CBA._row_side(row),
        "action": {"order_side": _clip(order.get("side") or ex.get("side")),
                   "outcome_side": _clip(order.get("outcomeSide")
                                         or ex.get("outcomeSide")),
                   "intent": _clip(order.get("intent") or ex.get("intent")),
                   "read_as": row_action(row)},
        "qty": None if qty is None else str(qty),
        "price_yes_quoted": None if px is None else str(px),
        "fee": None if fee is None else str(fee),
        "trade_id": _clip(VCA._row_id(row)),
        "is_aggressor": (body.get("isAggressor")
                         if isinstance(body.get("isAggressor"), bool)
                         else _clip(body.get("isAggressor"))),
        "venue_order_id_ours": _clip(vid_ours),
        "venue_order_id_ours_source": vid_src,
        "identity_fields": identity_fields(row)}


def detail(market: str, rows: List[Mapping[str, Any]], *,
           feed_complete: bool, as_of: str) -> Dict[str, Any]:
    """The `L5_VENUE_BASIS_DETAIL` record for one listed market."""
    trades = [r for r in rows if _kind(r) in CBA.FILL_KINDS]
    listed = [detail_row(r) for r in trades[:DETAIL_MAX_ROWS]]
    return {
        "market": market, "as_of": as_of, "feed_complete": bool(feed_complete),
        "venue_rows": len(trades), "rows_listed": len(listed),
        "rows_omitted": len(trades) - len(listed),
        "resolution_rows": sum(1 for r in rows
                               if _kind(r) in RESOLUTION_KINDS),
        "note": (None if trades else
                 "NO VENUE EXECUTION ROWS FOUND FOR THIS MARKET"
                 + ("" if feed_complete else
                    " (the feed was NOT read to its end, so this is not "
                    "evidence of absence)")),
        "rows": listed,
        "labels": ("READ-ONLY. The venue's rows as served, newest first; "
                   "price is the YES-quoted wire price. Identity fields are "
                   "an allowlist (order, trade/fill/execution and client "
                   "order ids, manual-order indicator) from the row and OUR "
                   "execution only, verbatim and uninterpreted: this line "
                   "attributes no order to anyone and nothing was written.")}


def _kind(row: Mapping[str, Any]) -> str:
    return str(row.get("kind") or row.get("type") or "").strip().lower()


def row_action(row: Mapping[str, Any]) -> str:
    """BUY, or UNKNOWN when the row's own words contradict an acquisition.

    The venue pairs ORDER_SIDE_BUY with OUTCOME_SIDE_YES and ORDER_SIDE_SELL
    with OUTCOME_SIDE_NO (its `side` is about the YES token). Any other
    pairing, or an intent that says SELL, is something this reader has never
    seen and does not interpret.
    """
    body = CBA._payload(row)
    ours = CBA._exec_objects(body) if isinstance(body, Mapping) else []
    if not ours:
        return "BUY"
    ex = ours[0]
    order = ex.get("order") if isinstance(ex.get("order"), Mapping) else {}

    def word(key: str) -> str:
        return str(order.get(key) or ex.get(key) or "").strip().upper()

    intent = word("intent")
    if "SELL" in intent:
        return "UNKNOWN"
    d, o = word("side"), word("outcomeSide")
    dn = "UP" if d.endswith("BUY") else "DOWN" if d.endswith("SELL") else None
    on = "UP" if o.endswith("YES") else "DOWN" if o.endswith("NO") else None
    if dn is not None and on is not None and dn != on:
        return "UNKNOWN"
    return "BUY"


def normalise(rows: List[Mapping[str, Any]], market: str
              ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """One market's feed rows as `l5_venue_basis` fills, through the
    attestation's own reader (`VCA.legs_from_rows`: side, our execution's
    shares and price, fee, trade id, and the venue's per-fill cost
    cross-check). An unreadable market yields ONE unreadable row and the
    reason, so the result is incomplete rather than partial."""
    trades = [r for r in rows if _kind(r) in CBA.FILL_KINDS]
    if any(row_action(r) != "BUY" for r in trades):
        return [{"side": None}], "FILL_ACTION_UNREADABLE"
    got = VCA.legs_from_rows(trades, market)
    if not got.get("ok"):
        why = str(got.get("reason"))
        if why == "NO_VENUE_FILLS_FOUND":
            return [], None
        return [{"side": None}], why
    fills: List[Dict[str, Any]] = []
    for side in ("UP", "DOWN"):
        for f in ((got["legs"].get(side) or {}).get("fills") or ()):
            px = CBA._d(f.get("price"))
            fills.append({
                "side": side, "action": "BUY", "qty": f.get("qty"),
                "price": None if px is None else CBA.own_price(px, side),
                "fee": f.get("fee"), "id": f.get("trade_id")})
    return fills, None


def resolution_check(rows: List[Mapping[str, Any]], result: Mapping[str, Any]
                     ) -> Dict[str, Any]:
    """The venue's own realized figure for the market, beside the result.
    Reported, never used: see the module docstring."""
    res = [r for r in rows if _kind(r) in RESOLUTION_KINDS]
    if not res:
        return {"state": "ABSENT"}
    if len(res) > 1:
        return {"state": "MULTIPLE_ROWS", "rows": len(res)}
    body = CBA._payload(res[0])
    after = body.get("afterPosition") if isinstance(body, Mapping) else None
    realized = CBA._d(after.get("realized")) \
        if isinstance(after, Mapping) else None
    if realized is None:
        return {"state": "UNREADABLE"}
    out = {"venue_realized": str(realized)}
    if result.get("pnl") is None:
        return dict(out, state="RESULT_INCOMPLETE")
    ok = abs(realized - result["pnl"]) <= RESOLUTION_TOLERANCE
    return dict(out, state="AGREES" if ok else "DISAGREES")


def scope(state: Mapping[str, Any]) -> Dict[str, Any]:
    """What a pass covers, read from the ledger. Writes nothing.

    `since` is the last accounting re-anchor, or the attestation's approved
    floor day when there is none: rows settled before it are not in the
    account-wide figure, so neither they nor unbooked markets older than it
    are compared.
    """
    ts = [str(r.get("t")) for r in (state.get("accounting_rebase_log") or [])
          if isinstance(r, Mapping) and r.get("t")]
    since = VCA.parse_time(max(ts)) if ts else None
    if since is None:
        since = VCA.parse_time(VCA.retro_floor_day() + "T00:00:00+00:00")
    retired: Dict[str, Mapping[str, Any]] = {}
    known = set()
    for m, row in (state.get("retired_markets") or {}).items():
        known.add(str(m))
        if not isinstance(row, Mapping):
            continue
        t = VCA.parse_time(row.get("t"))
        if t is not None and since is not None and t >= since:
            retired[str(m)] = row
    for name in ("positions", "leg_cost", "committed"):
        for k in (state.get(name) or {}):
            known.add(str(k).rsplit("|", 1)[0])
    return {"since": since, "retired": retired, "known": known}


def _str(x: Any) -> Any:
    if isinstance(x, D):
        return str(x)
    if isinstance(x, Mapping):
        return {k: _str(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_str(v) for v in x]
    return x


def build(state: Mapping[str, Any], rows_by_market: Mapping[str, List[Any]],
          *, feed_complete: bool, started_at: dt.datetime
          ) -> Dict[str, Any]:
    """Every in-scope market's comparison, and the totals. PURE: reads its
    arguments and returns new objects."""
    sc = scope(state)
    since, retired, known = sc["since"], sc["retired"], sc["known"]
    entries: List[Dict[str, Any]] = []
    skipped = {"RETIRED_AFTER_PASS_STARTED": 0, "UNBOOKED_TOO_RECENT": 0,
               "UNBOOKED_BEFORE_SCOPE": 0}
    for m in sorted(retired):
        row = retired[m]
        rt = VCA.parse_time(row.get("t"))
        if rt is None or rt > started_at:
            #: its fills may be newer than the pass's first page
            skipped["RETIRED_AFTER_PASS_STARTED"] += 1
            continue
        rows = list(rows_by_market.get(m) or ())
        fills, why = normalise(rows, m)
        res = VB.market_result(fills, CBA._d(row.get("yes_value")),
                               feed_complete=feed_complete)
        booked = row.get("settlement_pnl")
        cmp_ = VB.compare("unreadable" if booked is None else booked, res)
        at_bound = (row.get("pnl_provenance") == VCA.UPPER_BOUND
                    and not row.get("cost_superseded")
                    and not row.get("venue_cost_attested"))
        entries.append({
            "market": m, "result": res, "comparison": cmp_,
            "ledger": {"up_qty": row.get("up_qty"),
                       "dn_qty": row.get("dn_qty"),
                       "pnl_provenance": row.get("pnl_provenance")},
            "venue_unreadable_reason": why,
            "phantom": VB.phantom(row.get("up_qty"), row.get("dn_qty"), res),
            "path": VB.correction_path(
                cmp_["class"], res, ledger_up=row.get("up_qty"),
                ledger_dn=row.get("dn_qty"), at_bound=at_bound),
            "venue_resolution": resolution_check(rows, res)})
    for m in sorted(rows_by_market):
        if m in known:
            continue
        rows = list(rows_by_market.get(m) or ())
        if not any(_kind(r) in CBA.FILL_KINDS for r in rows):
            continue
        st = VCA.slug_time(m)
        times = [t for t in (VCA.row_time(r) for r in rows) if t is not None]
        ref = st if st is not None else (max(times) if times else None)
        age = UNBOOKED_AGE_S if st is not None else UNBOOKED_AGE_NO_SLUG_S
        if ref is None or (started_at - ref).total_seconds() < age:
            skipped["UNBOOKED_TOO_RECENT"] += 1
            continue
        if since is not None and ref < since:
            skipped["UNBOOKED_BEFORE_SCOPE"] += 1
            continue
        fills, why = normalise(rows, m)
        #: the ledger never saw this market, so it holds no settlement value
        #: for it. A matched pair needs none; anything else is incomplete.
        res = VB.market_result(fills, None, feed_complete=feed_complete)
        cmp_ = VB.compare(None, res)
        entries.append({
            "market": m, "result": res, "comparison": cmp_, "ledger": None,
            "venue_unreadable_reason": why, "phantom": None,
            "path": VB.correction_path(cmp_["class"], res),
            "venue_resolution": resolution_check(rows, res)})
    summary = VB.summarise(entries)
    resolution: Dict[str, int] = {}
    for e in entries:
        k = e["venue_resolution"]["state"]
        resolution[k] = resolution.get(k, 0) + 1
    summary["venue_resolution_cross_check"] = resolution
    summary["skipped"] = skipped
    summary["scope_since"] = None if since is None else since.isoformat()
    return {"summary": summary, "entries": entries}


def small(summary: Mapping[str, Any]) -> Dict[str, Any]:
    """Counts and totals only, for the heartbeat. Never the rows."""
    keys = ("markets", "by_class", "venue_confirmed_fills",
            "total_cost_usd", "total_fees_usd", "total_payout_usd",
            "venue_pnl_usd", "booked_pnl_usd",
            "upward_difference_pending_usd", "downward_adjustment_usd",
            "settlement_cash", "phantom_positions", "differences_total",
            "as_of", "feed_complete", "pages")
    return {k: summary.get(k) for k in keys if k in summary}


class Pass:
    """The paced pass. Everything it knows is in memory."""

    def __init__(self) -> None:
        self._walk: Optional[Dict[str, Any]] = None
        self.last: Optional[Dict[str, Any]] = None
        self.last_t: Optional[float] = None
        self.last_step = 0.0
        self.next_allowed = 0.0
        self.last_error: Optional[str] = None
        self.passes = 0
        #: wall clock, compared with the ledger's own retirement stamps
        self.wall = lambda: dt.datetime.now(dt.timezone.utc)

    def step(self, store: Any, transport: Any, now: float) -> Dict[str, Any]:
        if mode() != MODE_REPORT:
            return {"idle": MODE_OFF}
        if transport is None:
            return {"idle": "NO_TRANSPORT"}
        if now < self.next_allowed \
                or now - self.last_step < VCA.STEP_INTERVAL_S:
            return {"idle": "PACED"}
        if self._walk is None:
            if self.last_t is not None and now - self.last_t < REFRESH_S:
                return {"idle": "FRESH"}
            st = getattr(store, "state", None)
            sc = scope(st if isinstance(st, Mapping) else {})
            self._walk = {
                "cursor": None, "pages": 0, "rows": {}, "kept": 0,
                "seen": set(), "overflow": False,
                "started_at": self.wall(),
                "retired": set(sc["retired"]), "known": set(sc["known"]),
                "detail": set(detail_markets())}
        self.last_step = now
        w = self._walk
        for _ in range(VCA.PAGES_PER_STEP):
            got = CBA.walk_activities(transport, max_pages=w["pages"] + 1,
                                      cursor=w["cursor"], rows=[],
                                      pages_done=w["pages"])
            ended = str(got.get("ended") or "")
            if ended.startswith("error"):
                #: a transport fault says nothing about any market
                self.last_error = ended
                self._walk = None
                self.next_allowed = now + VCA.ERROR_BACKOFF_S
                return {"walk_failed": ended}
            w["pages"] = int(got.get("pages") or w["pages"] + 1)
            for r in got.get("rows") or ():
                self._keep(w, r)
            if ended == "eof":
                return self._finish(store, now, True, ended)
            nxt = got.get("next_cursor")
            if ended == "no_cursor" or not nxt or w["pages"] >= MAX_PAGES:
                return self._finish(store, now, False,
                                    ended if ended != "max_pages"
                                    or w["pages"] < MAX_PAGES
                                    else "max_pages_of_pass")
            w["cursor"] = str(nxt)
        return {"walking": {"pages": w["pages"], "rows_kept": w["kept"]}}

    @staticmethod
    def _keep(w: Dict[str, Any], r: Any) -> None:
        if not isinstance(r, Mapping):
            return
        kind = _kind(r)
        if kind not in CBA.FILL_KINDS and kind not in RESOLUTION_KINDS:
            return
        m = CBA._row_market(r)
        if not m:
            return
        if m in w["known"] and m not in w["retired"] \
                and m not in w["detail"]:
            return                      # open, or settled before the scope
        rid = VCA._row_id(r)
        if rid is not None:
            key = (kind, rid)
            if key in w["seen"]:
                return                  # a re-served row is not a new fill
            w["seen"].add(key)
        if w["kept"] >= MAX_ROWS:
            w["overflow"] = True
            return
        w["rows"].setdefault(m, []).append(dict(r))
        w["kept"] += 1

    def _finish(self, store: Any, now: float, eof: bool, ended: str
                ) -> Dict[str, Any]:
        w = self._walk or {}
        self._walk = None
        complete = bool(eof and not w.get("overflow"))
        st = getattr(store, "state", None)
        out = build(st if isinstance(st, Mapping) else {},
                    w.get("rows") or {}, feed_complete=complete,
                    started_at=w["started_at"])
        summary = dict(out["summary"])
        summary.update({"as_of": w["started_at"].isoformat(),
                        "feed_complete": complete,
                        "feed_ended": ended,
                        "rows_overflowed": bool(w.get("overflow")),
                        "pages": w.get("pages"), "mode": MODE_REPORT})
        self.last = _str(summary)
        self.last_t = now
        self.last_error = None
        self.passes += 1
        details = [detail(m, list((w.get("rows") or {}).get(m) or ()),
                          feed_complete=complete,
                          as_of=w["started_at"].isoformat())
                   for m in sorted(w.get("detail") or ())]
        return {"report": self.last, "details": details}

    def lines(self) -> Dict[str, Any]:
        """The small block for the heartbeat and the account-risk record."""
        md = mode()
        if md != MODE_REPORT:
            return {"state": MODE_OFF, "applied": False}
        w = self._walk
        out: Dict[str, Any] = {
            "state": MODE_REPORT, "applied": False,
            "passes_completed": self.passes,
            "walking_pages": None if w is None else int(w["pages"]),
            "last_error": self.last_error,
            "summary": None if self.last is None else small(self.last),
            "labels": ("REPORT ONLY. The venue's executions beside the "
                       "ledger's booked results; nothing is applied and no "
                       "gate reads this. Settled arithmetic on confirmed "
                       "fills, not realized cash.")}
        return out
