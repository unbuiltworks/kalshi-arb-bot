"""THE ORDER OBSERVER: is `events_appended: 0` a quiet system or a broken one?

PR #208 PRIORITY 2. The owner's question, exactly: distinguish "legitimately
no live orders while disarmed" from "a broken recorder", and prove the
lifecycle durably records ACK / rest / partial / full fill / cancel /
settlement and survives a restart -- WITHOUT submitting a real order.

THE ANSWER IS BOTH, and the second half is the one that matters.

  * The zero IS legitimate. Every producer sits on the submission path
    (`pre_submit`, `on_ack`) or the venue order-progress path (`on_fill`,
    `on_fees_actual`, `on_terminal`). Disarmed, nothing submits, so nothing
    appends. That is a quiet system, not a broken one.

  * AND the recorder is incomplete in one decisive way. `lifetime_observation`
    is one of the five REQUIRED_CAPTURE categories and its only producer is
    `CalibrationRecorder.observe()`, which NOTHING CALLS. So a live window
    would produce a record with four of five categories present,
    `usable_for_calibration: false`, and -- the part that matters -- no
    `prints_at_our_price`, which is the single field the whole queue-position
    question needs.

That is exactly what my own proposed pre-flight was too weak to catch:
"verify the recorder appends" would have passed on `pre_submit` alone and
still spent the window for an unusable record.

Everything below drives the recorder in-process with synthetic events. No
transport, no credentials, no order, no network.
"""
from __future__ import annotations

import inspect
import json
import os
from decimal import Decimal as D

import pytest

from src import l5_fill_calibration as CALIB
from src import l5_live_execution as LX
from src import l5_pmus_live_runner as RUN

SLUG = "cpc-btc-updown-15m-2026-11-08-0300z"
COID = "l5f-synthetic0001"


class FakeLadder:
    """The three methods `_queue_block` calls, and nothing else."""

    def __init__(self, ahead="120.0", depth="300.0", t=1000.0):
        self.books = {SLUG: {"t": t, "updates": 42}}
        self._ahead, self._depth = D(ahead), D(depth)

    def ahead_of_us(self, slug, side, wire):
        return self._ahead

    def depth_at(self, slug, side, wire):
        return self._depth


def book(bid="0.4400", ask="0.4500", t=1000.0):
    return {"bid": bid, "ask": ask, "t": t, "observed_t": t,
            "last_update_t": t, "updates": 42}


def recorder(tmp_path, clock=None):
    return CALIB.CalibrationRecorder(str(tmp_path),
                                     clock=clock or (lambda: 1000.0))


def drive_full_lifecycle(cal, *, coid=COID, qty="1", partials=(("1", True),),
                         observations=3, terminal="ORDER_STATE_FILLED",
                         settle=True):
    """pre_submit -> ack -> rest (observed) -> fill(s) -> terminal -> settle."""
    cal.pre_submit(coid, slug=SLUG, side="UP", own_px="0.4400", qty=qty,
                   book=book(), ladder=FakeLadder(), now=1000.0)
    cal.on_ack(coid, "VENUE-ORDER-1")
    for k in range(observations):
        cal.observe(1001.0 + k * 2.0, slug=SLUG, book=book(t=1001.0 + k * 2.0),
                    ladder=FakeLadder(t=1001.0 + k * 2.0),
                    prints=[{"t": 1001.0 + k * 2.0, "px": "0.4400",
                             "qty": "5"}],
                    open_coids=[coid])
    cum = D(0)
    for amount, _last in partials:
        cum += D(amount)
        cal.on_fill(coid, qty=amount, price="0.4400", cumulative_qty=str(cum),
                    remaining=str(D(qty) - cum))
    cal.on_fees_actual(coid, fee_usd=D("0"),
                       source="venue_order_progress.commission")
    cal.on_terminal(coid, state=terminal, reason="venue_order_progress",
                    venue_confirmed=True)
    if settle:
        cal.on_settlement(coid, slug=SLUG, settlement_px="1")
    return cal


# =====================================================================
# 1. DRIVEN, THE RECORDER WORKS AND THE RECORD IS USABLE.
# =====================================================================

def test_a_driven_lifecycle_produces_a_usable_record(tmp_path):
    cal = drive_full_lifecycle(recorder(tmp_path))
    c = cal.completeness(COID)
    assert c["known"] is True
    assert c["missing"] == [], c
    assert c["usable_for_calibration"] is True
    assert set(c["capture"]) == set(CALIB.REQUIRED_CAPTURE)
    assert all(v == CALIB.PRESENT for v in c["capture"].values())
    r = cal.report()
    assert r["orders"] == 1 and r["usable_for_calibration"] == 1
    assert r["events_appended"] > 0
    assert r["errors"] == {}


def test_the_observation_is_the_only_source_of_the_lifetime_category(tmp_path):
    """Drive everything EXCEPT `observe` and the record is not usable."""
    cal = recorder(tmp_path)
    drive_full_lifecycle(cal, observations=0)
    c = cal.completeness(COID)
    assert c["missing"] == ["lifetime_observation"], c
    assert c["usable_for_calibration"] is False
    # and the pre-submission half is still sound, which is the distinction
    # `precapture_ok` exists to draw
    assert cal.precapture_ok(COID) is True


def test_partial_fills_are_recorded_as_successive_fills(tmp_path):
    cal = recorder(tmp_path)
    drive_full_lifecycle(cal, qty="3",
                         partials=(("1", False), ("1", False), ("1", True)))
    rec = cal.record(COID)
    assert len(rec["fills"]) == 3
    assert [str(D(f["qty"])) for f in rec["fills"]] == ["1", "1", "1"]
    assert str(D(rec["fills"][-1]["cumulative_qty"])) == "3"
    assert rec["completeness"]["usable_for_calibration"] is True


def test_a_cancel_is_a_venue_confirmed_terminal_and_still_usable(tmp_path):
    """A cancelled order is evidence too: it says the queue did NOT clear."""
    cal = recorder(tmp_path)
    drive_full_lifecycle(cal, partials=(), terminal="ORDER_STATE_CANCELED",
                         settle=False)
    rec = cal.record(COID)
    assert rec["fills"] == []
    assert rec["completeness"]["usable_for_calibration"] is True
    assert rec["capture"]["terminal_state"] == CALIB.PRESENT


def test_settlement_is_recorded_when_driven(tmp_path):
    cal = drive_full_lifecycle(recorder(tmp_path))
    raw = [json.loads(ln) for ln in
           open(os.path.join(str(tmp_path), "fill_calibration.jsonl"))
           if ln.strip()]
    kinds = [e.get("kind") for e in raw]
    assert "settlement" in kinds, kinds


# =====================================================================
# 2. DURABILITY AND RESTART.
# =====================================================================

def test_every_event_reaches_disk_and_a_restart_rebuilds_the_record(tmp_path):
    cal = drive_full_lifecycle(recorder(tmp_path))
    before = cal.record(COID)
    appended = cal.report()["events_appended"]
    path = os.path.join(str(tmp_path), "fill_calibration.jsonl")
    lines = [ln for ln in open(path) if ln.strip()]
    assert len(lines) == appended

    reopened = recorder(tmp_path)                 # a restart
    after = reopened.record(COID)
    assert after is not None
    assert after["completeness"]["usable_for_calibration"] is True
    assert len(after["fills"]) == len(before["fills"])
    assert after["capture"] == before["capture"]
    # a replayed fold does not re-append: the file is not duplicated
    assert reopened.report()["events_appended"] == 0
    assert len([ln for ln in open(path) if ln.strip()]) == len(lines)


def test_a_restart_in_the_middle_of_an_order_is_marked_not_stitched(tmp_path):
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    cal.on_ack(COID, "VENUE-ORDER-1")
    reopened = recorder(tmp_path)                 # restart mid-flight
    assert reopened.record(COID) is not None
    # RELOADING ALONE IS NOT A BOUNDARY, or every restart would invent one
    # for every order that ever existed.
    assert reopened.record(COID)["restart_boundaries"] == 0
    # the boundary is the first LIVE event after the replay: the record's
    # events now demonstrably span two processes.
    reopened.observe(1010.0, slug=SLUG, book=book(t=1010.0),
                     ladder=FakeLadder(t=1010.0), prints=[],
                     open_coids=[COID])
    rec = reopened.record(COID)
    assert rec["observations"]
    assert rec["restart_boundaries"] == 1
    assert reopened.report()["orders_straddling_a_restart"] == 1
    # and a SECOND live event does not count a second boundary
    reopened.on_terminal(COID, state="ORDER_STATE_CANCELED",
                         reason="t", venue_confirmed=True)
    assert reopened.record(COID)["restart_boundaries"] == 1


def test_a_record_written_in_one_process_is_not_marked_as_straddling(tmp_path):
    cal = drive_full_lifecycle(recorder(tmp_path))
    assert cal.record(COID)["restart_boundaries"] == 0
    assert cal.report()["orders_straddling_a_restart"] == 0


def test_the_private_replay_flag_is_not_published_in_the_record(tmp_path):
    cal = drive_full_lifecycle(recorder(tmp_path))
    reopened = recorder(tmp_path)
    rec = reopened.record(COID)
    assert not any(k.startswith("_") for k in rec), sorted(rec)


def test_a_malformed_ledger_line_is_skipped_and_counted(tmp_path):
    cal = drive_full_lifecycle(recorder(tmp_path))
    path = os.path.join(str(tmp_path), "fill_calibration.jsonl")
    with open(path, "a") as fh:
        fh.write("{not json\n")
    reopened = recorder(tmp_path)
    assert reopened.errors.get("ledger_line_unreadable") == 1
    assert reopened.record(COID)["completeness"][
        "usable_for_calibration"] is True


# =====================================================================
# 3. THE RECORD SAYS WHAT IT DOES NOT KNOW.
# =====================================================================

def test_an_observation_gap_is_reported_rather_than_smoothed(tmp_path):
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    gap = CALIB.MAX_OBSERVATION_GAP_S + 5.0
    for t in (1001.0, 1001.0 + gap):
        cal.observe(t, slug=SLUG, book=book(t=t), ladder=FakeLadder(t=t),
                    prints=[], open_coids=[COID])
    rec = cal.record(COID)
    assert rec["capture"]["lifetime_observation"].startswith("MISSING")
    assert "coverage_gap" in rec["capture"]["lifetime_observation"]
    assert rec["coverage"]["gaps_over_%gs" % CALIB.MAX_OBSERVATION_GAP_S] == 1


def test_prints_are_credited_only_at_our_exact_price(tmp_path):
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    cal.observe(1002.0, slug=SLUG, book=book(t=1002.0),
                ladder=FakeLadder(t=1002.0),
                prints=[{"t": 1001.0, "px": "0.4400", "qty": "7"},
                        {"t": 1001.5, "px": "0.4300", "qty": "9"}],
                open_coids=[COID])
    obs = cal.record(COID)["observations"][-1]
    p = obs["prints_at_our_price"]
    assert p["at_our_price"] == 1 and D(p["at_our_price_qty"]) == D("7")
    assert p["elsewhere"] == 1
    assert p["unavailable"] is False


def test_an_absent_print_feed_is_said_to_be_absent(tmp_path):
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    cal.observe(1002.0, slug=SLUG, book=book(t=1002.0),
                ladder=FakeLadder(t=1002.0), prints=None, open_coids=[COID])
    p = cal.record(COID)["observations"][-1]["prints_at_our_price"]
    assert p["unavailable"] is True and p["at_our_price"] == 0


def test_the_queue_reading_is_a_bound_and_says_so(tmp_path):
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(ahead="500"), now=1000.0)
    q = cal.record(COID)["pre_submit"]["queue"]
    assert D(q["queue_ahead"]) == D("500")
    assert q["our_position_within_level"] == "UNKNOWN"
    assert "bound and not" in q["bound"]


# =====================================================================
# 4. A RECORDER FAULT CANNOT REACH THE CALLER.
# =====================================================================

@pytest.mark.parametrize("call", [
    lambda c: c.pre_submit(COID, slug=SLUG, side="UP", own_px=object(),
                           qty=object(), book=object(), ladder=object()),
    lambda c: c.on_ack(COID, object()),
    lambda c: c.on_fill(COID, qty=object(), price=object()),
    lambda c: c.on_terminal(COID, state=object()),
    lambda c: c.on_settlement(COID, slug=SLUG, settlement_px=object()),
    lambda c: c.on_fees_actual(COID, fee_usd=object()),
    lambda c: c.observe(object(), slug=SLUG, open_coids=object()),
])
def test_no_entry_point_raises_whatever_it_is_handed(tmp_path, call):
    cal = recorder(tmp_path)
    call(cal)                                      # must not raise
    assert isinstance(cal.report()["errors"], dict)


# =====================================================================
# 5. WHAT THE MONEY PATH ACTUALLY CALLS. The defect, pinned.
# =====================================================================

def _flat(mod):
    """Source with runs of whitespace collapsed, so a call split across
    lines still matches. `_calib_safe(\n "on_fill", ...)` does not contain
    the contiguous string `_calib_safe("on_fill"`, and asserting on the
    unflattened source read as "not wired" when it was."""
    return " ".join(inspect.getsource(mod).split())


def test_the_money_path_wires_every_venue_confirmed_hook():
    """`events_appended: 0` is legitimate BECAUSE these are the producers
    and every one of them needs a live order."""
    src = _flat(LX)
    assert "cal.pre_submit(" in src                 # before the wire
    assert "_cal.on_ack(" in src                    # after the ack
    for hook in ("on_fill", "on_fees_actual", "on_terminal"):
        assert '_calib_safe( "%s"' % hook in src or \
               '_calib_safe("%s"' % hook in src, hook


def test_live_resting_orders_are_observed_and_not_only_shadow_ones():
    """THE DEFECT THIS FIXES. `observe` is the only producer of
    `lifetime_observation` -- a REQUIRED category, and the only source of
    `prints_at_our_price`. It was reached ONLY from
    `calibration_shadow_tick`, with `shadow|...` coids, so:

        shadow records  observations, no venue-confirmed terminal
        live records    ack/fill/fees/terminal, NO observations

    Neither half could ever be usable and the two never met. An `l5f-`
    order that rested for two minutes produced four of five categories and
    not one reading of the queue it sat in.
    """
    assert "lifetime_observation" in CALIB.REQUIRED_CAPTURE
    src = _flat(RUN)
    assert "def calibration_live_tick(" in src
    assert "self.calibration_live_tick(" in src
    # it observes LIVE coids, not shadow ones
    assert 'startswith("l5f-")' in src
    # and it is called from the same tick, on the same inputs
    assert ("self.calibration_shadow_tick(now, slug=slug, book=book, "
            "prints=prints) " in src)


def test_the_live_observer_is_observational_and_cannot_refuse():
    """It must be unable to affect a submission, like every recorder call."""
    fn = inspect.getsource(RUN.Runner.calibration_live_tick)
    assert "return None" not in fn or "refuse" not in fn
    assert "submit" not in fn and "cancel" not in fn
    assert "BaseException" in fn                    # swallows its own faults
    assert "KeyboardInterrupt" in fn                # but not these


def test_settlement_is_implemented_and_still_uncalled():
    """Not required for `usable_for_calibration`, so a smaller gap --
    recorded so it is not mistaken for wired."""
    assert hasattr(CALIB.CalibrationRecorder, "on_settlement")
    assert "on_settlement" not in CALIB.REQUIRED_CAPTURE
    for mod in (LX, RUN):
        assert '_calib_safe("on_settlement"' not in inspect.getsource(mod)


# =====================================================================
# 6. THE NEW WIRING, EXERCISED rather than only grepped for.
# =====================================================================

class FakeStore:
    def __init__(self, coids):
        self._coids = dict.fromkeys(coids, {})

    def live_orders(self):
        return dict(self._coids)


class FakeExecutor:
    def __init__(self, store):
        self.store = store


class FakeBook:
    def __init__(self, t=1000.0, prints=(), raises=False):
        self._t, self._prints, self._raises = t, list(prints), raises

    def snapshot(self, slug):
        return book(t=self._t)

    def trades(self, slug):
        if self._raises:
            raise RuntimeError("book lock")
        return list(self._prints)


class FakeTransport:
    def __init__(self, slug=SLUG):
        self.market_slug = slug


class Bare:
    """Exactly the attributes `calibration_live_tick` reads, and no more.

    Bound as an unbound function so the method is exercised as the runner
    holds it, without constructing a Runner (which would need a volume and
    an authorization chain).
    """

    calibration_live_tick = RUN.Runner.calibration_live_tick

    def __init__(self, cal, coids, ladder, *, slug=SLUG, prints=(),
                 book_raises=False, t=1000.0):
        self.fill_calibration = cal
        self.executor = FakeExecutor(FakeStore(coids))
        self.shadow_ladder = ladder
        self.transport = FakeTransport(slug)
        self.book = FakeBook(t=t, prints=prints, raises=book_raises)
        self.stats = __import__("collections").Counter()


def test_the_live_tick_observes_a_resting_order_and_completes_the_record(tmp_path):
    """END TO END on the fixed path: a live `l5f-` order that rests now gets
    the observations it never used to, so its record becomes usable."""
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    cal.on_ack(COID, "VENUE-ORDER-1")
    for t in (1002.0, 1004.0, 1006.0):
        r = Bare(cal, [COID], FakeLadder(), t=t,
                 prints=[{"t": t, "px": "0.4400", "qty": "3"}])
        r.calibration_live_tick(t)
    assert r.stats["calib_live_observations"] == 1   # this tick
    assert r.stats["calib_live_orders_seen"] == 1
    assert len(cal.record(COID)["observations"]) == 3
    assert cal.record(COID)["capture"]["lifetime_observation"] == CALIB.PRESENT
    # finish it the way the money path does
    cal.on_fill(COID, qty="1", price="0.4400", cumulative_qty="1",
                remaining="0")
    cal.on_fees_actual(COID, fee_usd=D("0"), source="venue")
    cal.on_terminal(COID, state="ORDER_STATE_FILLED", reason="venue",
                    venue_confirmed=True)
    assert cal.completeness(COID)["usable_for_calibration"] is True


def test_the_live_tick_ignores_shadow_coids(tmp_path):
    """Shadow records are the rehearsal; they must not be fed live
    observations and counted as if a venue held them."""
    cal = recorder(tmp_path)
    shadow = "shadow|BASELINE|%s|1" % SLUG
    cal.pre_submit(shadow, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    r = Bare(cal, [shadow], FakeLadder(), t=1002.0)
    r.calibration_live_tick(1002.0)
    assert r.stats["calib_live_observations"] == 0
    assert cal.record(shadow)["observations"] == []


def test_the_live_tick_does_nothing_without_live_orders(tmp_path):
    cal = recorder(tmp_path)
    r = Bare(cal, [], FakeLadder())
    r.calibration_live_tick(1002.0)
    assert r.stats["calib_live_observations"] == 0
    assert cal.report()["events_appended"] == 0


def test_a_fault_in_the_live_tick_is_counted_and_never_raised(tmp_path):
    class Exploding:
        def live_orders(self):
            raise RuntimeError("store is unreadable")

    cal = recorder(tmp_path)
    r = Bare(cal, [], FakeLadder())
    r.executor.store = Exploding()
    r.calibration_live_tick(1002.0)
    assert r.stats["calib_live_error:RuntimeError"] == 1


def test_the_live_tick_runs_even_with_no_recorder_at_all(tmp_path):
    r = Bare(None, [COID], FakeLadder())
    r.calibration_live_tick(1002.0)
    assert not r.stats


def test_the_live_tick_is_independent_of_the_shadow_dispatch():
    """IT WAS NOT, AND THE REVERT THAT MADE IT SO WAS MY MISTAKE.

    The call sat at the end of `shadow_tick`, which returns early when the
    shadow state or the window is unavailable. I moved it to the main
    `tick`, claimed that broke 111 tests, and reverted it. It broke NONE:
    both runs behind that claim were contaminated by my own edits to the
    runner while they were in flight, and a clean run of the relocated tree
    is byte-identical to the baseline.

    The detailed contracts live in `test_l5_live_observer_independence.py`;
    this keeps the wiring file honest about where the call is.
    """
    assert "self.calibration_live_tick(now)" in inspect.getsource(
        RUN.Runner.tick)
    assert "calibration_live_tick" not in inspect.getsource(
        RUN.Runner.shadow_tick)


def test_it_takes_the_slug_from_the_transport_not_the_shadow_grid(tmp_path):
    """Live orders are in the market the TRANSPORT is on. The shadow grid's
    slug is derived from `self.ws` and can differ."""
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    other = Bare(cal, [COID], FakeLadder(), slug="some-other-market",
                 t=1002.0)
    other.calibration_live_tick(1002.0)
    # observe() filters on the record's own slug, so nothing is recorded
    assert cal.record(COID)["observations"] == []
    right = Bare(cal, [COID], FakeLadder(), t=1002.0)
    right.calibration_live_tick(1002.0)
    assert len(cal.record(COID)["observations"]) == 1


def test_no_slug_on_the_transport_means_no_observation(tmp_path):
    cal = recorder(tmp_path)
    r = Bare(cal, [COID], FakeLadder(), slug=None)
    r.calibration_live_tick(1002.0)
    assert r.stats["calib_live_observations"] == 0


def test_an_unreadable_print_feed_is_reported_and_recorded_as_absent(tmp_path):
    """Never guessed at as "no prints", which would read as a quiet level."""
    cal = recorder(tmp_path)
    cal.pre_submit(COID, slug=SLUG, side="UP", own_px="0.4400", qty="1",
                   book=book(), ladder=FakeLadder(), now=1000.0)
    r = Bare(cal, [COID], FakeLadder(), t=1002.0, book_raises=True)
    r.calibration_live_tick(1002.0)
    assert r.stats["calib_live_prints_error:RuntimeError"] == 1
    obs = cal.record(COID)["observations"][-1]
    assert obs["prints_at_our_price"]["unavailable"] is True
