"""The launcher's report, built from what a MOCKED VENUE actually did.

WHY THIS FILE EXISTS SEPARATELY FROM THE OTHER TWO.

  * `test_l5_launch_end_to_end` drives the launch SEQUENCE against a fake
    Railway that changes when written to. Its execution-report tests hand
    `execution_report` order rows written by hand, which proves the
    arithmetic and nothing about where the rows come from.

  * `test_l5_release_candidate_lifecycle` drives the REAL execution path --
    real `FaithfulExecutor.handle`, real `Runner.maybe_*`, a scripted
    venue -- and proves the order lifecycle at the deployed limits. It
    never looks at the launcher.

NEITHER JOINS THEM, and the join is where a report can overstate. So every
`orders` row here is DERIVED from the scripted venue's own record of what
was sent, acknowledged, filled and cancelled, and from the durable store --
not written by the test. A report that counts a fill the venue never gave
is then a failing test rather than a plausible number.

SCRIPTED, NOT SIMULATED. No network, no threads, no live order, and a clock
that moves only when a test moves it. Fills are named per order by the
test, so the test says what the venue did; nothing here models a queue and
nothing here is evidence about profitability.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

import tests.test_l5_e2e_window as H
import tests.test_l5_release_candidate_lifecycle as RC

from src import l5_launch as L

UTC = dt.timezone.utc
SLUG = H.SLUG
WS = H.WS


# =====================================================================
# THE JOIN: venue record + durable store -> launcher order rows
# =====================================================================
def rows_from(w) -> list:
    """The launcher's `orders` rows, derived from the venue and the store.

    This is the whole point of the file, so it is deliberately dumb: it
    reads the venue's `sent` list, its acknowledgements, its cancellations
    and the executor's own records, and reports exactly those. It invents
    nothing and it has no opinion about what SHOULD have happened.
    """
    out = []
    # one row per DECISION the executor recorded, so WOULD_SUBMIT and
    # refusals are represented too and cannot be quietly dropped.
    for rec in w.records:
        order = rec.get("ORDER") or {}
        coid = order.get("client_order_id")
        sent = next((s for s in w.venue.sent
                     if s["client_order_id"] == coid), None) if coid else None
        oid = None
        if sent is not None:
            oid = next((o for o, i in w.venue.by_oid.items()
                        if i["client_order_id"] == coid), None)
        out.append({
            "decision": True,
            "state": ("WOULD_SUBMIT" if order.get("would_submit")
                      else ("SUBMITTED" if sent is not None else "REFUSED")),
            "submitted": sent is not None,
            "venue_order_id": oid,
            "filled_qty": (w.venue.acked.get(oid) or "0") if oid else "0",
            "side": (sent or order or {}).get("side"),
            "cancelled": bool(oid and oid in w.venue.cancelled),
            "refused_reason": rec.get("refused_reason"),
        })
    return out


def heartbeat(*, realized="0.00", day="0.00", status="NONE_VERIFIED",
              **kw) -> dict:
    hb = {"realized": {"realized_total": realized,
                       "verified_l5_status": status},
          "day_realized": {"utc_day_realized_usd": day}}
    hb.update(kw)
    return hb


def report(w, **kw) -> dict:
    return L.execution_report(heartbeat=heartbeat(**kw), market=SLUG,
                              orders=rows_from(w))


#: THE DEPLOYED CONFIGURATION, re-exported rather than retyped.
#: `production_configuration` is autouse INSIDE the lifecycle module only;
#: importing that module does not apply it, and without it the planner
#: sizes to the $12 cash gate and every submission is refused
#: MAX_TOTAL_EXPOSURE -- which is correct behaviour and tests nothing. The
#: fixture object carries its own autouse flag, so binding the name here
#: applies it to this module too, from one definition.
production_configuration = RC.production_configuration


@pytest.fixture()
def w(tmp_path):
    return RC.win2(tmp_path)


# =====================================================================
# ORDERS AND ACKNOWLEDGEMENTS
# =====================================================================

def test_a_window_the_gate_blocked_reports_zero_on_every_order_key(tmp_path):
    """NOT `win2(armed=False)`, and that is deliberate.

    The e2e harness carries `Venue.armed` but its `submit` does not
    consult it, so that flag proves nothing about a disarmed service --
    a mistake this programme has already made once and corrected in
    `test_the_shadow_lock_defaults_to_BLOCKING_and_is_the_first_gate`.
    The real gate is `Runner.submission_gate`, UPSTREAM of
    `FaithfulExecutor.handle`, so a blocked window is one where `handle`
    is never reached. That is modelled here by not reaching it."""
    w = RC.win2(tmp_path)
    import src.l5_pmus_live_runner as R
    assert R.shadow_only() is True, "the default lock is what blocks"
    r = report(w)                             # no tick: the gate held
    assert w.venue.sent == []
    for k in ("decisions", "would_submit", "orders_submitted",
              "venue_acknowledged", "orders_with_fills", "cancellations"):
        assert r[k] == 0, k
    assert r["matched_pairs"] == "0"
    assert float(r["unmatched_residual"]) == 0.0


def test_a_refused_decision_is_counted_as_a_decision_and_not_an_order(
        tmp_path):
    """A refusal is not an absent window. The cap of 2 refuses the third
    leg through the real path, and the report must show three decisions
    against two orders."""
    w = RC.win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    r3 = w.tick(60.0)
    assert w.reason(r3) == "MAX_LIVE_ORDERS", w.reason(r3)
    r = report(w)
    assert r["decisions"] == 3
    assert r["orders_submitted"] == 2
    assert r["venue_acknowledged"] == 2


def test_two_resting_quotes_are_counted_as_acknowledged_and_not_as_fills(w):
    """BOTH LEGS REST AT THE DEPLOYED CAP OF 2, one per tick -- the shape
    the lifecycle file pins, read through the launcher's report."""
    w.tick(30.0)
    w.tick(45.0)
    assert sorted(s["side"] for s in w.venue.sent) == ["DOWN", "UP"]
    assert w.store.still_fillable_order_count() == 2
    r = report(w)
    assert r["orders_submitted"] == 2
    assert r["venue_acknowledged"] == 2
    assert r["orders_with_fills"] == 0
    assert r["matched_pairs"] == "0"
    assert float(r["filled_up"]) == 0.0 and float(r["filled_down"]) == 0.0


def test_the_report_cannot_count_an_ACK_the_venue_did_not_give(w):
    """Deleting the acknowledgement from the venue's own record must move
    the launcher's count, or the count is not reading the venue."""
    w.tick(60.0)
    before = report(w)["venue_acknowledged"]
    w.venue.by_oid.clear()
    assert report(w)["venue_acknowledged"] == 0 < before


# =====================================================================
# FILLS
# =====================================================================

def test_one_filled_leg_is_a_fill_and_an_unmatched_residual(tmp_path):
    w = RC.win2(tmp_path)
    w.venue.fill_next()                       # the FIRST leg fills whole
    w.tick(30.0)
    w.tick(45.0)                              # the complement rests unfilled
    filled = [o for o in rows_from(w) if float(o["filled_qty"] or 0) > 0]
    assert len(filled) == 1, "the harness filled exactly one leg"
    r = report(w)
    assert r["orders_with_fills"] == 1
    assert r["matched_pairs"] == "0"
    assert float(r["unmatched_residual"]) > 0


def test_both_legs_filling_reports_a_matched_pair_and_no_residual(tmp_path):
    w = RC.win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    w.venue.fill_next()
    w.tick(45.0)
    assert w.store.position(SLUG, "UP") == w.store.position(SLUG, "DOWN")
    r = report(w)
    assert r["orders_with_fills"] == 2
    assert float(r["matched_pairs"]) > 0
    assert float(r["unmatched_residual"]) == 0.0


def test_a_fill_is_never_reported_as_settled_money(tmp_path):
    """The one labelling error this programme has made most often."""
    w = RC.win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    r = report(w)
    assert r["orders_with_fills"] == 1
    assert r["settled_realized_net_usd"] == "0.00"
    assert r["settled_today_usd"] == "0.00"
    assert "not settled money" in r["labels"]


def test_settlement_is_the_ACCOUNT_figure_and_says_so(tmp_path):
    w = RC.win2(tmp_path)
    w.tick(30.0)
    r = report(w, realized="-1.27", day="-0.42",
               status="UNPROVEN_LEGACY_ATTRIBUTION")
    assert r["settled_realized_net_usd"] == "-1.27"
    assert r["verified_l5_status"] == "UNPROVEN_LEGACY_ATTRIBUTION"
    assert "ACCOUNT figure" in r["labels"]
    assert "not this window's attribution" in r["labels"]


def test_a_partial_fill_is_reported_as_partial_not_as_a_whole_leg(tmp_path):
    w = RC.win2(tmp_path)
    w.tick(30.0)
    first = w.venue.sent[0]
    oid = next(o for o, i in w.venue.by_oid.items()
               if i["client_order_id"] == first["client_order_id"])
    assert D(str(first["qty"])) >= D("1")
    w.venue.acked[oid] = "0.4"
    r = report(w)
    assert r["orders_with_fills"] == 1
    assert float(r["filled_up"]) + float(r["filled_down"]) == pytest.approx(
        0.4)
    assert r["matched_pairs"] == "0"


# =====================================================================
# CANCELLATIONS
# =====================================================================

def test_cancellations_are_counted_from_the_venues_own_record(tmp_path):
    w = RC.win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    assert report(w)["cancellations"] == 0
    # the REAL ttl sweep, at the deployed 120s order ttl
    w.tick(300.0)
    assert w.venue.cancelled, "the ttl sweep cancelled nothing"
    r = report(w)
    assert r["cancellations"] == len(
        [o for o in rows_from(w) if o["cancelled"]])
    assert r["cancellations"] > 0


def test_a_cancelled_leg_is_still_counted_as_submitted(tmp_path):
    """Cancelled is a state an order reached, not a reason to forget it."""
    w = RC.win2(tmp_path)
    w.tick(30.0)
    n = report(w)["orders_submitted"]
    w.tick(300.0)
    assert report(w)["orders_submitted"] >= n


def test_a_cancelled_UNFILLED_leg_contributes_no_fill(tmp_path):
    w = RC.win2(tmp_path)
    w.tick(30.0)
    w.tick(300.0)
    r = report(w)
    for row in rows_from(w):
        if row["cancelled"]:
            assert float(row["filled_qty"] or 0) == 0.0
    assert r["matched_pairs"] == "0"


# =====================================================================
# RECONCILIATION -- AN UNCERTAIN BOOK IS NOT A CLEAN ONE
# =====================================================================

def WSdt() -> dt.datetime:
    return dt.datetime.fromtimestamp(WS, UTC)


def shutdown(**hb):
    return L.shutdown_plan(heartbeat=heartbeat(**hb), window_start=WSdt(),
                           now=WSdt() + dt.timedelta(minutes=20))


def test_cancellation_is_ordered_before_the_lock_is_restored():
    """A lock blocks submission; it does not flatten a resting order."""
    sd = shutdown(order_progress={"live_orders": 2, "parked_orders": 0},
                  unresolved_accounting={"count": 0})
    acts = [a["action"] for a in sd["actions"]]
    assert acts.index("cancel_outstanding_by_exact_id") < acts.index(
        "restore_structural_submission_lock")
    assert "verify_terminal_state" in acts
    assert sd["outstanding_clear"] is False


def test_unresolved_accounting_halts_re_entry():
    sd = shutdown(order_progress={"live_orders": 0, "parked_orders": 0},
                  unresolved_accounting={"count": 3})
    acts = [a["action"] for a in sd["actions"]]
    assert "halt_reentry_pending_reconciliation" in acts
    assert sd["reconciliation_clear"] is False
    assert sd["outstanding_clear"] is True, (
        "the orders are gone; the ACCOUNTING is what is unresolved")


def test_an_UNREADABLE_order_count_is_never_reported_clear():
    """THE DEFECT THIS PINS. `_get` returns None for absent, and the old
    conditions read `live not in (0, None)` -- so a heartbeat with no
    `order_progress` produced no cancellation action and
    `outstanding_clear: True`. The orders a reporter cannot see are
    exactly the ones nobody would then cancel."""
    sd = shutdown()
    assert sd["outstanding_clear"] is False
    assert sd["reconciliation_clear"] is False
    assert set(sd["counts_unreadable"]) == {
        "live_orders", "parked_orders", "unresolved_accounting"}
    acts = [a["action"] for a in sd["actions"]]
    assert "cancel_outstanding_by_exact_id" in acts, (
        "the sweep must run: cancelling by exact owned id is safe when "
        "there is nothing to cancel, and skipping it is not")
    assert "halt_reentry_pending_reconciliation" in acts


def test_an_unreadable_count_says_so_rather_than_implying_a_number():
    sd = shutdown(order_progress={"live_orders": 1},
                  unresolved_accounting={})
    cancel = [a for a in sd["actions"]
              if a["action"] == "cancel_outstanding_by_exact_id"][0]
    assert cancel["counts_unreadable"] == ["parked_orders"]
    halt = [a for a in sd["actions"]
            if a["action"] == "halt_reentry_pending_reconciliation"][0]
    assert halt["readable"] is False
    assert "not evidence that it is zero" in halt["note"]


def test_a_window_with_nothing_outstanding_is_clear_on_both_counts():
    sd = shutdown(order_progress={"live_orders": 0, "parked_orders": 0},
                  unresolved_accounting={"count": 0})
    assert sd["outstanding_clear"] is True
    assert sd["reconciliation_clear"] is True
    assert sd["counts_unreadable"] is None
    assert "restore_structural_submission_lock" in [
        a["action"] for a in sd["actions"]]
    assert "absence is not zero" in sd["labels"]


def test_an_ambiguous_submit_leaves_the_book_uncertain_and_is_not_a_fill(
        tmp_path):
    """A POST whose answer never arrived may or may not have rested. The
    launcher must not count it as acknowledged, and it must not count it
    as nothing either -- it is a decision whose outcome is unknown."""
    w = RC.win2(tmp_path)
    boom = []

    def submit(intent):
        boom.append(intent)
        raise RuntimeError("TIMEOUT")

    w.venue.submit = submit
    w.tick(30.0)
    assert boom, "nothing was attempted"
    r = report(w)
    assert r["orders_submitted"] == 0, (
        "an unanswered POST is not an acknowledged order")
    assert r["orders_with_fills"] == 0
    assert r["decisions"] >= 1, "nor is it nothing: it was decided"


# =====================================================================
# RESTART, MID-WINDOW
# =====================================================================

def test_a_restart_mid_window_does_not_double_count_the_resting_legs(
        tmp_path):
    w = RC.win2(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    before = report(w)
    fresh = w.restart(tmp_path)
    fresh.records = list(w.records)          # the same tape, a new process
    after = L.execution_report(heartbeat=heartbeat(), market=SLUG,
                               orders=rows_from(fresh))
    assert after["orders_submitted"] == before["orders_submitted"]
    assert after["venue_acknowledged"] == before["venue_acknowledged"]
    assert len(fresh.venue.sent) == len(w.venue.sent)


def test_a_restart_after_one_fill_keeps_the_fill_and_adds_no_other(tmp_path):
    w = RC.win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    before = report(w)["orders_with_fills"]
    fresh = w.restart(tmp_path)
    fresh.records = list(w.records)
    after = L.execution_report(heartbeat=heartbeat(), market=SLUG,
                               orders=rows_from(fresh))["orders_with_fills"]
    assert after == before == 1


def test_a_restart_sends_no_duplicate_client_order_id(tmp_path):
    """The venue raises on a duplicate, so a resumption that re-sent would
    fail loudly rather than quietly double the position."""
    w = RC.win2(tmp_path)
    w.tick(30.0)
    fresh = w.restart(tmp_path)
    fresh.tick(90.0)
    coids = [s["client_order_id"] for s in fresh.venue.sent]
    assert len(coids) == len(set(coids))


# =====================================================================
# THE FIVE STATES ARE NEVER SUMMED
# =====================================================================

def test_the_five_states_are_separate_keys_and_none_is_a_total(tmp_path):
    w = RC.win2(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)
    r = report(w)
    for k in ("decisions", "would_submit", "orders_submitted",
              "venue_acknowledged", "orders_with_fills"):
        assert k in r
    assert r["orders_with_fills"] <= r["venue_acknowledged"] \
        <= r["orders_submitted"] <= r["decisions"], r


def test_a_WOULD_SUBMIT_is_never_counted_as_an_order(tmp_path):
    w = RC.win2(tmp_path)
    rows = [{"decision": True, "state": "WOULD_SUBMIT", "submitted": False},
            {"decision": True, "state": "WOULD_SUBMIT", "submitted": False}]
    r = L.execution_report(heartbeat=heartbeat(), market=SLUG, orders=rows)
    assert r["would_submit"] == 2
    assert r["orders_submitted"] == 0 and r["orders_with_fills"] == 0
    assert r["matched_pairs"] == "0"
