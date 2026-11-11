"""EMERGENCY HALT CANCELLATION: a latched halt cancels the whole known book.

The gap this closes. The executor cancels only while ARMED; the watchdog
cancelled only orders older than `max_order_age_s` (300s in production). So
stopping or halting the executor while a FRESH order rested left that order
unmanaged for up to five minutes, free to fill and open a residual with no
process watching it.

The fix is a separate immediate path, not a smaller deadline: lowering
`max_order_age_s` would cancel legitimately resting maker orders during
normal trading, which is the one thing it must not do. Both paths go through
`_cancel_targets`, so ownership, venue corroboration and UNCERTAIN handling
cannot drift apart between them.
"""
from __future__ import annotations

import datetime as dt

from src import l5_halt_gate as HG
from src import l5_watchdog as W

from tests.test_l5_watchdog import (                  # noqa: F401
    ClaimsOpenOrders, NOW, MARKET, Venue, gate, open_row, wd)

FRESH = "2026-10-12T21:59:55.000000000Z"              # 5s old at NOW
OLD = "2026-10-12T21:40:00.000000000Z"                # 1200s old at NOW


def latch(tmp_path, *, reason="OWNER_SIGNED_MARKER"):
    """Latch a halt the way the owner marker does, out of band."""
    HG.HaltAsserter(HG.FileStore(str(tmp_path)), who="owner").assert_halt(
        reason=reason, evidence={"who": "owner", "via": "signed marker"})


# --------------------------------------------------------------- fresh orders

def test_a_fresh_order_is_cancelled_when_a_halt_is_latched(tmp_path):
    """THE REGRESSION. 5 seconds old, a 300s deadline, cancelled anyway."""
    v = Venue([open_row("F1", created=FRESH)],
              reads={"F1": {"state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0"}})
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["emergency_cancel"] is True
    assert out["emergency_reason"] == "HALT_LATCHED:OWNER_SIGNED_MARKER"
    assert out["emergency_targets"] == 1
    assert v.cancelled == ["F1"], "the fresh order must be cancelled"
    assert [r["venue_order_id"] for r in out["cancelled"]] == ["F1"]
    assert out["cancelled"][0]["selected_by"] == "EMERGENCY_HALT_LATCHED"
    assert out["cancelled"][0]["age_s"] < 300, (
        "the age is reported but was not a condition")


def test_a_fresh_order_is_left_alone_with_no_halt(tmp_path):
    """BASELINE PARITY. Without a latch, nothing changes: young orders rest."""
    v = Venue([open_row("F1", created=FRESH)])
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["emergency_cancel"] is False
    assert out["overdue"] == [] and v.cancelled == []
    assert out["heartbeat"]["halt"] is False
    assert gate(tmp_path).submissions_permitted() is True


def test_every_resting_order_is_targeted_not_just_the_old_one(tmp_path):
    """ALL known open orders. The old one was already reachable; the two
    fresh ones are what the emergency path adds."""
    v = Venue([open_row("A", created=OLD), open_row("B", created=FRESH),
               open_row("C", created=FRESH)],
              reads={k: {"state": "ORDER_STATE_CANCELED",
                         "leavesQuantity": "0", "cumQuantity": "0"}
                     for k in ("A", "B", "C")})
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["emergency_targets"] == 3
    assert sorted(v.cancelled) == ["A", "B", "C"]
    assert len(out["cancelled"]) == 3


def test_a_terminal_row_is_never_a_target(tmp_path):
    """Already filled or cancelled is not 'open', emergency or not."""
    v = Venue([open_row("T", leaves="0", state="ORDER_STATE_FILLED"),
               open_row("F1", created=FRESH)],
              reads={"F1": {"state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0"}})
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["emergency_targets"] == 1
    assert v.cancelled == ["F1"]


# ------------------------------------------------------- partially filled

def test_a_partially_filled_fresh_order_is_cancelled_and_its_fill_reported(
        tmp_path):
    """The dangerous one: half of it already traded, the rest still live.

    Cancelling leaves the filled part owned, so the record must carry
    `cum_qty` -- that residual is now someone's to reconcile, and a sweep
    that reported only "cancelled" would hide it.
    """
    v = Venue([open_row("P1", created=FRESH, leaves="0.60")],
              reads={"P1": {"state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0.40"}})
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    rec = out["cancelled"][0]
    assert rec["venue_order_id"] == "P1"
    assert rec["cum_qty"] == "0.40", "the filled part must be visible"
    assert rec["leaves_qty"] == "0"


def test_an_order_that_fills_completely_during_the_cancel_is_not_a_failure(
        tmp_path):
    """It raced us and won. Terminal is terminal; it is no longer resting."""
    v = Venue([open_row("R1", created=FRESH)],
              reads={"R1": {"state": "ORDER_STATE_FILLED",
                            "leavesQuantity": "0", "cumQuantity": "1.00"}})
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["still_open"] == []
    assert out["cancelled"][0]["venue_state"] == "ORDER_STATE_FILLED"
    assert out["cancelled"][0]["cum_qty"] == "1.00"


# --------------------------------------------------------- failed cancels

def test_a_refused_cancel_that_leaves_the_order_resting_is_still_open(
        tmp_path):
    """The ack decides nothing; the re-read does. A still-live order after a
    failed cancel must NOT be reported as cancelled."""
    v = Venue([open_row("X1", created=FRESH)],
              reads={"X1": {"state": "ORDER_STATE_OPEN",
                            "leavesQuantity": "1.00", "cumQuantity": "0"}},
              cancel_raises=["X1"])
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["cancelled"] == []
    assert out["still_open"][0]["venue_order_id"] == "X1"
    assert out["still_open"][0]["cancel_error"] == "RuntimeError"


def test_a_cancel_whose_outcome_cannot_be_read_is_UNVERIFIED(tmp_path):
    """UNCERTAIN handling. Not cancelled, not still_open -- unverified."""
    v = Venue([open_row("U1", created=FRESH)], reads={},
              read_raises=["U1"])
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["cancelled"] == [] and out["still_open"] == []
    assert out["unverified"][0]["venue_order_id"] == "U1"
    assert out["unverified"][0]["why"] == "OWNERSHIP_UNCONFIRMED_NO_POST_SENT"
    assert v.cancelled == [], (
        "ownership could not be corroborated, so NOTHING may be sent")


def test_one_failure_does_not_abandon_the_rest_of_the_book(tmp_path):
    """A refused cancel on the first order must not strand the second."""
    v = Venue([open_row("X1", created=FRESH), open_row("G1", created=FRESH)],
              reads={"X1": {"state": "ORDER_STATE_OPEN",
                            "leavesQuantity": "1.00", "cumQuantity": "0"},
                     "G1": {"state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0"}},
              cancel_raises=["X1"])
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert [r["venue_order_id"] for r in out["still_open"]] == ["X1"]
    assert [r["venue_order_id"] for r in out["cancelled"]] == ["G1"]


# ----------------------------------------------------- executor unavailable

def test_an_unreadable_venue_cancels_nothing_and_does_not_claim_safety(
        tmp_path):
    """A watchdog that cannot see is not evidence of safety."""
    v = Venue(open_raises=True)
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["read_error"] == "OSError"
    assert "emergency_cancel" not in out, (
        "the read failed before any emergency decision was reached")
    assert v.cancelled == []


def test_a_stopped_executor_still_has_its_orders_cancelled(tmp_path):
    """THE WHOLE POINT. The executor is gone -- stopped, crashed, disarmed --
    so nothing of its own will ever cancel these. The watchdog does it,
    without waiting for the 300s deadline."""
    v = Venue([open_row("S1", created=FRESH), open_row("S2", created=FRESH)],
              reads={k: {"state": "ORDER_STATE_CANCELED",
                         "leavesQuantity": "0", "cumQuantity": "0"}
                     for k in ("S1", "S2")})
    latch(tmp_path, reason="EXECUTOR_REPORT_STALE")
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["emergency_reason"] == "HALT_LATCHED:EXECUTOR_REPORT_STALE"
    assert sorted(v.cancelled) == ["S1", "S2"]


def test_a_latch_with_no_cancel_capability_says_the_orders_are_still_resting(
        tmp_path):
    """Credential-free mode. It must not read as success."""
    v = Venue([open_row("N1", created=FRESH)])
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=False)
    out = w.sweep(now=NOW)

    assert out["emergency_cancel"] is True and out["emergency_targets"] == 1
    assert out["cancelled"] == []
    assert "STILL RESTING" in out["cancel_skipped"]
    assert v.cancelled == []


def test_an_empty_book_under_a_latch_is_reported_not_silent(tmp_path):
    v = Venue([])
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["emergency_targets"] == 0
    assert "nothing to cancel" in out["emergency_note"]
    assert "heartbeat" not in out


# -------------------------------------------------------- unknown ownership

def test_an_order_not_in_the_ownership_registry_is_never_cancelled(tmp_path):
    """On a shared account this is the owner's own manual order. The
    emergency path may not relax it -- an emergency is exactly when a desk
    is tempted to cancel everything in sight."""
    v = Venue([open_row("M1", created=FRESH)],
              reads={"M1": {"state": "ORDER_STATE_OPEN",
                            "leavesQuantity": "1.00", "cumQuantity": "0"}})

    class ClaimsNothing:
        last_refusal = None

        def claimed(self):
            return {"OTHER": {"venue_order_id": "OTHER", "market": MARKET}}

    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True,
              oracle=ClaimsNothing())
    out = w.sweep(now=NOW)

    assert v.cancelled == []
    assert out["not_ours"][0]["why"] == "NOT_IN_OWNERSHIP_REGISTRY"


def test_a_venue_row_marked_MANUAL_is_never_cancelled_even_in_an_emergency(
        tmp_path):
    v = Venue([open_row("H1", created=FRESH)],
              reads={"H1": {"state": "ORDER_STATE_OPEN",
                            "leavesQuantity": "1.00", "cumQuantity": "0",
                            "manualOrderIndicator": "MANUAL"}})
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert v.cancelled == []
    assert out["not_ours"][0]["why"] == "VENUE_SAYS_MANUAL"


def test_a_market_mismatch_is_never_cancelled_even_in_an_emergency(tmp_path):
    v = Venue([open_row("K1", created=FRESH)],
              reads={"K1": {"state": "ORDER_STATE_OPEN",
                            "leavesQuantity": "1.00", "cumQuantity": "0",
                            "marketSlug": "some-other-market"}})
    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert v.cancelled == []
    assert out["not_ours"][0]["why"] == "MARKET_MISMATCH"


def test_an_empty_registry_cancels_nothing_and_says_which_fact_it_is(
        tmp_path):
    """'We own nothing' and 'we could not find out' must not arrive alike."""
    v = Venue([open_row("Z1", created=FRESH)])

    class Blind:
        last_refusal = "REGISTRY_UNREACHABLE"

        def claimed(self):
            return {}

    latch(tmp_path)
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True,
              oracle=Blind())
    out = w.sweep(now=NOW)

    assert v.cancelled == []
    assert "STILL RESTING" in out["cancel_skipped"]
    assert out["claim_refused"] == "REGISTRY_UNREACHABLE"


# ---------------------------------------------- the latch is never disturbed

def test_the_emergency_path_never_reasserts_and_never_attests(tmp_path):
    """Re-asserting would overwrite the ORIGINAL reason and evidence -- the
    record of why the system stopped. Attesting would clear it."""
    v = Venue([open_row("Q1", created=FRESH)],
              reads={"Q1": {"state": "ORDER_STATE_CANCELED",
                            "leavesQuantity": "0", "cumQuantity": "0"}})
    latch(tmp_path, reason="OWNER_SIGNED_MARKER")
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["halted"] is None, "the latch was already set"
    assert "heartbeat" not in out
    rec = HG._read(HG.FileStore(str(tmp_path)))
    assert rec["reason"] == "OWNER_SIGNED_MARKER"
    assert rec["who"] == "owner", "the original author must survive"
    assert gate(tmp_path).submissions_permitted() is False


def test_an_unreadable_latch_is_not_treated_as_an_emergency(tmp_path):
    """Deliberate narrowing. Cancelling a resting COMPLETE leg strands the
    residual it was about to hedge, so that must not fire on a transient
    read failure. The age-based path is untouched and still catches it."""
    (tmp_path / "l5").mkdir(parents=True, exist_ok=True)
    (tmp_path / "l5" / "halt.json").write_text("{ this is not json")
    v = Venue([open_row("W1", created=FRESH)])
    w, _ = wd(tmp_path, v, max_order_age_s=300, cancel_enabled=True)
    out = w.sweep(now=NOW)

    assert out["emergency_cancel"] is False
    assert out["emergency_reason"] == "HALT_LATCH_UNREADABLE_NOT_EMERGENCY"
    assert v.cancelled == [], "a fresh order is not cancelled on a blip"
    assert gate(tmp_path).submissions_permitted() is False, (
        "the executor is stopped anyway, which is why waiting is safe")


def test_both_paths_share_one_cancel_implementation(tmp_path):
    """Ownership, corroboration and UNCERTAIN handling cannot drift apart
    between the age path and the emergency path if there is only one."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(W.Watchdog))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_cancel_targets"]
    assert len(calls) == 2, "exactly two callers, one implementation"
    bodies = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_cancel_targets"]
    assert len(bodies) == 1
