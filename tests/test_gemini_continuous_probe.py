"""The continuous L5 probe: the oscillator, the bounds, and the accounting.

Sized from L5_OPERATING_MODEL.md 2a, not from preference: P(EXTEND) = 0.66 so
the modal same-side run is about 2.9, run 4+ is the OOS-validated persistence
regime, and 84.3% of imbalance episodes end by overshooting THROUGH zero. A
residual cap below 2 x action size cannot produce an EXTEND at all, which is
the degenerate one-order test this probe exists to replace.
"""
import json
from decimal import Decimal as D

import pytest

from src.gemini_execution import continuous_probe as CP
from src.l5_surface import params as PR


def _state(**over):
    s = {"market_key": "GEMI-BTC05M2609181055-UP", "state_id": "s1",
         "t_utc": 1000.0, "tte_s": 200.0, "cohort": "BTC5m",
         "state_age_s": 1.0, "inventory_known": True,
         "outcomes": {"Up": {"bid": "0.64", "ask": "0.93", "tick": "0.01"},
                      "Down": {"bid": "0.36", "ask": "0.65", "tick": "0.01"}},
         "inventory": {}}
    s.update(over)
    return s


# --------------------------------------------------------- the oscillator

def test_flat_inventory_opens_on_both_outcomes():
    p = CP.plan(_state(), CP.Inventory())
    assert [a["action"] for a in p["actions"]] == [CP.OPEN, CP.OPEN]
    assert sorted(a["leg"] for a in p["actions"]) == ["NO", "YES"]


def test_a_leaning_book_extends_one_side_and_completes_the_other():
    inv = CP.Inventory()
    inv.apply_fill("YES", "0.01", "0.64", source="t")
    p = CP.plan(_state(), inv)
    by = {a["leg"]: a["action"] for a in p["actions"]}
    assert by == {"YES": CP.EXTEND, "NO": CP.COMPLETE}


def test_the_oscillator_suppresses_the_widening_side_at_the_cap():
    """The whole point: at the residual cap the engine withdraws the side
    that would widen, leaving only the reducing side quoting. That is what
    turns a run into a flip."""
    inv = CP.Inventory()
    for _ in range(CP.MAX_SAME_SIDE_RUN):
        inv.apply_fill("YES", "0.01", "0.64", source="t")
    assert inv.residual() == CP.MAX_RESIDUAL
    p = CP.plan(_state(), inv)
    assert [a["leg"] for a in p["actions"]] == ["NO"]
    held = [h for h in p["holds"] if h["leg"] == "YES"]
    assert held and held[0]["held_by"] == "OSCILLATOR"
    assert "max_residual" in held[0]["why"]


def test_a_run_of_four_is_reachable_before_suppression():
    """Below this the probe degenerates: a cap of one action size suppresses
    after the FIRST fill and no EXTEND can ever be observed."""
    inv = CP.Inventory()
    seen = []
    for i in range(CP.MAX_SAME_SIDE_RUN):
        p = CP.plan(_state(), inv, actions_taken=i)
        seen.append({a["leg"]: a["action"] for a in p["actions"]}.get("YES"))
        inv.apply_fill("YES", "0.01", "0.64", source="t")
    assert seen == [CP.OPEN] + [CP.EXTEND] * (CP.MAX_SAME_SIDE_RUN - 1)
    assert CP.MAX_RESIDUAL == CP.ACTION_SIZE * CP.MAX_SAME_SIDE_RUN


def test_the_cap_is_the_smallest_that_still_admits_an_extend():
    assert CP.MAX_RESIDUAL >= 2 * CP.ACTION_SIZE
    assert CP.ACTION_SIZE == D("0.01")          # the venue's quantityMinimum


def test_a_flip_through_zero_resets_the_run():
    """84.3% of episodes overshoot through zero; the run counter has to
    follow the leg, not the sign of the residual."""
    inv = CP.Inventory()
    for _ in range(3):
        inv.apply_fill("YES", "0.01", "0.64", source="t")
    assert inv.run_len == 3 and inv.run_leg == "YES"
    inv.apply_fill("NO", "0.01", "0.36", source="t")
    assert inv.run_len == 1 and inv.run_leg == "NO"


def test_side_selection_never_consults_price_direction():
    """sign(dp) is falsified as a side signal and favored-side accumulation is
    a coin flip, so classification must depend on inventory alone."""
    inv = CP.Inventory()
    inv.apply_fill("YES", "0.01", "0.64", source="t")
    a = CP.classify("Up", inv)
    b = CP.classify("Up", inv)     # identical inventory, identical answer
    assert a == b == CP.EXTEND
    # Asserted over the AST, not the source text: classify's own docstring
    # explains WHY price may not be consulted, so a substring scan flags the
    # sentence that forbids the thing it is checking for.
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(CP.classify).lstrip())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    keys = {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for banned in ("bid", "ask", "price", "momentum", "delta", "dp"):
        assert banned not in names, banned
        assert banned not in keys, banned


# ------------------------------------------------------------- the bounds

@pytest.mark.parametrize("field,value", [
    ("MAX_ACTIONS", 12), ("MAX_YES", D("0.10")), ("MAX_NO", D("0.10")),
    ("MAX_TOTAL_SPEND", D("0.15")), ("MAX_RESIDUAL", D("0.04")),
    ("MAX_IMBALANCE", D("0.04")), ("ACTION_SIZE", D("0.01")),
])
def test_the_envelope_is_exactly_what_is_declared(field, value):
    assert getattr(CP, field) == value


def test_max_actions_stops_the_probe():
    p = CP.plan(_state(), CP.Inventory(), actions_taken=CP.MAX_ACTIONS)
    assert p["stop"] and "max_actions" in p["stop"]
    assert p["actions"] == []


def test_a_quote_that_could_not_be_afforded_is_never_rested():
    """Checked BEFORE quoting: the probe must not rest an order whose fill
    would breach a bound."""
    inv = CP.Inventory()
    for _ in range(CP.MAX_SAME_SIDE_RUN):
        inv.apply_fill("YES", "0.01", "0.64", source="t")
    assert CP.would_breach("YES", "0.64", inv) is not None
    assert CP.would_breach("NO", "0.36", inv) is None


def test_spend_cap_holds_the_envelope(monkeypatch):
    inv = CP.Inventory()
    monkeypatch.setattr(CP, "MAX_TOTAL_SPEND", D("0.005"))
    inv.apply_fill("YES", "0.01", "0.40", source="t")
    p = CP.plan(_state(), inv)
    assert p["stop"] is None or "spend" in p["stop"]
    if p["stop"] is None:
        assert all(h["held_by"] == "ENVELOPE" for h in p["holds"]) or p["actions"]


@pytest.mark.parametrize("over,needle", [
    ({"inventory_known": False}, "never assumes flat"),
    ({"state_age_s": CP.DEADMAN_S + 1}, "DEADMAN"),
])
def test_unknown_or_stale_state_stops_rather_than_quoting(over, needle):
    p = CP.plan(_state(**over), CP.Inventory())
    assert p["stop"] and needle in p["stop"]
    assert p["actions"] == []


def test_the_tte_floor_withholds_the_surface():
    """The lane's own TTE_policy, not a probe invention."""
    p = CP.plan(_state(tte_s=5000.0), CP.Inventory())
    assert p["actions"] == []
    assert "TTE_policy" in str(p.get("why_no_surface"))


# ---------------------------------------------------------- the frames

def test_the_frames_are_the_proven_ws_dialect():
    from src.gemini_execution import spec
    p = CP.plan(_state(), CP.Inventory())
    frames = CP.frames_for(p, "GEMI-BTC05M2609181055-UP")
    assert len(frames) == 2
    for f in frames:
        params = f["frame"]["params"]
        assert f["frame"]["method"] == spec.WS_PLACE_METHOD == "order.place"
        assert list(params) == list(spec.WS_ORDER_FIELDS)
        assert params["type"] == "LIMIT" and params["timeInForce"] == "MOC"
        assert params["side"] == "BUY"
        assert params["quantity"] == "0.01"
        assert params["eventOutcome"] in ("YES", "NO")
        assert len(params["clientOrderId"]) <= spec.WS_CLIENT_ID_MAX


def test_no_rest_execution_path_is_reachable_from_the_probe():
    """The proven transport is the socket. A REST order path substituted here
    would be the one substitution that silently undoes the whole recovery."""
    import ast
    import pathlib
    from src.gemini_execution import spec
    src = pathlib.Path("src/gemini_execution/continuous_probe.py").read_text()
    tree = ast.parse(src)
    consts = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    docs = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef,
                              ast.AsyncFunctionDef))}
    for c in consts:
        if c in docs:
            continue
        assert spec.PLACE_PATH not in c, c
        assert "/v1/prediction-markets/order" not in c, c


def test_the_probe_cannot_send_anything_itself():
    """It plans and renders. Sending stays behind the armed, latched path."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(
        "src/gemini_execution/continuous_probe.py").read_text())
    calls = [n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    for banned in ("request", "connect", "send", "place", "submit_once_ws"):
        assert banned not in calls, banned


# -------------------------------------------------------- the accounting

def test_every_component_is_reported_separately():
    inv = CP.Inventory()
    inv.apply_fill("YES", "0.01", "0.64", source="venue")
    inv.apply_fill("NO", "0.01", "0.33", source="venue")
    r = inv.report()
    assert r["matched_qty"] == "0.01"
    assert r["directional_residual"] == "0.00"
    assert r["avg_yes_cost"] == "0.64" and r["avg_no_cost"] == "0.33"
    assert r["effective_matched_pair_cost"] == "0.97"
    assert r["total_spend_usd"] == "0.0097"
    assert "never summed into one number" in r["labels"]
    assert "REALIZED" in r["labels"]


def test_a_cost_basis_is_never_called_realized():
    inv = CP.Inventory()
    inv.apply_fill("YES", "0.01", "0.10", source="venue")
    inv.apply_fill("NO", "0.01", "0.10", source="venue")
    r = inv.report()
    # a 0.20 pair would settle at 1.00, but nothing here claims a profit
    assert "realized" not in json.dumps({k: v for k, v in r.items()
                                         if k != "labels"}).lower()


def test_an_empty_side_has_no_average_and_no_pair_cost():
    inv = CP.Inventory()
    inv.apply_fill("YES", "0.01", "0.64", source="venue")
    r = inv.report()
    assert r["avg_no_cost"] is None
    assert r["effective_matched_pair_cost"] is None


@pytest.mark.parametrize("bad", ["0", "-0.01", 0])
def test_a_zero_or_negative_fill_is_refused(bad):
    with pytest.raises(ValueError):
        CP.Inventory().apply_fill("YES", bad, "0.5", source="venue")


def test_an_unknown_leg_is_refused():
    with pytest.raises(ValueError):
        CP.Inventory().apply_fill("MAYBE", "0.01", "0.5", source="venue")


# --------------------------------------------------- the frozen lane set

def test_the_frozen_lane_artifact_is_not_mutated():
    before = PR.load_frozen()["L5_ADAPTED_GEMINI_CRYPTO"].spec_sha256()
    CP.probe_params()
    after = PR.load_frozen()["L5_ADAPTED_GEMINI_CRYPTO"].spec_sha256()
    assert before == after


def test_the_probe_overrides_are_marked_as_ours_not_as_findings():
    sp = CP.probe_params()
    for f in ("base_parent_qty", "secondary_rung_qty", "max_residual"):
        p = sp.params[f]
        assert p.status == PR.OURS_ADAPTED
        assert not p.is_l5_evidence()
        assert "citable as an l5 finding" in str(p.prior_evidence).lower()


def test_everything_else_comes_from_the_lane_set_untouched():
    lane = PR.load_frozen()["L5_ADAPTED_GEMINI_CRYPTO"]
    sp = CP.probe_params()
    for f in PR.FIELDS:
        if f in ("base_parent_qty", "secondary_rung_qty", "max_residual"):
            continue
        assert sp.params[f] == lane.params[f], f


def test_the_probe_params_resolve_so_a_surface_can_emit():
    assert CP.probe_params().unresolved() == []


# --------------------------------------------------- stale-order handling

def test_an_off_surface_quote_is_cancelled_once_it_has_rested():
    now = 1000.0
    resting = [{"leg": "YES", "price": "0.60", "resting_since": now - 60}]
    out = CP.stale_orders(resting, now, {"YES": "0.64"})
    assert out[0]["cancel"] is True


def test_a_quote_still_on_the_surface_is_left_alone():
    now = 1000.0
    resting = [{"leg": "YES", "price": "0.64", "resting_since": now - 60}]
    assert CP.stale_orders(resting, now, {"YES": "0.64"}) == []


def test_queue_position_is_not_churned_below_the_minimum_rest_time():
    now = 1000.0
    resting = [{"leg": "YES", "price": "0.60", "resting_since": now - 5}]
    out = CP.stale_orders(resting, now, {"YES": "0.64"})
    assert out[0]["cancel"] is False
    assert "minimum_rest_time" in out[0]["why"]
