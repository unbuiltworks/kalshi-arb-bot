"""ONE CORRECTLY MANAGED PAIR, END TO END AND DETERMINISTIC.

The 1215z window filled twenty first legs in 2.47 seconds and submitted no
complement at all, so complement management has never once run against a
real fill. These tests drive the whole sequence the owner specified:

  one first-leg submission -> partial fill -> inventory recognition
  -> correctly sized complement -> complement fill -> matched inventory

against a fake PMUS that serves ONLY the four endpoints the real venue
serves. `/v1/orders` is HTTP 501 there and every history and trade path is
404, so the fake refuses them: if the code reaches for one again, these
tests fail rather than pass against a venue that would have refused.

They also prove the four invariants that make the sequence safe:

  * a resting FIRST LEG blocks another first leg;
  * a resting first leg does NOT block the complement that closes it;
  * repeated first legs cannot drain the twenty-attempt window budget
    while one is resting;
  * exposure sums first legs, complements and filled inventory together.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.spec import (FIRST_LEG_QTY,
                                        MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_COHORT_REALIZED_LOSS_USD,
                                        MAX_EXPOSURE_PER_WINDOW_USD)

SLUG = "cpc-btc-updown-15m-2026-10-08-1215z"
UP = "OUTCOME_SIDE_YES"
DOWN = "OUTCOME_SIDE_NO"


class NotFound(Exception):
    def __init__(self, code=404):
        super().__init__("no such order")
        self.code = code


class FakeVenue:
    """A PMUS that only answers what PMUS answers.

    Orders live in `self.orders` keyed by id. `rest()` creates a resting
    order, `fill()` fills some or all of it. Positions are DERIVED from
    cumulative fills, exactly as the real venue derives them, so the
    module's positions cross-check is exercised rather than hand-fed.
    """

    def __init__(self):
        self.orders = {}
        self.reads = []

    # ---------------------------------------------------- venue mutations
    def rest(self, oid, *, side, wire, qty="0.13", slug=SLUG):
        self.orders[oid] = {
            "id": oid, "marketSlug": slug, "outcomeSide": side,
            "state": "ORDER_STATE_NEW", "quantity": qty,
            "cumQuantity": "0", "leavesQuantity": qty,
            "price": {"value": wire, "currency": "USD"}}
        return oid

    def fill(self, oid, qty):
        o = self.orders[oid]
        cum = D(o["cumQuantity"]) + D(str(qty))
        leaves = D(o["quantity"]) - cum
        assert leaves >= 0, "a venue cannot fill more than the order"
        o["cumQuantity"] = str(cum)
        o["leavesQuantity"] = str(leaves)
        o["state"] = ("ORDER_STATE_FILLED" if leaves == 0
                      else "ORDER_STATE_PARTIALLY_FILLED")

    def cancel(self, oid):
        o = self.orders[oid]
        o["state"] = "ORDER_STATE_CANCELED"
        o["leavesQuantity"] = "0"

    # ------------------------------------------------------- the four GETs
    def reader(self):
        def _read(path):
            self.reads.append(path)
            if path == OS.OPEN_PATH:
                return {"orders": [o for o in self.orders.values()
                                   if o["state"] in ("ORDER_STATE_NEW",
                                                     "ORDER_STATE_PARTIALLY"
                                                     "_FILLED")]}
            if path == OS.POSITIONS_PATH:
                net = {}
                for o in self.orders.values():
                    c = D(o["cumQuantity"])
                    if c > 0:
                        net[o["marketSlug"]] = net.get(
                            o["marketSlug"], D("0")) + c
                return {"positions": {
                    k: {"netPositionDecimal": str(v)}
                    for k, v in net.items()},
                    "availablePositions": [], "eof": True}
            if path.startswith("/v1/portfolio/activities"):
                return {"activities": [], "eof": True}
            if path.startswith("/v1/order/"):
                oid = path.rsplit("/", 1)[-1]
                if oid in self.orders:
                    return {"order": self.orders[oid]}
                raise NotFound()
            raise AssertionError(
                "PMUS does not serve %r: /v1/orders is 501 and history and "
                "trade paths are 404" % path)
        return _read

    def view(self, *, known, roles, now=1000.0, **kw):
        return OS.read_order_state(self.reader(), slugs=[SLUG],
                                   outcome_up=UP, now=now,
                                   known_ids=known, roles=roles, **kw)


# =====================================================================
# REQUIREMENT 3: the deterministic end-to-end sequence
# =====================================================================

def test_first_leg_partial_fill_inventory_complement_matched():
    """The whole pair, step by step, with the state asserted at each step.

    Deterministic: no randomness, no clock dependence beyond the injected
    `now`, and every number is checked rather than described.
    """
    v = FakeVenue()
    known, roles = [], {}

    # ---- STEP 0: nothing outstanding, a first leg is permitted ----------
    s0 = v.view(known=known, roles=roles)
    assert s0["ok"] and s0["n_outstanding_first_legs"] == 0
    assert OS.outstanding_for_snapshot(s0) == 0
    assert OS.reconcile_at_startup(s0)["may_submit"] is True
    assert OS.complement_gate(s0)["may_submit_complement"] is False, \
        "there is nothing to complement yet"

    # ---- STEP 1: ONE first leg rests. UP at own-side 0.49 --------------
    fl = v.rest("FL1", side=UP, wire="0.49")
    known.append(fl)
    roles[fl] = OS.FIRST_LEG
    s1 = v.view(known=known, roles=roles)
    assert s1["n_outstanding_first_legs"] == 1
    assert s1["outstanding_first_leg_ids"] == ["FL1"]
    # a SECOND first leg is refused, by the rule that could not fire before
    assert OS.outstanding_for_snapshot(s1) == 1
    assert bool(OS.outstanding_for_snapshot(s1))
    # exposure: 0.13 x 0.49 resting, nothing filled
    assert D(s1["open_notional_usd"]) == D("0.13") * D("0.49")
    assert D(s1["filled_notional_usd"]) == 0

    # ---- STEP 2: PARTIAL fill, 0.05 of 0.13 ----------------------------
    v.fill(fl, "0.05")
    s2 = v.view(known=known, roles=roles)
    assert s2["partially_filled_ids"] == ["FL1"]
    # still outstanding: 0.08 rests and can still fill
    assert s2["n_outstanding_first_legs"] == 1
    # INVENTORY RECOGNISED: 0.05 on the UP side, unmatched
    assert s2["net_filled_by_outcome_side"] == {UP: "0.05"}
    assert s2["residual"]["outcome_side"] == UP
    assert D(s2["residual"]["qty"]) == D("0.05")
    # the venue's own position corroborates it
    assert s2["venue_positions"] == {SLUG: "0.05"}
    assert s2["positions_corroborate_our_orders"] is True
    # exposure now splits: 0.08 resting + 0.05 held
    assert D(s2["open_notional_usd"]) == D("0.08") * D("0.49")
    assert D(s2["filled_notional_usd"]) == D("0.05") * D("0.49")

    # ---- STEP 3: the COMPLEMENT is permitted, sized to the fill --------
    g2 = OS.complement_gate(s2)
    assert g2["may_submit_complement"] is True, g2
    assert D(g2["qty_to_complement"]) == D("0.05")
    assert g2["blocked_by_first_leg_guard"] is False
    assert g2["n_outstanding_first_legs"] == 1, \
        "the first leg IS outstanding, and that is not a blocker"

    # ---- STEP 4: full fill of the first leg; complement resizes --------
    v.fill(fl, "0.08")
    s4 = v.view(known=known, roles=roles)
    assert s4["filled_ids"] == ["FL1"]
    assert s4["n_outstanding_first_legs"] == 0, "nothing rests any more"
    assert D(s4["residual"]["qty"]) == D("0.13")
    g4 = OS.complement_gate(s4)
    assert D(g4["qty_to_complement"]) == D("0.13"), \
        "the complement is sized to the CUMULATIVE fill, not the first " \
        "partial"

    # ---- STEP 5: complement rests. DOWN at own-side 0.47 (pair 0.96) ---
    # wire for the NO side is 1 - 0.47 = 0.53
    cp = v.rest("CP1", side=DOWN, wire="0.53", qty="0.13")
    known.append(cp)
    roles[cp] = OS.COMPLEMENT
    s5 = v.view(known=known, roles=roles)
    # THE DECISIVE SPLIT: the complement is outstanding, and it does NOT
    # register as an outstanding first leg.
    assert s5["n_outstanding_complements"] == 1
    assert s5["outstanding_complement_ids"] == ["CP1"]
    assert s5["n_outstanding_first_legs"] == 0
    assert OS.outstanding_for_snapshot(s5) == 0
    # a SECOND complement is refused while this one rests
    assert OS.complement_gate(s5)["may_submit_complement"] is False
    assert "COMPLEMENT_ALREADY_OUTSTANDING:1:CP1" in \
        OS.complement_gate(s5)["because"][0]
    # own-side price of a NO order at wire 0.53 is 0.47
    cprow = [o for o in s5["orders"] if o["id"] == "CP1"][0]
    assert cprow["own_side_price"] == "0.47"

    # ---- STEP 6: complement FILLS. inventory is MATCHED ---------------
    v.fill(cp, "0.13")
    s6 = v.view(known=known, roles=roles)
    assert s6["net_filled_by_outcome_side"] == {UP: "0.13", DOWN: "0.13"}
    assert D(s6["matched_qty"]) == D("0.13")
    assert s6["residual"] is None, "a matched pair has no residual"
    assert s6["n_outstanding"] == 0
    assert OS.complement_gate(s6)["may_submit_complement"] is False
    assert OS.complement_gate(s6)["because"] == ["NOTHING_TO_COMPLEMENT"]

    # ---- STEP 7: the pair economics, from the venue's own numbers ------
    # 0.13 UP at 0.49 + 0.13 DOWN at 0.47 = 0.13 x 0.96
    assert D(s6["filled_notional_usd"]) == D("0.13") * D("0.96")
    pair_cost_per_share = D(s6["filled_notional_usd"]) / D("0.13")
    assert pair_cost_per_share == D("0.96")
    # and the whole thing sits inside both frozen ceilings
    assert D(s6["aggregate_exposure_usd"]) < MAX_EXPOSURE_PER_WINDOW_USD
    assert D(s6["aggregate_exposure_usd"]) < MAX_COHORT_REALIZED_LOSS_USD


def test_the_sequence_is_deterministic_across_repeat_runs():
    """Same inputs, same numbers, every time."""
    outs = []
    for _ in range(3):
        v = FakeVenue()
        known, roles = [], {}
        v.rest("FL1", side=UP, wire="0.49")
        known.append("FL1")
        roles["FL1"] = OS.FIRST_LEG
        v.fill("FL1", "0.13")
        v.rest("CP1", side=DOWN, wire="0.53")
        known.append("CP1")
        roles["CP1"] = OS.COMPLEMENT
        v.fill("CP1", "0.13")
        s = v.view(known=known, roles=roles)
        outs.append((s["net_filled_by_outcome_side"], s["matched_qty"],
                     s["residual"], s["filled_notional_usd"]))
    assert outs[0] == outs[1] == outs[2]


# =====================================================================
# REQUIREMENT 4: the complement has its OWN authorized path
# =====================================================================

def test_the_complement_path_never_consults_the_first_leg_gate():
    """Read from the SOURCE. `submit_complement` must not call
    `eligible()`, which is where the first-leg E3 guard lives."""
    import inspect
    src = inspect.getsource(R.submit_complement)
    assert "eligible(" not in src, \
        "the complement path must not run the first-leg eligibility gate"
    # it has its own authorization and its own latch
    assert "authorize_complement" in src
    assert "consume_complement" in src
    # and the first-leg path has its own, distinct ones
    fl = inspect.getsource(R.submit_first_leg)
    assert "enforce_before_submission" in fl
    assert "consume_attempt" in fl


def test_a_resting_first_leg_does_not_block_its_own_complement():
    """The failure this prevents: a safety rule that causes the unhedged
    position it was meant to prevent."""
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    v.fill("FL1", "0.06")          # partial: still resting AND has inventory
    s = v.view(known=["FL1"], roles={"FL1": OS.FIRST_LEG})
    assert s["n_outstanding_first_legs"] == 1
    assert bool(OS.outstanding_for_snapshot(s)), "another first leg: no"
    g = OS.complement_gate(s)
    assert g["may_submit_complement"] is True, "its complement: yes"
    assert D(g["qty_to_complement"]) == D("0.06")


def test_an_unroled_id_is_treated_as_a_first_leg():
    """Conservative default: an id we cannot classify blocks a first leg
    rather than being waved through as 'something else'."""
    v = FakeVenue()
    v.rest("MYSTERY", side=UP, wire="0.49")
    s = v.view(known=["MYSTERY"], roles={})
    assert s["n_outstanding_first_legs"] == 1
    assert OS.outstanding_for_snapshot(s) == 1


def test_the_complement_gate_fails_closed_on_unknown_state():
    g = OS.complement_gate({"ok": False, "reason": "ORDER_STATE_STALE"})
    assert g["may_submit_complement"] is False
    assert g["FAILS_CLOSED"] is True
    assert g["blocked_by_first_leg_guard"] is False


# =====================================================================
# REQUIREMENT 5: the 20-attempt budget cannot be drained by first legs
# =====================================================================

def test_twenty_attempts_cannot_be_spent_while_one_first_leg_rests(
        monkeypatch, tmp_path):
    """The 1215z/1230z failure, as a budget question.

    A refused attempt consumes no latch slot and no window exposure, so a
    resting first leg cannot be turned into twenty by retrying.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    tracker = SP.CohortTracker()
    s = v.view(known=["FL1"], roles={"FL1": OS.FIRST_LEG})
    snap = {"window": 0, "window_open": True, "within_handover_tte": True,
            "market": SLUG, "pin": {"slug": SLUG}, "qty": "0.13",
            "first_leg_price": "0.49", "order_state": s,
            "our_outstanding_orders": OS.outstanding_for_snapshot(s),
            "our_open_residual": OS.residual_for_snapshot(s)}
    for _ in range(25):
        el = R.eligible(snap, tracker, now=1000.0)
        assert el["ok"] is False
        assert any(str(r).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
                   for r in el["refusals"])
    # NOTHING was spent: no attempts, no exposure, so the window budget is
    # intact and the twentieth slot is still available for the one order
    # that should eventually be placed.
    assert tracker.window_attempts.get(0, 0) == 0
    assert tracker.window_exposure_usd.get(0, D("0")) == D("0")
    assert tracker.placements == 0
    assert MAX_ATTEMPTS_PER_WINDOW == 20


# =====================================================================
# REQUIREMENT 6: both roles count toward both ceilings
# =====================================================================

def test_window_exposure_counts_first_legs_complements_and_inventory():
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    v.fill("FL1", "0.13")                        # filled inventory
    v.rest("CP1", side=DOWN, wire="0.53")        # resting complement
    s = v.view(known=["FL1", "CP1"],
               roles={"FL1": OS.FIRST_LEG, "CP1": OS.COMPLEMENT})
    e = OS.window_exposure(s, prospective_qty="0.13",
                           prospective_price="0.49")
    assert e["ok"] is True
    assert D(e["filled_notional_usd"]) == D("0.13") * D("0.49")
    assert D(e["open_notional_usd"]) == D("0.13") * D("0.47")
    assert D(e["prospective_usd"]) == D("0.13") * D("0.49")
    assert D(e["total_usd"]) == (D(e["open_notional_usd"])
                                 + D(e["filled_notional_usd"])
                                 + D(e["prospective_usd"]))
    assert e["counts_first_legs_and_complements"] is True
    assert D(e["total_usd"]) < MAX_EXPOSURE_PER_WINDOW_USD


def test_a_matched_pair_costs_at_most_the_frozen_pair_ceiling():
    """0.96 per share across both legs, which is the frozen discipline."""
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    v.fill("FL1", "0.13")
    v.rest("CP1", side=DOWN, wire="0.53")
    v.fill("CP1", "0.13")
    s = v.view(known=["FL1", "CP1"],
               roles={"FL1": OS.FIRST_LEG, "CP1": OS.COMPLEMENT})
    assert D(s["filled_notional_usd"]) / D("0.13") == D("0.96")


def test_exposure_fails_closed_when_state_is_unknown():
    e = OS.window_exposure({"ok": False, "reason": "ORDER_STATE_UNREADABLE"})
    assert e["ok"] is False and e["FAILS_CLOSED"] is True


# =====================================================================
# REQUIREMENT 1: reconciliation ACROSS RESTARTS
# =====================================================================

def test_a_restart_rebuilds_every_state_from_ids_alone():
    """A fresh process knows only the ids on its durable tape. Every one of
    the five states must come back from that plus four GETs."""
    v = FakeVenue()
    v.rest("RESTING", side=UP, wire="0.49")
    v.rest("PARTIAL", side=UP, wire="0.49")
    v.fill("PARTIAL", "0.05")
    v.rest("DONE", side=UP, wire="0.49")
    v.fill("DONE", "0.13")
    v.rest("GONE", side=UP, wire="0.49")
    v.cancel("GONE")
    v.rest("COMP", side=DOWN, wire="0.53")
    v.fill("COMP", "0.13")

    # the restarted process: ids from the tape, roles from the tape, no
    # in-memory state whatsoever.
    known = ["RESTING", "PARTIAL", "DONE", "GONE", "COMP"]
    roles = {"RESTING": OS.FIRST_LEG, "PARTIAL": OS.FIRST_LEG,
             "DONE": OS.FIRST_LEG, "GONE": OS.FIRST_LEG,
             "COMP": OS.COMPLEMENT}
    s = v.view(known=known, roles=roles)
    assert s["ok"], s.get("reason")
    assert s["outstanding_first_leg_ids"] == ["PARTIAL", "RESTING"]
    assert s["outstanding_complement_ids"] == []
    assert s["partially_filled_ids"] == ["PARTIAL"]
    assert sorted(s["filled_ids"]) == ["COMP", "DONE"]
    # inventory: 0.18 up (0.05 + 0.13) against 0.13 down
    assert s["net_filled_by_outcome_side"] == {UP: "0.18", DOWN: "0.13"}
    assert D(s["matched_qty"]) == D("0.13")
    assert D(s["residual"]["qty"]) == D("0.05")
    # and a restart may NOT open a new first leg into this
    rec = OS.reconcile_at_startup(s)
    assert rec["may_submit"] is False
    assert rec["reconciled"] is True
    assert any("E3_OUR_ORDER_IS_OUTSTANDING:2" in b for b in rec["because"])
    assert any("E3_OUR_RESIDUAL_IS_OPEN" in b for b in rec["because"])
    # but the complement that would close the 0.05 is still permitted
    assert OS.complement_gate(s)["may_submit_complement"] is True


def test_a_restart_with_an_empty_tape_cannot_see_an_untracked_order():
    """THE LIMIT OF ID-BASED ATTRIBUTION, STATED AS A TEST.

    If an order's id never reached the tape, a restart cannot read it by
    id. The positions cross-check is what catches it: the venue reports
    inventory our own orders cannot account for, and the view refuses
    rather than trading over it.
    """
    v = FakeVenue()
    v.rest("UNTRACKED", side=UP, wire="0.49")
    v.fill("UNTRACKED", "0.13")
    s = v.view(known=[], roles={})          # empty tape
    assert s["ok"] is False
    assert s["reason"].startswith(
        "ORDER_STATE_INCONSISTENT:VENUE_POSITION_")
    assert bool(OS.outstanding_for_snapshot(s))
    assert OS.reconcile_at_startup(s)["may_submit"] is False


def test_the_venue_is_never_asked_for_an_endpoint_it_does_not_serve():
    """Every path the module touches is one of the four verified ones."""
    v = FakeVenue()
    v.rest("FL1", side=UP, wire="0.49")
    v.view(known=["FL1"], roles={"FL1": OS.FIRST_LEG})
    for p in v.reads:
        assert (p == OS.OPEN_PATH or p == OS.POSITIONS_PATH
                or p.startswith("/v1/order/")
                or p.startswith("/v1/portfolio/activities")), p
    assert OS.OPEN_PATH in v.reads
    assert OS.POSITIONS_PATH in v.reads
    assert "/v1/order/FL1" in v.reads


# =====================================================================
# REQUIREMENT 7: settlement, fees and rebates, without cross-attribution
# =====================================================================

def _resolution(slug, netq, cost, cash, fees=None, outcome="Yes"):
    before = {"netPositionDecimal": netq,
              "cost": {"value": cost, "currency": "USD"},
              "cashValue": {"value": cash, "currency": "USD"},
              "avgPx": {"value": "0.4900", "currency": "USD"},
              "fees": (None if fees is None
                       else {"value": fees, "currency": "USD"}),
              "marketMetadata": {"outcome": outcome, "slug": slug}}
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "positionResolution": {"marketSlug": slug,
                                   "beforePosition": before}}


def _activities_reader(rows):
    def _read(path):
        assert path.startswith("/v1/portfolio/activities")
        return {"activities": list(rows), "eof": True}
    return _read


def test_settlement_is_attributed_only_on_an_exact_quantity_match():
    """The real 1215z row: 2.6000 against our 2.60, cost 1.2740, cash 2.60."""
    rows = [_resolution(SLUG, "2.6000", "1.2740", "2.6")]
    out = OS.settlement_for(_activities_reader(rows), slug=SLUG,
                            expected_qty="2.60")
    assert out["label"] == "SETTLED_VENUE_CONFIRMED_EXACT_QTY_MATCH"
    a = out["attributed"]
    assert a["exact_qty_match"] is True
    assert a["gross_net_usd"] == "1.3260"
    # fees null is NOT zero, and the view says so
    assert a["settlement_fees_usd"] is None
    assert a["settlement_fees_disclosed"] is False


def test_another_strategys_resolution_in_our_market_is_not_attributed():
    """THE SHARED-ACCOUNT RULE. 1,100 activity rows, 901 of them trades,
    mostly other desks at sizes like 16.21 and 23.49. A resolution naming
    our market is not automatically ours."""
    rows = [_resolution(SLUG, "16.2100", "12.8000", "16.21")]
    out = OS.settlement_for(_activities_reader(rows), slug=SLUG,
                            expected_qty="2.60")
    assert out["attributed"] is None
    assert out["label"] == "UNCONFIRMED_NOT_ATTRIBUTABLE_TO_E1"
    # its own figures are still reported, so a human can see WHY
    assert out["rows"][0]["net_position_decimal"] == "16.2100"
    assert out["rows"][0]["exact_qty_match"] is False


def test_the_right_row_is_picked_when_both_are_present():
    rows = [_resolution(SLUG, "16.2100", "12.8000", "16.21"),
            _resolution(SLUG, "2.6000", "1.2740", "2.6")]
    out = OS.settlement_for(_activities_reader(rows), slug=SLUG,
                            expected_qty="2.60")
    assert out["attributed"]["net_position_decimal"] == "2.6000"
    assert len(out["rows"]) == 2


def test_a_disclosed_fee_is_subtracted_and_an_undisclosed_one_is_flagged():
    rows = [_resolution(SLUG, "2.6000", "1.2740", "2.6", fees="0.1181")]
    out = OS.settlement_for(_activities_reader(rows), slug=SLUG,
                            expected_qty="2.60")
    a = out["attributed"]
    assert a["settlement_fees_disclosed"] is True
    assert a["gross_net_usd"] == "1.3260"
    assert a["net_usd_less_undisclosed_fees"] == "1.2079"


def test_a_resolution_for_another_market_is_ignored_entirely():
    rows = [_resolution("cpc-btc-updown-15m-2026-10-08-1230z",
                        "2.6000", "1.2740", "2.6")]
    out = OS.settlement_for(_activities_reader(rows), slug=SLUG,
                            expected_qty="2.60")
    assert out["rows"] == [] and out["attributed"] is None


def test_unreadable_activities_are_an_error_not_a_zero_settlement():
    def _boom(path):
        raise OSError("reset")
    out = OS.settlement_for(_boom, slug=SLUG, expected_qty="2.60")
    assert out["error"].startswith("ACTIVITIES_UNREADABLE")
    assert out["attributed"] is None


def test_no_rebate_is_ever_assumed():
    """CLAUDE.md: nothing is credited until the venue credits it. There is
    no rebate field in a settlement row, so none is invented."""
    rows = [_resolution(SLUG, "2.6000", "1.2740", "2.6")]
    out = OS.settlement_for(_activities_reader(rows), slug=SLUG,
                            expected_qty="2.60")
    assert "rebate" not in str(out).lower()
    assert out["NEVER_APPORTIONED"] is True
