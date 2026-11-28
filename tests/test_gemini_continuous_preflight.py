"""What the frozen controller actually does across a window, and what it does
not enforce on its own.

These are findings ABOUT `continuous_probe`, produced by running it rather than
by reading it. It is the frozen, reviewed artifact and nothing here edits it;
the point is that a reviewer asked to authorize a bounded live probe should be
able to see a whole window's behaviour first, and until now nobody could -- the
controller decides one turn and has never been advanced through a second.
"""
import time
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_preflight as CPF
from src.gemini_execution import continuous_probe as CP

SYM = "GEMI-BTC05M2609181200-UP"


def _states(n=60, ybid="0.55", yask="0.60", start_tte=300.0, step=5.0):
    t0 = 1_789_000_000.0
    out = []
    for i in range(n):
        out.append({
            "market_key": SYM, "state_id": "%s@%d" % (SYM, i),
            "t_utc": t0 + i * step, "state_age_s": 0.0,
            "tte_s": start_tte - i * step, "cohort": "BTC5m",
            "inventory_known": True,
            "outcomes": {
                "Up": {"bid": ybid, "ask": yask, "tick": "0.01"},
                "Down": {"bid": str(D("1") - D(yask)),
                         "ask": str(D("1") - D(ybid)), "tick": "0.01"}}})
    return out


def _fill_legs(legs_by_turn):
    def fills(i, turn):
        leg = legs_by_turn.get(i)
        if not leg:
            return []
        a = next((a for a in (turn.get("actions") or [])
                  if a["leg"] == leg), None)
        return [{"leg": leg, "qty": a["qty"], "price": a["price"]}] if a else []
    return fills


# -------------------------------------------------- the preflight comparison

def test_an_unread_field_is_a_discrepancy_not_a_pass():
    ok, bad = CPF.check_frozen({})
    assert ok is False
    assert len(bad) == len(CPF.FROZEN_ASSUMPTIONS)
    assert all(b["got"] is None for b in bad)


def test_an_exact_match_agrees():
    ok, bad = CPF.check_frozen(dict(CPF.FROZEN_ASSUMPTIONS))
    assert ok is True and bad == []


@pytest.mark.parametrize("field,value", [
    ("quantityIncrement", "0.05"),
    ("priceIncrement", "0.05"),
    ("contract_status", "settled"),
    ("market_state", "closed"),
    ("terms_accepted", False),
])
def test_a_venue_that_answers_differently_is_discrepant(field, value):
    ok, bad = CPF.check_frozen(dict(CPF.FROZEN_ASSUMPTIONS, **{field: value}))
    assert ok is False
    assert [b["field"] for b in bad] == [field], (
        "a preflight that SUCCEEDS is not a preflight that AGREES; the "
        "envelope was sized against the frozen increments")


def test_the_preflight_never_places_anything():
    """Structure, not prose.

    The docstring of the module under test NAMES the things it refuses to do,
    which is the point of it -- so a substring scan flags its own explanation
    and proves nothing. This walks the AST instead: what does it IMPORT, and
    what does it CALL.
    """
    import ast
    tree = ast.parse(open(CPF.__file__, encoding="utf-8").read())

    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
            if (n.module or "").startswith("src.gemini_execution"):
                imported |= {"src.gemini_execution.%s" % a.name
                             for a in n.names}
    assert "asyncio" not in imported, imported
    for banned in ("src.gemini_execution.ws_session",
                   "src.gemini_execution.ws_canary",
                   "src.gemini_execution.arming",
                   "src.gemini_execution.latch"):
        assert banned not in imported, (
            "%s would give this module a way to arm, spend an authorization "
            "or reach a socket" % banned)

    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & {"send", "connect", "request", "post", "submit",
                          "consume", "require"}), called

    assert not [n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef)], (
        "no coroutine: there is nothing here to await on a socket")


# ------------------------------------------------- the controller's behaviour

def test_from_flat_it_opens_BOTH_legs_and_both_are_BUY():
    w = CPF.walk_window(SYM, _states(1))
    d = w["turns"][0]["decided"]
    assert [a["action"] for a in d] == [CP.OPEN, CP.OPEN]
    assert sorted(a["leg"] for a in d) == ["NO", "YES"]
    # what keeps it from being a generic two-sided maker: both rungs BUY, so
    # it accumulates a PAIR rather than quoting both sides of one instrument
    assert all(f["frame"]["params"]["side"] == "BUY"
               for f in CP.frames_for({"actions": d}, SYM))


def test_a_same_side_run_is_EXTEND_and_the_other_leg_is_COMPLETE():
    w = CPF.walk_window(SYM, _states(6),
                        fills=_fill_legs({0: "YES", 1: "YES", 2: "YES"}))
    seq = [[a["action"] for a in t["decided"]] for t in w["turns"][:4]]
    assert seq[0] == [CP.OPEN, CP.OPEN]
    for row in seq[1:4]:
        assert CP.EXTEND in row and CP.COMPLETE in row, row
    # the run built residual, and the opposite leg is trying to reduce it
    assert w["turns"][2]["residual_after"] == "0.03"


def test_a_complement_fill_reduces_the_residual():
    w = CPF.walk_window(SYM, _states(8),
                        fills=_fill_legs({0: "YES", 1: "YES", 2: "YES",
                                          4: "NO", 5: "NO"}))
    res = [t["residual_after"] for t in w["turns"]]
    assert res[2] == "0.03" and res[-1] == "0.01", res
    assert D(w["matched"]) == D("0.02")


def test_the_end_of_window_floors_bite_before_expiry():
    # far enough in that the budget does not stop it first
    inv = CP.Inventory()
    for tte, expect in ((300.0, CP.WINDOW_OPEN),
                        (59.0, CP.WINDOW_COMPLETE_ONLY),
                        (29.0, CP.WINDOW_WITHDRAWN)):
        assert CP.window_phase(tte) == expect
    st = _states(1, start_tte=45.0)[0]
    out = CP.plan(st, inv, now=st["t_utc"])
    assert out["window_phase"] == CP.WINDOW_COMPLETE_ONLY
    assert out["actions"] == []
    assert {h["held_by"] for h in out["holds"]} == {"END_OF_WINDOW"}


def test_no_taker_cross_exists_anywhere_in_the_walk():
    w = CPF.walk_window(SYM, _states(12),
                        fills=_fill_legs({0: "YES", 1: "YES"}))
    for t in w["turns"]:
        for d in t["decided"]:
            assert d["action"] in (CP.OPEN, CP.EXTEND, CP.COMPLETE)
    src = open(CP.__file__, encoding="utf-8").read()
    for bad in ('"SELL"', '"IOC"', '"FOK"', '"MARKET"'):
        assert bad not in src, "%s appears in the frozen controller" % bad


def test_every_hard_bound_holds_across_the_walk():
    w = CPF.walk_window(SYM, _states(60),
                        fills=_fill_legs({0: "YES", 1: "YES", 2: "YES",
                                          4: "NO", 5: "NO"}))
    assert w["bounds_held"]["all_held"] is True, w["bounds_held"]
    assert D(w["spend"]) <= CP.MAX_TOTAL_SPEND
    assert abs(D(w["residual"])) <= CP.MAX_RESIDUAL


def test_the_window_is_terminal_and_never_rolls_forward():
    w = CPF.walk_window(SYM, _states(60))
    assert w["probe_state"] == CP.PROBE_STOPPED
    assert w["stopped_because"]
    with pytest.raises(CP.ProbeStop):
        CP.WindowGuard("GEMI-BTC15M2609181200-UP", auth_id="X")


# --------------------------------------------------------------- THE FINDINGS

def test_FINDING_the_action_budget_is_caller_enforced_not_self_enforced():
    """MAX_ACTIONS binds only a driver that opts in.

    `step()` reads `actions_taken` and passes it to `plan()`; it never raises
    it. Only `record()` raises it, and nothing in the frozen artifact calls
    `record()`. A driver that forgets runs an unbounded number of actions
    while the guard reports 0 taken and every bound check passes -- the same
    shape as the defect 6342171e fixed for this field, one level up.
    """
    b = CPF.budget_is_caller_enforced()
    assert b["verdict"] == "CALLER_ENFORCED"
    assert b["step_increments"] is False
    assert b["record_increments"] is True
    assert b["record_called_inside_the_artifact"] is False

    # and the consequence, measured: a driver that does not call record()
    guard = CP.WindowGuard(SYM, auth_id="X")
    inv = CP.Inventory()
    decided = 0
    for st in _states(40):
        out = guard.step(dict(st), inv, now=st["t_utc"])
        decided += len(out.get("actions") or [])
    assert guard.actions_taken == 0
    assert decided > CP.MAX_ACTIONS, (
        "%d actions decided against a stated maximum of %d, with the guard "
        "reporting 0 taken" % (decided, CP.MAX_ACTIONS))


def test_FINDING_the_budget_counts_re_decisions_not_new_orders():
    """A standing surface re-decides itself every turn, and that spends it.

    `plan()` emits the DESIRED SURFACE each turn and holds no resting-order
    state, so the same YES rung at the same price is decided again on every
    cycle. A driver that records each decided action -- the only way the
    budget binds at all -- exhausts 12 actions in 6 five-second cycles and
    stops the probe ~30s into a 300s contract, having placed 2 distinct
    prices.

    So MAX_ACTIONS is ambiguous between ORDERS PLACED and ACTIONS DECIDED, and
    the frozen artifact cannot disambiguate because it does not know what is
    resting. A driver must diff against its own resting orders, and that diff
    is exactly the part no reviewed code contains yet.
    """
    w = CPF.walk_window(SYM, _states(60),
                        fills=_fill_legs({0: "YES", 1: "YES", 2: "YES",
                                          4: "NO", 5: "NO"}))
    assert w["actions_taken"] == CP.MAX_ACTIONS
    assert w["stopped_because"].startswith("max_actions")
    assert w["turns_run"] == 6, w["turns_run"]

    # 12 actions, but only two distinct (leg, price) pairs were ever wanted
    distinct = {(d["leg"], d["price"])
                for t in w["turns"] for d in t["decided"]}
    assert len(distinct) == 2, distinct
    assert w["turns"][-1]["tte_s"] == 275.0, (
        "the probe stopped with 275s of a 300s contract left, on a budget "
        "meant to bound ORDERS")
