"""Order-ID-linked capture of what a real maker order actually met.

WHY THIS EXISTS. The whole negative shadow ledger rests on a fill model
that has never been compared against one real fill of ours, because
`OrderStore.record_submit` persists only time, market, side, price,
quantity, session and mode. The two fields the fill rule consumes -- the
queue ahead of us at our exact limit price, and the prints at that level
over the order's life -- have never been recorded for any order. Without
them a live probe spends money and produces the same unvalidatable record
as every order before it.

WHAT IT IS NOT. It holds no transport, no credentials and no executor. It
cannot submit, cancel, amend or price anything. It is a ledger of
observations, and every method is contained so that a recorder fault
cannot reach the order path.

THE TWO FAILURE DIRECTIONS, AND WHY ONLY ONE IS GUARDED HERE.

  * A recorder fault must never make a submission unsafe. Guaranteed
    structurally: every entry point swallows BaseException (bar
    KeyboardInterrupt/SystemExit), returns None, and mutates nothing the
    executor reads. It has no veto and cannot acquire one.
  * A recorder fault must never produce a record that LOOKS complete. That
    cannot be solved by containment, because a silently-truncated record is
    the dangerous artefact: it would be fed into a calibration and believed.
    So completeness is accounted explicitly -- every capture category
    carries PRESENT or MISSING:<reason>, and `usable_for_calibration` is
    true only when every category that a calibration needs is PRESENT.
    A record with a gap says so on its face.

DELIBERATELY NOT A GATE. The recorder does not refuse a submission whose
pre-capture failed, even though such an order is useless for calibration.
Letting a recorder block the money path is the more dangerous coupling: a
bug here would stop trading. `precapture_ok` is exposed so that a probe
configuration can decline to place an uncalibratable order -- that decision
belongs to the probe, not to the instrument.

QUEUE POSITION IS A BOUND, NOT A MEASUREMENT. `Ladder.ahead_of_us`
aggregates published depth at a price. It cannot say where in that level we
sit, because the venue publishes no per-order queue. So the record carries
the aggregate, the frame it came from, the frame's age, and an explicit
uncertainty block naming what is unknown. Anything else would dress a bound
up as a position.

FEES AND REBATES ARE TWO COLUMNS, NEVER ONE. The schedule-derived estimate
comes from `pmus_fees` (published, 99 rows reproduced exactly). The
venue-actual column starts PENDING and is only ever filled from a venue
statement. They are never summed and the estimate is never promoted.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

from src import l5_maker_fill_model as FM
from src import pmus_fees as FEE

#: the capture categories a calibration needs. A record missing any of
#: these is not usable, and says which.
REQUIRED_CAPTURE = (
    "pre_submit_book",      # the ladder and top of book before the wire
    "queue_estimate",       # aggregate depth at our exact price, + bound
    "lifetime_observation",  # book/print coverage while resting
    "terminal_state",       # how the order ended, venue-confirmed
    "fee_accounting",       # schedule estimate present, actual stated
)

PRESENT = "PRESENT"
RECORD_VERSION = 1

#: a frame older than this is still recorded, but the queue estimate is
#: marked stale: a position derived from a book we have not seen refreshed
#: is a guess about the past.
STALE_FRAME_S = 5.0

#: lifetime coverage: if the gap between consecutive observations of a
#: resting order exceeds this, the record says its coverage has a hole
#: rather than implying continuity it does not have.
MAX_OBSERVATION_GAP_S = 20.0


def _d(v: Any) -> Optional[D]:
    try:
        return None if v in (None, "") else D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _missing(reason: str) -> str:
    return "MISSING:%s" % reason


class CalibrationRecorder:
    """One append-only, order-ID-linked calibration ledger.

    Durable by append: every event is a line, and the per-order view is
    rebuilt by folding the file. A restart therefore loses nothing that
    reached disk, and a record whose events straddle a restart is marked
    with the gap rather than stitched over.
    """

    def __init__(self, root: str,
                 emit: Optional[Any] = None,
                 path: Optional[str] = None) -> None:
        self.root = str(root)
        self.path = path or os.path.join(self.root, "fill_calibration.jsonl")
        self._emit = emit
        #: coid -> folded record
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.errors: Dict[str, int] = {}
        self.appended = 0
        self._load()

    # ------------------------------------------------------------ durability
    def _load(self) -> None:
        """Fold the ledger back. A malformed line is skipped and counted,
        never guessed at."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        self._bump("ledger_line_unreadable")
                        continue
                    self._fold(ev, replay=True)
        except OSError:
            return

    def _append(self, ev: Mapping[str, Any]) -> None:
        d = os.path.dirname(self.path) or "."
        try:
            os.makedirs(d, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self.appended += 1
        except OSError:
            # the fold still happened in memory; the record will report the
            # write failure rather than pretend it persisted
            self._bump("ledger_write_failed")

    def _bump(self, key: str) -> None:
        self.errors[key] = self.errors.get(key, 0) + 1

    def _slot(self, coid: str) -> Dict[str, Any]:
        return self.orders.setdefault(coid, {
            "version": RECORD_VERSION, "coid": coid,
            "capture": {k: _missing("not_captured") for k in REQUIRED_CAPTURE},
            "events": 0, "observations": [], "fills": [],
            "restart_boundaries": 0,
        })

    # ------------------------------------------------------------- the folds
    def _fold(self, ev: Mapping[str, Any], *, replay: bool = False) -> None:
        coid = str(ev.get("coid") or "")
        if not coid:
            return
        r = self._slot(coid)
        r["events"] += 1
        kind = str(ev.get("kind") or "")
        if kind == "pre_submit":
            r["pre_submit"] = dict(ev)
            r["capture"]["pre_submit_book"] = (
                PRESENT if ev.get("book") else _missing("no_book_snapshot"))
            q = ev.get("queue") or {}
            r["capture"]["queue_estimate"] = (
                PRESENT if q.get("queue_ahead") is not None
                else _missing(str(q.get("why") or "queue_unavailable")))
            r["capture"]["fee_accounting"] = (
                PRESENT if ev.get("fees") else _missing("no_fee_estimate"))
        elif kind == "ack":
            r["ack"] = dict(ev)
        elif kind == "observation":
            r["observations"].append(dict(ev))
        elif kind == "fill":
            r["fills"].append(dict(ev))
        elif kind == "terminal":
            r["terminal"] = dict(ev)
            r["capture"]["terminal_state"] = (
                PRESENT if ev.get("venue_confirmed")
                else _missing("terminal_not_venue_confirmed"))
        elif kind == "settlement":
            r["settlement"] = dict(ev)
        elif kind == "fees_actual":
            r["fees_actual"] = dict(ev)
        self._recompute_coverage(r)

    def _recompute_coverage(self, r: Dict[str, Any]) -> None:
        obs = r.get("observations") or []
        if not obs:
            r["capture"]["lifetime_observation"] = _missing("no_observations")
            return
        ts = sorted(float(o.get("t") or 0.0) for o in obs)
        gaps = [b - a for a, b in zip(ts, ts[1:]) if (b - a) > MAX_OBSERVATION_GAP_S]
        r["coverage"] = {
            "observations": len(obs),
            "first_t": ts[0], "last_t": ts[-1],
            "span_s": round(ts[-1] - ts[0], 3),
            "gaps_over_%gs" % MAX_OBSERVATION_GAP_S: len(gaps),
            "largest_gap_s": round(max(gaps), 3) if gaps else 0.0,
        }
        r["capture"]["lifetime_observation"] = (
            PRESENT if not gaps else _missing("coverage_gap"))

    # ------------------------------------------------------- entry points
    def pre_submit(self, coid: str, *, slug: str, side: str,
                   own_px: Any, qty: Any, book: Any = None,
                   ladder: Any = None, now: Optional[float] = None,
                   when: Optional[dt.datetime] = None) -> None:
        """Before the wire. Never raises, never refuses, never mutates."""
        try:
            own = _d(own_px)
            n = float(now) if now is not None else 0.0
            wire = own if side == FM.UP else (
                None if own is None else (D(1) - own))
            resting = FM.BID if side == FM.UP else FM.ASK
            ev = {"kind": "pre_submit", "coid": coid, "t": n, "utc": _utc(),
                  "slug": slug, "side": side,
                  "own_px": None if own is None else str(own),
                  "wire_px": None if wire is None else str(wire),
                  "qty": None if _d(qty) is None else str(_d(qty)),
                  "resting_side": resting,
                  "book": self._book_block(book),
                  "queue": self._queue_block(ladder, slug, resting, wire, n),
                  "fees": self._fee_block(own, _d(qty), when)}
            self._fold(ev)
            self._append(ev)
            self._publish("L5_FILL_CALIB_PRESUBMIT", ev)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("pre_submit:%s" % type(exc).__name__)

    def _book_block(self, book: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(book, Mapping):
            return None
        bid, ask = _d(book.get("bid")), _d(book.get("ask"))
        if bid is None and ask is None:
            return None
        return {"bid": None if bid is None else str(bid),
                "ask": None if ask is None else str(ask),
                "spread": None if (bid is None or ask is None)
                else str(ask - bid)}

    def _queue_block(self, ladder: Any, slug: str, resting_side: str,
                     wire_px: Optional[D], now: float) -> Dict[str, Any]:
        """The aggregate depth ahead of our price, with its provenance and
        what it cannot tell us."""
        base = {"provenance": "authenticated_ladder_aggregate_depth",
                "our_position_within_level": "UNKNOWN",
                "bound": ("under strict FIFO with a current frame we are "
                          "behind AT MOST queue_ahead; the venue publishes "
                          "no per-order queue, so this is a bound and not "
                          "a position"),
                "queue_ahead": None, "level_depth": None,
                "frame_t": None, "frame_age_s": None,
                "frame_updates": None, "stale": None}
        if ladder is None:
            return dict(base, why="no_ladder")
        if wire_px is None:
            return dict(base, why="no_wire_price")
        try:
            bk = (getattr(ladder, "books", {}) or {}).get(slug)
            q = ladder.ahead_of_us(slug, resting_side, wire_px)
            dep = ladder.depth_at(slug, resting_side, wire_px)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("queue_block:%s" % type(exc).__name__)
            return dict(base, why="ladder_read_failed:%s" % type(exc).__name__)
        ft = None if not isinstance(bk, Mapping) else bk.get("t")
        age = None if ft is None else round(now - float(ft), 3)
        out = dict(base,
                   queue_ahead=None if q is None else str(q),
                   level_depth=None if dep is None else str(dep),
                   frame_t=ft, frame_age_s=age,
                   frame_updates=(None if not isinstance(bk, Mapping)
                                  else bk.get("updates")),
                   stale=(None if age is None else bool(age > STALE_FRAME_S)))
        if q is None:
            out["why"] = "price_outside_published_depth"
        return out

    def _fee_block(self, own_px: Optional[D], qty: Optional[D],
                   when: Optional[dt.datetime]) -> Optional[Dict[str, Any]]:
        """Schedule-derived ESTIMATE only. The venue-actual column is a
        separate key and starts PENDING; the two are never summed."""
        if own_px is None or qty is None:
            return None
        try:
            est = {"maker_rebate_usd": str(FEE.maker_rebate(own_px, qty,
                                                            when=when)),
                   "taker_fee_usd_if_we_crossed": str(
                       FEE.taker_fee(own_px, qty, when=when)),
                   "maker_fee_usd": "0.0000",
                   "maker_fee_basis": ("the published schedule has no "
                                       "maker-fee term; makers receive a "
                                       "rebate"),
                   "provenance": FEE.provenance(when)}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("fee_block:%s" % type(exc).__name__)
            return None
        return {"schedule_estimate": est,
                "venue_actual": None,
                "venue_actual_status": "PENDING",
                "labels": ("estimate and actual are separate columns and "
                           "are never summed; an estimate is never promoted "
                           "to an actual")}

    def on_ack(self, coid: str, venue_order_id: Any,
               now: Optional[float] = None) -> None:
        self._simple("ack", coid, now, venue_order_id=(
            None if venue_order_id is None else str(venue_order_id)))

    def on_fill(self, coid: str, *, qty: Any, price: Any,
                cumulative_qty: Any = None, remaining: Any = None,
                now: Optional[float] = None) -> None:
        """A partial or full fill, as the venue reported it."""
        self._simple("fill", coid, now,
                     qty=None if _d(qty) is None else str(_d(qty)),
                     price=None if _d(price) is None else str(_d(price)),
                     cumulative_qty=(None if _d(cumulative_qty) is None
                                     else str(_d(cumulative_qty))),
                     remaining=(None if _d(remaining) is None
                                else str(_d(remaining))))

    def on_terminal(self, coid: str, *, state: str, reason: Any = None,
                    venue_confirmed: bool = False,
                    now: Optional[float] = None) -> None:
        self._simple("terminal", coid, now, state=str(state),
                     reason=None if reason is None else str(reason),
                     venue_confirmed=bool(venue_confirmed))

    def on_settlement(self, coid: str, *, slug: str, settlement_px: Any,
                      venue_state: Any = None,
                      now: Optional[float] = None) -> None:
        self._simple("settlement", coid, now, slug=slug,
                     settlement_px=(None if _d(settlement_px) is None
                                    else str(_d(settlement_px))),
                     venue_state=None if venue_state is None
                     else str(venue_state))

    def on_fees_actual(self, coid: str, *, fee_usd: Any = None,
                       rebate_usd: Any = None, source: str = "",
                       now: Optional[float] = None) -> None:
        """The venue's OWN numbers. Only this fills the actual column."""
        self._simple("fees_actual", coid, now,
                     fee_usd=None if _d(fee_usd) is None else str(_d(fee_usd)),
                     rebate_usd=(None if _d(rebate_usd) is None
                                 else str(_d(rebate_usd))),
                     source=str(source))

    def _simple(self, kind: str, coid: str, now: Optional[float],
                **fields: Any) -> None:
        try:
            ev = {"kind": kind, "coid": coid,
                  "t": float(now) if now is not None else 0.0,
                  "utc": _utc()}
            ev.update(fields)
            self._fold(ev)
            self._append(ev)
            self._publish("L5_FILL_CALIB_%s" % kind.upper(), ev)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("%s:%s" % (kind, type(exc).__name__))

    def observe(self, now: float, *, slug: str, book: Any = None,
                ladder: Any = None, prints: Any = None,
                open_coids: Any = None) -> int:
        """One lifetime observation for every order still resting.

        Records the book, our level's depth, and the prints AT OUR PRICE
        since the last observation. Returns how many orders were observed,
        0 on any fault.
        """
        try:
            want = [c for c in (open_coids or []) if c in self.orders]
            n = 0
            for coid in want:
                r = self.orders[coid]
                pre = r.get("pre_submit") or {}
                if pre.get("slug") != slug:
                    continue
                wire = _d(pre.get("wire_px"))
                rs = str(pre.get("resting_side") or "")
                last = max((float(o.get("t") or 0.0)
                            for o in (r.get("observations") or [])),
                           default=float(pre.get("t") or 0.0))
                ev = {"kind": "observation", "coid": coid, "t": float(now),
                      "utc": _utc(), "slug": slug,
                      "book": self._book_block(book),
                      "queue": self._queue_block(ladder, slug, rs, wire,
                                                 float(now)),
                      "prints_at_our_price": self._prints_at(prints, wire,
                                                             last, float(now))}
                self._fold(ev)
                self._append(ev)
                n += 1
            return n
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("observe:%s" % type(exc).__name__)
            return 0

    def _prints_at(self, prints: Any, wire: Optional[D],
                   since: float, now: float) -> Dict[str, Any]:
        """Prints at our exact wire price in (since, now]. A print at
        another level is counted separately and never credited to us."""
        out = {"at_our_price": 0, "at_our_price_qty": "0",
               "elsewhere": 0, "window": [since, now],
               "unavailable": prints is None}
        if not prints:
            return out
        tot = D(0)
        for p in prints:
            try:
                t = float(p.get("t") or p.get("trade_time") or 0.0)
            except (TypeError, ValueError):
                continue
            if not (since < t <= now):
                continue
            px = _d(p.get("px") or p.get("price"))
            q = _d(p.get("qty")) or D(0)
            if wire is not None and px is not None and px == wire:
                out["at_our_price"] += 1
                tot += q
            else:
                out["elsewhere"] += 1
        out["at_our_price_qty"] = str(tot)
        return out

    # --------------------------------------------------------- the verdict
    def completeness(self, coid: str) -> Dict[str, Any]:
        r = self.orders.get(coid)
        if r is None:
            return {"coid": coid, "known": False,
                    "usable_for_calibration": False,
                    "why": "no_record"}
        cap = dict(r.get("capture") or {})
        missing = [k for k, v in cap.items() if v != PRESENT]
        return {"coid": coid, "known": True, "capture": cap,
                "missing": missing,
                "usable_for_calibration": not missing,
                "write_failures": self.errors.get("ledger_write_failed", 0),
                "labels": ("usable_for_calibration is true only when every "
                           "required category is PRESENT. A record with a "
                           "gap says so rather than reading as complete.")}

    def precapture_ok(self, coid: str) -> bool:
        """Is the PRE-SUBMISSION half of this record sound?

        Exposed so a probe configuration can decline to place an
        uncalibratable order. The recorder itself never refuses.
        """
        r = self.orders.get(coid) or {}
        cap = r.get("capture") or {}
        return (cap.get("pre_submit_book") == PRESENT
                and cap.get("queue_estimate") == PRESENT
                and cap.get("fee_accounting") == PRESENT)

    def record(self, coid: str) -> Optional[Dict[str, Any]]:
        r = self.orders.get(coid)
        if r is None:
            return None
        return dict(r, completeness=self.completeness(coid))

    def report(self) -> Dict[str, Any]:
        usable = sum(1 for c in self.orders
                     if self.completeness(c)["usable_for_calibration"])
        return {"path": self.path, "orders": len(self.orders),
                "usable_for_calibration": usable,
                "not_usable": len(self.orders) - usable,
                "events_appended": self.appended,
                "errors": dict(self.errors),
                "required_capture": list(REQUIRED_CAPTURE),
                "labels": ("observational only: holds no transport, no "
                           "credentials and no executor, and cannot submit, "
                           "cancel, amend or price anything")}

    def _publish(self, key: str, payload: Mapping[str, Any]) -> None:
        if self._emit is None:
            return
        try:
            self._emit(key, dict(payload))
        except BaseException:                                  # noqa: BLE001
            self._bump("emit_failed")
