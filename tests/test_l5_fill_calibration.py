"""The calibration recorder: can it be trusted, and can it hurt anything?

Two properties carry everything else:

  * a recorder fault must never reach the order path;
  * a record with a gap must never read as complete, because a silently
    truncated record is the artefact that would be fed into a calibration
    and believed.

Everything below guards one of those, or one of the five capture
categories the owner specified.
"""
from __future__ import annotations

import io
import json
import os
from decimal import Decimal as D

import pytest

from src import l5_fill_calibration as CAL
from src import l5_maker_fill_model as FM

SLUG = "cpc-btc-updown-15m-2026-10-19-2015z"
T0 = 1792440000.0


def _ladder(bid="0.45", bid_qty="900", ask="0.46", ask_qty="800", t=T0):
    lad = FM.Ladder()
    lad.ingest({"market_events": [{"marketSlug": SLUG,
                                   "bids": [{"px": bid, "qty": bid_qty}],
                                   "asks": [{"px": ask, "qty": ask_qty}]}]}, t)
    return lad


def _book(bid="0.45", ask="0.46"):
    return {"bid": bid, "ask": ask}


def _print(px, qty="1", t=T0 + 1.0):
    return {"t": t, "px": str(px), "qty": str(qty)}


class _Clock:
    """A settable stand-in for the recorder's own `read_t` source.

    The recorder reads the instant it looked at the book from its OWN
    clock, not from the `now` a caller passes -- deliberately, because the
    frame age is `read_t - frame_t` and those are two independent sources.
    The default is wall-clock, which against a fixture dated T0 makes every
    frame tens of days stale. These fixtures mean "the frame is current",
    so the clock is anchored to T0 and moved with the drive.
    """

    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _rec(tmp_path, **kw):
    kw.setdefault("clock", _Clock())
    r = CAL.CalibrationRecorder(str(tmp_path), **kw)
    r._clock = kw["clock"]
    return r


def _full(r, coid="c1", *, px="0.45", qty="1", side=FM.UP, lad=None,
          book=None, t=T0):
    """Drive a complete, usable record end to end."""
    if getattr(r, "_clock", None) is not None and hasattr(r._clock, "t"):
        r._clock.t = t
    r.pre_submit(coid, slug=SLUG, side=side, own_px=px, qty=qty,
                 book=book if book is not None else _book(),
                 ladder=lad if lad is not None else _ladder(), now=t)
    r.on_ack(coid, "V-1", now=t + 0.2)
    # TWO observations, because one is not lifetime coverage: the gap check
    # between a single point and itself is vacuous, and MIN_LIFETIME_
    # OBSERVATIONS now requires two. This fixture claims to drive a
    # COMPLETE record, so it has to satisfy the documented requirement
    # rather than the weaker one the code used to enforce.
    for _dt in (5.0, 9.0):
        if getattr(r, "_clock", None) is not None and hasattr(r._clock, "t"):
            r._clock.t = t + _dt
        r.observe(t + _dt, slug=SLUG, book=_book(),
                  ladder=_ladder(t=t + _dt),
                  prints=[_print(px, "5", t + _dt - 1.0)], open_coids=[coid])
    r.on_terminal(coid, state="CANCELLED", reason="deadline",
                  venue_confirmed=True, now=t + 10.0)
    return coid


# ----------------------------------------------- it cannot touch the order path

def test_it_holds_no_transport_credentials_or_executor(tmp_path):
    r = _rec(tmp_path)
    for bad in ("submit", "place", "cancel", "amend", "transport",
                "executor", "credentials", "arm", "disarm"):
        assert not any(bad in a.lower() for a in vars(r)), (bad, vars(r))


@pytest.mark.parametrize("call,args", [
    ("pre_submit", ("c",)),
    ("on_ack", ("c", "V")),
    ("on_terminal", ("c",)),
    ("observe", (T0,)),
])
def test_no_entry_point_raises_on_garbage(tmp_path, call, args):
    """A recorder fault must not reach the order path, so every entry point
    swallows and counts rather than propagating."""
    r = _rec(tmp_path)
    fn = getattr(r, call)
    kw = {"slug": object(), "side": object(), "own_px": object(),
          "qty": object(), "book": object(), "ladder": object(),
          "now": object(), "state": object(), "open_coids": object()}
    import inspect
    sig = inspect.signature(fn)
    # drop any name the positional args already bind, so this exercises the
    # containment rather than tripping a TypeError before reaching it
    bound = set(list(sig.parameters)[:len(args)])
    allowed = set(sig.parameters) - bound
    fn(*args, **{k: v for k, v in kw.items() if k in allowed})
    # it recorded a problem instead of raising
    assert isinstance(r.errors, dict)


def test_an_unwritable_ledger_does_not_raise_and_is_reported(tmp_path):
    r = _rec(tmp_path, path=str(tmp_path / "nodir" / "x" / "f.jsonl"))
    os.makedirs(str(tmp_path / "nodir"), exist_ok=True)
    io.open(str(tmp_path / "nodir" / "x"), "w").write("not a directory")
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(), now=T0)
    assert r.errors.get("ledger_write_failed", 0) >= 1
    # and the record reports the write failure rather than implying it persisted
    assert r.completeness("c1")["write_failures"] >= 1


# --------------------------------------------- a gap never reads as complete

def test_a_complete_record_is_usable(tmp_path):
    r = _rec(tmp_path)
    c = _full(r)
    comp = r.completeness(c)
    assert comp["usable_for_calibration"] is True, comp
    assert comp["missing"] == []
    assert set(comp["capture"]) == set(CAL.REQUIRED_CAPTURE)


def test_missing_market_data_makes_the_record_unusable_and_says_why(tmp_path):
    """No ladder: the queue estimate cannot exist, and the record must not
    pretend the order met a known queue."""
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=None, now=T0)
    comp = r.completeness("c1")
    assert comp["usable_for_calibration"] is False
    assert "queue_estimate" in comp["missing"]
    assert comp["capture"]["queue_estimate"] == "MISSING:no_ladder"
    q = r.record("c1")["pre_submit"]["queue"]
    assert q["queue_ahead"] is None and q["why"] == "no_ladder"
    assert q["our_position_within_level"] == "UNKNOWN"


def test_a_missing_book_is_not_silently_tolerated(tmp_path):
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book={"bid": None, "ask": None}, ladder=_ladder(), now=T0)
    comp = r.completeness("c1")
    assert comp["capture"]["pre_submit_book"] == "MISSING:no_book_snapshot"
    assert comp["usable_for_calibration"] is False


def test_an_unconfirmed_terminal_state_is_not_accepted(tmp_path):
    r = _rec(tmp_path)
    c = _full(r)
    r.on_terminal(c, state="CANCELLED", venue_confirmed=False, now=T0 + 20)
    comp = r.completeness(c)
    assert comp["capture"]["terminal_state"] == \
        "MISSING:terminal_not_venue_confirmed"
    assert comp["usable_for_calibration"] is False


def test_a_coverage_hole_is_declared_not_smoothed_over(tmp_path):
    """Two observations far apart do not describe a continuously observed
    order, and the record must not imply they do."""
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(), now=T0)
    r.observe(T0 + 1.0, slug=SLUG, book=_book(), ladder=_ladder(),
              prints=[], open_coids=["c1"])
    r.observe(T0 + 1.0 + CAL.MAX_OBSERVATION_GAP_S + 5.0, slug=SLUG,
              book=_book(), ladder=_ladder(), prints=[], open_coids=["c1"])
    comp = r.completeness("c1")
    assert comp["capture"]["lifetime_observation"] == "MISSING:coverage_gap"
    cov = r.record("c1")["coverage"]
    assert cov["largest_gap_s"] > CAL.MAX_OBSERVATION_GAP_S
    assert comp["usable_for_calibration"] is False


# ----------------------------------------------------- queue uncertainty

def test_the_queue_estimate_is_a_bound_with_its_provenance(tmp_path):
    r = _rec(tmp_path, clock=lambda: T0)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(bid="0.45", bid_qty="900"),
                 now=T0)
    q = r.record("c1")["pre_submit"]["queue"]
    assert q["provenance"] == "authenticated_ladder_aggregate_depth"
    assert q["our_position_within_level"] == "UNKNOWN"
    assert "bound" in q and "AT MOST" in q["bound"]
    assert q["level_depth"] == "900"
    assert q["frame_t"] == T0 and q["frame_age_s"] == 0.0
    assert q["stale"] is False


def test_a_stale_frame_is_marked_stale(tmp_path):
    r = _rec(tmp_path, clock=lambda: T0)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(),
                 ladder=_ladder(t=T0 - CAL.STALE_FRAME_S - 1.0), now=T0)
    q = r.record("c1")["pre_submit"]["queue"]
    assert q["stale"] is True
    assert q["frame_age_s"] > CAL.STALE_FRAME_S


def test_freshness_is_measured_against_the_read_instant_not_the_caller(
        tmp_path):
    """The defect this guards: the caller's `now` can be a whole window
    behind the instant we actually read the ladder, and measuring against
    it produced frame_age_s = -75.5 reported as stale=False."""
    r = _rec(tmp_path, clock=lambda: T0 + 60.0)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(t=T0 + 59.0),
                 # the caller is a minute behind the read instant
                 now=T0)
    q = r.record("c1")["pre_submit"]["queue"]
    assert q["frame_age_s"] == 1.0 and q["stale"] is False
    # and the caller's own timestamp is still the record's identity
    assert r.record("c1")["pre_submit"]["t"] == T0
    assert r.record("c1")["pre_submit"]["read_t"] == T0 + 60.0


def test_a_frame_newer_than_the_read_instant_is_not_called_fresh(tmp_path):
    """A frame AHEAD of the read instant by more than a thread race means
    the two timestamps are not on one clock. Freshness is then unknown --
    never False, which is what a negative age used to report."""
    r = _rec(tmp_path, clock=lambda: T0)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(t=T0 + 75.5), now=T0)
    q = r.record("c1")["pre_submit"]["queue"]
    assert q["frame_age_s"] == -75.5
    assert q["stale"] is None, "an impossible age must read unknown"
    assert q["why"] == "frame_clock_mismatch"
    assert "not" in q["clock_note"] and "one clock" in q["clock_note"]


def test_a_frame_inside_the_race_tolerance_is_not_stale_and_says_why(
        tmp_path):
    r = _rec(tmp_path, clock=lambda: T0)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(t=T0 + 0.06), now=T0)
    q = r.record("c1")["pre_submit"]["queue"]
    assert q["frame_age_s"] == -0.06
    assert q["stale"] is False
    assert "frame_newer_than_read" in q["clock_note"]
    assert "why" not in q


# ------------------------------------------------- the book, and WHEN it was


class _Mirror:
    """The authenticated mirror's shape: snapshot(slug) read on demand."""

    def __init__(self, bid="0.45", ask="0.46", last=T0, calls=None):
        self.bid, self.ask, self.last = bid, ask, last
        self.calls = calls if calls is not None else []

    def snapshot(self, slug):
        self.calls.append(slug)
        if self.bid is None:
            return None
        return {"bid": self.bid, "ask": self.ask, "last_update_t": self.last,
                "stream_age_s": 0.1, "book_updates": 12,
                "source": "authenticated_ws"}


def test_the_mirror_is_snapshotted_at_the_submission_instant(tmp_path):
    """The money path's hook used to receive a snapshot taken earlier in
    the tick, or None on the first tick of a process. Given the mirror,
    the recorder reads it itself, at the wire."""
    mir = _Mirror()
    r = _rec(tmp_path, clock=lambda: T0 + 2.0)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=mir, ladder=_ladder(), now=T0)
    assert mir.calls == [SLUG], "the mirror must be read, once, here"
    b = r.record("c1")["pre_submit"]["book"]
    assert b["capture_source"] == "mirror_snapshot_at_read"
    assert b["observed_t"] == T0 + 2.0
    assert b["last_update_t"] == T0 and b["age_s"] == 2.0
    assert r.completeness("c1")["capture"]["pre_submit_book"] == CAL.PRESENT


def test_a_book_block_always_carries_when_it_was_true(tmp_path):
    """Without a timestamp a book one tick old is indistinguishable from
    one read at the wire, so book staleness was unmeasurable."""
    r = _rec(tmp_path, clock=lambda: T0 + 0.5)
    snap = {"bid": "0.45", "ask": "0.46", "last_update_t": T0 - 3.0,
            "stream_age_s": 0.2, "book_updates": 7,
            "source": "authenticated_ws"}
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=snap, ladder=_ladder(), now=T0)
    b = r.record("c1")["pre_submit"]["book"]
    assert b["capture_source"] == "caller_supplied_mapping"
    assert b["observed_t"] == T0 + 0.5
    assert b["age_s"] == 3.5 and b["last_update_t"] == T0 - 3.0
    assert b["feed_source"] == "authenticated_ws" and b["book_updates"] == 7


def test_an_empty_mirror_reads_as_a_missing_book_not_an_empty_one(tmp_path):
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_Mirror(bid=None), ladder=_ladder(), now=T0)
    comp = r.completeness("c1")
    assert comp["capture"]["pre_submit_book"] == "MISSING:no_book_snapshot"
    assert comp["usable_for_calibration"] is False


def test_a_mirror_that_raises_cannot_reach_the_order_path(tmp_path):
    class _Angry:
        def snapshot(self, slug):
            raise RuntimeError("feed is down")

    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_Angry(), ladder=_ladder(), now=T0)
    assert r.record("c1")["pre_submit"]["book"] is None
    assert any(k.startswith("book_read_failed") for k in r.errors)
    assert r.completeness("c1")["usable_for_calibration"] is False


def test_a_price_outside_published_depth_says_so(tmp_path):
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.05", qty="1",
                 book=_book(), ladder=_ladder(bid="0.45"), now=T0)
    q = r.record("c1")["pre_submit"]["queue"]
    if q["queue_ahead"] is None:
        assert q["why"] == "price_outside_published_depth"
        assert r.completeness("c1")["usable_for_calibration"] is False


def test_the_down_side_queue_is_read_from_the_ask(tmp_path):
    """A NO order rests as an ASK at wire 1 - own_px, and its queue must be
    read from the ask ladder, not the bid."""
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.DOWN, own_px="0.54", qty="1",
                 book=_book(), ladder=_ladder(ask="0.46", ask_qty="800"),
                 now=T0)
    pre = r.record("c1")["pre_submit"]
    assert pre["resting_side"] == FM.ASK
    assert pre["wire_px"] == "0.46"
    assert pre["queue"]["level_depth"] == "800"


# ------------------------------------------------------------ partial fills

def test_partial_fills_accumulate_in_order(tmp_path):
    r = _rec(tmp_path)
    c = _full(r, qty="3")
    r.on_fill(c, qty="1", price="0.45", cumulative_qty="1", remaining="2",
              now=T0 + 6.0)
    r.on_fill(c, qty="2", price="0.45", cumulative_qty="3", remaining="0",
              now=T0 + 7.0)
    fills = r.record(c)["fills"]
    assert [f["qty"] for f in fills] == ["1", "2"]
    assert [f["remaining"] for f in fills] == ["2", "0"]
    assert fills[-1]["cumulative_qty"] == "3"
    # a partially filled order is still a usable calibration record
    assert r.completeness(c)["usable_for_calibration"] is True


def test_prints_at_our_price_are_separated_from_prints_elsewhere(tmp_path):
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(), now=T0)
    r.observe(T0 + 5.0, slug=SLUG, book=_book(), ladder=_ladder(),
              prints=[_print("0.45", "7", T0 + 1.0),
                      _print("0.44", "99", T0 + 2.0),
                      _print("0.45", "3", T0 + 3.0)],
              open_coids=["c1"])
    p = r.record("c1")["observations"][0]["prints_at_our_price"]
    assert p["at_our_price"] == 2
    assert p["at_our_price_qty"] == "10"
    assert p["elsewhere"] == 1


def test_absent_prints_are_marked_unavailable_not_zero(tmp_path):
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(), now=T0)
    r.observe(T0 + 5.0, slug=SLUG, book=_book(), ladder=_ladder(),
              prints=None, open_coids=["c1"])
    p = r.record("c1")["observations"][0]["prints_at_our_price"]
    assert p["unavailable"] is True


# ------------------------------------------------------------------ restarts

def test_a_restart_rebuilds_the_record_from_the_ledger(tmp_path):
    r = _rec(tmp_path)
    c = _full(r)
    assert r.completeness(c)["usable_for_calibration"] is True

    again = _rec(tmp_path)
    assert c in again.orders
    comp = again.completeness(c)
    assert comp["usable_for_calibration"] is True, comp
    assert again.record(c)["pre_submit"]["queue"]["level_depth"] == "900"
    assert again.record(c)["terminal"]["state"] == "CANCELLED"


def test_a_malformed_ledger_line_is_skipped_and_counted(tmp_path):
    r = _rec(tmp_path)
    _full(r)
    with io.open(r.path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    again = _rec(tmp_path)
    assert again.errors.get("ledger_line_unreadable", 0) == 1
    # the good record still folded
    assert again.completeness("c1")["usable_for_calibration"] is True


def test_a_record_that_straddles_a_restart_mid_life_is_not_usable(tmp_path):
    """Submitted before the restart, never observed after it: the lifetime
    coverage is absent and the record must say so."""
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(), now=T0)
    again = _rec(tmp_path)
    comp = again.completeness("c1")
    assert comp["capture"]["lifetime_observation"] == "MISSING:no_observations"
    assert comp["capture"]["terminal_state"] == "MISSING:not_captured"
    assert comp["usable_for_calibration"] is False


# -------------------------------------------- fees: two columns, never one

def test_the_fee_estimate_is_schedule_derived_and_the_actual_is_pending(tmp_path):
    r = _rec(tmp_path)
    r.pre_submit("c1", slug=SLUG, side=FM.UP, own_px="0.30", qty="1",
                 book=_book(), ladder=_ladder(), now=T0)
    f = r.record("c1")["pre_submit"]["fees"]
    assert f["venue_actual"] is None
    assert f["venue_actual_status"] == "PENDING"
    est = f["schedule_estimate"]
    #: ZERO, not 0.0026. The estimate is schedule-derived, and this
    #: account is Maker Tier 1 after the 2026-11-14 re-tier, which is
    #: credited nothing. A non-zero estimate here would be the engine
    #: booking income it will not receive.
    assert D(est["maker_rebate_usd"]) == D("0")
    assert est["maker_fee_usd"] == "0.0000"
    assert "no" in est["maker_fee_basis"] and "maker-fee" in est["maker_fee_basis"]
    assert est["provenance"]


def test_only_a_venue_statement_fills_the_actual_column(tmp_path):
    r = _rec(tmp_path)
    c = _full(r)
    r.on_fees_actual(c, fee_usd="0.0000", rebate_usd="0.0026",
                     source="venue_statement", now=T0 + 60)
    a = r.record(c)["fees_actual"]
    assert a["source"] == "venue_statement"
    assert a["rebate_usd"] == "0.0026"
    # and the estimate is still its own separate column
    assert r.record(c)["pre_submit"]["fees"]["venue_actual"] is None


# ------------------------------------------------- settlement reconciliation

def test_settlement_is_recorded_against_the_order(tmp_path):
    r = _rec(tmp_path)
    c = _full(r)
    r.on_fill(c, qty="1", price="0.45", cumulative_qty="1", remaining="0",
              now=T0 + 6.0)
    r.on_settlement(c, slug=SLUG, settlement_px="1.0000",
                    venue_state="MARKET_STATE_EXPIRED", now=T0 + 900)
    s = r.record(c)["settlement"]
    assert s["settlement_px"] == "1.0000"
    assert s["venue_state"] == "MARKET_STATE_EXPIRED"
    assert r.completeness(c)["usable_for_calibration"] is True


def test_precapture_ok_is_the_probes_hook_not_a_veto(tmp_path):
    """The recorder never refuses a submission. It exposes whether the
    pre-submission half is sound so a probe can decline."""
    r = _rec(tmp_path)
    r.pre_submit("good", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=_ladder(), now=T0)
    r.pre_submit("blind", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=None, now=T0)
    assert r.precapture_ok("good") is True
    assert r.precapture_ok("blind") is False
    assert r.precapture_ok("never-seen") is False
    # and nothing about the recorder can refuse: it has no such method
    assert not hasattr(r, "refuse")
    assert not hasattr(r, "veto")


def test_the_report_counts_usable_against_not_usable(tmp_path):
    r = _rec(tmp_path)
    _full(r, "c1")
    r.pre_submit("c2", slug=SLUG, side=FM.UP, own_px="0.45", qty="1",
                 book=_book(), ladder=None, now=T0)
    rep = r.report()
    assert rep["orders"] == 2
    assert rep["usable_for_calibration"] == 1
    assert rep["not_usable"] == 1
    assert "no transport" in rep["labels"]


# ============================================ WIRED, AND UNABLE TO INTERFERE

def test_the_presubmit_hook_is_contained_and_precedes_the_wire():
    """The structural guarantee, asserted on the source rather than hoped
    for: the capture call sits INSIDE a try/except and BEFORE the wire.

    If it ever moves outside the handler, a recorder fault becomes a
    money-path exception. If it ever moves after the wire, it stops being
    a PRE-submission capture and the queue it records is the queue after
    our own order joined it.
    """
    import ast
    import io as _io
    src = _io.open("src/l5_live_execution.py", encoding="utf-8").read()
    tree = ast.parse(src)

    def calls(node, name):
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == name:
                out.append(n)
        return out

    pre = calls(tree, "pre_submit")
    assert len(pre) == 1, "expected exactly one pre-submission capture site"
    site = pre[0]

    # contained: some enclosing Try has it in its body
    contained = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Try):
            for sub in ast.walk(n):
                if sub is site:
                    contained = True
                    break
    assert contained, "the capture call is not inside a try/except"

    # and it precedes the wire
    subs = [c for c in calls(tree, "submit")
            if isinstance(c.func.value, ast.Attribute)
            and c.func.value.attr == "transport"]
    assert subs, "could not find the transport submit site"
    assert site.lineno < min(s.lineno for s in subs), (
        "the capture must happen BEFORE the order is sent")


def test_the_recorder_has_no_veto_surface_the_executor_could_consult():
    """The recorder must not be able to refuse. If it grew a method the
    submission path might call to ask permission, the dangerous coupling
    would be one edit away."""
    import io as _io
    r = CAL.CalibrationRecorder
    for banned in ("refuse", "veto", "block", "allow", "gate", "approve"):
        assert not hasattr(r, banned), banned
    # and the submission path never CALLS the recorder's advisory, which a
    # substring check cannot tell from a comment mentioning it
    import ast as _ast
    src = _io.open("src/l5_live_execution.py", encoding="utf-8").read()
    called = {n.func.attr for n in _ast.walk(_ast.parse(src))
              if isinstance(n, _ast.Call)
              and isinstance(n.func, _ast.Attribute)}
    assert "precapture_ok" not in called, (
        "the submission path consults the recorder; that is a veto")


def test_the_runner_builds_the_recorder_and_hands_it_to_the_executor(
        tmp_path, monkeypatch):
    from tests.test_l5_shadow_controller_wiring import build  # reuse
    runner = build(tmp_path, monkeypatch)
    assert runner.fill_calibration is not None
    if runner.executor is not None:
        assert runner.executor.fill_calibration is runner.fill_calibration
    # DECLARED ON THE EXECUTOR'S OWN SURFACE, not invented by the runner.
    # Asserted on a real instance, because that is what the graft guard
    # checks and what the container actually builds.
    import tempfile as _tf
    import os as _os
    from src import l5_live_execution as LX
    from src import l5_pmus_transport as _T
    from src import l5_pmus_live_runner as _R
    with _tf.TemporaryDirectory() as d:
        ex = LX.FaithfulExecutor(
            LX.InventoryStore(_os.path.join(d, "inv.json")),
            LX.SafetyLimits(**_R.PROBE_LIMITS),
            transport=_T.PmusTransport(market_slug="cpc-btc-updown-15m-x"))
        for a in ("fill_calibration", "calib_book", "calib_ladder"):
            assert hasattr(ex, a), a
            assert getattr(ex, a) is None, (a, "must default to absent")


def test_the_money_paths_first_intent_is_not_blind(tmp_path, monkeypatch):
    """THE DEFECT THIS GUARDS. `shadow_tick` runs LAST in the tick, and the
    money path's pre-submit hook fires earlier, so the first intents of a
    process were captured with `book: null` and `queue: no_ladder`. `roll`
    now points the executor at the mirror and the window's ladder, so the
    capture is armed before anything can submit.
    """
    from collections.abc import Mapping as _Map
    from tests.test_l5_shadow_controller_wiring import build, NOW
    runner = build(tmp_path, monkeypatch)
    # the window roll builds the executor; NO shadow tick has run yet, which
    # is exactly the state the money path's first intent would fire in
    runner.roll(NOW)
    assert runner.executor is not None
    ex = runner.executor
    assert ex.calib_book is not None, "the first intent would capture no book"
    assert not isinstance(ex.calib_book, _Map), (
        "a snapshot goes stale; the hook needs the mirror itself")
    assert callable(getattr(ex.calib_book, "snapshot", None))
    assert ex.calib_book is runner.book
    assert ex.calib_ladder is runner.shadow_ladder
    # and a tick does not replace them with a snapshot
    runner.tick(NOW)
    assert runner.executor.calib_book is runner.book
    assert not isinstance(runner.executor.calib_book, _Map)


def test_the_shadow_path_records_real_market_data_but_never_looks_complete(
        tmp_path, monkeypatch):
    """The production verification: the mechanism runs against the real
    authenticated ladder, and every shadow record reports itself NOT usable
    because no venue confirmed anything."""
    from tests.test_l5_shadow_controller_wiring import build, capture, NOW
    runner = build(tmp_path, monkeypatch)
    capture(monkeypatch)
    runner.tick(NOW)
    cal = runner.fill_calibration
    rep = cal.report()
    # NOT VACUOUS: the shadow path must actually have opened a record, or
    # this test proves nothing about the mechanism. A pass with zero orders
    # is the failure mode this assertion exists to catch.
    assert rep["orders"] >= 1, (
        "the shadow calibration path recorded nothing; the test would "
        "otherwise pass vacuously")
    assert rep["usable_for_calibration"] == 0, rep
    # and the real ladder was read: a queue block with its provenance
    first = cal.record(sorted(cal.orders)[0])
    q = (first.get("pre_submit") or {}).get("queue") or {}
    assert q.get("provenance") == "authenticated_ladder_aggregate_depth"
    assert q.get("our_position_within_level") == "UNKNOWN"
    for coid in cal.orders:
        comp = cal.completeness(coid)
        assert comp["usable_for_calibration"] is False
        assert "terminal_state" in comp["missing"] or \
            "lifetime_observation" in comp["missing"], comp


def test_the_heartbeat_carries_the_calibration_report(tmp_path, monkeypatch):
    from tests.test_l5_shadow_controller_wiring import build, capture, NOW
    runner = build(tmp_path, monkeypatch)
    out = capture(monkeypatch)
    runner.tick(NOW)
    runner.heartbeat()
    hbs = [d for k, d in out if k == "L5_LIVE_HB"]
    assert hbs, [k for k, _ in out]
    block = hbs[-1].get("fill_calibration")
    assert block is not None
    assert set(block["required_capture"]) == set(CAL.REQUIRED_CAPTURE)
    assert "no transport" in block["labels"]
