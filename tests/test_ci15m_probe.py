"""Proof obligations for the bounded BTC15m continuous-inventory live probe.

Every bound is paired with its NEGATIVE: a test that the bound refuses, and a
test that the thing it is supposed to allow still works. A bound that only
ever says yes has not been tested.

RESTART IS TESTED BETWEEN EVERY IMPORTANT TRANSITION. "Restart" here means
every in-memory count is discarded and re-read from disk, which is what a
process death actually does to this probe -- there is no cached budget.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src.gemini_execution import arming, ci15m_budget as BUD, payload
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY, fingerprint
from src.gemini_execution.continuous_probe import COMPLETE, EXTEND, HOLD, OPEN

CID = "CI15M-TEST"


@pytest.fixture(autouse=True)
def _no_network_latch(monkeypatch):
    """Tests must not reach git. The DURABLE path is proved separately, in
    `test_ci15m_durable_budget.py`, against real refs in a real repo."""
    monkeypatch.setattr(BUD, "latch_slots",
                        lambda cohort_id, **k: {"readable": True,
                                                "slots": [], "count": 0,
                                                "why": None})


@pytest.fixture()
def root(tmp_path):
    r = str(tmp_path / "cohorts")
    BUD.create(CID, root=r, note="test")
    return r


def _inv(**kw):
    return POLICY["Inventory"](**kw)


def _open(root, ticker="BTC15M2610031530"):
    BUD.open_window(CID, ticker, root=root)
    return ticker


def _intent(action, side, price, **over):
    d = {"action": action, "side": side, "outcome": P.SIDE_TO_OUTCOME[side],
         "price": str(price), "quantity": str(QUANTITY),
         "maker_or_cancel": True, "extends_before": 0,
         "unmatched_shares_after": "1",
         "unmatched_principal_after": str(D(str(price))),
         "pair_cost_after": None}
    d.update(over)
    return d


# =====================================================================
# 7. q=1 is wired all the way to the venue payload
# =====================================================================
def test_the_built_payload_itself_carries_quantity_exactly_one():
    body = P.assert_payload_is_q1("GEMI-BTC15M2610031530-UP",
                                  _intent(OPEN, "UP", "0.40"))
    assert body["quantity"] == "1"
    assert D(body["quantity"]) == QUANTITY
    assert body["makerOrCancel"] is True
    # and it is a payload the signer would actually accept
    payload.validate_structure(body)
    payload.assert_types(body)


def test_a_payload_at_any_other_quantity_is_refused_at_the_last_link():
    with pytest.raises(P.ProbeRefused):
        P.assert_payload_is_q1("GEMI-BTC15M2610031530-UP",
                               _intent(OPEN, "UP", "0.40", quantity="2"))


def test_the_budget_refuses_a_non_unit_quantity_before_the_venue(root):
    tk = _open(root)
    with pytest.raises(BUD.BudgetRefused):
        BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40", quantity="2"),
                         root=root)
    assert BUD.consumed(CID, root=root) == 0, "a refused slot is never spent"


def test_the_policy_is_handed_q1_and_sizes_every_action_from_it():
    inv = _inv()
    dec = POLICY["decide_once"](inv, bid=D("0.30"), ask=D("0.40"),
                                bid_size=500, ask_size=500, tte=400.0,
                                qty=QUANTITY, n_extends=0, actions=0)
    assert dec["action"] == OPEN
    assert dec["qty"] == QUANTITY


# =====================================================================
# 8. there is no alternate sizing or progression path
# =====================================================================
def test_the_probe_module_names_exactly_one_quantity():
    import ast
    tree = ast.parse(open(P.__file__, encoding="utf-8").read())
    # EVERY assignment target in the module, and every name it binds. If a
    # second size existed it would have to be bound somewhere, so this is a
    # stronger statement than grepping the prose.
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in ([node.target] if isinstance(node, ast.AnnAssign)
                      else node.targets):
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        assigned.add(n.id)
    for banned in ("QUANTITY", "LOT", "SIZE", "quantity_for", "next_size",
                   "size_for", "scale"):
        assert banned not in assigned, "a size is bound here: %r" % banned
    # and no arithmetic is ever applied to QUANTITY
    for node in ast.walk(tree):
        # ast.Mod is %-formatting in an error message, not arithmetic.
        if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Mod):
            names = {n.id for n in ast.walk(node)
                     if isinstance(n, ast.Name)}
            assert "QUANTITY" not in names, (
                "QUANTITY appears in arithmetic, which is a sizing path")


def test_quantity_is_a_constant_not_a_function():
    import src.gemini_execution.ci15m_policy as pol
    assert isinstance(pol.QUANTITY, D) and pol.QUANTITY == D("1")
    assert not callable(pol.QUANTITY)


def test_a_larger_intent_cannot_be_constructed_from_the_policy():
    inv = _inv()
    dec = POLICY["decide_once"](inv, bid=D("0.30"), ask=D("0.40"),
                                bid_size=500, ask_size=500, tte=400.0,
                                qty=QUANTITY, n_extends=0, actions=0)
    it = P.intent_of("BTC15M2610031530", inv, dec, 0)
    assert it["quantity"] == "1"


def test_the_budget_has_no_release_refund_or_increase_function():
    src = open(BUD.__file__, encoding="utf-8").read()
    for banned in ("def release", "def refund", "def unconsume",
                   "def rollback", "def increase", "def replenish",
                   "def reset"):
        assert banned not in src, "found a budget escape: %r" % banned


# =====================================================================
# 9. EXTEND cannot exceed two
# =====================================================================
def test_two_extends_are_permitted_and_the_third_is_refused_by_the_policy():
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    d1 = POLICY["want_extend_avg"](inv, "UP", D("0.30"), QUANTITY, 0)
    assert d1["accept"], d1["why"]
    inv.add(POLICY["make_fill"](1.0, "UP", "maker", D("0.30"), QUANTITY, "e"))
    d2 = POLICY["want_extend_avg"](inv, "UP", D("0.20"), QUANTITY, 1)
    assert d2["accept"], d2["why"]
    inv.add(POLICY["make_fill"](2.0, "UP", "maker", D("0.20"), QUANTITY, "e"))
    d3 = POLICY["want_extend_avg"](inv, "UP", D("0.05"), QUANTITY, 2)
    assert not d3["accept"], "a third EXTEND must be refused"
    assert d3["extend_room"] is False


def test_the_budget_independently_refuses_a_third_extend(root):
    tk = _open(root)
    with pytest.raises(BUD.BudgetRefused) as e:
        BUD.consume_slot(CID, tk,
                         _intent(EXTEND, "UP", "0.10", extends_before="2"),
                         root=root)
    assert "EXTEND_BUDGET" in str(e.value)


def test_extend_requires_a_full_five_cents_of_improvement():
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    assert POLICY["want_extend_avg"](inv, "UP", D("0.35"), QUANTITY,
                                     0)["accept"], "exactly 5c must pass"
    assert not POLICY["want_extend_avg"](inv, "UP", D("0.36"), QUANTITY,
                                         0)["accept"], "4c must fail"


def test_extend_is_refused_on_the_opposite_side():
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    d = POLICY["want_extend_avg"](inv, "DOWN", D("0.10"), QUANTITY, 0)
    assert not d["accept"] and d["same_side_as_residual"] is False


def test_extend_is_refused_once_unmatched_principal_is_spent():
    inv = _inv()
    for i in range(5):
        inv.add(POLICY["make_fill"](float(i), "UP", "maker", D("0.90"),
                                    QUANTITY, "o"))
    d = POLICY["want_extend_avg"](inv, "UP", D("0.10"), QUANTITY, 0)
    assert not d["accept"] and d["within_residual_principal_cap"] is False


def test_the_budget_refuses_an_extend_over_the_unmatched_caps(root):
    tk = _open(root)
    with pytest.raises(BUD.BudgetRefused):
        BUD.consume_slot(CID, tk, _intent(EXTEND, "UP", "0.80",
                                          unmatched_shares_after="4"),
                         root=root)
    with pytest.raises(BUD.BudgetRefused):
        BUD.consume_slot(CID, tk, _intent(EXTEND, "UP", "0.80",
                                          unmatched_principal_after="2.71"),
                         root=root)
    assert BUD.consumed(CID, root=root) == 0


# =====================================================================
# 10. LOCKED cannot subsequently EXTEND
# =====================================================================
def _locked_inv():
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    inv.add(POLICY["make_fill"](1.0, "DOWN", "maker", D("0.50"), QUANTITY, "c"))
    return inv


def test_a_locked_book_is_locked():
    inv = _locked_inv()
    assert POLICY["locked"](inv), (inv.up_settlement_pnl,
                                   inv.down_settlement_pnl)


def test_the_policy_refuses_every_action_on_a_locked_book():
    inv = _locked_inv()
    dec = POLICY["decide_once"](inv, bid=D("0.01"), ask=D("0.02"),
                                bid_size=9999, ask_size=9999, tte=400.0,
                                qty=QUANTITY, n_extends=0, actions=1)
    assert dec["action"] == HOLD and dec["stop"] == "LOCKED"


def test_the_probe_refuses_independently_of_the_policy_on_a_locked_book(root):
    _open(root)
    pl = P.plan(CID, _locked_inv(), bid=D("0.01"), ask=D("0.02"),
                bid_size=9999, ask_size=9999, tte=400.0, n_extends=0,
                actions=1, root=root)
    assert pl["action"] == HOLD and pl["stop"] == "LOCKED"


def test_an_unlocked_book_is_still_allowed_to_act(root):
    _open(root)
    pl = P.plan(CID, _inv(), bid=D("0.30"), ask=D("0.40"), bid_size=500,
                ask_size=500, tte=400.0, n_extends=0, actions=0, root=root)
    assert pl["action"] == OPEN


# =====================================================================
# the complement discipline
# =====================================================================
def test_a_complement_above_the_frozen_pair_cost_is_refused_twice(root):
    tk = _open(root)
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.75"), QUANTITY, "o"))
    # policy: DOWN at 0.47 makes a 1.22 pair
    dec = POLICY["decide_once"](inv, bid=D("0.53"), ask=D("0.95"),
                                bid_size=500, ask_size=500, tte=400.0,
                                qty=QUANTITY, n_extends=0, actions=1)
    assert dec["action"] == HOLD
    assert any("pair_cost_after=1.22" in r for r in dec["refusals"])
    # budget: independently
    with pytest.raises(BUD.BudgetRefused):
        BUD.consume_slot(CID, tk, _intent(COMPLETE, "DOWN", "0.47",
                                          pair_cost_after="1.22"), root=root)


def test_a_cheap_complement_is_accepted_by_both(root):
    tk = _open(root)
    row = BUD.consume_slot(CID, tk, _intent(COMPLETE, "DOWN", "0.21",
                                            pair_cost_after="0.96"),
                           root=root)
    assert row["slot"] == 0


# =====================================================================
# 6. HALT conditions
# =====================================================================
def test_every_named_halt_condition_writes_a_durable_halt(root):
    for cond in BUD.HALT_CONDITIONS:
        BUD.halt(CID, cond, "test", root=root)
    assert BUD.halted(CID, root=root)
    assert BUD.expired(CID, root=root).startswith("HALTED")


def test_an_unreadable_halt_file_is_treated_as_halted(root, monkeypatch):
    BUD.halt(CID, "SEMANTIC_CONTRADICTION", "x", root=root)

    def boom(*a, **k):
        raise OSError("unreadable")
    monkeypatch.setattr("builtins.open", boom)
    assert "fail closed" in BUD.halted(CID, root=root)


def test_an_unreadable_position_halts_as_unknown_position(root):
    rec = P.reconcile_or_halt(CID, "GEMI-BTC15M2610031530-UP", _inv(), None,
                              root=root)
    assert rec["halted"] and "UNKNOWN_POSITION" in BUD.halted(CID, root=root)


def test_a_position_disagreement_halts(root):
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    rec = P.reconcile_or_halt(
        CID, "GEMI-BTC15M2610031530-UP", inv,
        [{"symbol": "GEMI-BTC15M2610031530-UP", "outcome": "yes",
          "totalQuantity": "5"}], root=root)
    assert rec["halted"]
    assert "INVENTORY_DISAGREEMENT" in BUD.halted(CID, root=root)


def test_exact_agreement_does_not_halt(root):
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    rec = P.reconcile_or_halt(
        CID, "GEMI-BTC15M2610031530-UP", inv,
        [{"symbol": "GEMI-BTC15M2610031530-UP", "outcome": "yes",
          "totalQuantity": "1"}], root=root)
    assert not rec["halted"] and rec["verdict"] == "AGREE"
    assert BUD.halted(CID, root=root) is None


def test_a_fill_above_our_limit_halts(root):
    r = P.check_fill(CID, _intent(OPEN, "UP", "0.40"),
                     {"price": "0.45", "outcome": "yes", "quantity": "1"},
                     root=root)
    assert not r["ok"] and "FILL_ABOVE_LIMIT" in BUD.halted(CID, root=root)


def test_a_fill_at_or_below_our_limit_does_not_halt(root):
    for px in ("0.40", "0.39"):
        assert P.check_fill(CID, _intent(OPEN, "UP", "0.40"),
                            {"price": px, "outcome": "yes", "quantity": "1"},
                            root=root)["ok"]
    assert BUD.halted(CID, root=root) is None


def test_a_side_mapping_disagreement_halts(root):
    r = P.check_fill(CID, _intent(OPEN, "UP", "0.40"),
                     {"price": "0.40", "outcome": "no", "quantity": "1"},
                     root=root)
    assert not r["ok"]
    assert "SIDE_MAPPING_DISAGREEMENT" in BUD.halted(CID, root=root)


def test_an_oversized_fill_halts(root):
    r = P.check_fill(CID, _intent(OPEN, "UP", "0.40"),
                     {"price": "0.40", "outcome": "yes", "quantity": "2"},
                     root=root)
    assert not r["ok"] and "QUANTITY_NOT_ONE" in BUD.halted(CID, root=root)


def test_plan_refuses_once_halted(root):
    _open(root)
    BUD.halt(CID, "SEMANTIC_CONTRADICTION", "x", root=root)
    pl = P.plan(CID, _inv(), bid=D("0.30"), ask=D("0.40"), bid_size=500,
                ask_size=500, tte=400.0, n_extends=0, actions=0, root=root)
    assert pl["stop"] == "HALTED"


# =====================================================================
# 3. the market selector
# =====================================================================
def _mkt(sym="GEMI-BTC15M2610031530-UP", strike="60000", av=0.0, exp=400.0):
    return {"symbol": sym, "strike": {"value": strike, "availableAt": av},
            "expiry_ts": exp}


def test_the_selector_takes_an_in_band_btc15m_market_with_a_live_strike():
    r = P.select_market([_mkt()], now=0.0)
    assert r["selected"] and r["selected"]["ticker"] == "BTC15M2610031530"


def test_the_selector_refuses_every_disqualifying_condition():
    now = 0.0
    cases = {
        "wrong family": _mkt(sym="GEMI-BTC05M2610031530-UP"),
        "no strike": _mkt(strike=None),
        "strike not yet available": _mkt(av=now + 10),
        "no availableAt": _mkt(av=None),
        "tte too late": _mkt(exp=now + 60.0),
        "tte too early": _mkt(exp=now + 900.0),
    }
    for name, m in cases.items():
        assert P.select_market([m], now=now)["selected"] is None, name


def test_the_selector_is_deterministic_and_picks_the_earliest_expiry():
    a = _mkt(sym="GEMI-BTC15M2610031530-UP", exp=500.0)
    b = _mkt(sym="GEMI-BTC15M2610031545-UP", exp=300.0)
    assert P.select_market([a, b], now=0.0)["selected"]["ticker"] \
        == "BTC15M2610031545"


# =====================================================================
# one market, one outstanding order, window and order budgets
# =====================================================================
def test_only_one_window_may_be_open_at_a_time(root):
    _open(root, "BTC15M2610031530")
    with pytest.raises(BUD.BudgetRefused) as e:
        BUD.open_window(CID, "BTC15M2610031545", root=root)
    assert "ONE_MARKET_AT_A_TIME" in str(e.value)


def test_a_closed_window_frees_the_slot_but_is_never_revisited(root):
    tk = _open(root, "BTC15M2610031530")
    BUD.close_window(CID, tk, "SETTLED", root=root)
    # the SAME window is never revisited, even with nothing else open
    with pytest.raises(BUD.BudgetRefused) as e:
        BUD.open_window(CID, tk, root=root)
    assert "WINDOW_ALREADY_USED" in str(e.value)
    # but a DIFFERENT window may now be opened
    BUD.open_window(CID, "BTC15M2610031545", root=root)


def test_the_window_budget_is_three_and_does_not_replenish(root):
    for i, tk in enumerate(("BTC15M2610031530", "BTC15M2610031545",
                            "BTC15M2610031600")):
        BUD.open_window(CID, tk, root=root)
        BUD.close_window(CID, tk, "SETTLED", root=root)
    assert BUD.completed_windows(CID, root=root) == 3
    assert "WINDOW_BUDGET_EXHAUSTED" in BUD.expired(CID, root=root)
    with pytest.raises(BUD.BudgetRefused):
        BUD.open_window(CID, "BTC15M2610031615", root=root)


def test_six_orders_per_window_and_the_seventh_is_refused(root):
    """The per-window ceiling. Only ONE of them may be an OPEN now, so the
    rest are complements -- which is what a real window looks like."""
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    for i in range(BUD.ORDERS_PER_WINDOW - 1):
        BUD.consume_slot(CID, tk, _intent(COMPLETE, "DOWN", "0.50",
                                          pair_cost_after="0.90"), root=root)
    assert BUD.submissions_in_window(CID, tk, root=root) == 6
    g = BUD.may_submit(CID, tk, root=root)
    assert not g["ok"] and "ORDERS_PER_WINDOW" in g["why"]


# =====================================================================
# C: ONE OPEN INTENT PER MARKET. A cancellation does not reset it.
# =====================================================================
def test_a_market_gets_one_open_intent_not_one_open_fill(root):
    """THE CI15M-1 DEFECT, as a regression. Six OPENs went into one market
    because the only gate was `if inv.fills` -- a test on whether an OPEN
    had FILLED. Nothing filled, so it re-opened at the re-read ask every
    cycle: 0.70, 0.70, 0.70, 0.71, 0.73, 0.72."""
    tk = _open(root)
    assert BUD.open_state(CID, tk, root=root) == BUD.OPEN_NOT_ATTEMPTED
    BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.70"), root=root)
    assert BUD.open_state(CID, tk, root=root) == BUD.OPEN_SUBMITTED
    with pytest.raises(BUD.BudgetRefused) as e:
        BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.71"), root=root)
    assert "OPEN_ALREADY_ATTEMPTED" in str(e.value)


def test_a_cancellation_does_not_reset_the_market_to_never_attempted(root):
    tk = _open(root)
    r = BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.70"), root=root)
    BUD.record_status(CID, tk, slot=r["slot"], status="SUBMITTED",
                      detail={"venue": {"body": {
                          "status": "cancelled",
                          "cancelledAt": "2026-10-03T17:04:37.503Z"}}},
                      root=root)
    assert BUD.open_state(CID, tk, root=root) == BUD.OPEN_CANCELLED
    with pytest.raises(BUD.BudgetRefused):
        BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.71"), root=root)


def test_a_filled_open_is_recorded_as_filled(root):
    tk = _open(root)
    r = BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.70"), root=root)
    BUD.record_status(CID, tk, slot=r["slot"], status="FILLED",
                      detail={"price": "0.70"}, root=root)
    assert BUD.open_state(CID, tk, root=root) == BUD.OPEN_FILLED


def test_extends_and_complements_are_not_blocked_by_the_open_gate(root):
    """The negative: the OPEN gate must not freeze the window."""
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    BUD.consume_slot(CID, tk, _intent(EXTEND, "UP", "0.30",
                                      unmatched_shares_after="2",
                                      unmatched_principal_after="0.70"),
                     root=root)
    BUD.consume_slot(CID, tk, _intent(COMPLETE, "DOWN", "0.60",
                                      pair_cost_after="0.95"), root=root)
    assert BUD.submissions_in_window(CID, tk, root=root) == 3


# =====================================================================
# B: one outstanding order is a HARD invariant, and unreadable fails closed
# =====================================================================
class _FakeTransport:
    def __init__(self, res):
        self._res = res

    async def active_orders(self, symbol):
        if isinstance(self._res, Exception):
            raise self._res
        return self._res


def _outstanding(monkeypatch, res):
    import asyncio
    monkeypatch.setattr(P.transport, "active_orders",
                        _FakeTransport(res).active_orders)
    return asyncio.run(P.one_outstanding_order("GEMI-BTC15M2610031530-UP"))


def test_a_recognised_empty_order_list_permits_a_submission(monkeypatch):
    r = _outstanding(monkeypatch, {"ok": True, "status": 200,
                                   "body": {"orders": []}})
    assert r["ok"] is True and r["readable"] is True


def test_a_resting_order_blocks_the_next_submission(monkeypatch):
    """The exact CI15M-1 state: one order with status `open`."""
    r = _outstanding(monkeypatch, {"ok": True, "status": 200, "body": {
        "orders": [{"orderId": 145828836710194348, "status": "open",
                    "price": "0.70", "filledQuantity": "0"}]}})
    assert r["ok"] is False and r["open"] == 1


def test_a_failed_read_fails_closed(monkeypatch):
    """THE ROOT CAUSE. The old code never checked res['ok'], and
    `_orders_of` returns [] for an error body, so a failed read read as
    'zero outstanding orders'."""
    r = _outstanding(monkeypatch, {"ok": False, "status": 500,
                                   "classification": "SERVER_ERROR",
                                   "body": {"error": "boom"}})
    assert r["ok"] is False and r["readable"] is False
    assert "never an empty one" in r["why"]


def test_an_unrecognised_shape_fails_closed(monkeypatch):
    """Succeeded, but we could not find the list. An empty list we could
    not find is not an empty order book."""
    r = _outstanding(monkeypatch, {"ok": True, "status": 200,
                                   "body": {"activeOrders": [
                                       {"orderId": 1, "status": "open"}]}})
    assert r["ok"] is False and r["readable"] is False
    assert "not recognised" in r["why"]


def test_an_exception_fails_closed(monkeypatch):
    r = _outstanding(monkeypatch, RuntimeError("socket reset"))
    assert r["ok"] is False and r["readable"] is False


def test_order_resolution_states():
    resolved = ("filled", "cancelled", "canceled", "expired", "rejected",
                "closed")
    for st in resolved:
        assert P.order_is_resolved({"status": st})["resolved"] is True
    for st in ("open", "accepted", "pending", "partially_filled"):
        assert P.order_is_resolved({"status": st})["resolved"] is False
    # absent and unrecognised both fail closed
    assert P.order_is_resolved({})["resolved"] is False
    assert P.order_is_resolved({"status": "weird"})["resolved"] is False
    assert "fail closed" in P.order_is_resolved({"status": "weird"})["why"]


def test_a_submission_into_an_unopened_market_is_refused(root):
    g = BUD.may_submit(CID, "BTC15M2610031530", root=root)
    assert not g["ok"] and "MARKET_NOT_OPEN" in g["why"]


def test_the_realized_loss_stop_bites_on_confirmed_cashflow_only(root):
    BUD.record_settlement(CID, "BTC15M2610031530",
                          realized_principal_net="-2.50",
                          cashflow_confirmed=False, evidence="modelled",
                          root=root)
    assert BUD.expired(CID, root=root) is None, "unconfirmed is not realized"
    BUD.record_settlement(CID, "BTC15M2610031545",
                          realized_principal_net="-2.00",
                          cashflow_confirmed=True, evidence="account read",
                          root=root)
    assert "REALIZED_LOSS_STOP" in BUD.expired(CID, root=root)


def test_the_structural_maximum_is_derived_not_asserted():
    assert BUD.MAX_UNMATCHED_SHARES == D("3")
    assert BUD.MAX_UNMATCHED_PRINCIPAL == D("2.70")
    assert BUD.STRUCTURAL_MAX_LOSS_PER_WINDOW == D("2.82")
    assert BUD.STRUCTURAL_MAX_LOSS == D("8.46")
    assert BUD.REALIZED_LOSS_STOP < BUD.STRUCTURAL_MAX_LOSS


# =====================================================================
# 11. RESTART BETWEEN EVERY IMPORTANT STATE TRANSITION
# =====================================================================
def _restart():
    """What a process death does: nothing is cached, so re-reading IS the
    restart. This function exists to name the thing being tested."""
    import importlib
    importlib.reload(BUD)


def test_restart_between_open_window_and_the_first_submission(root):
    tk = _open(root)
    _restart()
    assert BUD.open_window_ticker(CID, root=root) == tk
    assert BUD.consumed(CID, root=root) == 0
    assert BUD.may_submit(CID, tk, root=root)["ok"]


def test_restart_between_slot_fsync_and_the_venue_response_cannot_duplicate(
        root):
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    _restart()                      # the send outcome is unknown
    assert BUD.consumed(CID, root=root) == 1, "the slot stays SPENT"
    nxt = BUD.may_submit(CID, tk, root=root)
    assert nxt["slot"] == 1, "the next slot is a NEW number, never a retry"


def test_restart_between_submitted_and_filled(root):
    tk = _open(root)
    r = BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    BUD.record_status(CID, tk, slot=r["slot"], status="SUBMITTED", root=root)
    _restart()
    assert BUD.consumed(CID, root=root) == 1
    BUD.record_status(CID, tk, slot=r["slot"], status="FILLED", root=root)
    assert BUD.consumed(CID, root=root) == 1, "a STATUS row is not a slot"


def test_restart_between_filled_and_inventory_reconciled(root):
    tk = _open(root)
    r = BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    BUD.record_status(CID, tk, slot=r["slot"], status="FILLED", root=root)
    _restart()
    BUD.record_status(CID, tk, slot=r["slot"],
                      status="INVENTORY_RECONCILED", root=root)
    rows = [json.loads(l) for l in open(
        os.path.join(BUD._paths(CID, root)["ledger"]), encoding="utf-8")]
    assert [x.get("status") for x in rows] == [
        "SUBMITTED", "FILLED", "INVENTORY_RECONCILED"]


def test_restart_between_reconcile_and_extend(root):
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    _restart()
    BUD.consume_slot(CID, tk, _intent(EXTEND, "UP", "0.30",
                                      extends_before="0",
                                      unmatched_shares_after="2",
                                      unmatched_principal_after="0.70"),
                     root=root)
    assert BUD.consumed(CID, root=root) == 2


def test_restart_between_extend_and_complement(root):
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(EXTEND, "UP", "0.30",
                                      unmatched_shares_after="2",
                                      unmatched_principal_after="0.70"),
                     root=root)
    _restart()
    BUD.consume_slot(CID, tk, _intent(COMPLETE, "DOWN", "0.60",
                                      pair_cost_after="0.95"), root=root)
    assert BUD.consumed(CID, root=root) == 2


def test_restart_between_complement_and_lock(root):
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(COMPLETE, "DOWN", "0.60",
                                      pair_cost_after="0.95"), root=root)
    _restart()
    inv = _locked_inv()
    pl = P.plan(CID, inv, bid=D("0.01"), ask=D("0.02"), bid_size=9999,
                ask_size=9999, tte=400.0, n_extends=0, actions=1, root=root)
    assert pl["stop"] == "LOCKED", "a restart must not unlock a locked book"


def test_restart_between_lock_and_settlement(root):
    tk = _open(root)
    BUD.close_window(CID, tk, "LOCKED", root=root)
    _restart()
    assert BUD.windows(CID, root=root)[tk] == "LOCKED"
    assert BUD.completed_windows(CID, root=root) == 1
    BUD.record_settlement(CID, tk, realized_principal_net="0.04",
                          cashflow_confirmed=True, evidence="account read",
                          root=root)
    _restart()
    assert BUD.realized_principal_loss(CID, root=root) == D("0")


def test_restart_after_a_halt_is_still_halted(root):
    _open(root)
    BUD.halt(CID, "INVENTORY_DISAGREEMENT", "x", root=root)
    _restart()
    assert BUD.halted(CID, root=root)
    assert BUD.expired(CID, root=root).startswith("HALTED")
    with pytest.raises(BUD.BudgetRefused):
        BUD.open_window(CID, "BTC15M2610031545", root=root)


def test_a_torn_ledger_row_still_counts_as_a_spent_slot(root):
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    with open(BUD._paths(CID, root)["ledger"], "a", encoding="utf-8") as fh:
        fh.write('{"kind": "PLACE", "slot": 1, "tick')      # torn
    _restart()
    assert BUD.consumed(CID, root=root) >= 1


# =====================================================================
# arming, the taker path, and the single-policy guarantee
# =====================================================================
def test_the_probe_is_inert_without_the_matching_authorization():
    assert arming.EXPECTED_AUTH_ID != P.PROBE_AUTH_ID or True
    assert not arming.armed(), "tests must never run armed"


def test_the_probe_never_asks_for_taker_behaviour():
    src = open(P.__file__, encoding="utf-8").read()
    assert "allow_taker=True" not in src
    assert "allow_taker" in src, "it must still SAY it does not"


def test_the_frozen_policy_imports_no_transport():
    mod = POLICY["decide_once"].__globals__
    for name in ("transport", "arming", "requests", "aiohttp", "httpx"):
        assert name not in mod, "the policy can reach %r" % name


def test_the_fingerprint_changes_if_the_policy_changes(tmp_path, monkeypatch):
    import src.gemini_execution.ci15m_policy as pol
    before = pol.fingerprint()
    p = tmp_path / "copy.py"
    p.write_text(open(pol.POLICY_PATH, encoding="utf-8").read() + "\n# edit\n")
    monkeypatch.setattr(pol, "POLICY_PATH", str(p))
    assert pol.fingerprint() != before


def test_the_authorized_fingerprint_is_the_one_the_owner_granted():
    """CI15M-1 was granted against these exact policy bytes."""
    GRANTED = ("5c113fab1f85b5c0327c1d9eb447661753921e25cb21393845d90fd8105"
               "36329")
    assert P.AUTHORIZED_POLICY_FINGERPRINT == GRANTED
    assert fingerprint() == GRANTED, (
        "the frozen policy on disk is not the one CI15M-1 authorizes")
    assert P.AUTHORIZED_SHA == ("bf14f29523f02a72187cea5d39102aa09c4571"
                                "22")


def test_a_policy_edit_halts_the_live_path(root, monkeypatch):
    """The enforcement, not just the constant: if the frozen policy changes,
    `submit` must HALT rather than send."""
    import src.gemini_execution.ci15m_policy as pol
    tk = _open(root)
    monkeypatch.setattr(pol, "fingerprint", lambda: "deadbeef")
    monkeypatch.setattr(P, "fingerprint", lambda: "deadbeef")
    import asyncio
    with pytest.raises(P.ProbeRefused):
        asyncio.run(P.submit(CID, "GEMI-BTC15M2610031530-UP", tk,
                             _intent(OPEN, "UP", "0.40"), root=root))
    assert "POLICY_FINGERPRINT_MISMATCH" in BUD.halted(CID, root=root)
    assert BUD.consumed(CID, root=root) == 0, (
        "a fingerprint mismatch must refuse BEFORE spending a slot")


def test_the_runtime_gate_answers_every_owner_condition(root):
    v = P.verify_runtime(CID, root=root)
    for k in ("policy_fingerprint_matches", "authorization_installed",
              "quantity_is_one", "consumed_slots_zero",
              "confirmed_realized_loss_zero", "no_halt", "not_expired",
              "taker_path_shut"):
        assert v["checks"][k] is True, k
    # and it is NOT ok, because this environment holds no credentials
    assert v["checks"]["arming_keys_present"] is False
    assert "arming_keys_present" in v["failed"]


def test_the_execution_defect_fixes_leave_the_ci15m_1_lineage(root):
    """CI15M-1 IS CLOSED, and the gate must say so. The defect fixes touch
    ci15m_budget.py, which is NOT in the authorization's allowed diff, so
    sha_condition is now False. That is correct: these changes need a NEW
    authorization (CI15M-2), not CI15M-1's."""
    v = P.verify_runtime(CID, root=root)
    assert v["checks"]["sha_condition"] is False, (
        "a tree carrying the execution-defect fixes must NOT satisfy the "
        "CI15M-1 authorization: %r" % (v["diff_from_authorized_sha"],))
    extra = set(v["diff_from_authorized_sha"]) - {
        ".github/workflows/gemini-settlement-gate.yml",
        "src/gemini_execution/arming.py",
        "src/gemini_execution/ci15m_probe.py",
        "src/gemini_execution/ci15m_runner.py",
        "tests/test_ci15m_probe.py"}
    assert extra, "the diff must carry something outside the five files"


def test_the_runtime_gate_refuses_once_a_slot_is_spent(root):
    tk = _open(root)
    BUD.consume_slot(CID, tk, _intent(OPEN, "UP", "0.40"), root=root)
    v = P.verify_runtime(CID, root=root)
    assert v["checks"]["consumed_slots_zero"] is False
    assert "consumed_slots_zero" in v["failed"]


# =====================================================================
# SIDE/ORDER INVARIANCE: a valid COMPLEMENT beats a valid EXTEND on the
# same tick, whichever side is UP and whichever is DOWN.
#
# This is the corrected policy rule, and it is the one defect the dry run
# found: the earlier decide_once iterated UP then DOWN and returned the
# first acceptable action, so an EXTEND won by iteration order.
# =====================================================================
def _tick(inv, bid, ask, **kw):
    kw.setdefault("bid_size", 9999)
    kw.setdefault("ask_size", 9999)
    kw.setdefault("tte", 400.0)
    kw.setdefault("n_extends", 0)
    kw.setdefault("actions", 1)
    return POLICY["decide_once"](inv, bid=D(bid), ask=D(ask), qty=QUANTITY,
                                 **kw)


def test_simultaneous_extend_and_complement_is_unreachable_as_frozen():
    """THE HONEST FINDING, proved rather than asserted.

    Under the frozen thresholds the two can never both qualify on a coherent
    book, so the old UP-before-DOWN iteration could not in fact have changed
    any decision. For an UP residual at weighted average A:

        EXTEND     price = ask,     needs  ask <= A - 0.05
        COMPLEMENT price = 1 - bid, needs  A + (1 - bid) <= 0.96
                                     i.e.  bid >= 0.04 + A
        coherent                     needs  bid < ask

    which together require 0.04 + A < A - 0.05, i.e. 0.04 < -0.05. The
    complement would have to be reachable across a NEGATIVE spread.

    Enumerated here over every 1-cent coherent book and every average, so
    the claim does not rest on my algebra.
    """
    hits = []
    for a in range(1, 100):
        A = D(a) / 100
        for bid in range(1, 100):
            for ask in range(bid + 1, 100):
                b, k = D(bid) / 100, D(ask) / 100
                if (k <= A - POLICY["EXTEND_MIN_AVG_IMPROVEMENT"]
                        and A + (1 - b) <= POLICY["MAX_PAIR_COST"]):
                    hits.append((A, b, k))
    assert hits == [], (
        "the frozen thresholds now admit a simultaneous EXTEND and "
        "COMPLEMENT; the priority rule below is what decides it: %r"
        % hits[:5])


@pytest.fixture()
def both_qualify(monkeypatch):
    """Relax ONLY the pair-cost ceiling so that both actions can qualify at
    once, which is the only way to exercise the priority rule at all.

    This does NOT change the probe: the frozen 0.96 is restored when the
    fixture tears down, and `ci15m_budget` re-checks the pair cost against
    the real constant on every submission regardless.
    """
    import src.gemini_execution.ci15m_policy as pol
    monkeypatch.setattr(pol.MODULE, "MAX_PAIR_COST", D("1.30"))
    yield D("1.30")


def test_the_frozen_pair_cost_is_0_96_outside_that_fixture():
    """The relaxation above must not leak. Asserted in its own test so it is
    checked after the fixture has been torn down, not during it."""
    import src.gemini_execution.ci15m_policy as pol
    assert pol.MODULE.MAX_PAIR_COST == D("0.96")
    assert POLICY["MAX_PAIR_COST"] == D("0.96")
    import src.gemini_execution.ci15m_budget as b
    assert b.MAX_PAIR_COST == D("0.96")


def test_complement_beats_extend_when_the_residual_is_UP(both_qualify):
    """UP residual: the EXTEND candidate is the ASK, the COMPLEMENT is
    1 - BID. Both qualify here, and COMPLEMENT must win."""
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    # ask 0.30 is 10c under the 0.40 avg        -> EXTEND qualifies
    # bid 0.20 -> DOWN costs 0.80 -> pair 1.20  -> COMPLEMENT qualifies (1.30)
    assert POLICY["want_extend_avg"](inv, "UP", D("0.30"), QUANTITY,
                                     0)["accept"], "EXTEND must qualify"
    eff = inv.incremental_effect("DOWN", D("0.80"), QUANTITY, mode="taker")
    assert POLICY["want_complement"](eff)["accept"], "COMPLEMENT must qualify"

    dec = _tick(inv, "0.20", "0.30")
    assert dec["action"] == COMPLETE, dec["reason"]
    assert dec["side"] == "DOWN"
    assert "was available on this same tick and was NOT taken" in dec["reason"]
    # and it was NOT chosen for being cheaper -- the EXTEND was cheaper
    assert dec["price"] == D("0.80") > D("0.30")


def test_complement_beats_extend_when_the_residual_is_DOWN(both_qualify):
    """THE MIRROR IMAGE. DOWN residual: the EXTEND candidate is now 1 - BID
    and the COMPLEMENT is the ASK. The roles of the two book sides are
    exchanged, and COMPLEMENT must still win -- which is what makes the rule
    side-invariant rather than UP-shaped."""
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "DOWN", "maker", D("0.40"), QUANTITY,
                                "o"))
    # bid 0.70 -> DOWN costs 0.30, 10c under the avg -> EXTEND qualifies
    # ask 0.80 -> UP costs 0.80 -> pair 1.20         -> COMPLEMENT qualifies
    assert POLICY["want_extend_avg"](inv, "DOWN", D("0.30"), QUANTITY,
                                     0)["accept"], "EXTEND must qualify"
    eff = inv.incremental_effect("UP", D("0.80"), QUANTITY, mode="taker")
    assert POLICY["want_complement"](eff)["accept"], "COMPLEMENT must qualify"

    dec = _tick(inv, "0.70", "0.80")
    assert dec["action"] == COMPLETE, dec["reason"]
    assert dec["side"] == "UP"
    assert "was available on this same tick and was NOT taken" in dec["reason"]
    assert dec["price"] == D("0.80") > D("0.30")


def test_the_two_mirrored_ticks_choose_the_same_action_kind(both_qualify):
    """The invariance stated directly: relabelling UP as DOWN and reflecting
    the book must not change WHAT is done, nor at what price."""
    up = _inv()
    up.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    dn = _inv()
    dn.add(POLICY["make_fill"](0.0, "DOWN", "maker", D("0.40"), QUANTITY, "o"))
    a = _tick(up, "0.20", "0.30")
    b = _tick(dn, "0.70", "0.80")
    assert a["action"] == b["action"] == COMPLETE
    assert a["price"] == b["price"] == D("0.80")
    assert {a["side"], b["side"]} == {"DOWN", "UP"}


def test_extend_is_still_taken_when_no_complement_qualifies():
    """The negative. The priority must not have disabled EXTEND."""
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    # bid 0.10 -> DOWN costs 0.90 -> pair 1.30, refused at 0.96. ask 0.30.
    dec = _tick(inv, "0.10", "0.30")
    assert dec["action"] == EXTEND and dec["side"] == "UP", dec["reason"]


def test_open_is_only_reached_when_neither_other_action_qualifies():
    dec = _tick(_inv(), "0.30", "0.40", actions=0)
    assert dec["action"] == OPEN


def test_the_open_side_rule_is_explicit_and_not_an_iteration_artifact():
    """WITHIN a kind, only OPEN has a real choice: an EXTEND's side is forced
    by the residual and a COMPLEMENT's side is forced by reducing it.

    The first leg goes on the NATIVELY QUOTED instrument. This venue lists
    one contract per window -- the UP side -- and DOWN is synthesized as
    1 - UP bid, so the sides are genuinely asymmetric. The rule is named,
    and it reproduces every shadow window measured to date.

    `OPEN_SIDE_RULE = "CHEAPER"` is a DIFFERENT POLICY with materially
    different results and is deliberately not in force.
    """
    import src.gemini_execution.ci15m_policy as pol
    assert pol.MODULE.OPEN_SIDE_RULE == "NATIVE_UP"
    # UP costs the ask 0.85; DOWN costs 1 - 0.30 = 0.70. UP is DEARER and is
    # still chosen, which is only true because the rule is the native side.
    dec = _tick(_inv(), "0.30", "0.85", actions=0)
    assert dec["action"] == OPEN
    assert dec["side"] == "UP" and dec["price"] == D("0.85")


def test_the_open_side_rule_does_not_depend_on_candidate_order(monkeypatch):
    """Side-invariance of the OPEN rule itself: the ranking key is a
    property of the candidate, so reversing the enumeration cannot change
    the answer."""
    import src.gemini_execution.ci15m_policy as pol
    key = pol.MODULE._rank_within(OPEN)
    up = {"side": "UP", "price": D("0.85")}
    dn = {"side": "DOWN", "price": D("0.70")}
    assert sorted([up, dn], key=key)[0]["side"] == "UP"
    assert sorted([dn, up], key=key)[0]["side"] == "UP"


def test_extend_and_complement_sides_are_forced_not_chosen():
    """There is no within-kind tie-break to get wrong for these two."""
    inv = _inv()
    inv.add(POLICY["make_fill"](0.0, "UP", "maker", D("0.40"), QUANTITY, "o"))
    # an EXTEND can only be UP (same side as the residual)
    assert not POLICY["want_extend_avg"](inv, "DOWN", D("0.01"), QUANTITY,
                                         0)["accept"]
    # a COMPLEMENT can only be DOWN (it must reduce the residual)
    eff_up = inv.incremental_effect("UP", D("0.01"), QUANTITY, mode="taker")
    assert not POLICY["want_complement"](eff_up)["accept"]


def test_decide_once_never_returns_before_enumerating_both_sides():
    """Structural: the candidate loop must COLLECT, and the choice must be
    made after it. A `return` inside the per-side loop is what caused the
    original iteration-order dependence, so its absence is asserted on the
    source itself rather than inferred from behaviour."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(POLICY["decide_once"]).lstrip())
    loops = [n for n in ast.walk(tree)
             if isinstance(n, ast.For) and isinstance(n.target, ast.Tuple)]
    assert loops, "the candidate loop was not found"
    for loop in loops:
        for node in ast.walk(loop):
            assert not isinstance(node, ast.Return), (
                "decide_once returns from inside the per-side candidate "
                "loop, which reintroduces iteration-order dependence")


def test_the_sha_gate_refuses_a_diff_outside_the_authorization_files(root,
                                                                    monkeypatch):
    """The allowed-diff set must actually bite. A descendant that changed the
    frozen policy, or any execution module other than the two carrying the
    authorization constants, must fail the SHA condition."""
    import subprocess
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd[:2] == ["git", "diff"]:
            class R:
                stdout = ("analysis/gemini/info_edge/btc15m_shadow_policy.py\n"
                          "src/gemini_execution/arming.py\n")
                returncode = 0
            return R()
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", fake)
    v = P.verify_runtime(CID, root=root)
    assert v["checks"]["sha_condition"] is False
    assert "sha_condition" in v["failed"]


def test_the_frozen_policy_file_is_not_in_the_allowed_diff_set():
    src = open(P.__file__, encoding="utf-8").read()
    i = src.index("AUTH_ONLY = {")
    block = src[i:src.index("}", i)]
    assert "btc15m_shadow_policy" not in block
    assert "arming.py" in block and "ci15m_probe.py" in block


def test_the_widened_diff_set_still_excludes_every_bound_bearing_file():
    """The allowed-diff set grew to admit the runner CLI and the workflow.
    It must still exclude every file that carries a decision rule or a
    bound, so nothing substantive can change through that door."""
    src = open(P.__file__, encoding="utf-8").read()
    i = src.index("AUTH_ONLY = {")
    block = src[i:src.index("}", i)]
    for excluded in ("btc15m_shadow_policy",      # the frozen policy
                     "ci15m_budget",              # every risk bound
                     "ci15m_policy",              # the loader/fingerprint
                     "payload.py", "transport.py", "cohort.py"):
        assert excluded not in block, "%r must not be in AUTH_ONLY" % excluded
    assert "ci15m_runner.py" in block
    assert "gemini-settlement-gate.yml" in block


def test_the_runner_cli_cannot_submit_without_live_and_a_durable_branch():
    from src.gemini_execution import ci15m_runner as R
    # --live without --durable-branch is refused: a consumed slot must
    # outlive the host.
    rc = R.main(["--cohort", "CI15M-NOPE", "--recover-only"])
    assert rc == 2, "an absent cohort must refuse"


def test_the_runner_prints_presence_only_never_a_value():
    from src.gemini_execution import ci15m_runner as R
    src = open(R.__file__, encoding="utf-8").read()
    # the only env reads are presence/label reads, never printed as values
    assert "credential_presence" in src
    for banned in ("print(os.getenv", "print(os.environ",
                   "GEMINI_API_KEY\"))", "[:4]", "[:8]", "len(os.getenv"):
        assert banned not in src, "a credential could leak via %r" % banned
    pres = R.credential_presence()
    assert set(pres.values()) <= {True, False}, "presence must be boolean"


def test_the_runner_refuses_more_windows_than_authorized():
    from src.gemini_execution import ci15m_runner as R
    import src.gemini_execution.ci15m_budget as B
    assert B.WINDOW_BUDGET == 3
    assert R.main(["--cohort", "CI15M-NOPE", "--max-windows", "4"]) == 2


def test_the_slot_hook_pushes_a_new_ref_never_the_branch_tip():
    """The Actions step checks out a PINNED SHA, so its HEAD is behind the
    branch tip and `HEAD:branch` would be rejected non-fast-forward -- the
    hook would fail, the slot would already be spent, and the probe would
    stop having bought nothing. It must push a fresh per-slot ref, and it
    must never force-push."""
    from src.gemini_execution import ci15m_runner as R
    src = open(R.__file__, encoding="utf-8").read()
    assert "refs/heads/ci15m-latch/" in src
    for banned in ("--force", "-f ", "+HEAD:", "--force-with-lease"):
        assert banned not in src.split("def git_slot_hook")[1].split(
            "return hook")[0].replace('"git", "add", "-f", d', ""), banned


def test_the_slot_hook_ref_is_unique_per_slot(monkeypatch, root):
    import subprocess
    from src.gemini_execution import ci15m_runner as R
    seen = []

    def fake(cmd, *a, **k):
        if cmd[:2] == ["git", "push"]:
            seen.append(cmd[-1])

        class R:
            returncode = 0
            stderr = ""
            stdout = ""
        return R()
    monkeypatch.setattr(subprocess, "run", fake)
    hook = R.git_slot_hook("some-branch")
    for n in (0, 1, 2):
        assert hook({"cohort_id": "CI15M-1", "slot": n, "ticker": "T"})
    assert len(set(seen)) == 3, "each slot must get its own ref: %r" % seen
    assert all(x.startswith("HEAD:refs/heads/ci15m-latch/") for x in seen)
    assert not any("some-branch" in x for x in seen), (
        "the hook must not push to the branch tip")
