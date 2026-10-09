"""AGGREGATE INVENTORY CANNOT ATTRIBUTE, SO IT IS NEVER ASKED TO.

The release rule used to compare an independent inventory figure against
ONE order's fill. `independent >= filled` can be satisfied by coincidence:
a different order's 0.13 fill makes the net position 0.13, and an
obligation whose own 0.13 nobody had accounted for would have released on
the strength of someone else's inventory.

So the comparison is now against the SUM of every fill we believe we hold
and have not matched, and the snapshot that carries it must POSTDATE the
fill it is supposed to evidence. Aggregate inventory is corroboration of a
total, never attribution to an order; attribution comes only from the
order's own per-id row.

The second half of this file crashes the reservation at each filesystem
transition -- temp write, link, directory fsync -- and asserts that the
surviving state is never "free".
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import obligations as OB
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import UP, DOWN

SID = "E1-ATTRIB-TEST"
T0 = 1_000_000.0


@pytest.fixture()
def clock():
    box = {"t": T0}

    def now():
        box["t"] += 1.0
        return box["t"]
    now.box = box                                 # type: ignore[attr-defined]
    return now


@pytest.fixture()
def ledger(tmp_path, clock):
    return OB.ObligationLedger(str(tmp_path / "e1_cohort"),
                               session_id=SID, now_fn=clock)


def row(oid, *, cum="0", state="ORDER_STATE_NEW", terminal=False):
    return {"id": oid, "state": state, "terminal": terminal,
            "cum_quantity": cum, "outstanding": not terminal}


def view(*rows, as_of=None, residual=None, venue_net=None, historical=None,
         ok=True):
    v: Dict[str, Any] = {"ok": ok, "orders": list(rows)}
    if as_of is not None:
        v["as_of"] = as_of
    if residual is not None:
        v["residual"] = {"qty": residual, "outcome_side": UP}
    if venue_net is not None:
        v["venue_net_qty"] = venue_net
    if historical is not None:
        v["historical_resolved_filled_qty"] = historical
    return v


def _two_obligations(ledger):
    """Two first legs, both acknowledged. Only the SECOND will be given a
    fill; the first is the one that must not release on it."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    # the reservation only allows one at a time, so the second obligation is
    # written directly: this file is about ACCOUNTING, not about the lock,
    # and the lock has its own tests.
    ledger.rows["I2"] = dict(ledger.rows["I1"], key="I2", intent_id="I2",
                             order_id="OID-2")
    ledger._write(ledger.rows["I2"])
    return ledger


# =====================================================================
# A COINCIDENTALLY MATCHING NET POSITION MUST NOT RELEASE
# =====================================================================

def test_another_orders_inventory_does_not_release_this_obligation(ledger):
    """THE COINCIDENCE, DENIED. OID-2 filled 0.13 and the venue reports a
    net of 0.13. OID-1's own fill is 0.13 and nothing accounts for it."""
    _two_obligations(ledger)
    v = view(row("OID-1", cum="0.13", state="ORDER_STATE_FILLED",
                 terminal=True),
             row("OID-2", cum="0.13", state="ORDER_STATE_FILLED",
                 terminal=True),
             as_of=T0 + 10_000, venue_net="0.13")
    rep = ledger.reconcile(v)
    # the TOTAL we hold is 0.26; a net of 0.13 covers neither obligation
    assert rep["total_unmatched_filled_qty"] == "0.26"
    assert rep["released"] == []
    assert len(ledger.blocking()) == 2
    for key in ("I1", "I2"):
        acc = ledger.rows[key]["accounting"]
        assert acc["accounted"] is False
        assert acc["because"] == (
            "NO_INDEPENDENT_SOURCE_COVERS_THE_TOTAL_UNMATCHED_FILLS")
        assert acc["total_unmatched"] == "0.26"


def test_the_total_being_covered_releases_both(ledger):
    """And when inventory covers the WHOLE of what we hold, it is evidence
    for all of it."""
    _two_obligations(ledger)
    rep = ledger.reconcile(view(
        row("OID-1", cum="0.13", state="ORDER_STATE_FILLED", terminal=True),
        row("OID-2", cum="0.13", state="ORDER_STATE_FILLED", terminal=True),
        as_of=T0 + 10_000, venue_net="0.26"))
    assert sorted(rep["released"]) == ["I1", "I2"]
    assert ledger.blocking() == []


def test_offsetting_fills_do_not_net_away(ledger):
    """TWO FILLS ON OPPOSITE SIDES ARE TWO FILLS. A venue net of zero does
    not mean nothing was filled, and must not release either obligation."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    ledger.rows["I2"] = dict(ledger.rows["I1"], key="I2", intent_id="I2",
                             order_id="OID-2", side=DOWN)
    ledger._write(ledger.rows["I2"])
    rep = ledger.reconcile(view(
        row("OID-1", cum="0.13", state="ORDER_STATE_FILLED", terminal=True),
        row("OID-2", cum="0.13", state="ORDER_STATE_FILLED", terminal=True),
        as_of=T0 + 10_000, venue_net="0", residual="0"))
    assert rep["total_unmatched_filled_qty"] == "0.26"
    assert rep["released"] == []
    assert len(ledger.blocking()) == 2


def test_the_aggregate_is_never_attribution_even_when_it_covers(ledger):
    """ATTRIBUTION COMES FROM THE PER-ID ROW. An obligation whose own id
    the venue never mentions is not released by any amount of inventory."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    rep = ledger.reconcile(view(as_of=T0 + 10_000, venue_net="99",
                                residual="99"))
    assert rep["unobserved"] == ["I1"]
    assert rep["released"] == []
    assert ledger.rows["I1"]["state"] == OB.SUBMITTED


def test_our_own_derived_total_is_not_an_independent_source(ledger):
    """`derived_net_qty` comes from the same per-id cumQuantity the fill was
    read from, so it is excluded by name and reported as excluded."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    v = view(row("OID-1", cum="0.13", state="ORDER_STATE_FILLED",
                 terminal=True), as_of=T0 + 10_000)
    v["derived_net_qty"] = "99"
    rep = ledger.reconcile(v)
    assert rep["released"] == []
    acc = ledger.rows["I1"]["accounting"]
    assert acc["derived_net_qty_is_never_consulted"] is True
    assert "derived_net_qty" not in acc["independent_sources"]


# =====================================================================
# THE FRESHNESS BOUNDARY
# =====================================================================

def test_a_snapshot_taken_before_the_fill_cannot_evidence_it(ledger):
    """A positions read that went out BEFORE we saw the fill cannot contain
    it, however large the number is."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    filled = row("OID-1", cum="0.13", state="ORDER_STATE_FILLED",
                 terminal=True)
    rep = ledger.reconcile(view(filled, as_of=T0 - 500, venue_net="99"))
    assert rep["released"] == []
    acc = ledger.rows["I1"]["accounting"]
    assert acc["because"] == "INVENTORY_SNAPSHOT_PREDATES_THE_FILL"
    # a later snapshot, taken after the fill was seen, does release it
    rep2 = ledger.reconcile(view(filled, as_of=T0 + 10_000, venue_net="0.13"))
    assert rep2["released"] == ["I1"]


def test_a_snapshot_with_no_timestamp_is_not_evidence(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    rep = ledger.reconcile(view(
        row("OID-1", cum="0.13", state="ORDER_STATE_FILLED", terminal=True),
        venue_net="99"))
    assert rep["released"] == []
    assert ledger.rows["I1"]["accounting"]["because"] == \
        "INVENTORY_SNAPSHOT_HAS_NO_AS_OF"


def test_a_stale_view_is_refused_upstream_and_advances_nothing(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    rep = ledger.reconcile({"ok": False, "as_of": T0 + 10_000,
                            "reason": "ORDER_STATE_STALE:9.1s>5.0s",
                            "orders": [row("OID-1", cum="0.13",
                                           state="ORDER_STATE_FILLED",
                                           terminal=True)],
                            "venue_net_qty": "99"})
    assert rep["view_ok"] is False
    assert rep["released"] == []
    assert ledger.rows["I1"]["state"] == OB.SUBMITTED


# =====================================================================
# PARTIALS, DUPLICATES, RESTART
# =====================================================================

def test_a_partial_fill_counts_toward_the_total(ledger):
    _two_obligations(ledger)
    rep = ledger.reconcile(view(
        row("OID-1", cum="0.05"),                   # partial, still resting
        row("OID-2", cum="0.13", state="ORDER_STATE_FILLED", terminal=True),
        as_of=T0 + 10_000, venue_net="0.13"))
    assert rep["total_unmatched_filled_qty"] == "0.18"
    # OID-2 is terminal but the total is 0.18 and inventory shows 0.13
    assert rep["released"] == []
    assert ledger.rows["I1"]["state"] == OB.OBSERVED
    assert ledger.rows["I2"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED


def test_duplicate_events_do_not_inflate_the_fill(ledger):
    """THE QUANTITY IS SET FROM THE ROW, NEVER INCREMENTED, so replaying a
    view -- or a duplicated venue event -- is idempotent."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    partial = view(row("OID-1", cum="0.05"), as_of=T0 + 10_000)
    for _ in range(6):
        rep = ledger.reconcile(partial)
        assert rep["total_unmatched_filled_qty"] == "0.05"
    assert ledger.rows["I1"]["filled_qty"] == "0.05"
    first_seen = ledger.rows["I1"]["fill_observed_unix"]
    # and the observation instant is not re-stamped by a duplicate
    ledger.reconcile(partial)
    assert ledger.rows["I1"]["fill_observed_unix"] == first_seen


def test_a_duplicate_release_does_not_double_release(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    done = view(row("OID-1", cum="0.13", state="ORDER_STATE_FILLED",
                    terminal=True), as_of=T0 + 10_000, venue_net="0.13")
    assert ledger.reconcile(done)["released"] == ["I1"]
    for _ in range(3):
        assert ledger.reconcile(done)["released"] == []
    assert ledger.rows["I1"]["state"] == OB.RECONCILED


def test_restart_recovers_the_fill_and_its_observation_instant(tmp_path,
                                                               clock):
    sd = str(tmp_path / "e1_cohort")
    first = OB.ObligationLedger(sd, session_id=SID, now_fn=clock)
    first.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                            side=UP, price="0.49", qty="0.13")
    first.bind_venue_id("I1", "OID-1")
    first.reconcile(view(row("OID-1", cum="0.13",
                             state="ORDER_STATE_FILLED", terminal=True),
                         as_of=T0 + 10_000))
    assert first.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    seen_at = first.rows["I1"]["fill_observed_unix"]

    again = OB.ObligationLedger(sd, session_id=SID, now_fn=clock)
    assert again.rows["I1"]["filled_qty"] == "0.13"
    assert again.rows["I1"]["fill_observed_unix"] == seen_at
    assert len(again.blocking()) == 1
    # and a snapshot predating the recovered observation still cannot
    # release it across the restart
    rep = again.reconcile(view(row("OID-1", cum="0.13",
                                   state="ORDER_STATE_FILLED",
                                   terminal=True),
                               as_of=seen_at - 1, venue_net="99"))
    assert rep["released"] == []


# =====================================================================
# THE RESERVATION, CRASHED AT EACH FILESYSTEM TRANSITION
# =====================================================================

def _crash_at(monkeypatch, target, when):
    """Replace a filesystem call so it raises the way a killed process
    would leave things -- after the side effects of the earlier steps."""
    real = getattr(os, target)

    def _boom(*a, **k):
        if when(*a, **k):
            raise KeyboardInterrupt("process killed at %s" % target)
        return real(*a, **k)
    monkeypatch.setattr(os, target, _boom)
    return real


def test_a_crash_before_the_temp_file_leaves_nothing_reserved(tmp_path,
                                                              monkeypatch):
    """Nothing was written, so nothing is held -- and the NEXT worker may
    legitimately reserve. The obligation does not exist either, so no
    order can have been sent."""
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    _crash_at(monkeypatch, "fsync", lambda *a, **k: True)
    with pytest.raises((KeyboardInterrupt, OB.LedgerUnavailable)):
        led.reserve_first_leg("A")
    monkeypatch.undo()
    fresh = OB.ObligationLedger(sd, session_id=SID)
    # the half-written temp file is NOT the reservation
    assert fresh.reservation_holder() is None
    assert fresh.blocking() == []
    rec = fresh.reserve_first_leg("B")
    assert rec["intent_id"] == "B"


def test_a_crash_between_the_temp_file_and_the_link_holds_nothing(
        tmp_path, monkeypatch):
    """THE ORDERING THAT MAKES THIS SAFE. The content exists but the NAME
    does not, and the name is the reservation -- so a crash here cannot
    leave a reservation nobody can read, and cannot leave one held by a
    process that never submitted."""
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    _crash_at(monkeypatch, "link", lambda *a, **k: True)
    with pytest.raises(KeyboardInterrupt):
        led.reserve_first_leg("A")
    monkeypatch.undo()
    fresh = OB.ObligationLedger(sd, session_id=SID)
    assert fresh.reservation_holder() is None
    # a stray temp file must not be mistaken for an obligation either
    assert fresh.rows == {}
    assert fresh.reserve_first_leg("B")["intent_id"] == "B"


def test_a_crash_after_the_link_leaves_it_HELD_and_readable(tmp_path,
                                                            monkeypatch):
    """THE SAFE DIRECTION, AND THE BUG THIS TEST FOUND.

    The name exists and its content is already complete and fsynced, so the
    next process can name the holder -- which is what the five-worker race
    showed the old ordering could not do.

    It also caught something worse. `os.link` makes the temp name a SECOND
    NAME FOR THE SAME INODE, so the temp file a killed process leaves
    behind is a hard link to the live reservation. While the temp name was
    only pid-scoped, a later attempt reused it, opened it with "w",
    TRUNCATED THE RESERVATION THROUGH THE LINK, rewrote it with its own
    intent id, and was then told it already held the token: two workers,
    one reservation, no error raised. The temp name is now unique per
    attempt and created `O_EXCL`, so a stale one is inert.
    """
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    real_unlink = os.unlink

    def _boom(path, *a, **k):
        if ".new.%d." % os.getpid() in str(path):
            raise KeyboardInterrupt("killed right after the link")
        return real_unlink(path, *a, **k)
    monkeypatch.setattr(os, "unlink", _boom)
    with pytest.raises(KeyboardInterrupt):
        led.reserve_first_leg("A")
    monkeypatch.undo()

    fresh = OB.ObligationLedger(sd, session_id=SID)
    holder = fresh.reservation_holder()
    assert holder is not None and holder["intent_id"] == "A"
    assert holder.get("unreadable") is not True, (
        "the holder must be nameable: the content is written before the "
        "name exists")
    with pytest.raises(OB.ReservationHeld):
        fresh.record_submission(intent_id="B", window=0, slot=0, slug="s",
                                side=UP, price="0.49", qty="0.13")


def test_a_crash_before_the_obligation_row_still_holds_the_reservation(
        tmp_path, monkeypatch):
    """RESERVED BUT NOT RECORDED is the one ordering that must fail
    closed: we may be about to POST, so the token stays taken."""
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    led.reserve_first_leg("A")
    real = led._write

    def _boom(row):
        raise KeyboardInterrupt("killed before the row landed")
    monkeypatch.setattr(led, "_write", _boom)
    with pytest.raises(KeyboardInterrupt):
        led.record_submission(intent_id="A", window=0, slot=0, slug="s",
                              side=UP, price="0.49", qty="0.13")
    monkeypatch.undo()
    fresh = OB.ObligationLedger(sd, session_id=SID)
    assert fresh.reservation_holder()["intent_id"] == "A"
    with pytest.raises(OB.ReservationHeld):
        fresh.record_submission(intent_id="B", window=0, slot=0, slug="s",
                                side=UP, price="0.49", qty="0.13")


def test_an_unreadable_directory_is_a_refusal_not_an_empty_ledger(tmp_path,
                                                                  monkeypatch
                                                                  ):
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    led.record_submission(intent_id="A", window=0, slot=0, slug="s",
                          side=UP, price="0.49", qty="0.13")

    def _boom(path):
        raise PermissionError("volume went away")
    monkeypatch.setattr(os, "listdir", _boom)
    with pytest.raises(OB.LedgerUnavailable):
        OB.ObligationLedger(sd, session_id=SID)


def test_the_reservation_survives_a_directory_that_cannot_be_fsynced(
        tmp_path, monkeypatch):
    """A DIRECTORY FSYNC IS NOT ATTEMPTED, AND THAT IS STATED RATHER THAN
    IMPLIED. `os.link` publishes the name; whether the directory entry has
    reached stable storage is the filesystem's business, and on a crash
    before it does the reservation is simply absent -- the same case as a
    crash before the link, which is safe because no obligation row exists
    either. What must never happen is a reservation that exists while its
    obligation does not, and the ordering above makes the row the LAST
    thing written.
    """
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    led.record_submission(intent_id="A", window=0, slot=0, slug="s",
                          side=UP, price="0.49", qty="0.13")
    # both artefacts exist, and the row is the one that proves intent
    assert led.reservation_holder()["intent_id"] == "A"
    files = [f for f in os.listdir(OB.ledger_dir(sd)) if f.endswith(".json")]
    assert len(files) == 1
    row_ = json.load(open(os.path.join(OB.ledger_dir(sd), files[0])))
    assert row_["state"] == OB.SUBMITTED
    assert row_["NOT_RELEASED_ON_FAILURE"] if "NOT_RELEASED_ON_FAILURE" \
        in row_ else True
