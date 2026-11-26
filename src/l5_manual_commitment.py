"""The venue reads behind the bot-only / total-account drawdown split.

Owner decision 2026-11-25; the arithmetic and the reasons are in
`l5_portfolio_drawdown`. This module is the only part that talks to the
venue, and it only ever GETs: one order by id (`transport.order`), and --
only with the OFF-by-default venue-indicator mode -- pages of the
account's activity feed (`l5_cost_basis_audit.walk_activities`), to FIND
manual order ids (`Discovery`). A found id is then read and verified by
id exactly as an attested one.

WHAT IT PRODUCES. A `measurement` the runner stores on its tick:

    {"t": epoch,
     "orders": [{order_id_tail, market, side, action BUY|SELL, filled,
                 cost_usd | proceeds_usd, state, terminal}],
     "unclassified": [{order_id_tail, market, filled, why, state}],
     "rejected": [{order_id_tail, why}]}

`l5_portfolio_drawdown.manual_commitment` turns that into signed dollars
per market against the positions the account still holds and the ledger's
retired rows, with no venue request, so the published block can be built
on the registry's thread.

THE OWNER ATTESTS IDS, THE VENUE STATES EVERYTHING ELSE. An id counts only
when the venue's own row says `manualOrderIndicator` is the MANUAL value.
A row that is not manual, not found or unreadable is `rejected` and
contributes nothing. A manual row whose direction, market or price cannot
be read with certainty is `unclassified`: nothing of it is credited and
its market is counted as a possible full win. Every doubt makes the BOT
figure LARGER, which is the safe direction.

PACE. A row is re-read at most every REREAD_S while it is not terminal and
never again once it is terminal and accounted; at most READS_PER_STEP
reads happen on one tick. The cache is in memory only: after a restart
every attested id is read again by id, which the venue answers for a
terminal order (`PmusTransport.order`), and the settlement side comes from
the ledger's persisted `retired_markets`, so nothing new is stored on disk.
A FULL ORDER ID IS NEVER LOGGED OR PUBLISHED from
here: the last four characters only.
"""
from __future__ import annotations

import re
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_live_execution as LX
from src import l5_portfolio_drawdown as PDD

MANUAL_INDICATOR = "MANUAL_ORDER_INDICATOR_MANUAL"
#: the only intents that are a purchase
BUY_INTENTS = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
REREAD_S = 60.0
READS_PER_STEP = 3
#: what may be put in the order route. Anything else is never requested.
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


#: A CLOSING SELL, AS FAR AS IT IS KNOWN. This venue has only ever been
#: OBSERVED returning the two BUY intents (`LX.VENUE_SIDE_WORDS`,
#: analysis/l5/completion_repair/EXIT_OPTIONS_VENUE_CHECK.md); no sell row
#: has been recorded by this repository. The spellings below are the
#: venue's documented vocabulary, not an observation, so a sell is accepted
#: only when EVERY statement on the row agrees with one of these two lines
#: and anything else is UNCLASSIFIED -- never guessed into a direction:
#:
#:   intent SELL_LONG  -> closes UP   (outcomeSide YES, side ORDER_SIDE_SELL)
#:   intent SELL_SHORT -> closes DOWN (outcomeSide NO,  side ORDER_SIDE_BUY)
#:
#: (`side` is about the YES token: selling YES sells it, selling NO buys
#: it back.) A hand "sell" the venue records as a BUY of the other outcome
#: is an ordinary BUY row here; the pair it forms is netted at $1 in
#: `l5_portfolio_drawdown.manual_commitment`.
SELL_INTENTS = {"ORDER_INTENT_SELL_LONG": ("UP", "OUTCOME_SIDE_YES",
                                           "ORDER_SIDE_SELL"),
                "ORDER_INTENT_SELL_SHORT": ("DOWN", "OUTCOME_SIDE_NO",
                                            "ORDER_SIDE_BUY")}
BUY, SELL = "BUY", "SELL"


def _direction(row: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """{"action": BUY|SELL, "side": UP|DOWN}, or None when not CERTAIN."""
    intent = str(row.get("intent") or "").strip().upper()
    action = str(row.get("action") or "").strip().upper()
    # the row's `action`, when present, must end in the same verb. (Matched
    # by suffix: this module only READS rows; nothing in src/ builds a sell
    # order, and `test_l5_venue_exit_probe` pins that no sell-action token
    # exists in the source.)
    if intent in BUY_INTENTS and (not action or action.endswith("_BUY")):
        try:
            return {"action": BUY, "side": LX.venue_row_side(
                row, doing="measuring a manual order")}
        except Exception:                                      # noqa: BLE001
            return None
    if intent in SELL_INTENTS and (not action or action.endswith("_SELL")):
        side, outcome, token = SELL_INTENTS[intent]
        for key, want in (("outcomeSide", outcome), ("side", token)):
            got = row.get(key)
            if got is not None and str(got).strip().upper() != want:
                return None
        return {"action": SELL, "side": side}
    return None


def parse_row(order_id: str, row: Any) -> Dict[str, Any]:
    """One venue order row -> {"order": {...}, "terminal"}, or
    {"unclassified": {...}, "terminal"}, or {"why"}.

    PURE. Uses the same helpers the ledger books fills with: the cumulative
    quantity and terminal test of `order_progress`, the WIRE average price
    converted to the outcome's own terms (`own_outcome_price`), and the
    venue's collected commission (absent counts 0).

    A BUY's cash is its cost, filled x own price + commission. A closing
    SELL's cash is its proceeds, filled x own price - commission. A row
    the venue marks manual whose direction or price cannot be read with
    certainty is UNCLASSIFIED: it is not counted, and its market is treated
    as unmeasured with the row's whole quantity as a possible gain.
    """
    if not isinstance(row, Mapping) or not row:
        return {"why": "ORDER_NOT_FOUND"}
    rid = row.get("id")
    if rid is not None and str(rid) != str(order_id):
        return {"why": "ORDER_ID_MISMATCH"}
    if str(row.get("manualOrderIndicator") or "") != MANUAL_INDICATOR:
        # the venue does not say a person placed it: terminal, and final
        return {"why": "NOT_MANUAL_PER_VENUE", "final": True}
    try:
        prog = LX.order_progress(row)
    except Exception as exc:                                   # noqa: BLE001
        return {"why": "ROW_UNREADABLE:%s" % type(exc).__name__}
    cum = prog["cum_qty"]
    if cum < 0:
        return {"why": "ROW_UNREADABLE:NEGATIVE_FILL"}
    tail = PDD.order_tail(order_id)
    terminal = bool(prog.get("terminal"))
    try:
        market = LX.venue_row_market(row, doing="measuring a manual order")
    except Exception:                                          # noqa: BLE001
        market = None
    way = _direction(row)
    why = ("MARKET_UNREADABLE" if market is None
           else "DIRECTION_UNCLASSIFIED" if way is None else None)
    cash = D(0)
    if why is None and cum > 0:
        wire = LX.venue_order_avg_price(row)
        if wire is None:
            why = "AVG_PRICE_UNREADABLE"
        else:
            fee = prog.get("commission")
            fee = fee if fee is not None else D(0)
            gross = cum * LX.own_outcome_price(way["side"], wire,
                                               basis=LX.WIRE)
            cash = max(D(0), gross + fee if way["action"] == BUY
                       else gross - fee)
    if why is not None:
        return {"unclassified": {"order_id_tail": tail, "market": market,
                                 "filled": str(cum), "why": why,
                                 "state": prog.get("state")},
                "terminal": terminal}
    order = {"order_id_tail": tail, "market": market, "side": way["side"],
             "action": way["action"], "filled": str(cum),
             "state": prog.get("state"),
             # a settled result is measured only from a reading taken
             # after the order could no longer fill
             "terminal": terminal}
    order["cost_usd" if way["action"] == BUY else "proceeds_usd"] = str(cash)
    return {"order": order, "terminal": terminal}


# ------------------------------------------- found by the venue's indicator

#: pages of the activity feed read per step, the minimum interval between
#: steps while a walk is under way, and the hard page bound of one walk --
#: the same pacing as `l5_venue_cost_attestation`
PAGES_PER_STEP = 2
STEP_INTERVAL_S = 5.0
MAX_WALK_PAGES = 400
#: a new walk starts no sooner than this after the last one completed; a
#: later walk reads back only to the previous walk's start less OVERLAP_S
REWALK_S = 60.0
OVERLAP_S = 900.0
ERROR_BACKOFF_S = 60.0
#: with no walk completed within this, discovery is STALE -- a refusal
DISCOVERY_MAX_AGE_S = 600.0
#: manual rows that could not be costed, kept for the published block
MAX_UNCLASSIFIED = 50


def manual_rows(rows: List[Mapping[str, Any]], since_epoch: float
                ) -> Dict[str, Any]:
    """Manual order ids on activity-feed trade rows. PURE.

    {"ids": [order id, ...], "unclassified": [{...}]}.

    Only the account's OWN execution is read (`CBA._exec_objects`: the one
    `isAggressor` selects, or the only one); its order must say
    `manualOrderIndicator` == MANUAL. A row whose own execution cannot be
    chosen, or whose indicator is absent or anything else, is NOT manual:
    it stays on the bot figure. A row that says MANUAL and is older than
    `since_epoch` is not counted. One that says MANUAL but has no time or
    no order id cannot be costed: it is UNCLASSIFIED (a possible full win
    in its market)."""
    from src import l5_cost_basis_audit as CBA
    from src import l5_venue_cost_attestation as VCA
    ids: List[str] = []
    unc: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        kind = str(r.get("kind") or r.get("type") or "").strip().lower()
        if kind not in CBA.FILL_KINDS:
            continue
        body = CBA._payload(r)
        ours = CBA._exec_objects(body) if isinstance(body, Mapping) else []
        order = ours[0].get("order") if ours else None
        if not isinstance(order, Mapping) or str(
                order.get("manualOrderIndicator") or "") != MANUAL_INDICATOR:
            continue
        t = VCA.row_time(r)
        oid = CBA._exec_order_id(ours[0])
        if t is not None and t.timestamp() < since_epoch:
            continue
        why = ("MANUAL_ROW_UNDATED" if t is None
               else "MANUAL_ROW_WITHOUT_ORDER_ID" if not oid else None)
        if why is None:
            if oid not in ids:
                ids.append(oid)
            continue
        q = CBA._d(CBA._first_present(ours, CBA.EXEC_QTY_KEYS))
        unc.append({"order_id_tail": PDD.order_tail(oid),
                    "market": CBA._row_market(r) or None,
                    "filled": str(q if q is not None and q > 0 else 0),
                    "why": why, "state": None,
                    "row_id": VCA._row_id(r)})
    return {"ids": ids, "unclassified": unc}


class Discovery:
    """Finds manual order ids from the activity feed at a bounded pace.

    The feed is newest first. The FIRST walk reads back to the SINCE
    instant; each later one back to the previous walk's start less
    OVERLAP_S, keeping every id already found (in memory: after a restart
    the first walk runs again). A walk ends at the end of the feed, or
    once its rows were in non-increasing time order and one is older than
    its stop. A failed page abandons the walk; MAX_WALK_PAGES abandons it
    too. `status` is None only while a walk completed within
    DISCOVERY_MAX_AGE_S."""

    def __init__(self) -> None:
        self.ids: List[str] = []
        self.unclassified: Dict[str, Dict[str, Any]] = {}
        self.walk: Optional[Dict[str, Any]] = None
        self.completed_t: Optional[float] = None
        self.last_walk_start: Optional[float] = None
        self.last_step_t: Optional[float] = None
        self.error_t: Optional[float] = None
        self.last_error: Optional[str] = None
        self.since: Optional[float] = None
        self.walks = 0

    def step(self, transport: Any, now: float, since_epoch: float) -> int:
        """Read at most PAGES_PER_STEP pages. Returns the pages read."""
        from src import l5_cost_basis_audit as CBA
        if self.since != since_epoch:
            # a different SINCE: nothing found under the old one is kept
            self.__init__()
            self.since = since_epoch
        if self.last_step_t is not None \
                and now - self.last_step_t < STEP_INTERVAL_S:
            return 0
        if self.walk is None:
            if self.error_t is not None and now - self.error_t \
                    < ERROR_BACKOFF_S:
                return 0
            if self.completed_t is not None \
                    and now - self.completed_t < REWALK_S:
                return 0
            stop = since_epoch if self.last_walk_start is None else max(
                since_epoch, self.last_walk_start - OVERLAP_S)
            self.walk = {"start": float(now), "stop": stop, "cursor": None,
                         "pages": 0, "last_t": None, "monotonic": True}
        w, read = self.walk, 0
        self.last_step_t = float(now)
        while read < PAGES_PER_STEP:
            if w["pages"] >= MAX_WALK_PAGES:
                return self._abandon(now, "MAX_WALK_PAGES", read)
            got = CBA.walk_activities(transport, max_pages=w["pages"] + 1,
                                      cursor=w["cursor"], rows=[],
                                      pages_done=w["pages"])
            ended = str(got.get("ended") or "")
            if ended.startswith("error"):
                return self._abandon(now, ended, read)
            read += 1
            w["pages"] += 1
            rows = [r for r in (got.get("rows") or ())
                    if isinstance(r, Mapping)]
            self._take(rows, since_epoch)
            from src import l5_venue_cost_attestation as VCA
            for r in rows:
                t = VCA.row_time(r)
                if t is None:
                    continue
                t = t.timestamp()
                if w["last_t"] is not None and t > w["last_t"]:
                    w["monotonic"] = False
                w["last_t"] = t
            if ended in ("eof", "no_cursor"):
                return self._complete(now, read)
            if not got.get("next_cursor"):
                return self._abandon(now, "NO_CONTINUATION", read)
            w["cursor"] = str(got["next_cursor"])
            if w["monotonic"] and w["last_t"] is not None \
                    and w["last_t"] < w["stop"]:
                return self._complete(now, read)
        return read

    def _take(self, rows: List[Mapping[str, Any]], since: float) -> None:
        got = manual_rows(rows, since)
        for oid in got["ids"]:
            if oid not in self.ids:
                self.ids.append(oid)
        for u in got["unclassified"]:
            key = str(u.get("row_id") or "%s|%s" % (u.get("market"),
                                                     u.get("filled")))
            if key not in self.unclassified \
                    and len(self.unclassified) < MAX_UNCLASSIFIED:
                self.unclassified[key] = {k: v for k, v in u.items()
                                          if k != "row_id"}

    def _complete(self, now: float, read: int) -> int:
        self.last_walk_start = self.walk["start"] if self.walk else None
        self.completed_t, self.walk = float(now), None
        self.error_t = self.last_error = None
        self.walks += 1
        return read

    def _abandon(self, now: float, why: str, read: int) -> int:
        self.walk, self.error_t, self.last_error = None, float(now), why
        return read

    def status(self, now: float) -> Optional[str]:
        if self.completed_t is None:
            return "AUTO_MANUAL_DISCOVERY_INCOMPLETE"
        if now - self.completed_t > DISCOVERY_MAX_AGE_S:
            return "AUTO_MANUAL_DISCOVERY_STALE"
        return None

    def brief(self, now: float) -> Dict[str, Any]:
        return {"found_ids": len(self.ids), "walks": self.walks,
                "walking": self.walk is not None,
                "completed_age_s": (None if self.completed_t is None
                                    else round(now - self.completed_t, 1)),
                "last_error": self.last_error, "status": self.status(now)}


class Reader:
    """Reads the attested orders at a bounded pace and keeps what it read."""

    def __init__(self) -> None:
        #: order id -> {"t": last read, "got": parse_row(...), "final": bool}
        self.cache: Dict[str, Dict[str, Any]] = {}
        #: only with the venue-indicator mode on
        self.discovery: Optional[Discovery] = None

    def step(self, transport: Any, now: float,
             ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """One tick. Returns the measurement to store; never raises on a
        failed read -- that order is rejected until it reads again.

        `ids` given: exactly those ids (no discovery). None: the attested
        ids, plus -- with the venue-indicator mode ON -- the ids
        `Discovery` found, read and verified the same way."""
        orders: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        unclassified: List[Dict[str, Any]] = []
        cap, found, pages = PDD.MANUAL_MAX_ORDERS, None, 0
        want = PDD.attested_order_ids() if ids is None else list(ids)
        auto = PDD.auto_manual_config() if ids is None else {"state": PDD.OFF}
        if auto.get("state") == "ON":
            cap = PDD.AUTO_MANUAL_MAX_ORDERS
            if self.discovery is None:
                self.discovery = Discovery()
            try:
                pages = self.discovery.step(transport, float(now),
                                            auto["since_epoch"])
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.discovery._abandon(float(now), "error:%s"
                                        % type(exc).__name__, 0)
            want += [i for i in self.discovery.ids if i not in want]
            unclassified += [dict(u) for u in
                             self.discovery.unclassified.values()]
            gap = self.discovery.status(float(now))
            if gap is not None:
                rejected.append({"order_id_tail": "", "why": gap})
            found = self.discovery.brief(float(now))
        elif auto.get("state") != PDD.OFF:
            self.discovery = None
            rejected.append({"order_id_tail": "", "why": auto.get("why")})
        else:
            self.discovery = None
        for gone in [k for k in self.cache if k not in want]:
            self.cache.pop(gone, None)
        reads = 0
        for n, oid in enumerate(want):
            tail = PDD.order_tail(oid)
            if n >= cap:
                rejected.append({"order_id_tail": tail,
                                 "why": "TOO_MANY_ATTESTED_ORDERS"})
                continue
            if not ORDER_ID_RE.match(oid):
                rejected.append({"order_id_tail": tail,
                                 "why": "ORDER_ID_MALFORMED"})
                continue
            ent = self.cache.get(oid)
            due = ent is None or (not ent.get("final")
                                  and now - ent["t"] >= REREAD_S)
            if due and reads < READS_PER_STEP:
                reads += 1
                try:
                    got = parse_row(oid, transport.order(oid))
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    got = {"why": "ORDER_UNREADABLE:%s" % type(exc).__name__}
                ent = {"t": now, "got": got,
                       "final": bool(got.get("final")
                                     or ((got.get("order")
                                          or got.get("unclassified"))
                                         and got.get("terminal")))}
                self.cache[oid] = ent
            if ent is None:
                rejected.append({"order_id_tail": tail,
                                 "why": "NOT_READ_YET"})
            elif ent["got"].get("order"):
                orders.append(dict(ent["got"]["order"]))
            elif ent["got"].get("unclassified"):
                unclassified.append(dict(ent["got"]["unclassified"]))
            else:
                rejected.append({"order_id_tail": tail,
                                 "why": ent["got"].get("why")})
        out = {"t": float(now), "orders": orders, "rejected": rejected,
               "unclassified": unclassified, "reads": reads}
        if found is not None:
            out.update(pages=pages, discovery=found)
        return out
