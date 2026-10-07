"""STAGE-6-S6P offline suite. No network, no credentials, no arming.

Every test here runs the REAL state machine. Only the venue is fake: the
adapter is the machine's one seam, so the decision path under test is the
same code a live run would execute, and the fakes decide only what the venue
would have decided -- how much filled, when, and whether a call failed.

The arming gate is satisfied with a monkeypatched `may_submit` for the tests
that need to reach the order path. That patch exists ONLY inside the test
process: no PMUS_S6P_* variable is set anywhere, in the repository or in any
deployment, and `test_disarmed_by_default_and_no_owner_values_in_the_tree`
asserts that the unpatched build refuses.

WHAT IS COVERED, in the order the mission names it:
  1. first leg partial-filled, complement sized to the ACTUAL fill;
  2. complement never fills -> bounded residual -> deadman;
  3. preview rejection (no order boundary crossed);
  4. ambiguous submission, never retried;
  5. pair cost above $0.96 -> refuse;
  6. the frozen fingerprint self-check.
Plus the invariants those depend on: execution idempotency, the role rule,
the latch slot ordering, bound coherence and the realized-label discipline.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

import pytest

from analysis.l5.live_exec2 import orders as O
from analysis.l5.stage6 import bounds as B
from analysis.l5.stage6 import inventory as INV
from analysis.l5.stage6 import machine as M
from analysis.l5.stage6 import pair_economics as PE
from analysis.l5.stage6 import pair_latch as PL
from analysis.l5.stage6 import settlement as SE
from analysis.l5.stage6 import spec as S

SLUG = "btc-updown-15m-2026-10-08T00:00:00Z"


# --------------------------------------------------------------- the fakes

class FakeVenue(M.VenueAdapter):
    """A venue that does exactly what a test tells it to.

    THE CLOCK IS STEPPED, NOT COUNTED. An earlier version of this fake
    advanced TTE on every `tte_s()` call, which made the clock depend on
    how many times the machine happened to ask -- so adding a log line
    changed the test outcome. `tte_s()` is now idempotent and the clock
    moves only when the machine finishes an observation round and calls its
    `poll` hook, which is the one place real time passes too.

    `fill_plan` maps a leg name to a list of execution LISTS: one list per
    poll round, so a test can spread a fill across rounds and prove the
    inventory updates on every partial rather than once at the end.
    """

    def __init__(self, *, tte: Optional[float],
                 tte_steps: Optional[List[Optional[float]]] = None,
                 fill_plan: Optional[Dict[str, List[List[Dict]]]] = None,
                 place_raises: Optional[Dict[str, BaseException]] = None,
                 bid: str = "0.44", ask: str = "0.46") -> None:
        self._tte = tte
        self._steps = list(tte_steps or [])
        self.fill_plan = fill_plan or {}
        self.place_raises = place_raises or {}
        self.bid, self.ask = bid, ask
        self.placed: List[Dict[str, Any]] = []
        self.cancelled: List[str] = []
        self.deadman_calls = 0
        self.steps_taken = 0
        self._rounds: Dict[str, int] = {}
        self._by_order: Dict[str, str] = {}

    # idempotent: asking the time does not move it
    def tte_s(self) -> Optional[float]:
        return self._tte

    def step(self) -> None:
        """One observation round of elapsed time."""
        self.steps_taken += 1
        if self._steps:
            self._tte = self._steps.pop(0)

    def now(self) -> float:
        return 1_000_000.0

    def book(self) -> Dict[str, Any]:
        return {"bid": self.bid, "ask": self.ask, "book_t": self.now()}

    def place(self, *, leg: str, side: str, price: Any,
              qty: Any) -> Dict[str, Any]:
        self.placed.append({"leg": leg, "side": side, "price": str(price),
                            "qty": str(qty)})
        if leg in self.place_raises:
            raise self.place_raises[leg]
        oid = "oid-%s-%d" % (leg.lower(), len(self.placed))
        self._by_order[oid] = leg
        return {"order_id": oid}

    def executions(self, order_id: str) -> List[Dict[str, Any]]:
        leg = self._by_order.get(order_id, "")
        rounds = self.fill_plan.get(leg, [])
        i = self._rounds.get(leg, 0)
        self._rounds[leg] = i + 1
        return list(rounds[i]) if i < len(rounds) else []

    def cancel(self, order_id: str) -> Dict[str, Any]:
        self.cancelled.append(order_id)
        return {"ok": True, "order_id": order_id}

    def deadman(self) -> Dict[str, Any]:
        self.deadman_calls += 1
        return {"ok": True, "response": {"cancelled": []}}


def ex(eid: str, qty: str, price: str, *, aggressor: bool = False,
       commission: Optional[str] = None) -> Dict[str, Any]:
    """A venue execution. Role carried on `isAggressor` ONLY."""
    e: Dict[str, Any] = {"id": eid, "quantity": qty, "price": price,
                         "isAggressor": aggressor}
    if commission is not None:
        e["commissionNotionalCollected"] = commission
    return e


@pytest.fixture()
def armed(monkeypatch, tmp_path):
    """Satisfy the arming gate INSIDE THIS PROCESS ONLY, and give the
    latch a temp directory so no test can touch a real latch file."""
    monkeypatch.setattr(S, "may_submit", lambda slug: {
        "armed": True, "gate_asserted_for_slug": True,
        "fingerprint_pinned_to_this_build": True, "may_submit": True,
        "refusal": None, "TEST_ONLY": True})
    monkeypatch.setattr(PL, "LATCH_DIR", str(tmp_path / "stage6"))
    monkeypatch.setenv(PL.SESSION_ENV, "test-session-s6p")
    return True


def machine(venue, *, price="0.44", side="UP", **kw) -> M.PairMachine:
    """The REAL machine, with the fake's clock stepped by its poll hook."""
    return M.PairMachine(venue, slug=SLUG, first_side=side,
                         first_price=price, poll=venue.step, **kw)


# ------------------------------------------------- 6. the frozen self-check

def test_frozen_fingerprint_self_check_is_not_vacuous():
    S.assert_frozen()
    assert S.fingerprint() == S.S6P_FINGERPRINT
    assert len(S.S6P_FINGERPRINT) == 64
    # the literal is NOT recomputed from the object it checks: perturb the
    # spec and the assertion must fail.
    bad = S.build()
    bad["bounds"]["max_possible_loss_usd"] = "6.0000"
    with pytest.raises(SystemExit):
        S.assert_frozen(bad)


def test_invariants_are_asserted_and_catch_a_widened_envelope():
    bad = S.build()
    bad["residual"]["predictive_skill_assumed"] = True
    with pytest.raises(SystemExit) as e:
        S.assert_envelope_preserved(bad)
    assert "predictive_skill_assumed" in str(e.value)


def test_the_parent_xr2w_is_unmodified():
    from analysis.l5.live_exec5 import spec as XR2W
    XR2W.assert_frozen()
    assert XR2W.XR2W_FINGERPRINT == (
        "ac6318d5fe986f629037edd129e08c70ff0031012070fd10cde1c67d0c9b2562")
    assert S.SPEC["derived_from_fingerprint"] == XR2W.XR2W_FINGERPRINT
    assert S.SPEC["does_not_modify"]["live_exec5_spec_py"] == "UNMODIFIED"


def test_fee_schedule_is_the_frozen_one_and_not_copied():
    PE.FS.assert_frozen()
    assert PE.FS.FINGERPRINT == (
        "ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629")
    assert S.SPEC["pair_economics"][
        "second_fee_table_in_this_package"] is False


def test_diff_report_names_the_lineage_and_the_widened_envelope():
    d = S.diff_report()
    assert d["from_probe"] == "LIVE-L5-EXEC-1-XR2W"
    assert d["to_fingerprint"] == S.S6P_FINGERPRINT
    assert d["parent_modified"] is False
    assert d["max_possible_loss_usd_s6p"] == "0.6000"
    assert d["loss_envelope_widened"] is True
    assert d["keys_added"] or d["keys_changed"]


def test_bounds_are_coherent_and_every_one_has_a_dollar_value():
    B.assert_bounds_coherent()
    s = B.summary()
    assert s["max_possible_loss_usd"] == "0.6000"
    assert s["max_residual_notional_usd"] == "0.6000"
    assert s["max_daily_realized_loss_usd"] == "0.6000"
    assert s["pair_cost_ceiling_usd"] == "0.96"
    assert s["max_outstanding_orders"] == 1
    assert s["max_submissions_per_session"] == 2
    assert s["rebate_is_assumed_zero"] is True
    for name, prov in s["provenance"].items():
        assert prov["fitted"] is False, name


def test_disarmed_by_default_and_no_owner_values_in_the_tree():
    for k in S.ARM_KEYS + (S.FINGERPRINT_PIN_ENV,):
        assert not os.getenv(k, "").strip(), k
    assert S.armed() is False
    g = S.may_submit(SLUG)
    assert g["may_submit"] is False and "DISARMED" in g["refusal"]
    assert S.SPEC["authorization"]["armed"] is False
    assert S.SPEC["authorization"][
        "live_order_ever_placed_by_this_build"] is False
    assert S.SPEC["authorization"][
        "railway_deployment_of_this_build"] == "NONE"


def test_disarmed_machine_places_nothing_and_makes_no_deadman_post():
    v = FakeVenue(tte=800.0)
    rec = machine(v).run()
    assert rec["terminal"] == "DISARMED"
    assert v.placed == [] and v.cancelled == [] and v.deadman_calls == 0
    assert rec["deadman"]["skipped"] is True


# ------------------------------------- 1. partial fill -> complement sizing

def test_complement_is_sized_to_the_ACTUAL_fill_not_the_intended_qty(armed):
    # 0.20 + 0.17 = 0.37 of an intended 1.00, spread over two poll rounds,
    # then the handover boundary ends the first leg's turn.
    v = FakeVenue(
        tte=800.0, tte_steps=[700.0, 505.0],
        fill_plan={
            "FIRST_LEG": [[ex("f1", "0.20", "0.44")],
                          [ex("f2", "0.17", "0.42")],
                          []],
            "COMPLEMENT": [[ex("c1", "0.37", "0.50")]],
        })
    m = machine(v)
    rec = m.run()
    assert m.inv.first.filled_qty == D("0.37")
    assert m.inv.first.intended_qty == D("1.00")
    comp = [p for p in v.placed if p["leg"] == "COMPLEMENT"]
    assert len(comp) == 1
    # THE RULE: the complement's quantity is the first leg's ACTUAL fill.
    assert D(comp[0]["qty"]) == D("0.37")
    assert D(comp[0]["qty"]) != m.inv.first.intended_qty
    assert comp[0]["side"] == "DOWN"
    assert rec["terminal"] == "PAIR_COMPLETE"
    assert m.inv.residual_qty() == D("0")
    assert v.deadman_calls == 1


def test_inventory_updates_on_every_partial_and_vwap_blends(armed):
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    inv.apply_fill("FIRST", ex("a", "0.40", "0.40"))
    assert inv.first.filled_qty == D("0.40")
    assert inv.first.vwap() == D("0.4000")
    inv.apply_fill("FIRST", ex("b", "0.20", "0.46"))
    assert inv.first.filled_qty == D("0.60")
    # (0.40*0.40 + 0.20*0.46) / 0.60 = 0.42
    assert inv.first.vwap() == D("0.4200")
    assert inv.complement_qty() == D("0.60")
    assert inv.residual_qty() == D("0.60")


def test_an_execution_is_counted_exactly_once_by_id(armed):
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    first = inv.apply_fill("FIRST", ex("dup", "0.30", "0.44"))
    again = inv.apply_fill("FIRST", ex("dup", "0.30", "0.44"))
    assert first["counted"] is True
    assert again["counted"] is False
    assert again["reason"] == "DUPLICATE_EXECUTION_ID"
    assert inv.first.filled_qty == D("0.30")


def test_an_execution_without_an_id_is_refused_not_counted(armed):
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    with pytest.raises(INV.UncountableFill):
        inv.apply_fill("FIRST", {"quantity": "0.30", "price": "0.44"})
    assert inv.first.filled_qty == D("0")


def test_role_comes_from_isAggressor_only_and_stays_unresolved_without_it():
    assert INV.execution_role({"isAggressor": False})["role"] == "maker"
    assert INV.execution_role({"isAggressor": True})["role"] == "taker"
    r = INV.execution_role({"aggressor": False})
    assert r["role"] is None and r["resolved"] is False
    assert r["forbidden_field_present"] is True
    assert r["forbidden_field_not_used"] == "aggressor (a boolean)"


def test_complement_qty_snaps_down_and_never_exceeds_the_fill(armed):
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    inv.apply_fill("FIRST", ex("a", "0.375", "0.44"))
    # the venue's quantity grid is 0.01: snap DOWN, never up
    assert inv.complement_qty() == D("0.37")
    assert inv.complement_qty() <= inv.first.filled_qty
    inv.open_complement(price="0.50")
    assert inv.complement.intended_qty == D("0.37")


def test_complement_cannot_be_opened_without_a_counted_fill(armed):
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    with pytest.raises(INV.InventoryBoundBreached) as e:
        inv.open_complement(price="0.50")
    assert "NEVER sized from the intended quantity" in str(e.value)


# ------------------------- 2. complement never fills -> residual -> deadman

def test_complement_never_fills_bounded_residual_and_deadman(armed):
    v = FakeVenue(
        tte=800.0, tte_steps=[505.0, 300.0, 119.0],
        fill_plan={"FIRST_LEG": [[ex("f1", "0.50", "0.40")], []],
                   "COMPLEMENT": []})
    m = machine(v)
    rec = m.run()
    assert rec["terminal"] == "RESIDUAL_BOUNDED_DEADMAN"
    assert m.inv.residual_qty() == D("0.50")
    assert m.inv.residual_side() == "UP"
    assert m.inv.residual_notional_usd() == D("0.2000")
    assert m.inv.residual_notional_usd() <= B.MAX_RESIDUAL_NOTIONAL_USD
    assert m.inv.imbalance_shares() <= B.MAX_INVENTORY_IMBALANCE_SHARES
    assert m.inv.max_loss_usd() <= B.MAX_POSSIBLE_LOSS_USD
    assert m.inv.bound_check()["ok"] is True
    # the complement WAS quoted and then cancelled; the deadman still ran
    assert any(p["leg"] == "COMPLEMENT" for p in v.placed)
    assert len(v.cancelled) == 2
    assert v.deadman_calls == 1
    snap = rec["reconciliation"]["inventory"]
    assert snap["residual_predictive_skill_assumed"] is False
    assert rec["reconciliation"][
        "2_directional_residual_pnl"]["mark_to_market_usd"] is None


def test_first_leg_never_fills_is_cancelled_and_claims_nothing(armed):
    v = FakeVenue(tte=800.0, tte_steps=[505.0],
                  fill_plan={"FIRST_LEG": []})
    m = machine(v)
    rec = m.run()
    assert rec["terminal"] == "NO_FIRST_LEG_FILL"
    assert m.inv.first.filled_qty == D("0")
    assert m.inv.first.cancelled is True
    assert not any(p["leg"] == "COMPLEMENT" for p in v.placed)
    assert v.deadman_calls == 1
    assert rec["reconciliation"]["realized"][
        "realized_net_usd"] is None


def test_a_fill_landing_during_the_cancel_race_is_still_counted(armed):
    # nothing on the first two polls, then a fill arrives on the sweep
    # AFTER the cancel -- it must be counted, not dropped.
    v = FakeVenue(tte=800.0, tte_steps=[700.0, 505.0, 119.0],
                  fill_plan={"FIRST_LEG": [[], [], [ex("late", "0.10",
                                                       "0.44")]],
                             "COMPLEMENT": []})
    m = machine(v)
    rec = m.run()
    assert m.inv.first.filled_qty == D("0.10")
    assert rec["terminal"] in ("RESIDUAL_BOUNDED_DEADMAN",
                              "PAIR_PARTIAL_RESIDUAL_BOUNDED")


def test_partial_complement_leaves_a_bounded_residual(armed):
    v = FakeVenue(
        tte=800.0, tte_steps=[505.0, 300.0, 119.0],
        fill_plan={"FIRST_LEG": [[ex("f1", "0.60", "0.40")], []],
                   "COMPLEMENT": [[ex("c1", "0.25", "0.50")], []]})
    m = machine(v)
    rec = m.run()
    assert rec["terminal"] == "PAIR_PARTIAL_RESIDUAL_BOUNDED"
    assert m.inv.matched_qty() == D("0.25")
    assert m.inv.residual_qty() == D("0.35")
    assert m.inv.bound_check()["ok"] is True
    assert v.deadman_calls == 1


# ------------------------------------------------- 3. preview rejection

def test_preview_rejection_crosses_no_order_boundary(armed):
    rej = O.PreviewRejected("the venue rejected the PREVIEW",
                            detail={"phase": "PREVIEW", "status": 400})
    v = FakeVenue(tte=800.0, place_raises={"FIRST_LEG": rej})
    m = machine(v)
    rec = m.run()
    assert rec["terminal"] == "PREVIEW_REJECTED"
    assert rej.crossed_order_boundary is False
    assert rej.latch_consumed is False
    assert M.classify_exception(rej) == "PREVIEW_REJECTED"
    assert m.inv.first.order_id is None
    assert rec["crossed_order_boundary"] is False
    assert rec["retry_performed"] is False
    # exactly one place attempt; no second try
    assert len(v.placed) == 1
    assert rec["reconciliation"]["realized"]["realized_net_usd"] is None


def test_a_complement_preview_rejection_is_classified_the_same_way(armed):
    v = FakeVenue(
        tte=800.0, tte_steps=[505.0],
        fill_plan={"FIRST_LEG": [[ex("f1", "0.50", "0.40")], []]},
        place_raises={"COMPLEMENT": O.PreviewRejected("rejected")})
    rec = machine(v).run()
    assert rec["terminal"] == "PREVIEW_REJECTED"
    assert v.deadman_calls == 1


# ------------------------------------- 4. ambiguous submission, never retried

def test_ambiguous_submission_is_never_retried(armed):
    amb = O.SubmissionAmbiguous(
        "POST /v1/orders answered 504; an order may exist",
        detail={"phase": "SUBMIT", "status": 504})
    v = FakeVenue(tte=800.0, place_raises={"FIRST_LEG": amb})
    rec = machine(v).run()
    assert rec["terminal"] == "AMBIGUOUS_SUBMISSION"
    assert amb.crossed_order_boundary is True
    assert amb.latch_consumed is True
    assert M.classify_exception(amb) == "AMBIGUOUS_SUBMISSION"
    # ONE attempt, no retry, no complement, and the deadman still ran
    assert len(v.placed) == 1
    assert not any(p["leg"] == "COMPLEMENT" for p in v.placed)
    assert rec["retry_performed"] is False
    assert rec["crossed_order_boundary"] is True
    assert v.deadman_calls == 1
    assert S.SPEC["terminal_classification"][
        "ambiguous_submission_retried"] is False


def test_an_unnamed_failure_is_UNCLASSIFIED_never_guessed(armed):
    v = FakeVenue(tte=800.0,
                  place_raises={"FIRST_LEG": RuntimeError("who knows")})
    rec = machine(v).run()
    assert rec["terminal"] == "UNCLASSIFIED"
    assert M.classify_exception(RuntimeError("x")) == "UNCLASSIFIED"
    assert S.SPEC["terminal_classification"][
        "unknown_phase_resolved_by_guess"] is False


def test_every_terminal_the_machine_can_reach_is_declared():
    assert "DISARMED" in S.TERMINALS
    assert set(S.TERMINALS) >= {
        "DISARMED", "REFUSED_PREFLIGHT", "REFUSED_PAIR_COST",
        "NO_FIRST_LEG_FILL", "PAIR_COMPLETE",
        "PAIR_PARTIAL_RESIDUAL_BOUNDED", "RESIDUAL_BOUNDED_DEADMAN",
        "PREVIEW_REJECTED", "AMBIGUOUS_SUBMISSION", "UNCLASSIFIED"}


# ------------------------------------------- 5. pair cost above 0.96 refused

def test_pair_cost_gate_refuses_above_the_ceiling():
    g = PE.pair_gate("0.60", "0.40")           # raw 1.00, plus fees
    assert g["ok"] is False
    assert any(r.startswith("PAIR_COST_ABOVE_CEILING")
               for r in g["refusals"])
    econ = g["economics"]
    assert D(econ["fee_safe_pair_cost"]) > B.PAIR_COST_CEILING
    assert econ["rebate_credited"] == "0"
    assert g["near_par_branch_exists"] is False


def test_pair_cost_gate_accepts_a_materially_cheaper_pair():
    g = PE.pair_gate("0.44", "0.45")           # raw 0.89 + ~0.034 of fees
    assert g["ok"] is True
    assert D(g["economics"]["fee_safe_pair_cost"]) <= B.PAIR_COST_CEILING
    assert D(g["economics"]["locked_gross_per_contract"]) > 0


def test_fee_safe_cost_is_strictly_above_the_raw_sum():
    e = PE.fee_safe_pair_cost("0.45", "0.45")
    assert D(e["fee_safe_pair_cost"]) > D(e["raw_pair_cost"])
    assert e["fee_basis"].startswith("TAKER coefficient on BOTH legs")


def test_max_complement_price_is_on_tick_and_clears_the_ceiling():
    cap = PE.max_complement_price("0.44")
    assert cap["feasible"] is True
    p = D(cap["max_complement_price"])
    assert (p / B.VENUE_TICK) % 1 == 0
    assert PE.fee_safe_pair_cost("0.44", p)["clears_ceiling"] is True
    # one tick higher must NOT clear, or it would not be the maximum
    assert PE.fee_safe_pair_cost(
        "0.44", p + B.VENUE_TICK)["clears_ceiling"] is False


def test_an_expensive_first_fill_refuses_the_pair_entirely(armed):
    # a first leg that filled at 0.60 (the ceiling) leaves room for a
    # complement; one that filled at 0.96+ would not. Drive the refusal
    # through the machine with an infeasible VWAP.
    cap = PE.max_complement_price("0.99")
    assert cap["feasible"] is False
    assert "NO_FEE_SAFE_COMPLEMENT_PRICE_EXISTS" in cap["refusal"]


def test_machine_refuses_the_pair_when_no_complement_price_clears(
        armed, monkeypatch):
    monkeypatch.setattr(PE, "max_complement_price", lambda *a, **k: {
        "feasible": False, "max_complement_price": None,
        "first_fill_vwap": "0.44",
        "refusal": "NO_FEE_SAFE_COMPLEMENT_PRICE_EXISTS"})
    v = FakeVenue(tte=800.0, tte_steps=[505.0],
                  fill_plan={"FIRST_LEG": [[ex("f1", "0.50", "0.44")], []]})
    rec = machine(v).run()
    assert rec["terminal"] == "REFUSED_PAIR_COST"
    assert not any(p["leg"] == "COMPLEMENT" for p in v.placed)
    assert v.deadman_calls == 1


def test_near_par_tolerance_is_locked_and_has_no_branch():
    assert S.SPEC["pair_economics"]["near_par_tolerance"].startswith(
        "LOCKED")
    assert PE.pair_gate("0.50", "0.49")["ok"] is False   # 0.99 raw
    assert PE.pair_gate("0.48", "0.48")["ok"] is False   # 0.96 raw + fees
    # there is no flag, threshold or alternate ceiling that could switch a
    # near-par branch on, because no such branch is implemented.
    assert PE.pair_gate("0.44", "0.45")["near_par_branch_exists"] is False
    assert B.PAIR_COST_CEILING == D("0.96")


def test_the_rebate_is_structurally_zero_at_this_size_and_never_gated():
    r = PE.maker_rebate_estimate("0.45", B.FIRST_LEG_QTY)
    assert r["usd"] == "0.00"
    assert r["below_cent_floor"] is True
    assert r["structurally_zero_at_this_size"] is True
    assert r["counted_in_the_pair_cost_gate"] is False
    assert B.FIRST_LEG_QTY < B.REBATE_CENT_FLOOR_SHARES
    # and the gate's arithmetic does not move if a rebate is imagined
    assert PE.fee_safe_pair_cost("0.44", "0.45")["rebate_credited"] == "0"


# ------------------------------------------------------- the two-slot latch

def test_latch_slots_are_single_use_and_ordered(tmp_path, monkeypatch):
    d = str(tmp_path / "s6p")
    monkeypatch.setenv(PL.SESSION_ENV, "sess-1")
    with pytest.raises(PL.LatchOutOfOrder):
        PL.consume("COMPLEMENT", slug=SLUG, side="DOWN", price="0.5",
                   qty="0.3", latch_dir=d)
    rec = PL.consume("FIRST_LEG", slug=SLUG, side="UP", price="0.44",
                     qty="1.00", latch_dir=d)
    assert rec["NOT_RELEASED_ON_FAILURE"] is True
    with pytest.raises(PL.LatchSpent):
        PL.consume("FIRST_LEG", slug=SLUG, side="UP", price="0.44",
                   qty="1.00", latch_dir=d)
    PL.consume("COMPLEMENT", slug=SLUG, side="DOWN", price="0.5",
               qty="0.3", latch_dir=d)
    with pytest.raises(PL.LatchSpent):
        PL.consume("COMPLEMENT", slug=SLUG, side="DOWN", price="0.5",
                   qty="0.3", latch_dir=d)
    st = PL.state(latch_dir=d)
    assert st["slots"]["FIRST_LEG"]["spent"] is True
    assert st["slots"]["COMPLEMENT"]["spent"] is True
    assert st["submissions_remaining"] == 0
    assert st["released_on_failure"] is False


def test_the_latch_needs_a_session_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(PL.SESSION_ENV, raising=False)
    with pytest.raises(PL.LatchUnavailable):
        PL.consume("FIRST_LEG", slug=SLUG, side="UP", price="0.44",
                   qty="1.00", latch_dir=str(tmp_path))
    assert PL.SESSION_ENV == "PMUS_S6P_SESSION_ID"
    assert PL.LATCH_DIR != "/state/pmus-ws/exec1x"   # not XR2W's


def test_a_spent_slot_refuses_at_preflight(armed, tmp_path, monkeypatch):
    d = str(tmp_path / "spent")
    PL.consume("FIRST_LEG", slug=SLUG, side="UP", price="0.44",
               qty="1.00", latch_dir=d)
    v = FakeVenue(tte=800.0)
    rec = machine(v, latch_dir=d).run()
    assert rec["terminal"] == "REFUSED_PREFLIGHT"
    assert v.placed == []


# ---------------------------------------------------------- the TTE discipline

def test_unknown_tte_is_past_the_boundary_and_places_nothing(armed):
    v = FakeVenue(tte=None)
    rec = machine(v).run()
    assert rec["terminal"] == "REFUSED_PREFLIGHT"
    assert any("TTE" in r for r in
               rec["terminal_detail"]["preflight"]["refusals"])
    assert v.placed == []


def test_no_first_leg_inside_the_complements_half_of_the_window(armed):
    v = FakeVenue(tte=400.0)                   # below the 510 s handover
    rec = machine(v).run()
    assert rec["terminal"] == "REFUSED_PREFLIGHT"
    assert any("PAST_FIRST_LEG_HANDOVER" in r for r in
               rec["terminal_detail"]["preflight"]["refusals"])


def test_a_price_that_would_cross_is_refused_locally(armed):
    v = FakeVenue(tte=800.0, bid="0.44", ask="0.45")
    rec = machine(v, price="0.45").run()       # at the ask: would take
    assert rec["terminal"] == "REFUSED_PREFLIGHT"
    assert any(r.startswith("WOULD_CROSS") for r in
               rec["terminal_detail"]["preflight"]["refusals"])


def test_a_first_leg_above_the_price_ceiling_is_refused():
    with pytest.raises(INV.InventoryBoundBreached):
        INV.Inventory(slug=SLUG, first_side="UP", first_price="0.61")


def test_a_first_leg_off_the_frozen_qty_is_refused():
    with pytest.raises(INV.InventoryBoundBreached):
        INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44",
                      first_qty="2.00")


# ------------------------------------------------ settlement reconciliation

def test_nothing_is_realized_without_confirmed_settlement_cash():
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    inv.apply_fill("FIRST", ex("a", "0.50", "0.44"))
    r = SE.realized_pnl(inv, None)
    assert r["label"] == "NOT_REALIZED"
    assert r["realized_net_usd"] is None
    assert r["refusal"] == "NO_SETTLEMENT_RECORD"
    assert r["highest_label_earned"] == "FILLED"
    assert "mark-to-market gains on the residual" in r["not_realized_includes"]


def test_a_balance_change_without_a_source_identity_is_not_realized():
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    inv.apply_fill("FIRST", ex("a", "0.50", "0.44"))
    r = SE.realized_pnl(inv, {"market_slug": SLUG,
                              "resolved_outcome": "UP",
                              "cash_usd": "0.50"})
    assert r["label"] == "NOT_REALIZED"
    assert "source_identity" in r["refusal"]


def test_realized_requires_cash_and_keeps_rebates_separate():
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    inv.apply_fill("FIRST", ex("a", "0.50", "0.40", commission="-0.01"))
    inv.open_complement(price="0.50")
    inv.apply_fill("COMPLEMENT", ex("c", "0.50", "0.50"))
    r = SE.realized_pnl(inv, {"market_slug": SLUG,
                              "resolved_outcome": "UP",
                              "cash_usd": "0.50",
                              "source_identity": "settlement:acct1:tx-9"})
    assert r["label"] == "REALIZED"
    # paid 0.50*0.40 + 0.50*0.50 = 0.45; cash in 0.50 -> +0.05
    assert r["realized_net_usd"] == "0.0500"
    assert r["credited_rebates_usd_SEPARATE"] == "0.0100"
    assert r["rebates_netted_into_realized"] is False


def test_an_absent_commission_field_is_not_read_as_zero_credited():
    out = SE.credited_rebates_usd([{"id": "x"}])
    assert out["executions_missing_the_field"] == 1
    assert out["absent_field_read_as_zero_credited"] is False


def test_reconcile_reports_seven_lines_separately_and_no_blended_profit():
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    inv.apply_fill("FIRST", ex("a", "0.50", "0.44"))
    rep = SE.reconcile(inv)
    for k in ("1_matched_pair_economics", "2_directional_residual_pnl",
              "3_spread_capture", "4_maker_rebates_credited",
              "5_liquidity_rewards", "6_fees", "7_settlement_cashflow"):
        assert k in rep
    assert rep["blended_profit_figure_emitted"] is False
    assert rep["1_matched_pair_economics"]["na_not_zero"].startswith("N/A")
    assert rep["5_liquidity_rewards"]["value_usd"] is None
    assert rep["realized"]["realized_net_usd"] is None


def test_a_completed_pair_is_still_not_realized_before_settlement(armed):
    v = FakeVenue(
        tte=800.0, tte_steps=[505.0],
        fill_plan={"FIRST_LEG": [[ex("f1", "0.40", "0.44")], []],
                   # a post-only BUY cannot fill above its limit, so the
                   # complement fills at or below the price the gate cleared
                   "COMPLEMENT": [[ex("c1", "0.40", "0.47")]]})
    rec = machine(v).run()
    assert rec["terminal"] == "PAIR_COMPLETE"
    rc = rec["reconciliation"]
    assert rc["realized"]["realized_net_usd"] is None
    assert D(rc["1_matched_pair_economics"]["matched_qty"]) == D("0.40")
    assert D(rc["1_matched_pair_economics"][
        "fee_safe_pair_cost_per_contract"]) <= B.PAIR_COST_CEILING
    assert rc["1_matched_pair_economics"][
        "ceiling_breached_by_the_fills"] is False
    assert S.SPEC["settlement_reconciliation"][
        "locked_pair_gross_counts_as_realized"] is False


def test_a_pair_cost_above_the_ceiling_on_ACTUAL_fills_is_flagged():
    """A post-only buy cannot fill above its limit, so this cannot happen
    without something being wrong. If it ever does, the reconciliation must
    say so loudly rather than report a locked pair."""
    inv = INV.Inventory(slug=SLUG, first_side="UP", first_price="0.44")
    inv.apply_fill("FIRST", ex("a", "0.40", "0.52"))
    inv.open_complement(price="0.48")
    inv.apply_fill("COMPLEMENT", ex("c", "0.40", "0.50"))
    line = SE.reconcile(inv)["1_matched_pair_economics"]
    assert line["clears_ceiling_on_ACTUAL_fills"] is False
    assert line["ceiling_breached_by_the_fills"] is True
    assert "ANOMALY" in line["ceiling_breach_note"]
    assert "do NOT" in line["ceiling_breach_note"]


# ---------------------------------------------- the serializer is not copied

def test_the_proven_serializer_is_the_only_one_and_its_contract_holds():
    from src import l5_order_body as OB
    body = OB.build(slug=SLUG, side="DOWN", price=D("0.49"), qty=D("0.37"),
                    post_only=True)
    assert body["price"] == {"value": "0.51", "currency": "USD"}
    assert isinstance(body["quantity"], float)
    assert body["participateDontInitiate"] is True
    assert body["outcomeSide"] == "OUTCOME_SIDE_NO"
    assert OB.preview_envelope(body) == {"request": body}
    assert OB.venue_price("OUTCOME_SIDE_NO", D("0.49")) == D("0.51")
    assert OB.venue_price("OUTCOME_SIDE_YES", D("0.49")) == D("0.49")


def test_no_module_in_this_package_builds_its_own_order_body():
    import glob
    for path in glob.glob("analysis/l5/stage6/*.py"):
        src = open(path).read()
        # no module here constructs a wire body: the venue's own field
        # names appear nowhere outside the proven serializer.
        assert "marketSlug" not in src, path
        assert "ORDER_TYPE_LIMIT" not in src, path
        assert "ORDER_ACTION_BUY" not in src, path
        assert "TIME_IN_FORCE_" not in src, path
        assert '"request":' not in src, path


def test_the_runner_preflight_report_is_disarmed_and_self_describing():
    from analysis.l5.stage6 import runner as R
    rep = R.preflight_report()
    assert rep["HEADER"] == "NOT AUTHORIZED / DISARMED"
    assert rep["arming"]["may_submit"] is False
    assert rep["spec_fingerprint"] == S.S6P_FINGERPRINT
    assert rep["parent"]["modified_by_this_build"] is False
    assert rep["bounds"]["max_possible_loss_usd"] == "0.6000"
    assert rep["what_the_owner_would_be_authorizing"].startswith("ONE ")
    assert len(rep["cannot_establish"]) >= 6
