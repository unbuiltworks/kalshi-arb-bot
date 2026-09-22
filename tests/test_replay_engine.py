"""Sequential counterfactual replay, and the gate that licenses believing it.

A counterfactual is the easiest thing in this programme to get wrong because
nothing contradicts it. Two properties are therefore asserted before any arm
is read:

  FIDELITY   fed each recorded cycle's recorded inventory and recorded book,
             the PRODUCTION policy must emit the RECORDED actions -- same
             action, leg, price, qty. A replay that cannot reproduce the run
             it replays may not price an alternative to it.
  OFFLINE    `state()` must build the policy's input without the network read
             `CP.state_from_venue` does for the tick. That is not a
             micro-optimisation: a replay that calls the venue 720 times is
             not a replay of a past state, it is a fresh read of a present one.

The engine drives `CP.plan` ITSELF -- the same function `WindowGuard.step`
calls in the live driver -- so the policy cannot drift from production by
being reimplemented here. Only the RECONCILIATION is modelled separately,
because varying it is the whole point of P2-A.
"""
import glob
import json
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import fill_reconciliation as FRC
from src.gemini_execution import replay_engine as RE
from src.gemini_execution import residual_timeline as RT

COHORT2 = sorted(glob.glob("analysis/gemini/cohort_window_20260922T1[1-4]*.json"))


@pytest.fixture(scope="module")
def reps():
    out = [json.load(open(p)) for p in COHORT2]
    return [r for r in out
            if str(r.get("window", {}).get("symbol", "")).startswith(
                "GEMI-BTC15M260922")]


# -- the gate ---------------------------------------------------------------
def test_the_replayed_policy_reproduces_every_recorded_decision(reps):
    """THE FIDELITY GATE. 720 readable cycles, 0 mismatches, on COHORT-2."""
    sp = CP.probe_params(action_size=D("1"), quote_band=(60.0, 880.0))
    ok = mismatched = unreadable = 0
    first = None
    for rep in reps:
        symbol = rep["window"]["symbol"]
        for dec in rep["decisions"]:
            book = (dec.get("book_state") or {}).get("presented") or {}
            inv = RE.inventory_at(dec["inventory_before_decision"])
            st = RE.state(symbol, float(dec["tte_s"]), book, 1790000000.0)
            if st.get("inventory_known") is False:
                unreadable += 1
                continue
            turn = CP.plan(st, inv, sp=sp, action_size=D("1"),
                           actions_taken=dec["budget_before_decision"]["consumed"])
            got = [(a["action"], a["leg"], str(a["price"]), str(a["qty"]))
                   for a in (turn.get("actions") or [])]
            want = [(a["action"], a["leg"], str(a["price"]), str(a["qty"]))
                    for a in (dec.get("actions") or [])]
            if got == want:
                ok += 1
            else:
                mismatched += 1
                first = first or (symbol, dec["cycle"], got, want)
    assert ok >= 700, ok
    assert mismatched == 0, first
    assert unreadable > 0            # the unreadable branch is exercised


def test_state_matches_the_live_builder_field_for_field():
    """`state()` is `CP.state_from_venue` minus its per-cycle network read."""
    book = {"readable": True, "identity_consistent": True, "tick": "0.01",
            "yes": {"bid": "0.40", "ask": "0.45"},
            "no": {"bid": "0.55", "ask": "0.60"}}
    mine = RE.state("GEMI-BTC15M2609221300-UP", 500.0, book, 1790000000.0)
    assert mine["inventory_known"] is True
    assert mine["outcomes"]["Up"] == {"bid": "0.40", "ask": "0.45",
                                      "tick": "0.01"}
    assert mine["outcomes"]["Down"] == {"bid": "0.55", "ask": "0.60",
                                        "tick": "0.01"}
    assert mine["cohort"] == CP.cohort_label("GEMI-BTC15M2609221300-UP")


def test_an_unreadable_book_never_reaches_the_policy():
    for bad in ({"readable": False}, {"readable": True,
                                      "identity_consistent": False}):
        st = RE.state("GEMI-BTC15M2609221300-UP", 500.0, bad, 1.0)
        assert st["inventory_known"] is False
        assert st["outcomes"] == {}


def test_the_module_is_offline():
    import ast
    src = ast.parse(open("src/gemini_execution/replay_engine.py").read())
    mods = set()
    for n in ast.walk(src):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert mods <= {"decimal", "fractions", "typing", "__future__",
                    "datetime", "src"}, mods


def test_it_is_not_a_bundle_member():
    from src.gemini_execution import bundle
    assert "replay_engine.py" not in set(bundle.MEMBERS)


# -- the reconciliation under test -----------------------------------------
def _w(leg, price, since, qty="1"):
    return {"o": {"leg": leg, "price": price, "qty": qty,
                  "resting_since": since}}["o"]


def test_production_default_never_replaces_for_price_movement():
    working = {"a": _w("NO", "0.29", 0.0)}
    desired = [{"leg": "NO", "price": "0.45", "qty": "1", "action": "COMPLETE"}]
    plan = RE.reconcile(desired, working, now=10000.0)
    assert plan["mutations"] == 0
    assert plan["retain"] and plan["hold"]


def test_the_rest_floor_binds_before_the_threshold():
    working = {"a": _w("NO", "0.29", 9990.0)}
    desired = [{"leg": "NO", "price": "0.99", "qty": "1", "action": "COMPLETE"}]
    plan = RE.reconcile(desired, working, now=10000.0, reprice_ticks=1)
    assert plan["mutations"] == 0
    assert "rest floor" in plan["retain"][0]["why"]


def test_a_rested_order_replaces_once_the_threshold_is_met():
    working = {"a": _w("NO", "0.29", 0.0)}
    desired = [{"leg": "NO", "price": "0.45", "qty": "1", "action": "COMPLETE"}]
    plan = RE.reconcile(desired, working, now=10000.0, reprice_ticks=5)
    assert len(plan["cancel"]) == 1 and len(plan["place"]) == 1
    assert plan["mutations"] == 2             # a replace costs TWO slots


def test_a_move_under_the_threshold_keeps_queue_position():
    working = {"a": _w("NO", "0.29", 0.0)}
    desired = [{"leg": "NO", "price": "0.31", "qty": "1", "action": "COMPLETE"}]
    plan = RE.reconcile(desired, working, now=10000.0, reprice_ticks=5)
    assert plan["mutations"] == 0 and plan["retain"][0]["moved_ticks"] == 2


def test_only_the_reducing_leg_reprices():
    """P2-A is scoped to the stale COMPLEMENT. Repricing the accumulating leg
    would change EXTEND behaviour, which that arm may not do."""
    working = {"a": _w("YES", "0.20", 0.0)}
    desired = [{"leg": "YES", "price": "0.45", "qty": "1", "action": "EXTEND"}]
    assert RE.reconcile(desired, working, now=10000.0,
                        reprice_ticks=1)["mutations"] == 0
    desired[0]["action"] = "COMPLETE"
    assert RE.reconcile(desired, working, now=10000.0,
                        reprice_ticks=1)["mutations"] == 2


def test_terminal_withdrawal_overrides_the_floor():
    working = {"a": _w("NO", "0.29", 9999.0)}
    plan = RE.reconcile([], working, now=10000.0, safety=True)
    assert len(plan["cancel"]) == 1


# -- the overlays -----------------------------------------------------------
def test_a_reducing_action_is_never_gated():
    inv = CP.Inventory()
    inv.apply_fill("YES", 3, "0.40", source="t")
    d = [{"leg": "NO", "price": "0.05", "qty": "1", "action": "COMPLETE"},
         {"leg": "YES", "price": "0.05", "qty": "1", "action": "EXTEND"}]
    out = RE.gate_desired(d, inv, extend_floor=D("0.50"), cap=1)
    assert [a["action"] for a in out] == ["COMPLETE"]


def test_the_cap_counts_the_imbalance_the_fill_would_create():
    inv = CP.Inventory()
    inv.apply_fill("YES", 2, "0.40", source="t")
    d = [{"leg": "YES", "price": "0.40", "qty": "1", "action": "EXTEND"}]
    assert RE.gate_desired(d, inv, cap=3) == d
    assert RE.gate_desired(d, inv, cap=2) == []


def test_improving_never_crosses_the_leg_s_own_ask():
    book = {"yes": {"bid": "0.40", "ask": "0.42"},
            "no": {"bid": "0.58", "ask": "0.60"}}
    d = [{"leg": "YES", "price": "0.40", "qty": "1", "action": "OPEN"}]
    assert RE.improve(d, book, ticks=1)[0]["price"] == "0.41"
    # +2 would be 0.42, which IS the ask: left where the policy put it
    assert RE.improve(d, book, ticks=2)[0]["price"] == "0.40"


# -- the whole run ----------------------------------------------------------
def test_the_production_arm_lands_near_the_confirmed_record(reps):
    """FIDELITY OF THE FILL MODEL, stated as the bound it actually is.

    The confirmed record is 56 fills and $19.96. The replay reproduces the
    terminal position exactly in 10 of 11 windows and overshoots slightly
    overall, because `AT_LEVEL` cannot see queue position: a print at our
    price fills us here and did not always fill us in reality. The bound is
    asserted so that a regression which widened it could not pass unnoticed.
    """
    fills = 0
    spend = D(0)
    exact = 0
    for rep in reps:
        run = RE.replay(rep, tier=RT.AT_LEVEL)
        rec = FRC.reconcile_window(rep)
        inv = rep["window"]["inventory"]
        fills += run["n_fills"]
        spend += D(run["spend_usd"])
        if (run["yes_qty"], run["no_qty"]) == (str(D(inv["yes_qty"])),
                                               str(D(inv["no_qty"]))):
            exact += 1
        assert rec["verdict"] == FRC.COMPLETE
    assert exact >= 10, exact
    assert 56 <= fills <= 60, fills
    assert D("19.96") <= spend <= D("21.50"), spend


def test_the_through_tier_cannot_reproduce_a_maker_fill(reps):
    """WHY THE ARMS RUN AT `AT_LEVEL`. A maker fill prints AT the resting
    price by definition -- a print THROUGH it means the order already filled.
    So THROUGH is the right conservative test for a HYPOTHETICAL order and is
    structurally unable to reproduce a real one."""
    strict = sum(RE.replay(r, tier=RT.THROUGH)["n_fills"] for r in reps)
    at = sum(RE.replay(r, tier=RT.AT_LEVEL)["n_fills"] for r in reps)
    assert strict < at


def test_settlement_identity_holds_on_every_replayed_window(reps):
    side = {"1200": "no", "1215": "no", "1230": "yes", "1245": "no",
            "1300": "no", "1315": "yes", "1330": "no", "1345": "yes",
            "1400": "yes", "1415": "yes", "1430": "no"}
    for rep in reps:
        run = RE.replay(rep, tier=RT.AT_LEVEL)
        s = RE.settle(run, side[rep["window"]["symbol"][-7:-3]])
        assert s["identity_holds"], (rep["window"]["symbol"], s)


def test_a_replay_never_exceeds_the_action_budget(reps):
    for rep in reps:
        run = RE.replay(rep, tier=RT.AT_LEVEL, reprice_ticks=1,
                        action_budget=12)
        assert run["mutations_used"] <= 12, run["symbol"]
