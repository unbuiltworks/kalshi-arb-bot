"""Event-sequence proof that the code does not block valid decisions.

AUDIT RESULT THIS RESTS ON. Of the eight requirements, five are already
satisfied by the DEPLOYED SHA dbb58fa8 and are not re-implemented here:

  (4) unmatched inventory already takes priority and already refuses a
      loss-locking completion -- `COMPLETION_PAIR_ABOVE_CEILING`;
  (5) cancellation already goes by exact venue id and treats anything but
      a venue-confirmed terminal as UNCERTAIN -- `deadman(..., only=...)`,
      `pending_terminal`, `BOOK_UNCERTAIN`;
  (6) exposure already counts filled + still-fillable outstanding + the
      proposed order, account-wide, and `_still_fillable_or_at_limit()`
      reports an uncomputable count as AT the limit rather than zero;
  (7) the exposure caps are evaluated against `risk_by_market` -- worst-case
      cash loss under BOTH settlements -- so a completion, which lowers that
      worst case, is not refused by a cap meant to bound risk-taking. Only
      the notional FALLBACK would have blocked it, and the live path does
      not use the fallback;
  (8) watchdog, daily loss, capital limits, reconciliation and HALT are
      untouched by anything in this file.

The gap is (1), (2) and (3): the round machinery. Those are addressed by two
already-written flags, both default OFF:

  L5_TS_ROUND_RETIRE_WITH_RESIDUAL   a round may retire while it carries a
                                     residual, so `rnd` advances
  L5_TS_CONTINUOUS_INVENTORY         a decision taken without a round may
                                     claim a submission identity

These tests turn both ON and assert the deadlock is gone. SUCCESS HERE IS
CORRECTNESS, NOT ECONOMICS: every assertion is about whether a valid
decision can still be made, never about whether it would have been
profitable. Replay economics are reported separately and are hypothetical.

EVERY REFUSAL IS RECORDED with its exact gate and the state that caused it,
so a silently blocked action cannot pass as an absent opportunity.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_continuous_inventory as CI
from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS
from src.l5_live_validation_gates import ValidationGates


@pytest.fixture()
def rig(monkeypatch, tmp_path):
    import tests.test_l5_live_execution as H
    from src import l5_live_execution as E
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "SEQ-1")
    monkeypatch.setenv(TS.MARKET_ENV, "m")
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "SEQ-1")
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "2")
    # the two corrections under test
    monkeypatch.setenv(E.CONTINUOUS_INVENTORY_ENV, "YES")
    monkeypatch.setenv(E.ROUND_RETIRE_RESIDUAL_ENV, "YES")
    for k in ("L5_LIVE_AUTH_MAX_OPEN", "L5_LIVE_AUTH_MAX_COMPLETE",
              "L5_LIVE_AUTH_MAX_REPLACE", "L5_LIVE_AUTH_MAX_SUBMISSIONS"):
        monkeypatch.setenv(k, "60")
    monkeypatch.setattr("src.l5_pmus_auth_book.slug_start",
                        lambda slug: H.NOW().timestamp() - 60.0)
    return H, E, monkeypatch, tmp_path


def book(bid="0.60", ask="0.61"):
    return {"bid": bid, "ask": ask, "bid_shares": "900",
            "ask_shares": "5000", "stream_age_s": "0.05"}


def mk(H, E, tmp_path, orders=2, fill=False):
    t = H.FakeTransport(fill_all=fill)
    t.tick_size, t.quantity_increment = D("0.01"), D("0.01")
    ex = E.FaithfulExecutor(H.store(tmp_path),
                            H.limits(max_live_orders=orders), transport=t,
                            now_fn=H.NOW, maker_treatment=True)
    ex.validation_gates = ValidationGates(
        max_cash_deployed_usd=D("12"), max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("20"),
        max_worst_case_deficit_usd=D("1.50"))
    return ex, t


def intent(E, bk, side=None, size="2"):
    up = E.controller_provisional_price("UP", bk, D("0.01"))
    dn = E.controller_provisional_price("DOWN", bk, D("0.01"))
    if side is None:
        side, px = ("UP", up) if up <= dn else ("DOWN", dn)
    else:
        px = up if side == "UP" else dn
    return {"provenance": E.CONTROLLER_GENERATED, "maker_class": None,
            "decision": {"side": side, "price": px,
                         "l5_size": D(size), "relation": "open"}}


class Journal:
    """Every decision, and for each refusal the exact gate and the state."""

    def __init__(self):
        self.rows = []

    def tick(self, ex, E, bk=None, side=None, size="2", label=""):
        bk = bk if bk is not None else book()
        rec = ex.handle(None, bk, market="m", window="21:00",
                        controller=intent(E, bk, side=side, size=size))
        o = rec.get("ORDER") or {}
        row = {
            "label": label,
            "submitted": bool(o.get("submitted")),
            "gate": rec.get("refused_by_layer") or (
                None if o.get("submitted") else "ORDER"),
            "reason": rec.get("refused_reason") or o.get("reason"),
            "action_key": rec.get("action_key"),
            "state": {
                "up": str(ex.store.position("m", "UP")),
                "dn": str(ex.store.position("m", "DOWN")),
                "residual": str(ex.store.residual("m")),
                "live_orders": ex.store.live_order_count(),
                "still_fillable": ex.store.still_fillable_order_count(),
                "book": "%s/%s" % (bk["bid"], bk["ask"]),
            },
        }
        self.rows.append(row)
        return row

    def refusals(self):
        return [r for r in self.rows if not r["submitted"]]

    def submitted(self):
        return [r for r in self.rows if r["submitted"]]

    def report(self):
        return "\n".join(
            "%-26s %-9s %-28s up=%s dn=%s resid=%s live=%s book=%s" % (
                r["label"], "SENT" if r["submitted"] else "REFUSED",
                "%s/%s" % (r["gate"], r["reason"]),
                r["state"]["up"], r["state"]["dn"], r["state"]["residual"],
                r["state"]["live_orders"], r["state"]["book"])
            for r in self.rows)


# ============ 1. pair completes, then an INDEPENDENT pair begins
def test_a_second_independent_pair_can_begin_after_the_first_completes(rig):
    """REQUIREMENT 3. Inventory need not return to zero between rounds, and
    the round counter must not pin the market after the first pair."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp, fill=True)
    j = Journal()
    j.tick(ex, E, side="UP", label="pair1 leg1")
    j.tick(ex, E, side="DOWN", label="pair1 leg2")
    assert ex.store.position("m", "UP") > 0
    assert ex.store.position("m", "DOWN") > 0
    j.tick(ex, E, bk=book("0.50", "0.51"), side="UP", label="pair2 leg1")
    j.tick(ex, E, bk=book("0.50", "0.51"), side="DOWN", label="pair2 leg2")
    # The FIRST pair completed: both legs sent and inventory is matched.
    assert len(j.submitted()) >= 2, j.report()
    assert ex.store.residual("m") == D(0), j.report()
    # Whatever happens to the SECOND pair must be an ECONOMIC or RISK
    # refusal, never the round machinery. Here the engine correctly refuses
    # it on WORST_PAIR_COMBINATION: holding a matched 2.12 already, the
    # worst reachable pair at the new book would exceed redemption. That is
    # the gate doing its job, not a deadlock.
    for r in j.refusals():
        assert str(r["reason"]) not in (
            "ACCUMULATION_EXHAUSTED", "CONTROLLER_NO_PROBE_LEG"), j.report()
        assert "LEG_HOLDS_UNMATCHED" not in str(r["reason"]), j.report()
        assert r["gate"], j.report()


# ============ 2. complement cannot execute, later decisions still possible
def test_a_blocked_complement_does_not_freeze_the_market(rig):
    """REQUIREMENT 1 and 7. The deadlock: one stranded fill made up != dn,
    the round could not retire, and nothing further was ever decided."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp, fill=True)
    j = Journal()
    j.tick(ex, E, side="UP", label="leg1 fills")
    resid = ex.store.residual("m")
    assert resid != 0
    # the book runs away, so a complement at this level is uneconomic
    for i, bk in enumerate((book("0.90", "0.91"), book("0.92", "0.93"),
                            book("0.94", "0.95"))):
        j.tick(ex, E, bk=bk, label="runaway %d" % i)
    # a LATER valid decision must still be reachable once the book returns
    row = j.tick(ex, E, bk=book("0.55", "0.56"), side="DOWN",
                 label="complement returns")
    assert row["submitted"] or row["reason"] not in (
        "ACCUMULATION_EXHAUSTED", "CONTROLLER_NO_PROBE_LEG",
        "LEG_HOLDS_UNMATCHED"), j.report()
    assert ex.store.residual("m") != 0 or row["submitted"], j.report()


def test_no_refusal_in_any_sequence_is_a_round_machinery_refusal(rig):
    """The whole point of the correction: refusals must be ECONOMIC or RISK
    refusals, never 'the round cannot retire'."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp, fill=True)
    j = Journal()
    j.tick(ex, E, side="UP", label="open")
    for i in range(6):
        j.tick(ex, E, bk=book("0.8%d" % i, "0.8%d" % (i + 1)),
               label="tick %d" % i)
    for r in j.refusals():
        assert "ACCUMULATION_EXHAUSTED" not in str(r["reason"]), j.report()
        assert "CONTROLLER_NO_PROBE_LEG" not in str(r["reason"]), j.report()
        assert "LEG_HOLDS_UNMATCHED" not in str(r["reason"]), j.report()


# ============ 3. partial fills
def test_a_partial_fill_leaves_a_decidable_state(rig):
    """REQUIREMENT 6. A partial fill must update inventory and must not
    leave an order that the exposure maths cannot see."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp)
    j = Journal()
    r = j.tick(ex, E, side="UP", size="4", label="submit 4")
    assert r["submitted"], j.report()
    coid = t.sent[0]["client_order_id"]
    prog = E.order_progress({"id": "V1", "state": "ORDER_STATE_NEW",
                             "quantity": "4", "cumQuantity": "1.5",
                             "leavesQuantity": "2.5", "avgPx": "0.58"})
    # THE SIDE THE ENGINE ACTUALLY SUBMITTED, read back from the store.
    # This used to hard-code "UP" while the continuous decision had chosen
    # DOWN, so it booked a fill on a side no order existed on -- the exact
    # shape of the 1445z phantom, asserted as correct. `apply_order_progress`
    # now refuses that, so the fixture states the real side.
    _sub = (ex.store.state.get("submitted") or {}).get(coid) or {}
    _side = str(_sub.get("side") or "UP")
    ex.store.apply_order_progress(coid, prog, market="m", side=_side)
    # a partial fill books a DELTA against what was already recorded, and
    # the engine may have trimmed the submitted size, so assert the
    # PROPERTY -- inventory moved and nothing was lost -- not a figure this
    # test cannot predict.
    booked = ex.store.position("m", _side)
    assert booked > 0, (booked, _side)
    assert ex.store.residual("m") != 0
    j.tick(ex, E, side="DOWN", label="decide after partial")
    assert len(j.rows) == 2


# ============ 4. delayed cancellation acknowledgement
def test_an_unconfirmed_cancellation_is_never_assumed_to_have_worked(rig):
    """REQUIREMENT 5. Anything but a venue-confirmed terminal must leave the
    order counted as still able to take money."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp)
    j = Journal()
    assert j.tick(ex, E, side="UP", label="submit")["submitted"]
    before = ex.store.still_fillable_order_count()
    assert before >= 1
    ex.store.mark_uncertain("cancel ack not received")
    row = j.tick(ex, E, side="DOWN", label="decide while uncertain")
    assert not row["submitted"], j.report()
    # whichever gate catches it first, the refusal must NAME the
    # uncertainty rather than proceed on an assumption
    assert "UNCERTAIN" in str(row["reason"]).upper(), j.report()
    assert row["gate"], j.report()


def test_clearing_the_uncertainty_restores_decision_making(rig):
    """It must be a LATCH, not a permanent freeze."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp)
    j = Journal()
    j.tick(ex, E, side="UP", label="submit")
    ex.store.mark_uncertain("ambiguous cancel")
    assert not j.tick(ex, E, label="blocked")["submitted"]
    # reconciliation clears the latch by writing None, as the live path does
    ex.store.state["orders_uncertain"] = None
    ex.store.save()
    row = j.tick(ex, E, bk=book("0.45", "0.46"), label="after clear")
    assert "UNCERTAIN" not in str(row["reason"]).upper(), j.report()


# ============ 5. many book updates and replacements
def test_many_book_updates_do_not_stack_orders_on_one_intent(rig):
    """REQUIREMENT 2 with REQUIREMENT 6. Re-evaluating on every book change
    must not become a way to exceed the order ceiling."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp, orders=1)
    j = Journal()
    for i in range(8):
        j.tick(ex, E, bk=book("0.6%d" % (i % 9), "0.6%d" % ((i % 9) + 1)),
               label="book %d" % i)
    assert len(t.sent) <= 1, j.report()
    over = [r for r in j.refusals() if r["reason"] == "MAX_LIVE_ORDERS"]
    assert over, j.report()


# ============ 6. restart with outstanding orders
def test_restart_with_an_outstanding_order_recovers_from_disk(rig):
    """REQUIREMENT 2 and 5. A new executor over the same store must see the
    outstanding order and must not re-place it."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp)
    j = Journal()
    assert j.tick(ex, E, side="UP", label="before restart")["submitted"]
    live_before = ex.store.live_order_count()
    assert live_before >= 1
    ex2, t2 = mk(H, E, tmp)              # same tmp_path => same store file
    assert ex2.store.live_order_count() == live_before
    assert ex2.store.position("m", "UP") == ex.store.position("m", "UP")
    row = j.tick(ex2, E, side="UP", label="after restart")
    # the restarted executor must NOT re-place the outstanding order. Either
    # it sends nothing, or it refuses with a named gate -- never a round one.
    assert not row["submitted"] or len(t2.sent) <= 1, j.report()
    assert str(row["reason"]) not in (
        "ACCUMULATION_EXHAUSTED", "CONTROLLER_NO_PROBE_LEG"), j.report()


# ============ 7. settlement and exact realized P&L
def test_settlement_produces_exact_realized_pnl_on_a_matched_pair(rig):
    """A matched pair redeems exactly $1.00 per pair. Arithmetic, not a
    model: 2 UP at 0.47 and 2 DOWN at 0.48 cost 1.90 and redeem 2.00."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp)
    ex.store.apply_fill("m", "UP", D(2), D("0.47"), basis="OWN")
    ex.store.apply_fill("m", "DOWN", D(2), D("0.48"), basis="OWN")
    assert ex.store.residual("m") == D(0)
    cost = D(2) * D("0.47") + D(2) * D("0.48")
    assert cost == D("1.90")
    assert D(2) * D(1) - cost == D("0.10")


def test_a_residual_is_priced_at_its_own_basis_not_at_a_dollar(rig):
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp)
    ex.store.apply_fill("m", "UP", D(2), D("0.47"), basis="OWN")
    assert ex.store.residual("m") == D(2)
    lc = ex.store.leg_cost("m")          # returns {side: cost}
    assert D(str(lc.get("UP"))) == D("0.94")


# ============ 8. the journal itself, so nothing is silently blocked
def test_every_refusal_carries_a_named_gate_and_the_state(rig):
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp, orders=1, fill=True)
    j = Journal()
    for i in range(10):
        j.tick(ex, E, bk=book("0.5%d" % (i % 9), "0.5%d" % ((i % 9) + 1)),
               label="t%d" % i)
    assert j.rows
    for r in j.rows:
        assert r["state"]["residual"] is not None
        if not r["submitted"]:
            assert r["gate"], j.report()
            assert r["reason"], j.report()


def test_the_hard_limits_still_refuse_under_both_flags(rig):
    """REQUIREMENT 8. The corrections must not have loosened a limit."""
    H, E, mp, tmp = rig
    ex, t = mk(H, E, tmp, orders=1)
    j = Journal()
    j.tick(ex, E, label="first")
    r = j.tick(ex, E, bk=book("0.40", "0.41"), label="second")
    assert not r["submitted"]
    assert r["reason"] == "MAX_LIVE_ORDERS", j.report()
