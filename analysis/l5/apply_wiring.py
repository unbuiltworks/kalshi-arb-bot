"""Apply the reporting-only book-evidence wiring. Idempotent-checked."""
import pathlib, sys

R = pathlib.Path("/home/user/kalshi-arb-bot")

# ---- A) the no-fallback venue timestamp helper, in the reporting module ----
be = R / "src/l5_book_evidence.py"
s = be.read_text()
anchor = "# ---------------------------------------------------------------- the ring"
helper = '''#: the venue timestamp keys this stack already recognises, in the order
#: `l5_live_execution._ack_epoch` tries them.
VENUE_TIME_KEYS = ("transact_time", "transactTime", "ack_time", "createdAt",
                   "created_at", "updateTime", "update_time")


def venue_event_t(row: Any) -> Tuple[Optional[float], Optional[str]]:
    """The venue's OWN event time, or (None, None). NEVER a fallback.

    `_ack_epoch` on the money path falls back to `now()` when the venue
    served no timestamp, which is correct there -- a deadline computed from
    our receipt errs early, which is the safe direction. It is WRONG here:
    substituting our receipt would turn "we do not know when the venue
    filled this" into a confident fill time and quietly relabel a
    processing-time book as an event-time one. So this returns None and the
    record says NO_VENUE_EVENT_TIME_SUPPLIED.
    """
    if not isinstance(row, Mapping):
        return None, None
    for k in VENUE_TIME_KEYS:
        v = row.get(k)
        if v is None:
            continue
        try:
            if isinstance(v, str):
                t = dt.datetime.fromisoformat(
                    v.replace("Z", "+00:00")).timestamp()
            else:
                t = float(v)
        except (ValueError, TypeError, OverflowError):
            continue
        # venue epochs have arrived in seconds and in milliseconds
        if t > 1e11:
            t = t / 1000.0
        if t == t and 1e9 < t < 1e11:
            return t, k
    return None, None


'''
if "def venue_event_t" not in s:
    assert anchor in s
    s = s.replace(anchor, helper + anchor, 1)
    s = s.replace(
        "from decimal import Decimal as D, InvalidOperation\n"
        "from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple",
        "import datetime as dt\n"
        "from decimal import Decimal as D, InvalidOperation\n"
        "from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple",
        1)
    be.write_text(s)
    print("A: venue_event_t added")
else:
    print("A: already present")

# ---- the import-boundary test now allows stdlib datetime --------------------
tf = R / "tests/test_l5_book_evidence.py"
ts = tf.read_text()
old_t = '''def test_imports_nothing_from_the_execution_stack():
    """The boundary is structural, not promised."""'''
new_t = '''def test_imports_nothing_from_the_execution_stack():
    """The boundary is structural, not promised: stdlib only, and nothing
    from the execution stack, so this module cannot reach a venue."""'''
if old_t in ts:
    ts = ts.replace(old_t, new_t, 1)
    ts = ts.replace(
        '    assert mods <= {"__future__", "decimal", "typing"}, mods',
        '    assert mods <= {"__future__", "decimal", "typing",\n'
        '                    "datetime"}, mods', 1)
    tf.write_text(ts)
    print("A2: import test widened to stdlib")

# ---- B) the runner wiring ---------------------------------------------------
rf = R / "src/l5_pmus_live_runner.py"
r = rf.read_text()

# B0. the probe's own depth_ticks, read the same way the probe reads it, so
# the evidence reports the price the engine would actually have quoted.
b0_anchor = "def _window_submits(runner: Any, market: str)"
b0 = '''def _evidence_depth_ticks() -> Optional[int]:
    """How far behind the touch the probe rests, from the same env var it
    reads (`L5_TS_DEPTH_TICKS`, default 2). Reporting only: this feeds the
    book record so a reader can compare against the engine's own quote, and
    nothing here changes what the engine does. None if unreadable, which the
    record then reports as an absent behind-touch figure rather than zero.
    """
    try:
        return int(os.getenv("L5_TS_DEPTH_TICKS", "2"))
    except (ValueError, TypeError):
        return None


'''
assert b0_anchor in r
if "_evidence_depth_ticks" not in r:
    r = r.replace(b0_anchor, b0 + b0_anchor, 1)

# B1. build the ring and sampler on the window roll, with their OWN ladder
b1_old = """        self.fanout = FrameFanout(primary)
        self.shadow_attach_ladder(slug)"""
b1_new = """        self.fanout = FrameFanout(primary)
        self.shadow_attach_ladder(slug)
        self.evidence_attach(slug)"""
assert b1_old in r and "self.evidence_attach(slug)" not in r
r = r.replace(b1_old, b1_new, 1)

# B2. the attach method, beside shadow_attach_ladder
b2_anchor = "    def shadow_controller(self) -> Dict[str, Any]:"
b2 = '''    def evidence_attach(self, slug: str) -> None:
        """Book-evidence recorders for this window. REPORTING ONLY.

        ITS OWN LADDER, not the shadow one. `shadow_attach_ladder` returns
        early when shadow is off, so hanging the evidence off it would make
        the answer to the owner's question depend on an unrelated switch.
        One extra `Ladder` costs one parse per frame -- a few per second --
        and the fanout wraps extra observers individually, so neither the
        ladder nor the ring can reach the money path's observer.

        A failure here leaves both recorders None and is counted. Every
        caller treats None as "no evidence this window", never as an error.
        """
        self.book_ring = None
        self.residual_sampler = None
        self.evidence_ladder = None
        try:
            from src import l5_book_evidence as BE
            self.book_ring = BE.BookRing()
            self.residual_sampler = BE.ResidualSampler(
                every_s=float(os.getenv("L5_EVIDENCE_SAMPLE_S") or 5.0))
            lad = FM.Ladder()
            self.evidence_ladder = lad
            ring = self.book_ring

            def _observe(msg: Any, now: float, _l=lad, _r=ring) -> None:
                _l.ingest(msg, now)
                _r.observe_ladder(_l, now)

            if self.fanout is not None:
                self.fanout.add(_observe)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.book_ring = None
            self.residual_sampler = None
            self.evidence_ladder = None
            self.stats["evidence_attach_error:%s" % type(exc).__name__] += 1

    def emit_fill_evidence(self, ev: Mapping[str, Any]) -> None:
        """The opposite side at one fill, on both time bases.

        REPORTING ONLY, and wrapped: a book record must never be the reason
        a fill fails to be processed.
        """
        try:
            from src import l5_book_evidence as BE
            ring = getattr(self, "book_ring", None)
            if ring is None:
                return
            m = ev.get("market")
            if not m:
                return                      # unidentified rows are not filed
            coid = str(ev.get("coid") or "")
            orders = getattr(getattr(self, "fill_calibration", None),
                             "orders", None) or {}
            sub = ((orders.get(coid) or {}).get("pre_submit")) or {}
            side = ev.get("side_tracked") or sub.get("side")
            rec = BE.fill_evidence(
                slug=str(m), coid=coid, side=side,
                fill_qty=ev.get("delta"), fill_own_px=ev.get("avg_px"),
                venue_event_t=ev.get("venue_event_t"),
                processing_t=self.now_t,
                residual_after=self.store.residual(str(m)),
                first_leg_own_px=sub.get("own_px") or ev.get("avg_px"),
                qty_needed=self.store.residual(str(m)),
                ring=ring, order_resting=None,
                depth_ticks=_evidence_depth_ticks())
            rec["venue_event_t_key"] = ev.get("venue_event_t_key")
            emit("L5_FILL_BOOK_EVIDENCE", rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["fill_evidence_error:%s" % type(exc).__name__] += 1

    def sample_residual_book(self, market: Optional[str]) -> None:
        """Book snapshots while inventory is unmatched, order or no order.

        Driven by the residual, NOT by an order existing, because the
        intervals with nothing resting are exactly the ones no existing
        record covers. REPORTING ONLY and wrapped.
        """
        try:
            s = getattr(self, "residual_sampler", None)
            ring = getattr(self, "book_ring", None)
            if s is None or ring is None or not market:
                return
            resid = self.store.residual(str(market))
            live = 0
            try:
                for _c, _o in (self.store.live_orders() or {}).items():
                    if str((_o or {}).get("market") or "") == str(market):
                        live += 1
            except BaseException:                              # noqa: BLE001
                live = 0
            rec = s.sample(
                slug=str(market), now=self.now_t, residual=resid,
                order_resting=bool(live), ring=ring,
                depth_ticks=_evidence_depth_ticks())
            if rec is not None:
                emit("L5_RESIDUAL_BOOK_SAMPLE", rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.stats["residual_sample_error:%s" % type(exc).__name__] += 1

'''
assert b2_anchor in r and "def evidence_attach" not in r
r = r.replace(b2_anchor, b2 + b2_anchor, 1)

# B3. carry the venue's own event time and the tracked side with the delta
b3_old = """            for k in ("avg_px", "commission", "leaves", "cum_qty", "state"):
                if prog.get(k) is not None and k not in res:
                    res[k] = str(prog.get(k))"""
b3_new = """            for k in ("avg_px", "commission", "leaves", "cum_qty", "state"):
                if prog.get(k) is not None and k not in res:
                    res[k] = str(prog.get(k))
            # THE VENUE'S OWN EVENT TIME, additively and with no fallback.
            # Without it a fill can only ever be placed against the book as
            # WE later read it; with it, against the book as the venue had
            # it. `venue_event_t` returns None rather than substituting our
            # receipt, so an absent timestamp reads as absent. Reporting
            # only: no gate, deadline, price or order path reads either key.
            try:
                from src import l5_book_evidence as _BE
                vt, vk = _BE.venue_event_t(row)
                res["venue_event_t"] = vt
                res["venue_event_t_key"] = vk
                res["side_tracked"] = str(o.get("side") or "") or None
            except BaseException as _exc:                      # noqa: BLE001
                if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["venue_event_t_error:%s"
                           % type(_exc).__name__] += 1"""
assert b3_old in r
r = r.replace(b3_old, b3_new, 1)

# B4. emit the fill record on a positive delta
b4_old = """                rows = self.win_lifecycle.setdefault(str(m), [])
                if len(rows) < 4096:
                    rows.append(rec)"""
b4_new = """                rows = self.win_lifecycle.setdefault(str(m), [])
                if len(rows) < 4096:
                    rows.append(rec)
            # one book record per POSITIVE delta: a partial fill is several
            # deltas and each one saw its own book, while a zero-delta
            # progress poll saw nothing new and is not a fill.
            try:
                if D(str(e.get("delta") or 0)) > 0:
                    self.emit_fill_evidence(rec)
            except (ArithmeticError, ValueError, TypeError):
                pass"""
assert b4_old in r
r = r.replace(b4_old, b4_new, 1)
# B5. sample the book each tick while inventory is unmatched. Placed AFTER
# the deadline work, so nothing reporting can delay a withdrawal, and before
# anything is priced.
b5_old = """        self.maybe_acc_deadline(now)"""
b5_new = """        self.maybe_acc_deadline(now)
        # REPORTING ONLY: the opposite side while we hold unmatched
        # inventory, sampled whether or not an order is resting.
        self.sample_residual_book(
            getattr(self.transport, "market_slug", None)
            if self.transport is not None else None)"""
assert b5_old in r
r = r.replace(b5_old, b5_new, 1)
rf.write_text(r)
print("B: runner wired")
